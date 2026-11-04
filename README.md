# ROCK 4D 内核适配

本仓库独立维护 Radxa ROCK 4D / RK3576 的 Linux 内核补丁、驱动、配置、构建安装脚本及验证报告。应用代码位于 [autonomous-driving-car](https://github.com/qianruo-orange/autonomous-driving-car)，不再混放内核源码。

当前基线为用户提供的官方 Linux 7.4-rc1 源码归档。完整 Linux 源树及构建产物保留在本机 `/home/qianruo/kbuild/kernels/7.4-rc1/source`，不重复提交到本仓库。适配仓库位于 `/home/qianruo/kbuild/kernels/rock4d-kernel`。

## 当前状态

2026-11-04 已启动 `7.4.0-rc1-npu-rt-vepu510`，启用 PREEMPT_RT、1000 Hz；NPU、Wi-Fi、蓝牙模块及 JPEG 硬解通过开机检查，应用正常运行。

VEPU510 第一版已生成可独立软件解码的 H.264 I/P 帧，包括 180 帧、GOP 60 测试。但 Linux 7.4 的通用 IOMMU fault handler 不能直接注册到 DMA domain，测试触发告警，整体验收未通过。修正版 `7.4.0-rc1-npu-rt-vepu510-r2` 使用独立的 Rockchip fault 通知接口，正在重新构建内核及匹配模块；完成安装、重启及硬件验证前，应用继续使用 libx264。

## 目录

| 路径 | 内容 |
| --- | --- |
| [rock4d-linux74](rock4d-linux74/README.md) | 7.4 来源、NPU/JPEG/Wi-Fi/蓝牙补丁与配置 |
| [rockchip-jpegd](rockchip-jpegd/README.md) | JPEG 驱动及 RK3576 适配 |
| `live-overlay/` | JPEG 设备树临时 overlay 辅助模块 |
| [rockchip-vepu510-mpp](rockchip-vepu510-mpp/README.md) | VEPU510 MPP 外置驱动、内核集成补丁、来源清单和许可证 |
| `scripts/build_kernel74.sh`、`scripts/install_kernel74.sh` | 原 7.4 基础版的构建与安装流程 |
| `scripts/install_jpeg_decoder.sh` | 为当前运行内核构建、安装 JPEG 驱动 |
| [scripts/vepu510](scripts/vepu510/README.md) | 当前独立版本的构建、安装与硬件验证入口 |
| [docs/KERNEL_VERIFICATION.md](docs/KERNEL_VERIFICATION.md) | 历史内核及应用实时调度验证记录 |
| [docs/KERNEL_BUILD_REPORT.md](docs/KERNEL_BUILD_REPORT.md) | 中文历史内核报告，附 [PDF](docs/pdf/KERNEL_BUILD_REPORT_ZH.pdf) |
| `legacy/linux73/` | 原内核仓库的 7.3 资料归档；历史脚本不用于当前构建或安装 |

历史报告记录当时的版本和测量，不替代当前候选的验收。当前构建日志、输入 SHA-256、安装备份及测试输出保留在 kbuild 工作目录。

## 构建与安装

基础版流程见 [7.4 适配说明](rock4d-linux74/README.md)。VEPU510 使用独立版本后缀，所有外置模块必须针对同一配置、最终内核符号表和 headers 构建；不能复用旧 `.ko` 或放宽 MODPOST。当前候选的流程见 [VEPU510 脚本说明](scripts/vepu510/README.md)。

安装先备份并保留已验证的 7.4 启动载荷，完成版本、模块、Image、设备树和 initrd 检查后才切换启动选择。构建与安装脚本均不自行重启。硬件编码须通过连续 I/P 帧、独立解码及无新增内核错误检查后，再进入性能对比和应用接入。

## 来源与许可证

7.4 基线、Kiln 补丁以及 JPEG、MPP 的固定来源和修改范围见各目录说明。保留原作者信息、SPDX 和许可证；各文件按自身声明授权。本轮迁移和适配通过 Codex 辅助整理，没有添加人的 DCO 签署。
