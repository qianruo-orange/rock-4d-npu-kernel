#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Wait for the board's kernel build, then compile the reviewed VEPU port.

This script builds only. It never installs a kernel, loads a module, restarts
services, changes boot files or pushes source. Use --check before --run.
"""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

EXPECTED_SOURCE = Path('/home/qianruo/kbuild/kernels/7.4-rc1/source')
DESTINATION = '.rock4d/drivers/rockchip-vepu510'
MODULE_FILES = {
    'Makefile', 'mpp_service.c', 'mpp_common.c', 'mpp_iommu.c',
    'mpp_rkvenc2.c', 'mpp_common.h', 'mpp_iommu.h', 'mpp_debug.h',
    'linux74-compat.h', 'include/uapi/linux/rk-mpp.h',
    'include/soc/rockchip/rockchip_iommu.h',
}
BUILD_NAMES = re.compile(
    r'(^|[-])(make|gmake|cc|c\+\+|cc1|cc1plus|gcc|g\+\+|clang|clang\+\+|'
    r'ld|ld\.bfd|ld\.lld|lld|as|rustc)(-[0-9.]+)?$'
)


class GuardStop(RuntimeError):
    pass


class StageFailure(RuntimeError):
    pass


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def safe_child(root, relative):
    if not isinstance(relative, str):
        raise GuardStop('File path must be a string')
    path = Path(relative)
    if path.is_absolute() or not path.parts or '..' in path.parts:
        raise GuardStop('Unsafe relative path: ' + relative)
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise GuardStop('Path escapes its root: ' + relative)
    return resolved


class Continuation:
    def __init__(self, base, proc_root=Path('/proc'),
                 expected_source=EXPECTED_SOURCE, poll_seconds=30, validate=True):
        self.base = base.resolve()
        self.proc_root = proc_root
        self.source = expected_source.resolve()
        self.poll_seconds = poll_seconds
        self.manifest_path = self.base / 'integration-manifest.json'
        self.manifest = json.loads(self.manifest_path.read_text())
        self.manifest_sha = digest(self.manifest_path)
        self.run_dir = None
        self.lock_file = None
        self.instance_lock = None
        self.owned_group = None
        self.state = {'schema': 1, 'source': str(self.source),
                      'manifest_sha256': self.manifest_sha, 'stages': []}
        self.ignore_pids = self.ancestor_pids()
        if validate:
            self.validate_manifest()

    def ancestor_pids(self):
        ignored = {os.getpid()}
        pid = os.getppid()
        while pid > 0 and pid not in ignored:
            ignored.add(pid)
            try:
                fields = (self.proc_root / str(pid) / 'stat').read_text()
                pid = int(fields.rsplit(')', 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                break
        return ignored

    def boot_id(self):
        return (self.proc_root / 'sys/kernel/random/boot_id').read_text().strip()

    def guard(self):
        if digest(self.manifest_path) != self.manifest_sha:
            raise GuardStop('Integration manifest changed while waiting/building')
        if self.boot_id() != self.manifest['boot_id']:
            raise GuardStop('Boot ID changed; review the current build first')
        if digest(self.source / '.config') != self.manifest['config_sha256']:
            raise GuardStop('Kernel configuration changed; review the manifest first')

    def validate_manifest(self):
        m = self.manifest
        if not Path(m['source']).is_absolute() or Path(m['source']).resolve() != self.source:
            raise GuardStop('Manifest source does not match the expected kernel tree')
        if m['module_source'] != 'module' or m['module_destination'] != DESTINATION:
            raise GuardStop('Unexpected module source or destination')
        if m['patch'] != 'kernel-integration.patch':
            raise GuardStop('Unexpected integration patch path')
        if not isinstance(m['release'], str) or not m['release']:
            raise GuardStop('Missing target kernel release')
        entries = m['files']
        paths = [entry['path'] for entry in entries]
        if len(set(paths)) != len(paths) or not paths:
            raise GuardStop('Patch file list is empty or duplicated')
        for entry in entries:
            path = safe_child(self.source, entry['path'])
            if entry['path'] == '.config' or path.is_relative_to(self.source / DESTINATION):
                raise GuardStop('Patch cannot change configuration or module copies')
            for key in ('before_sha256', 'after_sha256'):
                value = entry[key]
                if value is None and key == 'before_sha256':
                    continue
                if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
                    raise GuardStop('Invalid file checksum: ' + entry['path'])
        module_paths = [entry['path'] for entry in m['module_files']]
        if len(set(module_paths)) != len(module_paths) or set(module_paths) != MODULE_FILES:
            raise GuardStop('Module whitelist differs from the reviewed source set')
        for entry in m['module_files']:
            safe_child(self.base / 'module', entry['path'])
        self.guard()
        self.validate_inputs()

    def validate_inputs(self):
        self.guard()
        patch = self.base / self.manifest['patch']
        if digest(patch) != self.manifest['patch_sha256']:
            raise GuardStop('Integration patch checksum mismatch')
        header_paths = set()
        for line in patch.read_text().splitlines():
            if not (line.startswith('--- ') or line.startswith('+++ ')):
                continue
            value = line[4:].split()[0]
            if value == '/dev/null':
                continue
            if not value.startswith(('a/', 'b/')):
                raise GuardStop('Patch paths must use a/ and b/ prefixes')
            relative = value[2:]
            safe_child(self.source, relative)
            header_paths.add(relative)
        if header_paths != {item['path'] for item in self.manifest['files']}:
            raise GuardStop('Patch headers do not match the file manifest')
        for entry in self.manifest['module_files']:
            path = safe_child(self.base / 'module', entry['path'])
            if digest(path) != entry['sha256']:
                raise GuardStop('Module source checksum mismatch: ' + entry['path'])

    def verify_tree(self, before):
        for entry in self.manifest['files']:
            path = safe_child(self.source, entry['path'])
            expected = entry['before_sha256' if before else 'after_sha256']
            if expected is None:
                if path.exists() or path.is_symlink():
                    raise GuardStop('New patch path already exists: ' + entry['path'])
            elif not path.is_file() or digest(path) != expected:
                raise GuardStop('Kernel source checksum mismatch: ' + entry['path'])

    def builders(self):
        found = []
        for entry in self.proc_root.iterdir():
            if not entry.name.isdigit() or int(entry.name) in self.ignore_pids:
                continue
            try:
                comm = (entry / 'comm').read_text().strip()
                # Ignore unrelated processes before reading potentially protected argv.
                if not BUILD_NAMES.search(comm) and not comm.startswith(
                        ('aarch64-', 'arm-', 'x86_64-', 'riscv', 'clang')):
                    continue
                # /proc comm truncates long tool names, so also inspect argv[0].
                argv = (entry / 'cmdline').read_bytes().split(b'\0', 1)[0]
                executable = Path(os.fsdecode(argv)).name
                if not BUILD_NAMES.search(comm) and not BUILD_NAMES.search(executable):
                    continue
                fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
                if fields[0] == 'Z':
                    continue
                pgid = int(fields[2])
                if self.owned_group is not None and pgid == self.owned_group:
                    continue
                cwd = (entry / 'cwd').resolve(strict=True)
                if cwd.is_relative_to(self.source):
                    found.append({'pid': int(entry.name), 'pgid': pgid,
                                  'comm': comm, 'cwd': str(cwd)})
            except (FileNotFoundError, ProcessLookupError):
                continue
            except PermissionError as error:
                raise GuardStop('Cannot inspect a potential build process: ' + entry.name) from error
        return sorted(found, key=lambda item: item['pid'])

    def publish(self, state, stage, **details):
        self.state.update(state=state, stage=stage, updated_at=timestamp(), **details)
        atomic_json(self.base / 'continue-port-state.json', self.state)
        if self.run_dir is not None:
            atomic_json(self.run_dir / 'state.json', self.state)

    def wait_and_lock(self):
        while True:
            self.guard()
            active = self.builders()
            if active:
                self.publish('waiting', 'user-build', active_builds=active)
            else:
                # Use the existing shared lock; do not create another source tree.
                lock_path = self.source / '.rock4d/build.lock'
                lock_file = lock_path.open('a+')
                try:
                    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    lock_file.close()
                    self.publish('waiting', 'build-lock', active_builds=[])
                else:
                    self.lock_file = lock_file
                    self.guard()
                    active = self.builders()
                    if not active:
                        self.publish('running', 'lock-acquired', active_builds=[])
                        return
                    fcntl.flock(lock_file, fcntl.LOCK_UN)
                    lock_file.close()
                    self.lock_file = None
                    self.publish('waiting', 'user-build', active_builds=active)
            time.sleep(self.poll_seconds)

    def environment(self):
        env = dict(os.environ)
        for key in list(env):
            if key.startswith('KBUILD_') or key in {
                'MAKEFLAGS', 'MFLAGS', 'GNUMAKEFLAGS', 'MAKEFILES',
                'MAKEOVERRIDES', 'KCONFIG_CONFIG',
                'LOCALVERSION', 'ARCH', 'CROSS_COMPILE', 'LLVM', 'LLVM_IAS',
                'KCFLAGS', 'KCPPFLAGS', 'KAFLAGS', 'LDFLAGS_MODULE',
            }:
                del env[key]
        env['KCONFIG_CONFIG'] = '.config'
        return env

    def group_alive(self, pgid):
        try:
            os.killpg(pgid, 0)
            return True
        except ProcessLookupError:
            return False

    def stop_child(self, child):
        # start_new_session made this child PID the private process group ID.
        if self.owned_group != child.pid:
            raise GuardStop('Refusing to signal a process group not owned by this runner')
        if self.group_alive(child.pid):
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 5
            while self.group_alive(child.pid) and time.monotonic() < deadline:
                child.poll()
                time.sleep(0.05)
            if self.group_alive(child.pid):
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        child.wait()

    def stage(self, name, command):
        self.guard()
        if self.builders():
            raise GuardStop('Another kernel build started before stage ' + name)
        log = self.run_dir / (name + '.log')
        record = {'stage': name, 'command': command, 'started_at': timestamp(),
                  'log': str(log), 'status': 'running'}
        self.state['stages'].append(record)
        self.publish('running', name, active_builds=[])
        code = None
        child = None
        failure = None
        try:
            with log.open('w') as output:
                child = subprocess.Popen(command, cwd=self.source,
                                         env=self.environment(), stdout=output,
                                         stderr=subprocess.STDOUT,
                                         start_new_session=True)
                self.owned_group = child.pid
                while True:
                    try:
                        code = child.wait(timeout=self.poll_seconds)
                        break
                    except subprocess.TimeoutExpired:
                        self.guard()
                        active = self.builders()
                        if active:
                            raise GuardStop('Another kernel build appeared during ' + name)
                        self.publish('running', name, active_builds=[])
                self.guard()
                if self.builders():
                    raise GuardStop('Another kernel build appeared at the end of ' + name)
                if self.group_alive(child.pid):
                    raise StageFailure('Build parent exited with processes still in its group')
        except BaseException as error:
            failure = str(error) or type(error).__name__
            if child is not None:
                self.stop_child(child)
                code = child.returncode
            raise
        finally:
            self.owned_group = None
            record.update(finished_at=timestamp(), exit_code=code,
                          status='passed' if code == 0 and failure is None else 'failed')
            if failure is not None:
                record['error'] = failure
            atomic_json(self.run_dir / (name + '.json'), record)
            (self.run_dir / (name + '.exit')).write_text(str(code) + '\n')
        if code != 0:
            raise StageFailure(name + ' exited with ' + str(code))

    def kernel_command(self):
        return ['make', '-C', str(self.source), '-j8', 'KBUILD_MODPOST_WARN=',
                'KBUILD_EXTRA_SYMBOLS=', 'Image', 'modules', 'dtbs']

    def verify_artifacts(self):
        release_path = self.source / 'include/config/kernel.release'
        if release_path.read_text().strip() != self.manifest['release']:
            raise GuardStop('Built kernel release differs from the manifest')
        for relative in ('arch/arm64/boot/Image', 'vmlinux', 'Module.symvers',
                         'modules.order', 'arch/arm64/boot/dts/rockchip/rk3576-rock-4d.dtb'):
            path = self.source / relative
            if not path.is_file() or not path.stat().st_size:
                raise StageFailure('Missing or empty kernel artifact: ' + relative)

    def copy_module(self):
        name = 'copy-module'
        log = self.run_dir / (name + '.log')
        record = {'stage': name, 'status': 'running', 'started_at': timestamp(),
                  'log': str(log), 'files': []}
        self.state['stages'].append(record)
        self.publish('running', name, active_builds=[])
        code = 1
        try:
            self.guard()
            self.validate_inputs()
            if self.builders():
                raise GuardStop('Another kernel build started before copying the module')
            target = safe_child(self.source, DESTINATION)
            if target.exists():
                raise GuardStop('Module destination already exists; review previous run first')
            target.mkdir(parents=True)
            for entry in self.manifest['module_files']:
                src = safe_child(self.base / 'module', entry['path'])
                dst = safe_child(target, entry['path'])
                dst.parent.mkdir(parents=True, exist_ok=True)
                data = src.read_bytes()
                if hashlib.sha256(data).hexdigest() != entry['sha256']:
                    raise GuardStop('Module input changed during copying: ' + entry['path'])
                dst.write_bytes(data)
                record['files'].append(entry['path'])
            code = 0
        except BaseException as error:
            record['error'] = str(error) or type(error).__name__
            raise
        finally:
            record.update(finished_at=timestamp(), exit_code=code,
                          status='passed' if code == 0 else 'failed')
            atomic_json(self.run_dir / (name + '.json'), record)
            (self.run_dir / (name + '.exit')).write_text(str(code) + '\n')
            log.write_text('\n'.join(record['files']) + '\n' + record.get('error', ''))
        self.publish('running', name, active_builds=[])

    def backup_base_artifacts(self):
        name = 'backup-base-artifacts'
        record = {'stage': name, 'status': 'running', 'started_at': timestamp(),
                  'files': []}
        self.state['stages'].append(record)
        self.publish('running', name, active_builds=[])
        code = 1
        try:
            self.guard()
            if self.builders():
                raise GuardStop('Another kernel build started before backing up artifacts')
            for relative in ('arch/arm64/boot/Image', 'Module.symvers', '.config',
                             'System.map',
                             'arch/arm64/boot/dts/rockchip/rk3576-rock-4d.dtb'):
                src = self.source / relative
                if not src.is_file() or not src.stat().st_size:
                    raise StageFailure('Missing or empty base artifact: ' + relative)
                dst = self.run_dir / 'base-artifacts' / relative
                dst.parent.mkdir(parents=True, exist_ok=True)
                before = digest(src)
                shutil.copy2(src, dst)
                if digest(dst) != before or digest(src) != before:
                    raise GuardStop('Base artifact changed during backup: ' + relative)
                record['files'].append({'path': relative, 'sha256': before,
                                        'bytes': dst.stat().st_size})
            self.guard()
            code = 0
        except BaseException as error:
            record['error'] = str(error) or type(error).__name__
            raise
        finally:
            record.update(finished_at=timestamp(), exit_code=code,
                          status='passed' if code == 0 else 'failed')
            atomic_json(self.run_dir / (name + '.json'), record)
            (self.run_dir / (name + '.exit')).write_text(str(code) + '\n')
            (self.run_dir / (name + '.log')).write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + '\n')

    def snapshot_patch(self):
        name = 'snapshot-patch'
        snapshot = self.run_dir / 'kernel-integration.patch'
        record = {'stage': name, 'status': 'running', 'started_at': timestamp(),
                  'snapshot': str(snapshot)}
        self.state['stages'].append(record)
        self.publish('running', name, active_builds=[])
        code = 1
        try:
            self.validate_inputs()
            data = (self.base / self.manifest['patch']).read_bytes()
            checksum = hashlib.sha256(data).hexdigest()
            if checksum != self.manifest['patch_sha256']:
                raise GuardStop('Patch changed while making its run snapshot')
            snapshot.write_bytes(data)
            snapshot.chmod(0o400)
            if digest(snapshot) != checksum:
                raise GuardStop('Patch snapshot checksum mismatch')
            record['sha256'] = checksum
            code = 0
        except BaseException as error:
            record['error'] = str(error) or type(error).__name__
            raise
        finally:
            record.update(finished_at=timestamp(), exit_code=code,
                          status='passed' if code == 0 else 'failed')
            atomic_json(self.run_dir / (name + '.json'), record)
            (self.run_dir / (name + '.exit')).write_text(str(code) + '\n')
            (self.run_dir / (name + '.log')).write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + '\n')
        return snapshot

    def run(self):
        # A second waiter must never overwrite the active instance's state.
        instance_lock = (self.base / 'continue-port.lock').open('a+')
        try:
            fcntl.flock(instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            instance_lock.close()
            print(json.dumps({'state': 'instance-already-running'}), file=sys.stderr)
            return 3
        self.instance_lock = instance_lock
        run_name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + str(os.getpid())
        self.run_dir = self.base / 'continuation' / run_name
        try:
            self.run_dir.mkdir(parents=True, exist_ok=False)
            self.publish('waiting', 'initial-check', boot_id=self.manifest.get('boot_id'))
            self.validate_manifest()
            self.verify_tree(before=True)
            if safe_child(self.source, DESTINATION).exists():
                raise GuardStop('Module destination already exists; review previous run first')
            self.wait_and_lock()
            self.validate_inputs()
            self.verify_tree(before=True)
            self.stage('verify-base-build', self.kernel_command())
            self.verify_artifacts()
            self.backup_base_artifacts()
            self.validate_inputs()
            self.verify_tree(before=True)
            snapshot = self.snapshot_patch()
            patch_command = ['patch', '--batch', '--forward', '-p1', '-i', str(snapshot)]
            self.stage('patch-dry-run', patch_command + ['--dry-run'])
            self.stage('patch-apply', patch_command)
            self.verify_tree(before=False)
            self.copy_module()
            self.stage('rebuild-kernel', self.kernel_command())
            self.verify_artifacts()
            self.verify_tree(before=False)
            target = self.source / DESTINATION
            self.stage('build-vepu-module', ['make', '-C', str(self.source),
                       'M=' + str(target), '-j8', 'KBUILD_MODPOST_WARN=',
                       'KBUILD_EXTRA_SYMBOLS=', 'KCFLAGS=-Werror', 'modules'])
            module = target / 'rockchip_vepu510.ko'
            if not module.is_file() or not module.stat().st_size:
                raise StageFailure('Strict module build produced no rockchip_vepu510.ko')
            self.publish('compiled-awaiting-hardware-validation', 'complete',
                         module={'path': str(module), 'sha256': digest(module)},
                         installed=False, loaded=False, rebooted=False)
            return 0
        except GuardStop as error:
            self.publish('guarded-stop', self.state.get('stage', 'guard'), reason=str(error))
            return 3
        except (Exception, KeyboardInterrupt) as error:
            self.publish('failed', self.state.get('stage', 'unknown'),
                         reason=str(error) or type(error).__name__)
            return 1
        finally:
            if self.lock_file is not None:
                fcntl.flock(self.lock_file, fcntl.LOCK_UN)
                self.lock_file.close()
                self.lock_file = None
            if self.instance_lock is not None:
                fcntl.flock(self.instance_lock, fcntl.LOCK_UN)
                self.instance_lock.close()
                self.instance_lock = None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check', action='store_true', help='read-only input/process validation')
    action.add_argument('--run', action='store_true', help='wait and execute reviewed build stages')
    args = parser.parse_args()
    try:
        runner = Continuation(Path(os.environ.get('ROCK4D_VEPU_WORKDIR', '/home/qianruo/kbuild/media/vepu510-mpp-linux74')).resolve(), validate=not args.run)
        if args.check:
            runner.verify_tree(before=True)
            result = {'state': 'check-passed', 'source': str(runner.source),
                      'boot_id': runner.boot_id(), 'active_builds': runner.builders(),
                      'manifest_sha256': runner.manifest_sha, 'read_only': True}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        def interrupted(signum, frame):
            raise InterruptedError('Received signal ' + str(signum))
        signal.signal(signal.SIGTERM, interrupted)
        return runner.run()
    except (Exception, KeyboardInterrupt) as error:
        result = {'state': 'guarded-stop', 'reason': str(error) or type(error).__name__,
                  'updated_at': timestamp()}
        # Shared status belongs exclusively to run(), after its instance lock.
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        return 3


if __name__ == '__main__':
    sys.exit(main())
