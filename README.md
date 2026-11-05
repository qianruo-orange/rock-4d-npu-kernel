# ROCK 4D 内核适配

本仓库独立维护 Radxa ROCK 4D / RK3576 的 Linux 内核补丁、驱动、配置、构建安装脚本及验证报告。应用代码位于 [autonomous-driving-car](https://github.com/qianruo-orange/autonomous-driving-car)，不再混放内核源码。

当前基线为用户提供的官方 Linux 7.4-rc1 源码归档。完整 Linux 源树及构建产物保留在本机 `/home/qianruo/kbuild/kernels/7.4-rc1/source`，不重复提交到本仓库。适配仓库位于 `/home/qianruo/kbuild/kernels/rock4d-kernel`。

## 当前状态

2026-11-04 已安装并启动 `7.4.0-rc1-npu-rt-vepu510-r2`，启用 PREEMPT_RT、1000 Hz；NPU、Wi-Fi、蓝牙模块及 JPEG 硬解通过开机检查。蓝牙仅验证模块加载，未进行配对测试。

R2 的 VEPU510 硬件验收通过：8 帧实际输入、180 帧 GOP 60 连续编码及三次模块卸载、加载均成功，H.264 I/P 码流可独立完整解码，测试窗口内无新增内核错误。独立 Rockchip fault 通知接口解决了第一版在 DMA domain 上注册通用 IOMMU 回调的告警，保留正常 DMA cookie。另通过实际应用编码接口完成 180 帧、3 个关键帧及 SPS/PPS 检查。

同输入、同色彩范围的三组编码对照中，硬件吞吐中位数为 72.765 帧/秒，进程 CPU 时间为 2.419 ms/帧；历史软件对照为 45.159 帧/秒、63.083 ms/帧。这是短时固定样本的整进程测量，不能视为相机到浏览器的延迟。实际码率、PSNR 与限制见 [应用编解码报告](https://github.com/qianruo-orange/autonomous-driving-car/blob/main/docs/CODEC_PERFORMANCE.md)。应用使用 JPEG 硬件解码和 VEPU510 H.264 硬件编码，具体部署版本由独立应用仓库维护。

## 当前发布：7.4.0-rc1-npu-rt-full

2026-11-05 一次性重建并部署。相对 `-vepu510-r2` 的变化是把四个驱动从"外部模块/未启用"改为**内核内置**：

| 驱动 | 之前 | 现在 | 验证 |
|---|---|---|---|
| RKNPU（含 0005 的 1188 MHz / 950 mV） | DKMS 模块 | 内置 | `rknpu_probe` 在 System.map；无 `rknpu.ko` |
| JPEG 解码 `rkjpegd` | `rockchip_jpegd.ko` | 内置 | `/dev/video0`；`rockchip-jpegd` 平台驱动绑定 |
| VEPU510 编码 `mpp` | `rockchip_vepu510.ko` | 内置 | `mpp_service`+`mpp_rkvenc2` 探测成功；`/dev/mpp_service` |
| RGA3（`rockchip-rga3/`） | 无驱动 | 内置 | 双核 probe successfully；`/dev/rga` |

仍为模块：`aic8800`（WiFi，换内核需按版本重编）、`uvcvideo`（相机）。

构建命令：

```sh
make -C <tree> ARCH=arm64 -j8 LOCALVERSION=-full Image dtbs modules
make -C <tree> ARCH=arm64 LOCALVERSION=-full INSTALL_MOD_STRIP=1 modules_install
```

版本串由 `LOCALVERSION=-full` 决定。

部署连带影响：

- **相机节点变了**：`rkjpegd` 内置后先于 USB 摄像头注册，相机从 `/dev/video1` 移到 `/dev/video2`；应用改用稳定路径
  `/dev/v4l/by-id/usb-UGREEN_Camera_2K_UGREEN_Camera_2K_SN0001-video-index0`。
- 启动后约 13 秒有一次 `jpeg: hardware decode failed ... Connection timed out`，之后稳定 1 ms / 30 fps，属首次任务瞬态。
- 板端无 `mkinitramfs`/`dracut`/`update-initramfs`：initrd 以现有 cpio 为基础替换模块树后重新打包（PCIe PHY 是模块，必须包含在 initrd）。

部署结果：内核 `7.4.0-rc1-npu-rt-full`，NPU 1188 MHz，应用 active，端到端 47 ms，30 fps，64 °C。


### 复现 `7.4.0-rc1-npu-rt-full`

四个驱动全部内置，配置见 `rock4d-linux74/config-npu-rt-full`（与 `config-vepu510-r2` 相差 59 条，全部是四个驱动及其 `VIDEO_DEV`/媒体级联）：

```sh
# 1) 基础树：7.4-rc1 + rock4d-linux74 补丁
# 2) 并入三个驱动包
cp -a rockchip-rga3  <tree>/drivers/video/rockchip/rga3
cp -a rockchip-rknpu <tree>/drivers/rknpu
cp -a rockchip-mpp   <tree>/drivers/media/platform/rockchip/mpp
# 接线（见各包 README）
# 3) 配置与构建
cp rock4d-linux74/config-npu-rt-full <tree>/.config
make -C <tree> ARCH=arm64 oldconfig
make -C <tree> ARCH=arm64 -j8 LOCALVERSION=-full Image dtbs modules
make -C <tree> ARCH=arm64 LOCALVERSION=-full INSTALL_MOD_STRIP=1 modules_install
```

设备树：`rockchip-rga3/rk3576-rga2.dtsi` 提供 RGA 双核节点（默认 disabled，板级 DTS 需逐个 okay）；NPU 与 JPEG 节点来自 `rock4d-linux74/0001`、`0002` 补丁。

## 目录

| 路径 | 内容 |
| --- | --- |
| [rock4d-linux74](rock4d-linux74/README.md) | 7.4 来源、NPU/JPEG/Wi-Fi/蓝牙补丁与配置 |
| [rockchip-jpegd](rockchip-jpegd/README.md) | JPEG 驱动及 RK3576 适配 |
| `live-overlay/` | JPEG 设备树临时 overlay 辅助模块 |
| `rockchip-rga3/` | RGA2E（2D 加速器）驱动移植包，含 `rk3576-rga2.dtsi` 设备树片段；已适配 7.4 的三处 API、编译通过，**未部署**（换内核前需先并回 vepu510 MPP 源码），详见包内 README |
| [rockchip-vepu510-mpp](rockchip-vepu510-mpp/README.md) | VEPU510 MPP 外置驱动、内核集成补丁、来源清单和许可证 |
| `scripts/build_kernel74.sh`、`scripts/install_kernel74.sh` | 原 7.4 基础版的构建与安装流程 |
| `scripts/install_jpeg_decoder.sh` | 为当前运行内核构建、安装 JPEG 驱动 |
| `scripts/install_mpp_userspace.sh`、`scripts/enable_vepu510.sh` | 安装已验证 MPP 用户库及头文件，并限定当前通过验收的内核开机加载编码驱动 |
| [scripts/vepu510](scripts/vepu510/README.md) | 当前独立版本的构建、安装与硬件验证入口 |
| [docs/KERNEL_VERIFICATION.md](docs/KERNEL_VERIFICATION.md) | 历史内核及应用实时调度验证记录 |
| [docs/KERNEL_BUILD_REPORT.md](docs/KERNEL_BUILD_REPORT.md) | 中文历史内核报告，附 [PDF](docs/pdf/KERNEL_BUILD_REPORT_ZH.pdf) |
| `legacy/linux73/` | 原内核仓库的 7.3 资料归档；历史脚本不用于当前构建或安装 |

历史报告记录当时的版本和测量，不替代 R2 的验收。当前构建日志、输入 SHA-256、安装备份及测试输出保留在 kbuild 工作目录。

## 构建与安装

基础版流程见 [7.4 适配说明](rock4d-linux74/README.md)。VEPU510 使用独立版本后缀，所有外置模块必须针对同一配置、最终内核符号表和 headers 构建；不能复用旧 `.ko` 或放宽 MODPOST。R2 的流程见 [VEPU510 脚本说明](scripts/vepu510/README.md)。

安装先备份并保留已验证的 7.4 启动载荷，完成版本、模块、Image、设备树和 initrd 检查后才切换启动选择。构建与安装脚本均不自行重启。R2 已完成连续 I/P 帧、独立解码、模块重载及无新增内核错误检查。后续更换配置、内核或模块仍须重新验收；当前通过的短时测试不代表已完成长期热稳定性或 H.265 验证。

## 来源与许可证

7.4 基线、Kiln 补丁以及 JPEG、MPP 的固定来源和修改范围见各目录说明。保留原作者信息、SPDX 和许可证；各文件按自身声明授权。本轮迁移和适配通过 Codex 辅助整理，没有添加人的 DCO 签署。
