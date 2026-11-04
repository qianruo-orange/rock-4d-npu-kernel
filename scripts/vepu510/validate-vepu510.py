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
    start = float(Path('/proc/uptime').read_text().split()[0])
    report = {'started_at': datetime.now().astimezone().isoformat(),
              'kernel': platform.release(), 'tests': {}, 'passed': False,
              'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
              'input_sha256': source['sha256'],
              'application_encoder_changed': False,
              'timing_note': 'Process wall/CPU time is standalone throughput, not application end-to-end latency.'}
    code = 1
    try:
        for name, expected in [('captured_8_frame_smoke', 8),
                               ('synthetic_180_frame_gop60', 180)]:
            report['tests'][name] = run_test(name, plan['commands'][name], expected)
        code = 0
    except Exception as error:
        report['error'] = str(error) or type(error).__name__
    finally:
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
