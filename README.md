# rock-4d-npu-kernel

Radxa ROCK 4D（RK3576）上的 **mainline Linux 7.3 + RKNPU 可用内核** 复现仓库。

Armbian 官方内核（7.2.8-edge-rockchip64）的设备树没有 NPU 节点，`rknpu` 模块加载后
不绑定（无 `/dev/dri/render*`、无 debugfs），NPU 实际不可用。本仓库给出把这颗
RK3576 双核 NPU 在主线上跑通所需的**全部内核侧差异**与驱动适配，并在实机上验证通过。

仓库不含上游 1.4 GB 源码本体：`scripts/kprep.sh` 从 kernel.org 的 `linux-7.3.tar.xz`
出发，应用 `patches/` 与 `config/`，可复现出与实机一致的内核。

## 基线

| 项 | 值 |
|---|---|
| 板卡 | Radxa ROCK 4D |
| SoC | Rockchip RK3576（NPU 双核，max core mask = 3） |
| 系统 | Armbian trixie (aarch64) |
| 内核 | mainline 7.3 + 10 个 NPU 补丁，`LOCALVERSION=-npu` |
| 内核配置 | 继承 7.2.8 运行配置，关闭 `CONFIG_DRM_ACCEL_ROCKET`（避免与 vendor rknpu 抢占） |
| 产物 | `linux-image-7.3.0-npu_7.3-npu-1_arm64.deb`（52 MB）/ headers（9.8 MB） |

## 目录

```
patches/                10 个 NPU 补丁（pmdomain / iommu / arm64 dts）+ 上游 README
config/config-7.3.0-npu 实机使用的 .config
scripts/kprep.sh        解压源码 + 打补丁 + 写入 config
scripts/kbuild.sh       bindeb-pkg 打包（含 debhelper 13 适配）
scripts/deploy.sh       安装内核 + 修正 vmlinuz 格式 + 换 DTB + 切启动链
aic8800/                板载 AIC8800 USB WiFi 驱动的 7.3 适配（含 cookie 全场景修复）
docs/VALIDATION.md      实机验证记录
```

## 复现

```bash
# 0. 依赖（Debian/Armbian）
sudo apt install build-essential bc bison flex libssl-dev libelf-dev \
                 rsync kmod cpio xz-utils debhelper

# 1. 取源码
cd rock-4d-npu-kernel
wget https://cdn.kernel.org/pub/linux/kernel/v7.x/linux-7.3.tar.xz

# 2. 打补丁 + 配置
scripts/kprep.sh

# 3. 编译打包（aarch64 原生编译约需 8 GB 空闲内存/磁盘）
scripts/kbuild.sh

# 4. 部署（在目标板上，会重启）
sudo scripts/deploy.sh
```

## 三个必须的部署细节

1. **vmlinuz 必须是未压缩 arm64 Image**
   若 `.config` 带 `CONFIG_KERNEL_GZIP=y`，`bindeb-pkg` 产出的 vmlinuz 实为 gzip 流，
   vendor U-Boot 的 `booti` 会报 `Bad Linux ARM64 Image magic!` 并停在 `=>`。
   `deploy.sh` 自动检测文件头 `1f 8b` 并 gunzip。正确文件头为 `4d 5a`，
   offset 0x38 处为 `41 52 4d 64`（ARMd magic）。

2. **DTB 必须换成带 NPU 节点的版本**
   `patches/0004` + `0010` 才会生成 `npu@27700000` 与 `vdd_npu_s0`。
   部署时覆盖 `/boot/dtb/rockchip/rk3576-rock-4d.dtb`（`armbianEnv.txt` 的
   `fdtfile=rockchip/rk3576-rock-4d.dtb` 指向它）。

3. **NPU 电源轨要 `regulator-always-on`**
   即 `patches/0010`。缺它时“第一次推理正常、第二次硬挂”（PMU 报 on 但 PMIC 轨已断电）。

## 验证结果

详见 [docs/VALIDATION.md](docs/VALIDATION.md)，摘要：

- `rknpu iommu is enabled`，MMU `st=0x19/0x19/0x19/0x19`，`/dev/dri/renderD128` + `renderD129`
- `rknn_init` 加载 `yolo26.rknn` 成功
- `kiln-doctor`：`0 critical`，NPU core healthy
- 板载 WiFi（aic8800）重启后自动连回，dmesg 无驱动错误

## 已知坑

| 现象 | 原因 | 处理 |
|---|---|---|
| `Bad Linux ARM64 Image magic!` | bindeb-pkg 产出 gzip vmlinuz | `deploy.sh` 自动 gunzip |
| U-Boot 手动 `load` 后硬挂需断电 | RK3576 DRAM 基址 `0x40000000`，低于此值触发 Synchronous Abort | 不要手工 load 到低位地址 |
| 内存解压引导不可行 | vendor U-Boot（rk2410-2017.09）无 `unzip` 命令 | 坚持未压缩 Image |
| NPU 第二次推理挂死 | NPU 电源轨被 regulator core 关闭 | 打 `0010` |
| 内核 7.3 无需重复打 IOMMU 时钟补丁 | 主线已合入 | `0003` 在 7.3 上为可选/幂等 |
| aic8800 编译失败 | GCC 14 + 7.3 cfg80211 接口变更 | 见 `aic8800/README.md` |
| 改 DKMS 源码后仍用旧模块 | `dkms build` 检测到已构建会跳过 | 加 `--force` |
| `/dev/video0` 不是摄像头 | 是 rkvdec 解码器软链（video-dec0） | 端到端检测需外接 USB 摄像头 |

## 补丁来源

`patches/0001`–`0010` 来自 Kiln 项目（`/opt/kiln/kernel-patches`，仓库内 `patches/README.md`
保留了原始说明），本仓库只做归档与实机验证，未改动补丁内容。

## 许可

内核补丁与 aic8800 驱动代码均为 **GPL-2.0**（与上游一致）。
