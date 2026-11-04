#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compare standalone encoders on identical frames after hardware validation."""
import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import re
import resource
import statistics
import subprocess
import time

BASE = Path(os.environ.get('ROCK4D_VEPU_WORKDIR',
    '/home/qianruo/kbuild/media/vepu510-mpp-linux74')).resolve()
FRAMES = 180
FRAME_BYTES = 1280 * 720 * 3 // 2


def command(args, log, timeout=120):
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    start = time.monotonic()
    with log.open('w') as stream:
        subprocess.run(args, stdout=stream, stderr=subprocess.STDOUT,
                       check=True, timeout=timeout)
    wall = time.monotonic() - start
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime
    return {'wall_seconds': wall, 'process_cpu_seconds': cpu,
            'throughput_fps': FRAMES / wall,
            'wall_ms_per_frame_from_throughput': 1000 * wall / FRAMES,
            'cpu_ms_per_frame': 1000 * cpu / FRAMES,
            'average_cpu_cores': cpu / wall, 'command': args}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    validation = json.loads((BASE / 'validation/hardware-validation.json').read_text())
    if (not validation.get('passed') or validation['kernel'] != platform.release() or
            validation.get('boot_id') != Path('/proc/sys/kernel/random/boot_id').read_text().strip()):
        raise RuntimeError('A passing hardware validation for the running kernel is required')
    if not args.run:
        print('Preflight passed; --run encodes three alternating pairs of 180 frames.')
        return
    if os.getuid() != 0 or not Path('/dev/mpp_service').exists():
        raise RuntimeError('Root and the validated MPP device are required')
    plan = json.loads((BASE / 'mpp-userspace-validation-plan.json').read_text())
    source = Path(plan['input_nv12']['path'])
    sample = source.read_bytes()
    if (hashlib.sha256(sample).hexdigest() != plan['input_nv12']['sha256'] or
            len(sample) != 8 * FRAME_BYTES):
        raise RuntimeError('The eight captured source frames changed')
    out = BASE / 'benchmarks' / datetime.now().astimezone().strftime('%Y%m%dT%H%M%S%z')
    out.mkdir(parents=True, exist_ok=False)
    reference = out / 'input-180.nv12'
    with reference.open('wb') as stream:
        for i in range(FRAMES):
            index = i % 8
            stream.write(sample[index * FRAME_BYTES:(index + 1) * FRAME_BYTES])
    spec = importlib.util.spec_from_file_location('validation',
        Path(__file__).resolve().with_name('validate-vepu510.py'))
    checks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checks)
    start_uptime = float(Path('/proc/uptime').read_text().split()[0])
    report = {'kernel': platform.release(), 'started_at': datetime.now().astimezone().isoformat(),
              'input_sha256': hashlib.sha256(reference.read_bytes()).hexdigest(),
              'input_description': 'Eight captured frames repeated to 180; identical input for both encoders.',
              'frames': FRAMES, 'fps_metadata': 30, 'gop': 60, 'target_bitrate': 2500000,
              'color_range': 'full (both PSNR inputs explicitly preserve full range)',
              'cpu_affinity': sorted(os.sched_getaffinity(0)),
              'application_service_state': subprocess.run(['systemctl', 'is-active',
                  'autonomous-driving-car'], capture_output=True, text=True).stdout.strip(),
              'thermal_before_millicelsius': {str(p): p.read_text().strip()
                  for p in Path('/sys/class/thermal').glob('thermal_zone*/temp')},
              'software_threads': min(4, len(os.sched_getaffinity(0))),
              'note': 'Whole-process throughput and child CPU time; not per-frame latency or camera-to-browser latency. '
                      'CBR implementations and output quality may differ; report measured bitrate and PSNR.',
              'runs': [], 'passed': False}
    try:
        for pair, order in enumerate([('hardware', 'software'), ('software', 'hardware'),
                                      ('hardware', 'software')], 1):
            for backend in order:
                name = f'{pair}-{backend}'
                output = out / (name + '.h264')
                if backend == 'hardware':
                    cmd = list(plan['commands']['captured_8_frame_smoke'])
                    for flag, value in [('-i', reference), ('-n', FRAMES), ('-o', output)]:
                        cmd[cmd.index(flag) + 1] = str(value)
                else:
                    cmd = ['ffmpeg', '-nostdin', '-v', 'warning', '-y', '-f', 'rawvideo',
                           '-pixel_format', 'nv12', '-color_range', 'pc', '-video_size', '1280x720', '-framerate', '30',
                           '-i', str(reference), '-frames:v', str(FRAMES), '-an', '-c:v', 'libx264',
                           '-preset', 'veryfast', '-tune', 'zerolatency', '-threads',
                           str(report['software_threads']), '-b:v', '2500000', '-maxrate', '2500000',
                           '-bufsize', '5000000', '-g', '60', '-keyint_min', '60', '-sc_threshold', '0',
                           '-bf', '0', '-color_range', 'pc', '-x264-params', 'nal-hrd=cbr:force-cfr=1', '-f', 'h264', str(output)]
                row = command(cmd, out / (name + '.log'))
                row.update(backend=backend, pair=pair, **checks.check_stream(output, FRAMES))
                row['measured_bitrate'] = output.stat().st_size * 8 * 30 / FRAMES
                psnr = out / (name + '.psnr')
                filters = ('[0:v]scale=in_range=full:out_range=full,format=yuv420p,setpts=PTS-STARTPTS[ref];'
                           '[1:v]scale=in_range=full:out_range=full,format=yuv420p,setpts=PTS-STARTPTS[dec];'
                           f'[dec][ref]psnr=stats_file={psnr}:shortest=1')
                command(['ffmpeg', '-nostdin', '-v', 'warning', '-f', 'rawvideo',
                         '-pixel_format', 'nv12', '-video_size', '1280x720', '-framerate', '30',
                         '-i', str(reference), '-hwaccel', 'none', '-i', str(output),
                         '-filter_complex', filters, '-frames:v', str(FRAMES), '-f', 'null', '-'],
                        out / (name + '-quality.log'))
                mse = [float(x) for x in re.findall(r'mse_avg:([\d.]+)', psnr.read_text())]
                if len(mse) != FRAMES:
                    raise RuntimeError('Quality comparison frame count differs')
                mean_mse = statistics.mean(mse)
                row['psnr_db_from_mean_mse'] = (10 * math.log10(255 ** 2 / mean_mse)
                                               if mean_mse else 'infinite')
                report['runs'].append(row)
        report['summary'] = {backend: {
            key: statistics.median(row[key] for row in report['runs'] if row['backend'] == backend)
            for key in ['throughput_fps', 'cpu_ms_per_frame', 'wall_ms_per_frame_from_throughput',
                        'measured_bitrate', 'psnr_db_from_mean_mse']}
            for backend in ['hardware', 'software']}
        report['passed'] = True
    except Exception as error:
        report['error'] = str(error)
    finally:
        recent = []
        for line in subprocess.check_output(['dmesg', '--raw'], text=True).splitlines():
            match = re.search(r'\[\s*(\d+\.\d+)\]', line)
            if match and float(match.group(1)) >= start_uptime:
                recent.append(line)
        (out / 'kernel.log').write_text('\n'.join(recent) + '\n')
        report['kernel_errors'] = [line for line in recent if (
            re.search(r'iommu|vepu|rkvenc|mpp_service|rockchip-pm-domain', line, re.I)
            and re.search(r'fault|timed out|timeout|failed|resetting|error', line, re.I))
            or re.search(r'BUG:|Oops:|WARNING:|Call trace:|hung task', line)]
        report['passed'] = report['passed'] and not report['kernel_errors']
        report['finished_at'] = datetime.now().astimezone().isoformat()
        report['thermal_after_millicelsius'] = {str(p): p.read_text().strip()
            for p in Path('/sys/class/thermal').glob('thermal_zone*/temp')}
        (out / 'comparison.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({'passed': report['passed'], 'report': str(out / 'comparison.json'),
                          'summary': report.get('summary'), 'error': report.get('error')}, indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
