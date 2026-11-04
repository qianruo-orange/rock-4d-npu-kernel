# VEPU510 本机构建与验收入口

本目录保存当前接续工作的脚本源码。运行数据与代码分开：默认工作目录为 `/home/qianruo/kbuild/media/vepu510-mpp-linux74`，可通过 `ROCK4D_VEPU_WORKDIR` 指定已有工作目录。完整 Linux 源树仍为 `/home/qianruo/kbuild/kernels/7.4-rc1/source`。

这些入口是本次已准备源树的受控接续工具，依赖工作目录中留存的输入清单、SHA-256、启动实例和前置阶段状态。它们不自动下载或重新准备基础源树，也不应通过修改状态文件跳过守卫检查。

| 脚本 | 用途 |
| --- | --- |
| `continue-port.py` | 初次基础构建、备份、补丁接入和严格构建；已接入源树不重复执行 |
| `build-isolated-kernel.py` | 构建独立版本 `-vepu510-r2` 的 image/headers 和匹配 NPU、Wi-Fi、蓝牙、MPP 模块 |
| `install-isolated-kernel.py` | 校验候选包；显式 `--install` 后备份旧启动载荷、安装并选择候选，不重启 |
| `validate-vepu510.py` | 在候选内核和 MPP 设备上运行 8 帧摄像头输入与 180 帧 GOP 60 测试，独立解码并检查测试期间内核错误 |
| `compare-encoders.py` | 验收通过后，用相同的 180 帧 NV12 对比 MPP 与 libx264，记录吞吐、CPU 时间、实际码率及 PSNR |
| `../install_mpp_userspace.sh` | 当前启动通过硬件验收后，把固定版本 MPP 共享库和公共头文件安装到 `/usr/local`，保留被替换文件的备份 |
| `../enable_vepu510.sh` | 当前启动验收通过、模块身份及版本匹配后，仅为该内核版本启用驱动开机加载 |

在本仓库根目录执行；构建和安装默认只检查输入，修改操作需要显式参数：

```bash
python3 scripts/vepu510/build-isolated-kernel.py
python3 scripts/vepu510/build-isolated-kernel.py --run
python3 scripts/vepu510/install-isolated-kernel.py
sudo python3 scripts/vepu510/install-isolated-kernel.py --install
# 完成启动验证、显式加载当前候选 MPP 驱动后：
sudo python3 scripts/vepu510/validate-vepu510.py
sudo python3 scripts/vepu510/compare-encoders.py --run
# 固定版本 MPP 已完整构建且仍是本次验收使用的产物后：
sudo bash scripts/install_mpp_userspace.sh
sudo bash scripts/enable_vepu510.sh
```

构建默认 `-j8`，使用严格最终符号表；生成包保存在源树旁的 `packages/`。状态文件为工作目录的 `isolated-kernel-state.json`，只有 `packages-ready-for-installation` 才允许安装。安装备份位于 `/var/backups/rock4d-vepu510-*`，包含回退入口。已部署候选不会被脚本重复覆盖。

硬件验证输出位于工作目录的 `validation/`，重复执行前先归档旧结果。独立进程的吞吐、CPU 时间不等同于摄像头到浏览器的端到端延迟；出现 IOMMU、复位、超时或内核告警时不得宣告验收通过。

软硬编码对比应在内核构建完成后运行，记录应用是否同时运行。输入为八帧实际摄像头 NV12 循环形成的 180 帧，两个后端使用同一份文件、1280×720、30 FPS 时间基准、GOP 60 和 2.5 Mbps 目标码率。软件使用 veryfast/zerolatency、最多四个线程；三组交替测试取中位数。两种码率控制实现不同，必须同时查看实际码率和 PSNR，不能仅凭吞吐宣称同等画质或端到端延迟提升。结果保存在工作目录 `benchmarks/`。

对照输入按全范围处理，软件原始输入与输出均显式指定全范围，PSNR 两侧也显式保留全范围。否则自动范围转换会产生与实际编码误差无关的画质损失。应用标注 RGB 转 NV12 使用有限范围，生产编码设置应与该输入契约匹配。对照工具及独立解码只用于验收，应用不以软件编解码器作为运行后端。

MPP 安装器要求固定提交 `7916c3cc000fefc0bdf96636b7d54fe174724f44` 的干净源码、目标库 SONAME 和本次启动的验收结果，使用工作目录 `mpp-userspace-build/mpp/librockchip_mpp.so.0`。安装前应确保产物自验收后没有变化；脚本会保留被替换库与头文件，并在 `/var/backups/rock4d-mpp-*` 记录本次安装库 SHA-256 与来源提交。头文件安装到 `/usr/local/include/rockchip`，共享库安装到 `/usr/local/lib` 后执行 `ldconfig`。

开机加载单元 `rock4d-vepu510.service` 以 `ConditionKernelVersion` 限定已通过验证的版本，并排在应用服务之前。保留早期 RFC 的 blacklist；显式加载在已验证版本上执行，选择旧回退内核时不自动加载同名旧驱动。JPEG 安装器不会为识别出的 MPP 实现新增 blacklist，也不会删除此前保留的限制。

## R2 验收记录

2026-11-04 已启动 `7.4.0-rc1-npu-rt-vepu510-r2`。`validation/hardware-validation.json` 的实际 8 帧、180 帧 GOP 60 均通过；`reload-validation.json` 的三轮模块卸载、加载及编码均通过，两个验收窗口均无新增内核错误。实际应用接口另完成 180 帧、3 个关键帧和 SPS/PPS 检查。来源清单中的 `fault_notification_revision.hardware_retest_pending` 已改为 `false`，验收结果与证据 SHA-256 保存在同一节。

最终性能对照为 `benchmarks/20261104T132042+0800/comparison.json`：硬件吞吐中位数 72.765 帧/秒、进程 CPU 时间 2.419 ms/帧、实际码率 2.474 Mbit/s、PSNR 48.319 dB；历史软件对照为 45.159 帧/秒、63.083 ms/帧、2.517 Mbit/s、48.634 dB。早先未显式匹配色彩范围的质量结果不作为最终结论。这些短时测试不代替长期稳定性、4K 或 H.265 验证。
