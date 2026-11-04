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

在本仓库根目录执行；构建和安装默认只检查输入，修改操作需要显式参数：

```bash
python3 scripts/vepu510/build-isolated-kernel.py
python3 scripts/vepu510/build-isolated-kernel.py --run
python3 scripts/vepu510/install-isolated-kernel.py
sudo python3 scripts/vepu510/install-isolated-kernel.py --install
# 完成启动验证、显式加载当前候选 MPP 驱动后：
sudo python3 scripts/vepu510/validate-vepu510.py
sudo python3 scripts/vepu510/compare-encoders.py --run
```

构建默认 `-j8`，使用严格最终符号表；生成包保存在源树旁的 `packages/`。状态文件为工作目录的 `isolated-kernel-state.json`，只有 `packages-ready-for-installation` 才允许安装。安装备份位于 `/var/backups/rock4d-vepu510-*`，包含回退入口。已部署候选不会被脚本重复覆盖。

硬件验证输出位于工作目录的 `validation/`，重复执行前先归档旧结果。独立进程的吞吐、CPU 时间不等同于摄像头到浏览器的端到端延迟；出现 IOMMU、复位、超时或内核告警时不得宣告验收通过。

软硬编码对比应在内核构建完成后运行，记录应用是否同时运行。输入为八帧实际摄像头 NV12 循环形成的 180 帧，两个后端使用同一份文件、1280×720、30 FPS 时间基准、GOP 60 和 2.5 Mbps 目标码率。软件使用 veryfast/zerolatency、最多四个线程；三组交替测试取中位数。两种码率控制实现不同，必须同时查看实际码率和 PSNR，不能仅凭吞吐宣称同等画质或端到端延迟提升。结果保存在工作目录 `benchmarks/`。
