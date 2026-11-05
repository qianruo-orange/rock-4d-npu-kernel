# ROCK 4D 内核适配

**中文** | **[English](#english-documentation)**

本仓库独立维护 Radxa ROCK 4D / RK3576 的 Linux 内核补丁、驱动、配置、构建安装脚本及验证报告。应用代码位于 [autonomous-driving-car](https://github.com/qianruo-orange/autonomous-driving-car)，不再混放内核源码。

当前基线为用户提供的官方 Linux 7.4-rc1 源码归档。完整 Linux 源树及构建产物保留在本机 `/home/qianruo/kbuild/kernels/7.4-rc1/source`，不重复提交到本仓库。适配仓库位于 `/home/qianruo/kbuild/kernels/rock4d-kernel`。

## 当前发布：7.4.0-rc1-npu-rt-full

2026-11-05 构建并部署，运行中：PREEMPT_RT、1000 Hz。四个驱动**内置于内核**：

| 驱动 | 验证方式 |
|---|---|
| RKNPU（含补丁 0005 的 1188 MHz / 950 mV） | `rknpu_probe` 在 System.map |
| JPEG 解码 `rkjpegd` | `/dev/video0`；`rockchip-jpegd` 平台驱动已绑定 |
| VEPU510 编码 `mpp` | `mpp_service` + `mpp_rkvenc2` 探测成功；`/dev/mpp_service` |
| RGA3 / RGA2E（`rockchip-rga3/`） | 双核 `probe successfully`；`/dev/rga`；hw_version 3.e |

仍为模块：`aic8800`（WiFi，换内核需按版本重编）、`uvcvideo`（相机）。

构建命令：

```sh
make -C <tree> ARCH=arm64 -j8 LOCALVERSION=-full Image dtbs modules
make -C <tree> ARCH=arm64 LOCALVERSION=-full INSTALL_MOD_STRIP=1 modules_install
```

版本串由 `LOCALVERSION=-full` 决定。

### 部署连带影响

- **相机节点**：`rkjpegd` 先于 USB 摄像头注册，相机为 `/dev/video2`；应用使用稳定路径
  `/dev/v4l/by-id/usb-UGREEN_Camera_2K_UGREEN_Camera_2K_SN0001-video-index0`。
- 启动后约 13 秒有一次 `jpeg: hardware decode failed ... Connection timed out`，之后稳定 1 ms / 30 fps，属首次任务的瞬态。
- 板端没有 `mkinitramfs` / `dracut` / `update-initramfs`：initrd 以现有 cpio 为基础替换模块树后重新打包（PCIe PHY 是模块，必须包含在 initrd）。

部署结果：内核 `7.4.0-rc1-npu-rt-full`，NPU 1188 MHz，应用 active，端到端 **44 ms**，30 fps，约 60 °C。

### 复现 `7.4.0-rc1-npu-rt-full`

四个驱动全部内置，配置见 `rock4d-linux74/config-npu-rt-full`（与 `config-vepu510-r2` 相差 59 条，全部是四个驱动及其 `VIDEO_DEV` / 媒体级联）：

```sh
# 1) 基础树：7.4-rc1 + rock4d-linux74 补丁
# 2) 并入三个驱动包
cp -a rockchip-rga3  <tree>/drivers/video/rockchip/rga3
cp -a rockchip-rknpu <tree>/drivers/rknpu
cp -a rockchip-mpp   <tree>/drivers/media/platform/rockchip/mpp
# 接线见各包 README
# 3) 配置与构建
cp rock4d-linux74/config-npu-rt-full <tree>/.config
make -C <tree> ARCH=arm64 oldconfig
make -C <tree> ARCH=arm64 -j8 LOCALVERSION=-full Image dtbs modules
make -C <tree> ARCH=arm64 LOCALVERSION=-full INSTALL_MOD_STRIP=1 modules_install
```

设备树：`rockchip-rga3/rk3576-rga2.dtsi` 提供 RGA 双核节点（默认 disabled，板级 DTS 需逐个 okay）；NPU 与 JPEG 节点来自 `rock4d-linux74/0001`、`0002` 补丁。

## 应用对驱动的依赖

应用（`autonomous-driving-car`）的推理链路是相机 MJPEG → JPEG 硬解 → RGA 缩放 → NPU 推理，因此需要前三个驱动，**不再需要** VEPU510 编码器：

| 驱动 | 应用是否需要 |
| --- | --- |
| RKNPU | 需要 |
| JPEG 解码 | 需要 |
| RGA | 需要（应用无 CPU 回退） |
| VEPU510 编码 | 不需要 |

## 目录

| 路径 | 内容 |
| --- | --- |
| [rock4d-linux74](rock4d-linux74/README.md) | 7.4 来源、NPU/JPEG/Wi-Fi/蓝牙补丁、`config-npu-rt-full` 配置 |
| [rockchip-jpegd](rockchip-jpegd/README.md) | JPEG 驱动及 RK3576 适配 |
| `rockchip-rga3/` | RGA2E（2D 加速器）驱动移植包，含 `rk3576-rga2.dtsi`；已适配 7.4 的三处 API，**已部署**（内置），详见包内 README |
| `rockchip-rknpu/` | RKNPU 内置集成包（驱动源码 + mainline compat 头 + Kconfig/Makefile） |
| `rockchip-mpp/` | VEPU510 编码器内置集成包 |
| [rockchip-vepu510-mpp](rockchip-vepu510-mpp/README.md) | VEPU510 MPP 外置驱动、内核集成补丁、来源清单和许可证 |
| `live-overlay/` | JPEG 设备树临时 overlay 辅助模块 |
| `scripts/build_kernel74.sh`、`scripts/install_kernel74.sh` | 原 7.4 基础版的构建与安装流程 |
| `scripts/install_jpeg_decoder.sh` | 为当前运行内核构建、安装 JPEG 驱动 |
| `scripts/install_mpp_userspace.sh`、`scripts/enable_vepu510.sh` | 安装已验证 MPP 用户库及头文件，并限定通过验收的内核开机加载编码驱动 |
| [scripts/vepu510](scripts/vepu510/README.md) | 独立版本的构建、安装与硬件验证入口 |
| [docs/KERNEL_VERIFICATION.md](docs/KERNEL_VERIFICATION.md) | 历史内核及应用实时调度验证记录 |
| [docs/KERNEL_BUILD_REPORT.md](docs/KERNEL_BUILD_REPORT.md) | 中文历史内核报告，附 [PDF](docs/pdf/KERNEL_BUILD_REPORT_ZH.pdf) |
| `legacy/linux73/` | 原内核仓库的 7.3 资料归档；历史脚本不用于当前构建或安装 |

历史报告记录各自的版本与测量。构建日志、输入 SHA-256、安装备份及测试输出保留在 kbuild 工作目录。

## 构建与安装

基础版流程见 [7.4 适配说明](rock4d-linux74/README.md)。所有内置驱动必须与同一份配置一起构建；外置模块必须针对同一配置、最终内核符号表和 headers 构建，不能复用旧 `.ko` 或放宽 MODPOST。

安装先备份并保留已验证的启动载荷，完成版本、模块、Image、设备树和 initrd 检查后才切换启动选择；构建与安装脚本均不自行重启。更换配置、内核或模块后仍须重新验收；短时测试不代表长期热稳定性。

## VEPU510 编码器验收

VEPU510 编码器的硬件验收：8 帧实际输入、180 帧 GOP 60 连续编码及三次模块卸载/加载均成功，H.264 I/P 码流可独立完整解码，测试窗口内无新增内核错误。独立的 Rockchip fault 通知接口把 IOMMU 回调注册在自有 DMA domain 上，并保留正常 DMA cookie。

同输入、同色彩范围的三组编码对照中，硬件吞吐中位数为 72.765 帧/秒，进程 CPU 时间为 2.419 ms/帧；历史软件对照为 45.159 帧/秒、63.083 ms/帧。这是短时固定样本的整进程测量，不能视为相机到浏览器的延迟。实际码率、PSNR 与限制见[应用编解码报告](https://github.com/qianruo-orange/autonomous-driving-car/blob/main/docs/CODEC_PERFORMANCE.md)。

编码器以内置驱动形式保留，应用本身不参与编码。

## 来源与许可证

7.4 基线、Kiln 补丁以及 JPEG、MPP、RGA 的固定来源和修改范围见各目录说明。保留原作者信息、SPDX 和许可证；各文件按自身声明授权。本轮迁移和适配通过 Codex 辅助整理，没有添加人的 DCO 签署。

## English documentation

This repository maintains the Linux kernel patches, drivers, configuration,
build/install scripts and validation records for the Radxa ROCK 4D / RK3576.
The application lives in
[autonomous-driving-car](https://github.com/qianruo-orange/autonomous-driving-car);
kernel sources are maintained here.

The baseline is the vendor-provided Linux 7.4-rc1 source archive. The full tree
and build outputs stay on the build host under
`/home/qianruo/kbuild/kernels/7.4-rc1/source`.

### Current release: 7.4.0-rc1-npu-rt-full

Built and deployed on 2026-11-05, running PREEMPT_RT at 1000 Hz. Four drivers are
**built into the kernel**:

| Driver | Verified by |
| --- | --- |
| RKNPU (carrying patch 0005: 1188 MHz / 950 mV) | `rknpu_probe` in System.map |
| JPEG decoder `rkjpegd` | `/dev/video0`, `rockchip-jpegd` platform driver bound |
| VEPU510 encoder `mpp` | `mpp_service` + `mpp_rkvenc2` probe, `/dev/mpp_service` |
| RGA3 / RGA2E (`rockchip-rga3/`) | both cores `probe successfully`, `/dev/rga`, hw_version 3.e |

Still modules: `aic8800` (WiFi, must be rebuilt per kernel version) and
`uvcvideo` (camera).

```sh
make -C <tree> ARCH=arm64 -j8 LOCALVERSION=-full Image dtbs modules
make -C <tree> ARCH=arm64 LOCALVERSION=-full INSTALL_MOD_STRIP=1 modules_install
```

The version string comes from `LOCALVERSION=-full`.

**Deployment consequences**

- **Camera device.** With `rkjpegd` built in it registers before the USB camera,
  so the camera is `/dev/video2`; the application opens the by-id path
  `/dev/v4l/by-id/usb-UGREEN_Camera_2K_UGREEN_Camera_2K_SN0001-video-index0`.
- About 13 s after boot there is one `jpeg: hardware decode failed … Connection
  timed out`; afterwards decoding is steady at 1 ms / 30 fps. It is a
  first-job transient.
- The board has no `mkinitramfs`, `dracut` or `update-initramfs`; the initrd was
  rebuilt by replacing the module tree inside the existing cpio. The PCIe PHY is
  a module and must be present in the initrd.

Result: kernel `7.4.0-rc1-npu-rt-full`, NPU 1188 MHz, application active, 44 ms
end to end, 30 fps, about 60 °C.

### Reproducing `7.4.0-rc1-npu-rt-full`

All four drivers are built in; the configuration is
`rock4d-linux74/config-npu-rt-full` (59 entries differ from
`config-vepu510-r2`, all of them those drivers plus the `VIDEO_DEV`/media
cascade they pull in).

```sh
# 1) base tree: 7.4-rc1 + rock4d-linux74 patches
# 2) copy the three driver packages
cp -a rockchip-rga3  <tree>/drivers/video/rockchip/rga3
cp -a rockchip-rknpu <tree>/drivers/rknpu
cp -a rockchip-mpp   <tree>/drivers/media/platform/rockchip/mpp
# wire up Kconfig/Makefile as described in each package README
# 3) configure and build
cp rock4d-linux74/config-npu-rt-full <tree>/.config
make -C <tree> ARCH=arm64 oldconfig
make -C <tree> ARCH=arm64 -j8 LOCALVERSION=-full Image dtbs modules
make -C <tree> ARCH=arm64 LOCALVERSION=-full INSTALL_MOD_STRIP=1 modules_install
```

Device tree: `rockchip-rga3/rk3576-rga2.dtsi` provides the two RGA nodes
(disabled by default; the board DTS enables each one). The NPU and JPEG nodes
come from patches `0001` and `0002` in `rock4d-linux74/`.

### Driver requirements of the application

The application pipeline is camera MJPEG → JPEG decode → RGA scale → NPU, so it
needs the first three drivers and **not** the VEPU510 encoder:

| Driver | Needed by the application |
| --- | --- |
| RKNPU | yes |
| JPEG decoder | yes |
| RGA | yes (there is no CPU fallback) |
| VEPU510 encoder | no |

### Contents

| Path | Contents |
| --- | --- |
| [rock4d-linux74](rock4d-linux74/README.md) | 7.4 provenance, NPU/JPEG/WiFi/Bluetooth patches, `config-npu-rt-full` |
| [rockchip-jpegd](rockchip-jpegd/README.md) | JPEG driver and RK3576 adaptation |
| `rockchip-rga3/` | RGA2E port package with `rk3576-rga2.dtsi`; three API fixes for 7.4, **deployed** (built in) |
| `rockchip-rknpu/` | RKNPU in-tree package (sources, mainline compat headers, Kconfig/Makefile) |
| `rockchip-mpp/` | VEPU510 encoder in-tree package |
| [rockchip-vepu510-mpp](rockchip-vepu510-mpp/README.md) | VEPU510 MPP external driver, integration patch, provenance and licence |
| `live-overlay/` | temporary JPEG device-tree overlay helper |
| `scripts/build_kernel74.sh`, `scripts/install_kernel74.sh` | original 7.4 base build and install flow |
| `scripts/install_jpeg_decoder.sh` | build and install the JPEG driver for the running kernel |
| `scripts/install_mpp_userspace.sh`, `scripts/enable_vepu510.sh` | install the validated MPP user library and gate encoder loading on validation |
| [scripts/vepu510](scripts/vepu510/README.md) | standalone version's build, install and hardware validation entry point |
| [docs/KERNEL_VERIFICATION.md](docs/KERNEL_VERIFICATION.md) | historical kernel and real-time scheduling validation |
| [docs/KERNEL_BUILD_REPORT.md](docs/KERNEL_BUILD_REPORT.md) | historical kernel report (Chinese) with [PDF](docs/pdf/KERNEL_BUILD_REPORT_ZH.pdf) |
| `legacy/linux73/` | archived 7.3 material; its scripts are not used for current builds |

Historical reports record their own versions and measurements. Build logs, input SHA-256 sums,
install backups and test output remain in the kbuild working directory.

### Build and install

See the [7.4 adaptation notes](rock4d-linux74/README.md) for the base flow. All
built-in drivers must be built from the same configuration; external modules
must be built against that configuration's kernel symbols and headers — never
reuse an old `.ko` or relax MODPOST.

Installation backs up the known-good boot payload and only switches the boot
selection after checking version, modules, Image, device tree and initrd. The
scripts never reboot on their own. Any change of configuration, kernel or module
still requires re-validation; short tests do not prove long-term thermal
stability.

### VEPU510 encoder validation

The encoder passed hardware validation: 8 real input frames, a 180-frame GOP-60
continuous encode, and three module unload/load cycles all succeeded; the H.264
I/P stream decoded completely on its own; no new kernel errors appeared during
the test window. A dedicated Rockchip fault notifier registers the IOMMU
callback on its own DMA domain while keeping a valid DMA cookie.

In three matched encode runs the hardware throughput median was 72.765 fps with
2.419 ms of process CPU time per frame; the historical software reference was
45.159 fps at 63.083 ms per frame. Those are whole-process measurements over a
short fixed sample and are not end-to-end camera-to-browser latency. Actual
bitrate, PSNR and limits are in the
[codec report](https://github.com/qianruo-orange/autonomous-driving-car/blob/main/docs/CODEC_PERFORMANCE.md).

The encoder is retained as a built-in driver; the application itself does not
encode.

### Provenance and licence

Fixed sources and modification ranges for the 7.4 baseline, the Kiln patches and
the JPEG, MPP and RGA drivers are recorded in each directory's README. Original
author attribution, SPDX identifiers and licences are preserved; each file is
licensed as it declares. Adaptation work in this repository was prepared with Codex
assistance and adds no human DCO sign-off.
