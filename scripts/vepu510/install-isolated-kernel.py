#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Install the checked separate kernel and select it; never reboot."""
import argparse
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
from datetime import datetime

if not __debug__:
    raise RuntimeError('Installer requires normal Python checks; do not use -O')

BASE = Path(os.environ.get('ROCK4D_VEPU_WORKDIR', '/home/qianruo/kbuild/media/vepu510-mpp-linux74')).resolve()
RELEASE = '7.4.0-rc1-npu-rt-vepu510-r2'
OLD = '7.4.0-rc1-npu-rt-vepu510'
HOOK = Path('/etc/initramfs/post-update.d/99-uboot')
DISABLED = HOOK.with_name('99-uboot.vepu510-disabled')
EXPECTED = Path('/var/lib/rock4d-rt/expected-kernel')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def run(*args, capture=False):
    return subprocess.run(list(args), check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def atomic_link(name, target):
    path = Path('/boot') / name
    tmp = path.with_name('.' + name + '.vepu-' + str(os.getpid()))
    if tmp.exists() or tmp.is_symlink():
        raise RuntimeError('Temporary boot link already exists: ' + str(tmp))
    tmp.symlink_to(target)
    os.replace(tmp, path)


def preflight():
    s = json.loads((BASE / 'isolated-kernel-state.json').read_text())
    assert s['state'] == 'packages-ready-for-installation', 'Packages are not ready'
    assert s['release'] == RELEASE and platform.release() == OLD
    assert s['boot_id'] == Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    assert b'radxa,rock-4d\0' in Path('/proc/device-tree/compatible').read_bytes()
    source = Path(s['source'])
    assert digest(source / '.config') == s['config_sha256']
    assert (source / 'include/config/kernel.release').read_text().strip() == RELEASE
    assert {'rknpu', 'aic_load_fw', 'aic8800_fdrv', 'aic_btusb', 'rockchip_vepu510'} == {
        m['name'] for m in s['modules']}
    for f in [*s['packages'], *s['modules']]:
        assert digest(f['path']) == f['sha256'], f['path']
    for package in s['packages']:
        fields = run('dpkg-deb', '-f', package['path'], 'Package', 'Version',
                     'Architecture', capture=True)
        expected = {'Package': package['package'], 'Version': package['version'],
                    'Architecture': 'arm64'}
        assert dict(row.split(': ', 1) for row in fields.splitlines()) == expected
    assert HOOK.is_file() and not DISABLED.exists()
    for name in ['Image', 'uInitrd', 'dtb']:
        p = Path('/boot') / name
        assert p.is_symlink() and OLD in os.readlink(p) and p.resolve().exists()
    assert Path('/lib/modules/' + OLD).is_dir()
    assert not Path('/lib/modules/' + RELEASE).exists(), 'Candidate was already installed'
    assert not Path('/boot/Image-' + RELEASE).exists()
    assert not Path('/boot/dtb-' + RELEASE).exists()
    npu_source = Path('/usr/src/kiln-rknpu-0.9.8/driver/rknpu/rknpu_drv.c')
    assert '#include "../compat/soc/rockchip/rockchip_iommu.h"' in npu_source.read_text(), \
        'Apply the NPU private IOMMU header compatibility patch before installing headers'
    return s


def install(s):
    assert os.getuid() == 0, 'Run the installer as root'
    os.environ['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
    os.environ['DEBIAN_FRONTEND'] = 'noninteractive'
    for key in ['KBUILD_MODPOST_WARN', 'KBUILD_EXTRA_SYMBOLS', 'MAKEFLAGS',
                'MAKEOVERRIDES', 'MAKEFILES', 'LOCALVERSION']:
        os.environ.pop(key, None)
    lock = open('/var/lock/rock4d-kernel-install.lock', 'a+')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    s = preflight()
    backup = Path(tempfile.mkdtemp(prefix='rock4d-vepu510-', dir='/var/backups'))
    backup.chmod(0o700)
    old = {'links': {name: os.readlink(Path('/boot') / name)
                     for name in ['Image', 'uInitrd', 'dtb']},
           'expected': EXPECTED.read_text(), 'payloads': []}
    for label, path in [('Image', Path('/boot/Image')),
                        ('uInitrd', Path('/boot/uInitrd')),
                        ('board.dtb', Path('/boot/dtb/rockchip/rk3576-rock-4d.dtb'))]:
        shutil.copy2(path, backup / label)
        old['payloads'].append({'path': str(path.resolve()),
                                'backup': label, 'sha256': digest(path)})
    shutil.copy2('/boot/armbianEnv.txt', backup / 'armbianEnv.txt')
    shutil.copy2(HOOK, backup / '99-uboot')
    (backup / 'selection.json').write_text(json.dumps(old, indent=2) + '\n')
    rollback = '''#!/usr/bin/env python3
import json,os,shutil
from pathlib import Path
b=Path(__file__).resolve().parent
s=json.loads((b/'selection.json').read_text())
for f in s['payloads']:shutil.copy2(b/f['backup'],f['path'])
for name,target in s['links'].items():
 p=Path('/boot')/name;t=p.with_name('.'+name+'.rollback-'+str(os.getpid()))
 t.symlink_to(target);os.replace(t,p)
Path('/var/lib/rock4d-rt/expected-kernel').write_text(s['expected'])
os.sync()
print('Previous kernel selected; no reboot performed.')
'''
    (backup / 'rollback.py').write_text(rollback)
    (backup / 'rollback.py').chmod(0o700)
    record = {'started_at': datetime.now().astimezone().isoformat(),
              'release': RELEASE, 'backup': str(backup), 'installed': False,
              'rebooted': False}
    try:
        HOOK.rename(DISABLED)
        packages = {p['package']: p['path'] for p in s['packages']}
        run('dpkg', '-i', packages['linux-headers-' + RELEASE],
            packages['linux-image-' + RELEASE])
        headers = Path('/usr/src/linux-headers-' + RELEASE)
        conf = (headers / 'include/config/auto.conf').read_text().splitlines()
        assert 'CONFIG_HZ=1000' in conf and 'CONFIG_PREEMPT_RT=y' in conf
        for name, version in [('kiln-rknpu', '0.9.8'),
                              ('aic8800-usb', '5.0-kiln-linux74')]:
            status = run('dkms', 'status', '-m', name, '-v', version, '-k',
                         RELEASE, '-a', 'aarch64', capture=True)
            if ': installed' not in status:
                run('dkms', 'install', '-m', name, '-v', version, '-k', RELEASE,
                    '-a', 'aarch64', '-j', '8')
        mpp = next(m for m in s['modules'] if m['name'] == 'rockchip_vepu510')
        target = Path('/lib/modules') / RELEASE / 'updates/rockchip_vepu510.ko'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(mpp['path'], target)
        assert digest(target) == mpp['sha256']
        run('depmod', '-a', RELEASE)
        installed_modules = {}
        for name in ['rknpu', 'aic_load_fw_usb', 'aic8800_fdrv_usb',
                     'aic_btusb_usb', 'rockchip_jpegd', 'rockchip_vepu510']:
            magic = run('modinfo', '-k', RELEASE, '-F', 'vermagic', name,
                        capture=True).strip()
            assert magic.startswith(RELEASE + ' ') and 'preempt_rt' in magic
            run('modprobe', '--show-depends', '--set-version', RELEASE, name,
                capture=True)
            installed_modules[name] = magic
        source = Path(s['source'])
        packed = Path('/boot/vmlinuz-' + RELEASE).read_bytes()
        image = gzip.decompress(packed) if packed[:2] == b'\x1f\x8b' else packed
        assert image == (source / 'arch/arm64/boot/Image').read_bytes()
        image_path = Path('/boot/Image-' + RELEASE)
        image_path.write_bytes(image)
        image_path.chmod(0o644)
        dtbdir = Path('/boot/dtb-' + RELEASE)
        shutil.copytree('/usr/lib/linux-image-' + RELEASE, dtbdir, symlinks=True)
        dtb = dtbdir / 'rockchip/rk3576-rock-4d.dtb'
        assert digest(dtb) == digest(source / 'arch/arm64/boot/dts/rockchip/rk3576-rock-4d.dtb')
        for node in ['/soc/npu@27700000', '/soc/jpegd@27910000']:
            assert run('fdtget', str(dtb), node, 'status', capture=True).strip() == 'okay'
        assert run('fdtget', str(dtb), '/video-codec@27a00000', 'compatible',
                   capture=True).strip() == 'rockchip,rkv-encoder-rk3576'
        assert 'rockchip,rk3576-iommu' in run('fdtget', str(dtb),
            '/iommu@27a0f000', 'compatible', capture=True)
        assert run('fdtget', str(dtb), '/mpp-srv', 'compatible',
                   capture=True).strip() == 'rockchip,mpp-service'
        initrd = Path('/boot/initrd.img-' + RELEASE)
        run('update-initramfs', '-u' if initrd.exists() else '-c', '-k', RELEASE)
        run('gzip', '-t', str(initrd))
        uinitrd = Path('/boot/uInitrd-' + RELEASE)
        run('mkimage', '-A', 'arm64', '-O', 'linux', '-T', 'ramdisk', '-C',
            'gzip', '-n', 'uInitrd', '-d', str(initrd), str(uinitrd))
        extracted = backup / 'new-initrd-payload'
        run('dumpimage', '-T', 'ramdisk', '-p', '0', '-o', str(extracted), str(uinitrd))
        assert digest(extracted) == digest(initrd)
        listing = run('lsinitramfs', str(initrd), capture=True)
        assert 'lib/modules/' + RELEASE + '/' in listing
        (backup / 'new-initramfs-files.txt').write_text(listing)
        for f in old['payloads']:
            assert digest(f['path']) == f['sha256'], 'Old boot payload changed'
        assert digest('/boot/armbianEnv.txt') == digest(backup / 'armbianEnv.txt')
        EXPECTED.write_text(RELEASE + '\n')
        for name, target_name in [('dtb', 'dtb-' + RELEASE),
                                  ('uInitrd', 'uInitrd-' + RELEASE),
                                  ('Image', 'Image-' + RELEASE)]:
            atomic_link(name, target_name)
        record.update(installed=True, modules=installed_modules,
                      boot_selection={n: os.readlink(Path('/boot') / n)
                                      for n in ['Image', 'uInitrd', 'dtb']})
    except BaseException as error:
        record['error'] = str(error) or type(error).__name__
        run(sys.executable, str(backup / 'rollback.py'))
        raise
    finally:
        if DISABLED.exists():
            DISABLED.rename(HOOK)
        record['finished_at'] = datetime.now().astimezone().isoformat()
        (backup / 'installation.json').write_text(json.dumps(record, indent=2) + '\n')
        (BASE / 'isolated-installation.json').write_text(json.dumps(record, indent=2) + '\n')
        os.sync()
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true')
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(InterruptedError('SIGTERM')))
    state = preflight()
    if args.install:
        install(state)
    else:
        print(json.dumps({'state': 'preflight-passed', 'release': RELEASE,
                          'packages': state['packages'], 'installation_started': False}))
