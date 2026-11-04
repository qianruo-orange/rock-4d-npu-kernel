#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
# Install the same pinned shared library used for the successful hardware test.
set -euo pipefail
export PATH="$PATH:/usr/sbin:/sbin"
work=${ROCK4D_VEPU_WORKDIR:-/home/qianruo/kbuild/media/vepu510-mpp-linux74}
source_tree=$work/mpp-userspace
library=$work/mpp-userspace-build/mpp/librockchip_mpp.so.0
commit=7916c3cc000fefc0bdf96636b7d54fe174724f44
[[ $(id -u) == 0 ]] || { echo 'Run as root'; exit 1; }
[[ $(git -c safe.directory="$source_tree" -C "$source_tree" rev-parse HEAD) == "$commit" ]]
git -c safe.directory="$source_tree" -C "$source_tree" diff --exit-code --quiet
git -c safe.directory="$source_tree" -C "$source_tree" diff --cached --exit-code --quiet
[[ -s $library ]]
[[ $(readelf -d "$library" | sed -n 's/.*SONAME.*\[\(.*\)\]/\1/p') == librockchip_mpp.so.1 ]]
validated_sha=$(python3 - "$work/validation/hardware-validation.json" "$library" "$commit" <<'PY'
import hashlib,json,platform,sys
from pathlib import Path
s=json.loads(Path(sys.argv[1]).read_text())
if not (s.get('passed') and s['kernel']==platform.release() and
        s['boot_id']==Path('/proc/sys/kernel/random/boot_id').read_text().strip()):
    raise SystemExit('Passing hardware validation for this boot is required')
artifacts=s.get('mpp_artifacts',{})
recorded=artifacts.get('library',{})
library=Path(sys.argv[2]).resolve()
digest=hashlib.sha256(library.read_bytes()).hexdigest()
if not (s.get('mpp_artifacts_unchanged') is True and
        artifacts.get('source',{}).get('commit')==sys.argv[3] and
        recorded.get('path')==str(library) and recorded.get('sha256')==digest):
    raise SystemExit('A fresh hardware validation of this exact MPP library is required')
print(digest)
PY
)
backup=$(mktemp -d /var/backups/rock4d-mpp-XXXXXXXX)
mkdir -p "$backup/lib" "$backup/include" /usr/local/lib /usr/local/include/rockchip
for path in /usr/local/lib/librockchip_mpp.so{,.0,.1}; do
    [[ ! -e $path && ! -L $path ]] || cp -a "$path" "$backup/lib/"
done
for path in "$source_tree"/inc/*.h; do
    name=$(basename "$path")
    [[ ! -e /usr/local/include/rockchip/$name ]] || cp -a "/usr/local/include/rockchip/$name" "$backup/include/"
    install -m 644 "$path" "/usr/local/include/rockchip/$name"
done
staged_library=$(mktemp /usr/local/lib/.rockchip-mpp-XXXXXXXX)
trap 'rm -f "$staged_library"' EXIT
install -m 755 "$library" "$staged_library"
[[ $(sha256sum "$staged_library" | cut -d ' ' -f 1) == "$validated_sha" ]] || {
    echo 'MPP library changed after validation; installed library remains unchanged'; exit 1;
}
mv -f "$staged_library" /usr/local/lib/librockchip_mpp.so.0
ln -sfn librockchip_mpp.so.0 /usr/local/lib/librockchip_mpp.so.1
ln -sfn librockchip_mpp.so.1 /usr/local/lib/librockchip_mpp.so
ldconfig
cmp "$library" /usr/local/lib/librockchip_mpp.so.0
sha256sum "$library" /usr/local/lib/librockchip_mpp.so.0 > "$backup/library.sha256"
printf '%s\n' "$commit" > "$backup/source-commit"
echo "MPP shared library and headers installed; backup: $backup"
