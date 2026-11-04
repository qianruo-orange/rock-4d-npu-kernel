#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build a separate VEPU510 kernel and matching modules; never install them."""

import argparse
from datetime import datetime, timezone
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

BASE = Path(os.environ.get('ROCK4D_VEPU_WORKDIR', '/home/qianruo/kbuild/media/vepu510-mpp-linux74')).resolve()
RELEASE = '7.4.0-rc1-npu-rt-vepu510-r2'
VERSION = '7.4~rc1-npu-rt-vepu510-2'
spec = importlib.util.spec_from_file_location('port_support', Path(__file__).resolve().with_name('continue-port.py'))
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)


class Build(support.Continuation):
    def __init__(self):
        super().__init__(BASE)
        self.state.update(release=RELEASE, package_version=VERSION,
                          config_sha256=self.manifest['config_sha256'],
                          boot_id=self.boot_id(), installed=False)

    def publish(self, state, stage, **details):
        self.state.update(state=state, stage=stage,
                          updated_at=support.timestamp(), **details)
        support.atomic_json(BASE / 'isolated-kernel-state.json', self.state)
        if self.run_dir:
            support.atomic_json(self.run_dir / 'state.json', self.state)

    def environment(self):
        env = super().environment()
        env['PATH'] = env.get('PATH', '') + ':/usr/sbin:/sbin'
        env['DEB_BUILD_PROFILES'] = 'pkg.linux-upstream.nokerneldbg'
        env['DEB_BUILD_OPTIONS'] = 'parallel=8'
        return env

    def preflight(self):
        self.validate_manifest()
        self.verify_tree(before=False)
        upstream = json.loads((BASE / 'continue-port-state.json').read_text())
        if upstream.get('state') != 'compiled-awaiting-hardware-validation':
            raise support.GuardStop('Complete the upstream kernel port first')
        unit = subprocess.run(
            ['systemctl', '--user', 'show', 'vepu510-linux74-port.service',
             '-p', 'ActiveState', '-p', 'ExecMainStatus'],
            text=True, capture_output=True, check=True)
        if set(unit.stdout.splitlines()) != {'ActiveState=inactive', 'ExecMainStatus=0'}:
            raise support.GuardStop('Upstream build unit has not exited successfully')
        for entry in self.manifest['module_files']:
            target = self.source / self.manifest['module_destination'] / entry['path']
            if support.digest(target) != entry['sha256']:
                raise support.GuardStop('Copied module input changed: ' + entry['path'])
        config = (self.source / '.config').read_text().splitlines()
        for value in ['CONFIG_PREEMPT_RT=y', 'CONFIG_HZ=1000']:
            if value not in config:
                raise support.GuardStop('Missing required configuration: ' + value)
        if self.builders():
            raise support.GuardStop('Another kernel build is active')

    def make(self, *arguments):
        return ['make', '-C', str(self.source), '-j8', 'LOCALVERSION=-vepu510-r2',
                'KBUILD_MODPOST_WARN=', 'KBUILD_EXTRA_SYMBOLS=', *arguments]

    def verify_packages(self):
        parent = self.source.parent
        destination = parent / 'packages'
        destination.mkdir(exist_ok=True)
        records = []
        expected = [f'linux-image-{RELEASE}', f'linux-headers-{RELEASE}']
        for package in [*expected, 'linux-libc-dev']:
            name = f'{package}_{VERSION}_arm64.deb'
            src = parent / name
            dst = destination / name
            if src.exists():
                if dst.exists():
                    raise support.GuardStop('An installation package already exists: ' + name)
                shutil.move(str(src), dst)
            if package not in expected:
                continue
            fields = subprocess.check_output(
                ['dpkg-deb', '-f', str(dst), 'Package', 'Version', 'Architecture'],
                text=True).splitlines()
            actual = dict(row.split(': ', 1) for row in fields)
            if actual != {'Package': package, 'Version': VERSION, 'Architecture': 'arm64'}:
                raise support.StageFailure('Package metadata mismatch: ' + name)
            listing = subprocess.check_output(['dpkg-deb', '-c', str(dst)], text=True)
            (self.run_dir / (package + '.contents')).write_text(listing)
            required = ([f'./boot/vmlinuz-{RELEASE}',
                         f'./boot/config-{RELEASE}',
                         f'./usr/lib/linux-image-{RELEASE}/rockchip/rk3576-rock-4d.dtb']
                        if package.startswith('linux-image-') else
                        [f'./usr/src/linux-headers-{RELEASE}/Module.symvers',
                         f'./usr/src/linux-headers-{RELEASE}/include/generated/utsrelease.h'])
            for path in required:
                if not any(row.split()[-1] == path for row in listing.splitlines()):
                    raise support.StageFailure('Package is missing ' + path)
            records.append({'package': package, 'version': VERSION,
                            'architecture': 'arm64', 'path': str(dst),
                            'sha256': support.digest(dst), 'bytes': dst.stat().st_size})
        return records

    def execute(self, resume_external=False):
        previous = json.loads((BASE / 'isolated-kernel-state.json').read_text()) if resume_external else None
        with (BASE / 'isolated-kernel.lock').open('a+') as instance:
            fcntl.flock(instance, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.run_dir = BASE / 'isolated-build' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            self.run_dir.mkdir(parents=True, exist_ok=False)
            try:
                self.publish('running', 'preflight')
                self.preflight()
                with (self.source / '.rock4d/build.lock').open('a+') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.preflight()
                    if resume_external:
                        passed = next((x for x in previous.get('stages', [])
                                       if x['stage'] == 'kernel-packages' and
                                       x.get('status') == 'passed' and x.get('exit_code') == 0), None)
                        if not passed or previous.get('release') != RELEASE:
                            raise support.GuardStop('No successful matching package stage to resume')
                        self.state['stages'].append(dict(passed, reused=True))
                    else:
                        self.stage('kernel-packages', self.make(
                            'KDEB_PKGVERSION=' + VERSION, 'KDEB_COMPRESS=zstd', 'bindeb-pkg'))
                    if (self.source / 'include/config/kernel.release').read_text().strip() != RELEASE:
                        raise support.StageFailure('Built release differs from target')
                    self.verify_tree(before=False)
                    packages = self.verify_packages()
                    drivers = self.source / '.rock4d/drivers'
                    aic = drivers / 'aic8800-usb-5.0-kiln/USB/driver_fw/drivers'
                    groups = [('npu', drivers / 'kiln-rknpu-0.9.8'),
                              ('wifi', aic / 'aic8800'), ('bluetooth', aic / 'aic_btusb'),
                              ('mpp', drivers / 'rockchip-vepu510')]
                    for name, path in groups:
                        self.stage(name + '-clean', self.make('M=' + str(path), 'clean'))
                        options = ['KCFLAGS=-Werror'] if name == 'mpp' else []
                        self.stage(name, self.make('M=' + str(path), *options, 'modules'))
                    module_paths = [('rknpu', groups[0][1] / 'rknpu.ko'),
                                    ('aic_load_fw', aic / 'aic8800/aic_load_fw/aic_load_fw.ko'),
                                    ('aic8800_fdrv', aic / 'aic8800/aic8800_fdrv/aic8800_fdrv.ko'),
                                    ('aic_btusb', aic / 'aic_btusb/aic_btusb.ko'),
                                    ('rockchip_vepu510', groups[3][1] / 'rockchip_vepu510.ko')]
                    modules = []
                    for name, path in module_paths:
                        magic = subprocess.check_output(['/usr/sbin/modinfo', '-F', 'vermagic', str(path)], text=True).strip()
                        if not magic.startswith(RELEASE + ' ') or 'preempt_rt' not in magic:
                            raise support.StageFailure('Module ABI mismatch: ' + name)
                        modules.append({'name': name, 'path': str(path), 'vermagic': magic,
                                        'sha256': support.digest(path), 'bytes': path.stat().st_size})
                    symbols = {row.split()[1] for row in (self.source / 'Module.symvers').read_text().splitlines()}
                    if not {'rockchip_iommu_reset', 'rockchip_iommu_mask_irq',
                            'rockchip_pmu_idle_request', 'rockchip_iommu_set_fault_handler'}.issubset(symbols):
                        raise support.StageFailure('Final kernel helpers are absent')
                    os.sync()
                    self.publish('packages-ready-for-installation', 'complete',
                                 packages=packages, modules=modules)
                    return 0
            except Exception as error:
                self.publish('failed', self.state['stage'], reason=str(error))
                return 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--resume-external', action='store_true')
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(InterruptedError('SIGTERM')))
    builder = Build()
    if args.run:
        sys.exit(builder.execute(args.resume_external))
    builder.preflight()
    print(json.dumps({'state': 'preflight-passed', 'release': RELEASE,
                      'source': str(builder.source), 'build_started': False}))
