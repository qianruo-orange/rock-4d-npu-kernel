#!/bin/bash
# 编译并打包内核 deb（bindeb-pkg）
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO/build/linux-7.3"
# Debian trixie 需要 debhelper 13，主线脚本默认写 12
sed -i 's/debhelper-compat (= 12)/debhelper-compat (= 13)/' scripts/package/mkdebian
make -j"$(nproc)" KDEB_PKGVERSION=7.3-npu-1 bindeb-pkg
ls -lh ../linux-image-7.3.0-npu_7.3-npu-1_arm64.deb
echo BUILD_DONE
