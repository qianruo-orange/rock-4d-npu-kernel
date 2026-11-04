#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
# Load only the kernel revision which passed this boot's hardware validation.
set -euo pipefail
export PATH="$PATH:/usr/sbin:/sbin"
[[ $(id -u) == 0 ]] || { echo 'Run as root'; exit 1; }
work=${ROCK4D_VEPU_WORKDIR:-/home/qianruo/kbuild/media/vepu510-mpp-linux74}
release=$(uname -r)
python3 - "$work/validation/hardware-validation.json" <<'PY'
import json,platform,sys
from pathlib import Path
s=json.loads(Path(sys.argv[1]).read_text())
if not (s.get('passed') and s['kernel']==platform.release() and
        s['boot_id']==Path('/proc/sys/kernel/random/boot_id').read_text().strip()):
    raise SystemExit('Passing hardware validation for this boot is required')
PY
[[ $(modinfo -F description rockchip_vepu510) == 'Rockchip mpp service driver' ]]
[[ $(modinfo -F vermagic rockchip_vepu510) == "$release "* ]]
unit=/etc/systemd/system/rock4d-vepu510.service
backup=$(mktemp -d /var/backups/rock4d-vepu510-autoload-XXXXXXXX)
[[ ! -f $unit ]] || cp -a "$unit" "$backup/"
cat > "$unit" <<EOF
[Unit]
Description=Validated RK3576 VEPU510 MPP encoder
ConditionKernelVersion=$release
After=systemd-modules-load.service
Before=autonomous-driving-car.service

[Service]
Type=oneshot
ExecStart=/usr/sbin/modprobe rockchip_vepu510
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
# Retain the older RFC blacklists. Explicit loading bypasses them only for
# this validated release; selecting an older fallback kernel leaves it blocked.
systemctl daemon-reload
systemctl enable --now rock4d-vepu510.service
echo "VEPU510 enabled for $release; backup: $backup"
