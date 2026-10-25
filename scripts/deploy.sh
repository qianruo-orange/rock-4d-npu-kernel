#!/bin/bash
# 安装 7.3.0-npu 内核并修正 vendor U-Boot 必需的镜像格式
# 必须在目标板本地执行（需 root）
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
B="$REPO/build"

dpkg -i "$B"/linux-image-7.3.0-npu_7.3-npu-1_arm64.deb \
        "$B"/linux-headers-7.3.0-npu_7.3-npu-1_arm64.deb

# 关键修正：bindeb-pkg 产出的 vmlinuz 可能是 gzip 压缩的 Image，
# vendor U-Boot 的 booti 只接受未压缩 arm64 Image（否则 Bad Linux ARM64 Image magic!）
cd /boot
if [ "$(od -An -tx1 -N2 vmlinuz-7.3.0-npu | tr -d ' ')" = "1f8b" ]; then
    mv vmlinuz-7.3.0-npu vmlinuz-7.3.0-npu.gz
    gunzip -k vmlinuz-7.3.0-npu.gz
fi

# 使用带 NPU 节点的 DTB（rockchip 目录内原地替换）
cp "$B/linux-7.3/arch/arm64/boot/dts/rockchip/rk3576-rock-4d.dtb" \
   /boot/dtb/rockchip/rk3576-rock-4d.dtb

ln -sf vmlinuz-7.3.0-npu Image
ln -sf uInitrd-7.3.0-npu uInitrd

# 确认启动链
ls -l /boot/Image /boot/uInitrd
od -An -tx1 -N8 /boot/vmlinuz-7.3.0-npu
systemctl reboot
