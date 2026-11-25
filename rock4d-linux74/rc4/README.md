# ROCK 4D `7.4.0-rc4-npu-rt-full-rkmppdec`

## 中文

2026-11-25 在运行 Armbian trixie ARM64 的 Radxa ROCK 4D（RK3576）上验证了此 PREEMPT_RT 构建。板级设备树在 `/soc` 下定义两路 RGA 及各自的 IOMMU。

上游基线为 Linux 提交 `4dce97c6c265544342e716e62fd24bbb232590c5`。从适配仓库根目录执行以下命令，`linux_src` 指向干净的上游 Git 工作树。 [上游提交](https://github.com/torvalds/linux/commit/4dce97c6c265544342e716e62fd24bbb232590c5)

```sh
linux_src=/path/to/linux
release_dir="$PWD/rock4d-linux74/rc4"
git -C "$linux_src" checkout --detach 4dce97c6c265544342e716e62fd24bbb232590c5
git -C "$linux_src" apply "$release_dir/integration.patch"

mkdir -p "$linux_src/drivers/rknpu" \
    "$linux_src/drivers/video/rockchip/rga3" \
    "$linux_src/drivers/media/platform/rockchip/mpp" \
    "$linux_src/drivers/media/platform/rockchip/rkjpegd"
cp -a rockchip-rknpu/. "$linux_src/drivers/rknpu/"
cp -a rockchip-rga3/. "$linux_src/drivers/video/rockchip/rga3/"
cp -a rockchip-mpp/. "$linux_src/drivers/media/platform/rockchip/mpp/"
cp -a rockchip-jpegd/. "$linux_src/drivers/media/platform/rockchip/rkjpegd/"
cp "$release_dir/config-npu-rt-full-rkmppdec" "$linux_src/.config"
make -C "$linux_src" ARCH=arm64 -j16 Image rockchip/rk3576-rock-4d.dtb modules
```

本次生成了 3,449 个树内模块。AIC8800 `5.0-kiln-linux74` 的 Wi-Fi 和蓝牙模块针对同一版本单独构建。启动用 initramfs 包含 NVMe 所需的 `phy-rockchip-snps-pcie3`。

重启后核实了运行版本、NVMe 上的 ext4 根文件系统以及 Wi-Fi 和 SSH 连通性。RKNPU、JPEG、两路 RGA、MPP 服务及 VEPU510 当时均已绑定驱动。

## English

This PREEMPT_RT build was verified on a Radxa ROCK 4D (RK3576) running Armbian trixie ARM64 on 2026-11-25. The board device tree defines both RGA engines and their IOMMUs under `/soc`.

The build is based on Linux commit `4dce97c6c265544342e716e62fd24bbb232590c5`. Set `linux_src` to a clean upstream Git working tree and run the commands above from the board integration repository root. [Upstream commit](https://github.com/torvalds/linux/commit/4dce97c6c265544342e716e62fd24bbb232590c5)

The build produced an ARM64 kernel image, the board DTB, and 3,449 in-tree modules. The AIC8800 `5.0-kiln-linux74` Wi-Fi and Bluetooth modules were built separately for the same release. The boot initramfs includes the `phy-rockchip-snps-pcie3` driver needed for NVMe boot.

After reboot, the board reported the new release, mounted its ext4 root filesystem from NVMe, and accepted SSH over Wi-Fi. RKNPU, JPEG, both RGA engines, the MPP service, and VEPU510 showed active driver bindings.
