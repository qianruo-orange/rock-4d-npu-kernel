#!/bin/bash
# 准备内核构建树：解压主线源码 + 应用 NPU 补丁 + 写入配置
# 用法：把 linux-7.3.tar.xz 放到本仓库根目录后执行 scripts/kprep.sh
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
[ -d build/linux-7.3 ] || { mkdir -p build; tar xJf linux-7.3.tar.xz -C build; }

cd build/linux-7.3
for p in "$REPO"/patches/00*.patch; do
    if patch -p1 --fuzz=2 -s < "$p"; then
        echo "APPLIED $(basename "$p")"
    else
        echo "FAILED  $(basename "$p")"; exit 1
    fi
done
cp "$REPO/config/config-7.3.0-npu" .config
# LOCALVERSION 与 DRM_ACCEL_ROCKET 已固化在 config 中，此处仅做一致性校验
make olddefconfig >/dev/null
grep -E '^CONFIG_LOCALVERSION=|^CONFIG_DRM_ACCEL_ROCKET' .config
echo PREP_DONE
