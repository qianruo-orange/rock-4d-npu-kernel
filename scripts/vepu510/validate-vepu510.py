#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run bounded encoder checks on the installed candidate; leave the app alone."""
from collections import Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import resource
import subprocess
import sys
import time

BASE = Path(os.environ.get('ROCK4D_VEPU_WORKDIR', '/home/qianruo/kbuild/media/vepu510-mpp-linux74')).resolve()
OUT = BASE / 'validation'
MPP_COMMIT = '7916c3cc000fefc0bdf96636b7d54fe174724f44'
TESTS = [('captured_8_frame_smoke', 8), ('synthetic_180_frame_gop60', 180)]


def mpp_artifacts(plan):
    """Identify the files selected by the actual encoder test commands."""
    library = (BASE / 'mpp-userspace-build/mpp/librockchip_mpp.so.0').resolve(strict=True)
    encoder = (BASE / 'mpp-userspace-build/test/mpi_enc_test').resolve(strict=True)
    source_tree = (BASE / 'mpp-userspace').resolve(strict=True)
    if (library.parent / 'librockchip_mpp.so.1').resolve(strict=True) != library:
        raise RuntimeError('The test library SONAME link must select the recorded library')
    for name, _ in TESTS:
        command = plan['commands'][name]
        # These plans deliberately run one pinned CLI through timeout and env.
        if (len(command) < 8 or command[:5] != ['timeout', '-k', '5s', '60s', 'env'] or
                not command[5].startswith('LD_LIBRARY_PATH=') or
                Path(command[5].split('=', 1)[1]).resolve() != library.parent or
                command[6] != 'mpp_dmabuf_has_partial_ops=0' or
                Path(command[7]).resolve() != encoder):
            raise RuntimeError('Encoder command does not select the recorded MPP artifacts')
    git = ['git', '-c', f'safe.directory={source_tree}', '-C', str(source_tree)]
    commit = subprocess.check_output(git + ['rev-parse', 'HEAD'], text=True).strip()
    if commit != MPP_COMMIT:
        raise RuntimeError('Unexpected MPP source commit')
    subprocess.run(git + ['diff', '--exit-code', '--quiet'], check=True)
    subprocess.run(git + ['diff', '--cached', '--exit-code', '--quiet'], check=True)
    return {'source': {'path': str(source_tree), 'commit': commit},
            'library': {'path': str(library), 'sha256': hashlib.sha256(library.read_bytes()).hexdigest()},
            'encoder': {'path': str(encoder), 'sha256': hashlib.sha256(encoder.read_bytes()).hexdigest()}}


def check_stream(path, expected):
    def probe(*options):
        output = subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams',
            'v:0', *options, '-of', 'json', str(path)], text=True, timeout=30)
        return json.loads(output)
    stream = probe('-count_frames', '-show_entries',
                   'stream=codec_name,width,height,nb_read_frames')['streams'][0]
    assert stream['codec_name'] == 'h264'
    assert (stream['width'], stream['height']) == (1280, 720)
    assert int(stream['nb_read_frames']) == expected
    packets = probe('-show_packets', '-show_entries', 'packet=size')['packets']
    assert len(packets) == expected and all(int(x['size']) > 0 for x in packets)
    frames = probe('-show_frames', '-show_entries', 'frame=pict_type')['frames']
    types = Counter(x['pict_type'] for x in frames)
    assert types['I'] > 0 and types['P'] > 0
    if expected >= 180:
        assert types['I'] >= 3
    subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-err_detect', 'explode',
                    '-hwaccel', 'none', '-i', str(path), '-f', 'null', '-'],
                   timeout=30, check=True, stdout=subprocess.DEVNULL)
    return {'frames': expected, 'frame_types': dict(types),
            'bytes': path.stat().st_size, 'software_decode_passed': True}


def run_test(name, command, expected):
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    start = time.monotonic()
    with (OUT / (name + '.log')).open('w') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=75)
    elapsed = time.monotonic() - start
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    assert result.returncode == 0, name + ' failed; inspect its log'
    path = Path(command[command.index('-o') + 1])
    checked = check_stream(path, expected)
    checked.update(wall_seconds=elapsed,
                   process_cpu_seconds=after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime,
                   throughput_fps=expected / elapsed)
    return checked


if __name__ == '__main__':
    if not __debug__:
        raise RuntimeError('Do not disable validation checks with -O')
    assert os.getuid() == 0 and platform.release() == Path('/var/lib/rock4d-rt/expected-kernel').read_text().strip() and platform.release().startswith('7.4.0-rc1-npu-rt-vepu510')
    assert Path('/dev/mpp_service').exists()
    OUT.mkdir(exist_ok=True)
    plan = json.loads((BASE / 'mpp-userspace-validation-plan.json').read_text())
    source = plan['input_nv12']
    assert hashlib.sha256(Path(source['path']).read_bytes()).hexdigest() == source['sha256']
    artifacts = mpp_artifacts(plan)
    start = float(Path('/proc/uptime').read_text().split()[0])
    report = {'started_at': datetime.now().astimezone().isoformat(),
              'kernel': platform.release(), 'tests': {}, 'passed': False,
              'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
              'input_sha256': source['sha256'],
              'mpp_artifacts': artifacts,
              'mpp_artifacts_unchanged': False,
              'application_encoder_changed': False,
              'timing_note': 'Process wall/CPU time is standalone throughput, not application end-to-end latency.'}
    code = 1
    try:
        for name, expected in TESTS:
            report['tests'][name] = run_test(name, plan['commands'][name], expected)
        code = 0
    except Exception as error:
        report['error'] = str(error) or type(error).__name__
    finally:
        try:
            report['mpp_artifacts_unchanged'] = mpp_artifacts(plan) == artifacts
            if not report['mpp_artifacts_unchanged']:
                raise RuntimeError('MPP artifacts changed during hardware validation')
        except Exception as error:
            report['mpp_artifacts_unchanged'] = False
            report['artifact_error'] = str(error) or type(error).__name__
            code = 1
        log = subprocess.check_output(['dmesg', '--raw'], text=True)
        recent = []
        for line in log.splitlines():
            match = re.search(r'\[\s*(\d+\.\d+)\]', line)
            if match and float(match.group(1)) >= start:
                recent.append(line)
        (OUT / 'kernel-test-window.log').write_text('\n'.join(recent) + '\n')
        errors = [line for line in recent if (
            re.search(r'iommu|vepu|rkvenc|mpp_service|rockchip-pm-domain', line, re.I)
            and re.search(r'fault|timed out|timeout|failed|resetting|error', line, re.I))
            or re.search(r'BUG:|Oops:|WARNING:|Call trace:|hung task', line)]
        report['kernel_errors'] = errors
        report['passed'] = code == 0 and not errors
        report['finished_at'] = datetime.now().astimezone().isoformat()
        (OUT / 'hardware-validation.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))
    sys.exit(0 if report['passed'] else 1)
