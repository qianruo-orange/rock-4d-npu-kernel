# RKNPU 内置集成包

把 Kiln 移植的 Rockchip NPU 驱动（v0.9.8）作为**内核内置**驱动编译。相对 `kiln-rknpu` DKMS 模块的差别只在于构建方式，源码相同（含 `0005-kiln-rknpu-clock-voltage.patch` 的 1188 MHz / 950 mV 默认值与 `npu-supply` 回退逻辑）。

## 文件

| 路径 | 说明 |
|---|---|
| `*.c` / `*.h` | 驱动源码（`rknpu_drv.c`、`rknpu_job.c`、`rknpu_gem.c`、`rknpu_iommu.c`、`rknpu_fence.c`、`rknpu_reset.c`、`rknpu_debugger.c`） |
| `include/` | 驱动内部头 |
| `compat/soc/rockchip/` | mainline 缺失的厂商头桩（`rockchip_iommu.h`、`rockchip_opp_select.h`、`rockchip_system_monitor.h`、`rockchip_ipa.h`） |
| `Kconfig` / `Makefile` | 已按内置方式调整 |

## 接入步骤

1. 复制到内核树：
   ```sh
   cp -a rockchip-rknpu <tree>/drivers/rknpu
   ```
2. 接线：
   ```sh
   echo 'source "drivers/rknpu/Kconfig"' >> <tree>/drivers/Kconfig
   echo 'obj-y += rknpu/'               >> <tree>/drivers/Makefile
   ```
3. 配置：`CONFIG_ROCKCHIP_RKNPU=y`（另有 `_DRM_GEM`、`_FENCE`、`_DEBUG_FS` =y）。

## 与 DKMS 版本的差异

| 项目 | DKMS 模块 | 内置 |
|---|---|---|
| 加载方式 | `insmod rknpu` | 内核自带，`/dev/rknpu` 直接可用 |
| `rknpu_devfreq.o` | 不参与（固定频率） | **同样不参与**，`Makefile` 里已注释 |
| `-Werror` | 顶层 Kbuild 未加 | 已注释（新内核告警不应阻断构建） |
| `MODULE_IMPORT_NS` | 字符串参数 | 同左（已适配 7.4） |

`include` 路径已改为 `-I$(src)` + `-I$(src)/compat`，因此 `#include "compat/soc/rockchip/rockchip_iommu.h"` 能解析。
