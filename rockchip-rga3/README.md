# RGA3 驱动移植包（未部署）

厂商 RGA3 驱动的移植版本，用于 RK3576 的 RGA2E 双核 2D 加速器。当前状态：**能编译进 7.4-rc1 内核，但尚未部署**。

## 1. 来源与改动

- 源码：`rockchip-linux/kernel` 分支 `develop-6.1`，路径 `drivers/video/rockchip/rga3/`（12 个 .c、12 个 .h，约 1.5 万行）。
- 对 7.4-rc1 只需要三处 API 适配，其余原样：

| 位置 | 6.1 写法 | 7.4 写法 |
|---|---|---|
| `rga_drv.c` `MODULE_IMPORT_NS` | `MODULE_IMPORT_NS(DMA_BUF)` | 需要字符串参数 `MODULE_IMPORT_NS("DMA_BUF")` |
| `rga_drv.c` `rga_init_timer()` | `hrtimer_init()` + 赋值 `.function` | `hrtimer_setup()`（新 API） |
| `rga_drv.c` 版本字符串 | `strncpy()` | `strscpy_pad()`（`strncpy` 已从内核移除） |

驱动内部只用标准 IOMMU / dma-buf / DRM-GEM 接口，没有依赖厂商私有内存管理器。

## 2. 接入步骤

1. 复制本目录到内核树：
   ```sh
   cp -a rockchip-rga3 <tree>/drivers/video/rockchip/rga3
   ```
2. 建立 `<tree>/drivers/video/rockchip/{Kconfig,Makefile}`：
   ```make
   # Kconfig
   source "drivers/video/rockchip/rga3/Kconfig"
   # Makefile
   obj-y += rga3/
   ```
   并在 `drivers/video/Kconfig`、`drivers/video/Makefile` 中引入该目录。
3. 设备树：把本目录的 `rk3576-rga2.dtsi` 包含进板级 DTS（节点默认 `disabled`，需逐个 `status = "okay"`）。
4. 配置：`CONFIG_ROCKCHIP_MULTI_RGA`（`=y` 或 `=m`）。
   - 编成模块会在 modpost 阶段因 `pte_offset_map_lock` 未导出而失败，**只能内置**。

## 3. 验证状态

| 项 | 结果 |
|---|---|
| 编译 | `drivers/video/rockchip/rga3/` 零错误零告警 |
| 符号 | `rga_drv_probe` 已进入 vmlinux |
| 运行 | **未在板上启动过**（需要替换内核，见下） |
| 未部署原因 | 换上来的内核来自 `kernels/7.4-rc1/source`，该树缺少 `vepu510-mpp` 编码器驱动；直接替换会丢掉 H.264/H.265 编码能力。需要先把 MPP 源码并回同一棵树再一次性构建 |

## 4. 预期收益（未实测）

RGA 硬件已上电（`aclk_rga2e_0/1` = 702 MHz，`hclk` = 198 MHz），但当前无驱动绑定。应用里 letterbox 缩放走 CPU 上的 `cv::resize`，实测约 2–3 ms（1280×720 → 512）。RGA2E 硬件缩放预计 <1 ms，端到端约省 2 ms（47 → 45 ms，约 4%）。

## 5. 设备树节点

`rk3576-rga2.dtsi` 提供了两个 core 及其 IOMMU，IOMMU 用主线 `rockchip,rk3568-iommu`（与 jpegd 一致），不用厂商的 `rockchip,iommu-v2`：

```dts
rga2_core0: rga@27920000 { compatible = "rockchip,rga2"; ... };
rga2_core0_mmu: iommu@27920f00 { compatible = "rockchip,rk3568-iommu"; ... };
rga2_core1: rga@27930000 { ... };
rga2_core1_mmu: iommu@27930f00 { ... };
```
