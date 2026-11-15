# RK3576 MPP：VDPU383 解码与 VEPU510 编码

本目录提供 Linux 7.4 的内置 MPP 服务与 VDPU383/VEPU510 驱动。2026-11-15 在 Radxa ROCK 4D 上实际验证 H.264 硬件解码；运行内核 `7.4.0-rc2-npu-rt-full-rkmppdec`。旧内核及对应模块、Image、uInitrd 和 DTB 均保留。

解码移植以 Rockchip 官方 develop-6.1 提交 `77168c8d5ab82399f65a80e9f807b50ba37cf483` 的直接任务路径、寄存器表和 RK3576 workaround 为依据，保留原版权和许可证。采用标准 Linux DMA-BUF/IOMMU、clock、reset 和 PM 接口，HAL 提供 DRAM RCB，不依赖固定 SRAM IOVA 或 BSP DVFS。多会话共享单个硬件任务槽；不代表多硬件核。

## 接入已有 Linux 7.4 编码器移植树

基线为本机 `/home/qianruo/kbuild/kernels/7.4-rc2/source`；应在独立源码副本操作，保留基线配置和启动文件。

1. 用本目录的 `.c`、`.h`、`include/`、`Kconfig`、`Makefile` 更新目标 `drivers/media/platform/rockchip/mpp/`。目标必须已有 MPP Kconfig/Kbuild 接线，且 `CONFIG_ROCKCHIP_MPP=y`。
2. 将 `rk3576-vdpu383-mpp.dtsi` 复制到 `arch/arm64/boot/dts/rockchip/`；在实际板级 `rk3576-rock-4d.dts` 的编码器 include 之后加入该文件。它禁用原 V4L2 `&vdec`，启用解码器 IOMMU，并为解码器增加独立任务队列和 reset group。`rk3576-rock-4d-rkmpp.dts` 保存本次完整板级参考，勿直接覆盖其它板型。
3. 使用独立 LOCALVERSION。完整配置见 `../rock4d-linux74/config-npu-rt-rkmppdec`。构建 `Image modules rockchip/rk3576-rock-4d.dtb`，为相同 release 重新构建外置 Wi-Fi/蓝牙驱动，执行 modules_install、depmod，并生成对应 initramfs/uInitrd。
4. 仅在完整 Image、DTB、initramfs 和全部模块验证后，统一切换三个启动链接。关闭 V4L2 解码器会改变 USB 摄像头的 `/dev/videoN` 编号，应用应使用 `/dev/v4l/by-id/...-video-index0`。

本次源码、构建及回退工具保存在 `/home/qianruo/kbuild/media/rkmpp-decoder-linux74-prep-20261115-0144/`。`tools/install_rkmpp_trial.py` 是本机一次性安装工具；新内核已安装后不要直接重复运行其首次安装流程。

## 实测

`validation/20261115-h264.json` 保存机器生成结果。18 个用例：720p I/P、720p CABAC/B 帧、1920×1080、720×1280、长流、动态分辨率、双进程并发、八次重开、截断流与后续恢复。17 个正常/恢复用例共 1320 帧 NV12，与独立 FFmpeg 软件解码的逐帧 MD5 全部一致。截断用例输出 30 帧并报告 1 个错误，后续正常码流恢复；测试窗口无新增 fatal 内核消息。

MPP 能力从 `0x10000` 变为 `0x10200`，H.264 decoder mpp_init 成功。3453 个模块（含 4 个外置模块变体）的 vermagic 均匹配新 release。新内核下 Wi-Fi 联网、机器人服务正常；蓝牙只确认模块加载，未验证配对。

只验证上述 H.264 样本，不据此声明 HEVC/VP9/AV1、10 bit、4K 或所有摄像头兼容性。应用当前将带 stride 的输出整理为紧密 NV12，后续还有 CPU/DMA 拷贝，不是端到端零拷贝。预先存在的 RGA fault-handler 注册告警和 NPU资源重复请求日志也在旧内核出现，不属于本端口新增问题。

## 运行检查与回退

查看 `/sys/bus/platform/drivers/mpp_rkvdec2/`、`/dev/mpp_service` 和内核日志中的 `RK3576 VDPU383 MPP decoder ready`。仅有用户库的 format-list 支持不能证明内核可用，必须实际初始化、解码并检查像素。

本机显式回退命令（验证原启动文件后恢复旧链接并重启）：

```sh
sudo python3 /home/qianruo/kbuild/media/rkmpp-decoder-linux74-prep-20261115-0144/tools/rkmpp_trial_guard.py --rollback
```

代码由 Codex 辅助移植、审查和验证，无人的 DCO 签署。
