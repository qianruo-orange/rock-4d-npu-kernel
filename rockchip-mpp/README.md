# VEPU510 编码器内置集成包

RK3576 的 H.264/H.265 编码驱动（厂商 MPP 的 VEPU510 分支，`linux74-vepu510-prototype`）。原先作为外部模块 `rockchip_vepu510.ko` 加载，现改为内核内置。

## 文件

`mpp_service.c`、`mpp_common.c`、`mpp_iommu.c`、`mpp_rkvenc2.c` 及其头文件、`include/`、`Kconfig`、`Makefile`。

## 接入步骤

1. 复制：`cp -a rockchip-mpp <tree>/drivers/media/platform/rockchip/mpp`
2. 接线：
   ```sh
   echo 'source "drivers/media/platform/rockchip/mpp/Kconfig"' >> <tree>/drivers/media/platform/rockchip/Kconfig
   echo 'obj-$(CONFIG_ROCKCHIP_MPP) += mpp/'                 >> <tree>/drivers/media/platform/rockchip/Makefile
   ```
3. 配置：`CONFIG_ROCKCHIP_MPP=y`
4. 设备树：板级 DTS 需包含 `rk3576-vepu510-mpp.dtsi`（提供 `video-codec@27a00000` 节点）。

## 验证

```sh
ls /sys/bus/platform/drivers/mpp_service /sys/bus/platform/drivers/mpp_rkvenc2
ls /dev/mpp_service
dmesg | grep -i "mpp-srv"
```
