# RK3576 VEPU510：Linux 7.4 MPP 移植

本目录保存 Radxa ROCK 4D / RK3576 的单核 VEPU510 编码驱动移植源码及内核集成补丁。它采用 Rockchip 厂商 MPP 接口，属于面向 Linux 7.4-rc1 的外置驱动适配，尚未合入 Linux 主线。

2026-11-04 已完成修正版 `7.4.0-rc1-npu-rt-vepu510-r2` 的完整内核与外置模块严格构建、安装、重启及硬件验收。8 帧实际输入和 180 帧、GOP 60 测试均生成可独立完整解码的 H.264 I/P 帧，三次卸载、加载后的实际图像编码也通过，测试窗口内没有新增 IOMMU 或其他内核错误。

第一版 `7.4.0-rc1-npu-rt-vepu510` 虽能编码，但在 DMA domain 上注册通用 IOMMU fault handler 时触发告警，相关验收记录保留为失败历史。R2 使用独立 Rockchip IOMMU fault 通知接口，避免改写 DMA cookie。第一版和基础 7.4 启动载荷保留回退；两个 VEPU510 版本均使用 PREEMPT_RT、1000 Hz，旧 250 Hz 基础版模块不能混用。

## 来源与目录

厂商内核来源为 [rockchip-linux/kernel 的 develop-6.1 分支](https://github.com/rockchip-linux/kernel/tree/77168c8d5ab82399f65a80e9f807b50ba37cf483/drivers/video/rockchip/mpp)，固定提交为 `77168c8d5ab82399f65a80e9f807b50ba37cf483`。所用文件包括 MPP 服务、任务调度公共层、DMA-BUF/IOMMU 层、`mpp_rkvenc2.c` 和原有用户态接口定义。保留 VEPU510 寄存器编程及 DVBM 硬件规避逻辑。

配套用户态参考实现为 [rockchip-linux/mpp](https://github.com/rockchip-linux/mpp/tree/7916c3cc000fefc0bdf96636b7d54fe174724f44)，固定提交为 `7916c3cc000fefc0bdf96636b7d54fe174724f44`。其中的 VEPU510 HAL 负责生成编码寄存器和任务请求；本目录只保存内核适配，不捆绑用户态库、克隆仓库或测试二进制。匹配用户态已在 kbuild 工作目录完整构建，R2 硬件复测和实际应用接口的 180 帧验证均已通过。固定版本共享库及公共头文件可通过本仓库 `scripts/install_mpp_userspace.sh` 安装到 `/usr/local`。

| 路径 | 内容 |
| --- | --- |
| `module/` | 10 个构建所需的源码、头文件及外置模块 Makefile，生成 `rockchip_vepu510.ko` |
| `0001-linux74-vepu510-integration.patch` | 在已完成 Kiln NPU、JPEG 适配的 7.4 源树上新增真实电源域空闲握手、IOMMU 恢复接口及板级设备树连接 |
| `0002-kiln-iommu-header-scope.patch` | 应用到 Kiln NPU 驱动包根目录；显式选择 NPU 私有兼容头，避免被新增的内核 IOMMU 头覆盖 |
| `rk3576-vepu510-mpp.dtsi` | 与补丁内版本一致的单核编码器、MMU、MPP 服务设备树片段，便于单独审查 |
| `provenance.json` | 固定来源提交、逐文件 SHA-256、原文件路径及修改标记 |
| `LICENSES/` | 源码使用的 GPL-2.0 与 MIT 许可证文本 |

补丁已包含设备树片段的新增和板级 `#include`；应用补丁后不要再重复追加节点。补丁针对本仓库记录的已准备 7.4 源树上下文，其他版本需重新审查，不能把成功应用补丁视为硬件兼容性证明。

第二份补丁修复重编 NPU 时发现的头文件查找冲突：内核的新增头会先于驱动的兼容目录被查找，导致 `rockchip_iommu_is_enabled()` 未声明。补丁将 NPU 的引用限定为驱动自己的兼容头，保留原有电源管理适配行为；同时更新重复执行和新抓取源码的补丁入口。构建副本和 `/usr/src/kiln-rknpu-0.9.8` 的 DKMS 源都需应用该补丁，然后才能为新内核重编 NPU。

外置模块不再自带 `include/soc/rockchip/rockchip_iommu.h` 副本。内核源树中由第一份补丁提供的同名头是唯一权威定义，而内核 `include` 路径优先于模块的兼容目录，副本一旦与内核不一致就会在无告警的情况下改变模块 ABI。因此模块要求目标内核必须已应用第一份补丁；缺少该头时构建直接失败，而不是静默回退到一个陈旧副本。

## 从 6.1 到 7.4 的接口适配

- 保留厂商 MPP ioctl 与 `rk-mpp.h`，外置模块只注册编码核心，移除与本轮无关的解码器注册依赖。
- 适配 `class_create()`、返回 `void` 的 platform remove、文件描述符访问接口及 DMA-BUF 命名空间导入。
- DMA-BUF 映射与 CPU 虚拟映射采用当前接口及对应锁语义，检查映射长度和连续 IOVA，拒绝不适合 CPU 指针使用的映射。
- 新增真实的 `rockchip_pmu_idle_request()`、`rockchip_iommu_reset()` 和 `rockchip_iommu_mask_irq()`。复位过程执行电源域握手与 MMU 恢复；不能用空函数或忽略错误代替这些内核接口。
- Linux 7.4 的通用 `iommu_set_fault_handler()` 拒绝 DMA cookie。修正版在 Rockchip IOMMU 驱动提供独立回调、单 master 注册和注销；IRQ 分发与注销串行化，MPP 保护活动任务的生命周期，保留正常 DMA 映射语义。
- 保留 VEPU510 的 DVBM 核心时钟规避操作；运行任务期间只切换已准备时钟的 enable 状态，避免在禁止抢占的区间执行可能睡眠的 prepare/unprepare 操作。
- 传播时钟、复位、运行时电源管理和任务启动失败；失败任务唤醒等待者，并清理超时工作及引用，避免等待未产生的编码结果。

厂商私有 IPA、OPP、调频与系统监控接口被隔离在 `CONFIG_ROCKCHIP_MPP_VENDOR_DEVFREQ` 条件中，本移植默认不定义该选项。内核启用通用 `CONFIG_PM_DEVFREQ` 不会自动开启厂商调频分支。

## 初始硬件配置

只接入 VEPU0 单核，使用独立编码器兼容项 `rockchip,rkv-encoder-rk3576`，不启用 CCU 多核调度，也不申请 SRAM 固定 IOVA 或 `rockchip,rcb-iova`。帧缓存和相关工作缓冲使用正常 DMA-BUF/DRAM 路径。

设备树初始固定时钟为：ACLK 根时钟 **200 MHz**、HCLK 根时钟 **100 MHz**、编码核心时钟 **200 MHz**。驱动读取板级分配的时钟，不在任务运行时切换到厂商 600/702 MHz 配置。这组数值已用于 R2 短时编码与重载测试，**没有完成 OPP、电压裕量、温度或长时间稳定性验证**，不能视为已验证的最低 OPP 或性能配置。

MMU 使用主线支持的 RK3576/RK3568 compatible。时钟、复位、IRQ、电源域以及寄存器范围依据厂商 RK3576 设备树核对，再适配当前 7.4 的绑定头文件；R2 已完成设备绑定、短时连续编码和重复模块加载验收。

## 本机接续构建流程

脚本源码保存在本仓库 [scripts/vepu510](../scripts/vepu510/README.md)。初次接续由 `vepu510-linux74-port.service` 完成；当前独立修正版由 `vepu510-linux74-r2-package.service` 构建。本目录保存可审查源码；运行状态、构建日志、基础产物备份和本机配置校验清单保留在 kbuild 工作目录，不纳入这份源码快照。

接续脚本按以下顺序工作：

1. 等待当前源树的内核构建退出，取得构建锁，再核对启动实例、配置及输入文件校验值。发现配置变化、输入变化或其他构建时停止接入。
2. 先对原基础内核执行严格的 `make -j8 Image modules dtbs`。只有该步骤成功，且版本与产物检查通过，才继续打补丁；原构建失败不能被当作构建完成。
3. 备份基础 Image、设备树、`Module.symvers`、`System.map` 与配置；校验补丁，先 dry-run，再合入真实电源域、IOMMU 和设备树适配。
4. 将本轮批准的 10 个模块源文件复制到源树的 `.rock4d/drivers/rockchip-vepu510`，重编内核和设备树，重新生成带有新增导出接口的最终 `Module.symvers`。
5. 针对同一源树和最终符号表严格构建外置模块；缺符号、构建失败或守卫条件变化即停止。
6. 全部编译通过后仅进入“等待硬件验证”状态。接续脚本本身不安装内核、不加载模块、不切换工程编码器、不修改启动选择，也不重启。

共享构建锁仅能约束遵守该锁的构建入口。脚本另行扫描源树中的构建进程；接续期间应通过同一个构建入口工作，避免另外直接启动并行的手动 make。

## 编译检查与硬件验证的区别

目前已通过四个驱动对象的 `-Werror` 编译、内核辅助接口对象编译、独立设备树预处理和 DTC 检查，以及合入补丁后的完整内核与外置模块严格构建。R2 构建包含 3475 个内核树模块；MPP 最终 MODPOST、`.ko` 链接和 BTF 生成均成功。修正版新增的第四个导出接口、完整 image/headers 包及匹配外置模块均完成严格编译和上板验收。

最初使用旧 headers 时，MODPOST 缺少三个新导出的内核辅助接口；该依赖已通过内核补丁和完整重建解决。后续外置模块仍须针对最终配置、头文件和符号表严格构建，不能放宽缺符号检查或复用旧模块。仅运行 `modules_prepare` 不足以生成所需的最终符号信息。

R2 上板验证已确认：匹配内核启动、设备树绑定与 `/dev/mpp_service`；实际 NV12 输入及连续 180 帧的 H.264 编码，包括 3 个 I 帧和 177 个 P 帧；独立完整解码；三次模块卸载、加载与编码；验收窗口内无新增内核错误。实际应用编码接口另验证 180 帧、3 个关键帧、首帧 IDR 和 SPS/PPS。证据及摘要见 `provenance.json` 的 `fault_notification_revision.validation`，原始日志位于 kbuild 工作区。

同分辨率、帧率、GOP、目标码率及全范围 NV12 输入的三组对照已完成：硬件吞吐中位数 72.765 帧/秒，进程 CPU 时间 2.419 ms/帧，实际码率 2.474 Mbit/s，PSNR 48.319 dB；历史软件对照为 45.159 帧/秒、63.083 ms/帧、2.517 Mbit/s、48.634 dB。这里只覆盖短时固定样本，不能代替长期温度/频率稳定性、所有分辨率或 H.265 验证。详细方法与限制见 [应用编解码报告](https://github.com/qianruo-orange/autonomous-driving-car/blob/main/docs/CODEC_PERFORMANCE.md)。

## 许可证

厂商文件保留原版权、作者信息与各自 SPDX 标记；其中使用 `(GPL-2.0+ OR MIT)` 的文件继续按原 GPL-2.0-or-later/MIT 双许可处理。新增兼容头、外置模块构建文件及内核辅助接口使用 `GPL-2.0-only`；设备树片段标记为 `(GPL-2.0-only OR MIT)`。具体许可以各文件标记为准，相关完整文本在 `LICENSES/GPL-2.0` 和 `LICENSES/MIT`。

本轮适配通过 Codex 辅助编写、整理与验证，没有添加人的 `Signed-off-by`，也不代替人工 DCO 认证。
