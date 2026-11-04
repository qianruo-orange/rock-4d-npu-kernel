/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef ROCKCHIP_IOMMU_RECOVERY_H
#define ROCKCHIP_IOMMU_RECOVERY_H

struct device;

typedef int (*rockchip_iommu_fault_handler_t)(struct device *iommu_dev,
					    unsigned long iova, int flags, void *data);

int rockchip_iommu_set_fault_handler(struct device *master,
				   rockchip_iommu_fault_handler_t handler, void *data);

/* The caller must quiesce its engine and disable the IOMMU IRQ first. */
int rockchip_iommu_reset(struct device *dev);

/* For a fault callback invoked with the IOMMU clocks already enabled. */
void rockchip_iommu_mask_irq(struct device *dev);

#endif
