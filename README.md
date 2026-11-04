# ROCK 4D 内核适配

本仓库独立维护 Radxa ROCK 4D / RK3576 的 Linux 内核补丁、驱动、配置、构建安装脚本及验证报告。应用代码位于 [autonomous-driving-car](https://github.com/qianruo-orange/autonomous-driving-car)，不再混放内核源码。

当前基线为用户提供的官方 Linux 7.4-rc1 源码归档。完整 Linux 源树及构建产物保留在本机 `/home/qianruo/kbuild/kernels/7.4-rc1/source`，不重复提交到本仓库。适配仓库位于 `/home/qianruo/kbuild/kernels/rock4d-kernel`。

## 当前状态

2026-11-04 已安装并启动 `7.4.0-rc1-npu-rt-vepu510-r2`，启用 PREEMPT_RT、1000 Hz；NPU、Wi-Fi、蓝牙模块及 JPEG 硬解通过开机检查。蓝牙仅验证模块加载，未进行配对测试。

R2 的 VEPU510 硬件验收通过：8 帧实际输入、180 帧 GOP 60 连续编码及三次模块卸载、加载均成功，H.264 I/P 码流可独立完整解码，测试窗口内无新增内核错误。独立 Rockchip fault 通知接口解决了第一版在 DMA domain 上注册通用 IOMMU 回调的告警，保留正常 DMA cookie。另通过实际应用编码接口完成 180 帧、3 个关键帧及 SPS/PPS 检查。

同输入、同色彩范围的三组编码对照中，硬件吞吐中位数为 72.765 帧/秒，进程 CPU 时间为 2.419 ms/帧；历史软件对照为 45.159 帧/秒、63.083 ms/帧。这是短时固定样本的整进程测量，不能视为相机到浏览器的延迟。实际码率、PSNR 与限制见 [应用编解码报告](https://github.com/qianruo-orange/autonomous-driving-car/blob/main/docs/CODEC_PERFORMANCE.md)。应用使用 JPEG 硬件解码和 VEPU510 H.264 硬件编码，具体部署版本由独立应用仓库维护。

## 目录

| 路径 | 内容 |
| --- | --- |
| [rock4d-linux74](rock4d-linux74/README.md) | 7.4 来源、NPU/JPEG/Wi-Fi/蓝牙补丁与配置 |
| [rockchip-jpegd](rockchip-jpegd/README.md) | JPEG 驱动及 RK3576 适配 |
| `live-overlay/` | JPEG 设备树临时 overlay 辅助模块 |
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
