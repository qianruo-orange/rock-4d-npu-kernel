// SPDX-License-Identifier: (GPL-2.0+ OR MIT)
/*
 * Copyright (c) 2020, 2021, 2024 Rockchip Electronics Co., Ltd.
 *
 * Authors: Alpha Lin <alpha.lin@rock-chips.com>
 *          Ding Wei <leo.ding@rock-chips.com>
 *          Herman Chen <herman.chen@rock-chips.com>
 *
 * RK3576 VDPU383 direct-task port for Linux 7.4. Register translation,
 * completion and the RK3576 workaround come from rockchip-linux/kernel,
 * develop-6.1 commit 77168c8d5ab82399f65a80e9f807b50ba37cf483.
 *
 * Independent MPP sessions share one hardware task slot. RCB buffers remain
 * in the DMA-BUFs supplied by the userspace HAL; no fixed SRAM IOVA or vendor
 * DMA-cookie assumptions are needed. Clock rates come from assigned-clocks.
 */

#include <linux/clk.h>
#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/interrupt.h>
#include <linux/iopoll.h>
#include <linux/nospec.h>
#include <linux/of_address.h>
#include <linux/of_platform.h>
#include <linux/pm_runtime.h>
#include <linux/slab.h>
#include <linux/uaccess.h>

#include "mpp_debug.h"
#include "mpp_common.h"
#include "mpp_iommu.h"

#define RKVDEC_REG_NUM                  360
#define RKVDEC_PERF_SEL_NUM             64
#define RKVDEC_PERF_SEL_OFFSET          0x20000
#define RKVDEC_PERF_SEL_BASE            0x424
#define RKVDEC_SEL_VAL0_BASE            0x428
#define RKVDEC_SEL_VAL1_BASE            0x42c
#define RKVDEC_SEL_VAL2_BASE            0x430
#define RKVDEC_SET_PERF_SEL(a, b, c)    ((a) | ((b) << 8) | ((c) << 16))
#define RKVDEC_GET_FORMAT(x)            ((x) & 0x3ff)
#define RKVDEC_REG_RLC_BASE             0x200
#define RKVDEC_REG_RLC_BASE_INDEX       128
#define RKVDEC_PERF_WORKING_CNT         0x41c
#define RKVDEC_REG_CACHE0_SIZE_BASE     0x51c
#define RKVDEC_REG_CLR_CACHE0_BASE      0x510
#define RKVDEC_REG_MAX_READS            0x518
#define RKVDEC_CACHE_PERMIT_CACHEABLE_ACCESS BIT(0)
#define RKVDEC_CACHE_PERMIT_READ_ALLOCATE     BIT(1)
#define RKVDEC_CACHE_LINE_SIZE_64_BYTES      BIT(4)
#define RKVDEC_START_EN                 BIT(0)
#define RKVDEC_MAX_RCB_NUM               16
#define RK3576_HACK_MAGIC               0x76543210

#define to_rkvdec2_task(task) container_of(task, struct rkvdec2_task, mpp_task)
#define to_rkvdec2_dev(dev) container_of(dev, struct rkvdec2_dev, mpp)

enum rkvdec_format {
	RKVDEC_FMT_H265D,
	RKVDEC_FMT_H264D,
	RKVDEC_FMT_VP9D,
	RKVDEC_FMT_AVS2,
	RKVDEC_FMT_AV1D,
};

struct rcb_info_elem {
	u32 index;
	u32 size;
};

struct rkvdec2_rcb_info {
	u32 cnt;
	struct rcb_info_elem elem[RKVDEC_MAX_RCB_NUM];
};

struct rkvdec_link_info {
	u32 ip_reset_base;
	u32 ip_reset_en;
	u32 irq_base;
	u32 irq_mask;
	u32 status_base;
	u32 status_mask;
	u32 err_mask;
	u32 ip_reset_mask;
	u32 ip_time_base;
	u32 en_base;
	u32 ip_en_base;
	u32 ip_en_val;
};

struct rkvdec_link_dev {
	void __iomem *reg_base;
	const struct rkvdec_link_info *info;
};

struct rkvdec2_task {
	struct mpp_task mpp_task;
	u32 reg[RKVDEC_REG_NUM];
	struct reg_offset_info off_inf;
	u32 reg_sel[RKVDEC_PERF_SEL_NUM];
	u32 strm_addr;
	u32 irq_status;
	u32 w_req_cnt;
	struct mpp_request w_reqs[MPP_MAX_MSG_NUM];
	u32 r_req_cnt;
	struct mpp_request r_reqs[MPP_MAX_MSG_NUM];
};

struct rkvdec2_session_priv {
	struct {
		u32 flag;
		u64 val;
	} codec_info[DEC_INFO_BUTT];
	struct rkvdec2_rcb_info rcb_inf;
};

struct rkvdec2_dev {
	struct mpp_dev mpp;
	struct clk_bulk_data clocks[5];
	struct reset_control *resets;
	struct rkvdec_link_dev link;
	struct rkvdec_link_dev *link_dec;
	struct mpp_dma_buffer *fix;
	unsigned long clock_hz;
	bool need_workaround;
};

static struct rkvdec_link_info vdpu383_link_info = {
	.ip_reset_base = 0x44,
	.ip_reset_en = BIT(0),
	.irq_base = 0x48,
	.irq_mask = 0x30000,
	.status_base = 0x4c,
	.status_mask = 0x3ff0000,
	.err_mask = 0x3fe,
	.ip_reset_mask = 0x8000000,
	.ip_time_base = 0x54,
	.en_base = 0x40,
	.ip_en_base = 0x58,
	.ip_en_val = 0x01000000,
};

static struct mpp_hw_info rkvdec_vdpu383_hw_info = {
	.reg_num = RKVDEC_REG_NUM,
	.reg_id = 0,
	.reg_start = 0,
	.reg_end = RKVDEC_REG_NUM - 1,
	.reg_en = 16,
	.reg_fmt = 8,
	.reg_ret_status = 15,
	.magic_base = 0x100,
	.link_info = &vdpu383_link_info,
};

static const u16 trans_vdpu383_tbl_h265d[] = {
	128, 129, 130, 131, 132, 133, 134, 140, 142, 144, 146, 148, 150, 152, 154,
	156, 158, 160, 168, 169, 170, 171, 172, 173, 174, 175, 176, 177, 178, 179,
	180, 181, 182, 183, 184, 185, 192, 194, 195, 196, 197, 198, 199, 200, 201,
	202, 203, 204, 205, 206, 207, 208, 209, 210, 216, 217, 218, 219, 220, 221,
	222, 223, 224, 225, 226, 227, 228, 229, 230, 231, 232
};

static const u16 trans_vdpu383_tbl_h264d[] = {
	128, 129, 130, 131, 132, 133, 134, 140, 142, 144, 146, 148, 150, 152, 154,
	156, 158, 168, 169, 170, 171, 172, 173, 174, 175, 176, 177, 178, 179, 180,
	181, 182, 183, 184, 185, 192, 194, 195, 196, 197, 198, 199, 200, 201, 202,
	203, 204, 205, 206, 207, 208, 209, 210, 216, 217, 218, 219, 220, 221, 222,
	223, 224, 225, 226, 227, 228, 229, 230, 231, 232
};

static const u16 trans_vdpu383_tbl_vp9d[] = {
	128, 129, 130, 131, 132, 133, 134, 140, 142, 144, 146, 148, 150, 152, 154,
	156, 158, 160, 168, 169, 170, 171, 172, 173, 174, 175, 176, 177, 178, 179,
	180, 181, 182, 183, 184, 185, 192, 194, 195, 196, 197, 198, 199, 200, 201,
	202, 203, 204, 205, 206, 207, 208, 209, 210, 216, 217, 218, 219, 220, 221,
	222, 223, 224, 225, 226, 227, 228, 229, 230, 231, 232
};

static const u16 trans_vdpu383_tbl_avs2d[] = {
	128, 129, 130, 131, 132, 133, 134, 140, 142, 144, 146, 148, 150, 152, 154,
	156, 158, 160, 168, 169, 170, 171, 172, 173, 174, 175, 176, 177, 178, 179,
	180, 181, 182, 183, 184, 185, 192, 194, 195, 196, 197, 198, 199, 200, 201,
	202, 203, 204, 205, 206, 207, 208, 209, 210, 216, 217, 218, 219, 220, 221,
	222, 223, 224, 225, 226, 227, 228, 229, 230, 231, 232
};

static const u16 trans_vdpu383_tbl_av1d[] = {
	128, 131, 133, 134, 140, 142, 144, 146, 148, 150, 152, 154,
	156, 158, 160, 168, 169, 170, 171, 172, 173, 174, 175, 176, 177, 178, 179,
	180, 181, 182, 183, 184, 185, 192, 194, 195, 196, 197, 198, 199, 200, 201,
	202, 203, 204, 205, 206, 207, 208, 209, 210, 216, 217, 218, 219, 220, 221,
	222, 223, 224, 225, 226, 227, 228, 229, 230, 231, 232
};

static struct mpp_trans_info rkvdec_vdpu383_trans[] = {
	[RKVDEC_FMT_H265D] = {
		.count = ARRAY_SIZE(trans_vdpu383_tbl_h265d),
		.table = trans_vdpu383_tbl_h265d,
	},
	[RKVDEC_FMT_H264D] = {
		.count = ARRAY_SIZE(trans_vdpu383_tbl_h264d),
		.table = trans_vdpu383_tbl_h264d,
	},
	[RKVDEC_FMT_VP9D] = {
		.count = ARRAY_SIZE(trans_vdpu383_tbl_vp9d),
		.table = trans_vdpu383_tbl_vp9d,
	},
	[RKVDEC_FMT_AVS2] = {
		.count = ARRAY_SIZE(trans_vdpu383_tbl_avs2d),
		.table = trans_vdpu383_tbl_avs2d,
	},
	[RKVDEC_FMT_AV1D] = {
		.count = ARRAY_SIZE(trans_vdpu383_tbl_av1d),
		.table = trans_vdpu383_tbl_av1d,
	}
};

#define RK3576_HACK_DATA_RLC_OFFSET		(0)
#define RK3576_HACK_DATA_PPS_OFFSET		(RK3576_HACK_DATA_RLC_OFFSET + 64)
#define RK3576_HACK_DATA_RPS_OFFSET		(RK3576_HACK_DATA_PPS_OFFSET + 256)
#define RK3576_HACK_DATA_RCB_STRMD_OFFSET	(RK3576_HACK_DATA_RPS_OFFSET + 256)
#define RK3576_HACK_DATA_RCB_INTRA_OFFSET	(RK3576_HACK_DATA_RCB_STRMD_OFFSET + 256)
#define RK3576_HACK_DATA_OUT_OFFSET		(RK3576_HACK_DATA_RCB_INTRA_OFFSET + 512)
#define RK3576_HACK_DATA_COLMV_OFFSET		(RK3576_HACK_DATA_OUT_OFFSET + 1536)

#define RK3576_HACK_REGS_OFFSET			(4096)
#define RK3576_HACK_REG_NODE_OFFSET		(RK3576_HACK_REGS_OFFSET)
#define RK3576_HACK_REG_SEG0_OFFSET		(RK3576_HACK_REGS_OFFSET + 32)
#define RK3576_HACK_REG_SEG1_OFFSET		(RK3576_HACK_REGS_OFFSET + 256)
#define RK3576_HACK_REG_SEG2_OFFSET		(RK3576_HACK_REGS_OFFSET + 512)
#define RK3576_HACK_REG_DEBUG_OFFSET		(RK3576_HACK_REGS_OFFSET + 1024)


static const char rk3576_hack_h264_rlc_data[] = {
	0x00, 0x00, 0x01, 0x65,
	0x88, 0x82, 0x0b, 0x01,
	0x2f, 0x08, 0xc5, 0x00,
	0x01, 0x51, 0x78, 0xe0,
	0x00, 0x24, 0xf7, 0x1c,
	0x00, 0x04, 0xcc, 0xeb,
	0x89, 0xd7, 0x80, 0x00,
	0x00, 0x00, 0x00, 0x00,
};

static const char rk3576_hack_h264_pps_data[] = {
	0x40, 0x26, 0x00, 0x10,
	0x04, 0x08, 0x00, 0x08,
	0x80, 0x01, 0x00, 0x00,
	0x00, 0x40, 0x01, 0xd8,
	0x07, 0x7c, 0x7a, 0x00,
	0x00, 0x00, 0x00, 0x04,
	0x00, 0x00, 0x00, 0x00,
};

static bool rk3576_hack_flag;

static bool is_rk3576(struct device *dev)
{
	int ret;

	ret = device_property_match_string(dev, "compatible", "rockchip,rkv-decoder-rk3576");

	return (ret < 0) ? false : true;
}

static int rk3576_workaround_init(struct mpp_dev *mpp)
{
	struct rkvdec2_dev *dec = to_rkvdec2_dev(mpp);
	u32 *reg;

	/* check current soc is rk3576 */
	rk3576_hack_flag = is_rk3576(mpp->dev);
	if (!rk3576_hack_flag)
		return 0;

	/* alloc buffer for node */
	dec->fix = mpp_dma_alloc(mpp->dev, 2 * PAGE_SIZE);
	if (!dec->fix) {
		dev_err(mpp->dev, "failed to create buffer for hack\n");
		return -ENOMEM;
	}
	memset(dec->fix->vaddr, 0, dec->fix->size);

	/* set rlc_in */
	memcpy(dec->fix->vaddr + RK3576_HACK_DATA_RLC_OFFSET,
		rk3576_hack_h264_rlc_data, sizeof(rk3576_hack_h264_rlc_data));

	/* set sps_pps */
	memcpy(dec->fix->vaddr + RK3576_HACK_DATA_PPS_OFFSET,
		rk3576_hack_h264_pps_data, sizeof(rk3576_hack_h264_pps_data));

	/* set core registers */
	reg = dec->fix->vaddr + RK3576_HACK_REGS_OFFSET;
	reg[8]  = 0x00000001; // dec_mode
	reg[13] = 0x0000ffff; // core time out
	reg[16] = 0x00000101; // disable error proc
	reg[20] = 0xffffffff; // cabac error low bits
	reg[21] = 0x3ff3ffff; // cabac error high bits

	reg[64] = RK3576_HACK_MAGIC; // magic number
	reg[65] = 0x00000000; // stream start bit
	reg[66] = 0x00000020; // stream_len
	reg[67] = 0x000000a8; // global_len
	reg[68] = 0x00000002; // hor_virstride
	reg[69] = 0x00000002; // ver_virstride
	reg[70] = 0x00000040; // y_virstride

	reg[128] = dec->fix->iova + RK3576_HACK_DATA_RLC_OFFSET; // rlc_base
	reg[129] = dec->fix->iova + RK3576_HACK_DATA_RPS_OFFSET; // rps_base
	reg[131] = dec->fix->iova + RK3576_HACK_DATA_PPS_OFFSET; // pps_base
	reg[140] = dec->fix->iova + RK3576_HACK_DATA_RCB_STRMD_OFFSET; // streamd_rcb
	reg[141] = 0x000000c0;
	reg[148] = dec->fix->iova + RK3576_HACK_DATA_RCB_INTRA_OFFSET; // intra_rcb
	reg[149] = 0x00000200;
	reg[168] = dec->fix->iova + RK3576_HACK_DATA_OUT_OFFSET; // decout_base
	reg[216] = dec->fix->iova + RK3576_HACK_DATA_COLMV_OFFSET; // colmv_base

	/* set link node */
	reg = dec->fix->vaddr + RK3576_HACK_REG_NODE_OFFSET;
	reg[0] = dec->fix->iova + RK3576_HACK_DATA_OUT_OFFSET; // next link node
	reg[1] = 0x76543212; // table info
	reg[2] = dec->fix->iova + RK3576_HACK_REG_DEBUG_OFFSET; // debug regs
	reg[3] = dec->fix->iova + RK3576_HACK_REG_SEG0_OFFSET; // seg0 regs
	reg[4] = dec->fix->iova + RK3576_HACK_REG_SEG1_OFFSET; // seg1 regs
	reg[5] = dec->fix->iova + RK3576_HACK_REG_SEG2_OFFSET; // seg2 regs

	return 0;
}

static int rk3576_workaround_exit(struct mpp_dev *mpp)
{
	struct rkvdec2_dev *dec = to_rkvdec2_dev(mpp);

	/* check current soc is rk3576 */
	if (!rk3576_hack_flag)
		return 0;

	if (dec->fix) {
		mpp_dma_free(dec->fix);
		dec->fix = NULL;
	}

	return 0;
}

static int rk3576_workaround_run(struct mpp_dev *mpp)
{
	struct rkvdec2_dev *dec = to_rkvdec2_dev(mpp);
	struct rkvdec_link_dev *link = dec->link_dec;
	int ret;
	u32 irq_val, status;

	/* check current soc is rk3576 */
	if (!rk3576_hack_flag || !dec->fix || !link)
		return 0;

	/* disable hardware irq */
	writel_relaxed(0x00008000, link->reg_base + 0x58);

	/* set link registers */
	writel_relaxed(0x0007ffff, link->reg_base + 0x54); // set ip time out
	writel_relaxed(0x00010001, link->reg_base + 0x00); // ccu mode
	writel_relaxed(dec->fix->iova + RK3576_HACK_REG_NODE_OFFSET, link->reg_base + 0x4); // addr
	writel_relaxed(0x00000001, link->reg_base + 0x08); // link mode
	writel_relaxed(0x00000001, link->reg_base + 0x18); // link en

	/* enable hardware */
	wmb();
	writel(0x01, link->reg_base + 0x0c); // config link

	/* wait hardware */
	ret = readl_relaxed_poll_timeout_atomic(link->reg_base + 0x48, irq_val, irq_val, 1, 500);
	if (ret == -ETIMEDOUT) {
		pr_err("%s timeout.\n", __func__);
	} else {
		status = readl(link->reg_base + 0x4c);
		if (status & 0x3fe) {
			dev_err(mpp->dev, "workaround status %#x\n", status);
			ret = -EIO;
		}
	}
	/* clear irq and status */
	writel_relaxed(0xffff0000, link->reg_base + 0x48);
	writel_relaxed(0xffff0000, link->reg_base + 0x4c);

	/* reset register */
	writel_relaxed(0x00000000, link->reg_base + 0x00); // ccu mode
	writel_relaxed(0x00000000, link->reg_base + 0x08); // link mode
	writel_relaxed(0x00000000, link->reg_base + 0x18); // link en

	/* enable irq */
	writel(0x00000000, link->reg_base + 0x58);
	udelay(5);

	return ret;
}

static int vdpu383_reset(struct mpp_dev *mpp)
{
	struct rkvdec2_dev *dec = to_rkvdec2_dev(mpp);
	struct rkvdec_link_dev *link = dec->link_dec;
	u32 status;
	int ret;

	writel(link->info->ip_en_val | BIT(15),
	       link->reg_base + link->info->ip_en_base);
	writel(link->info->ip_reset_en,
	       link->reg_base + link->info->ip_reset_base);
	ret = readl_poll_timeout(link->reg_base + link->info->status_base,
				status, status & BIT(11), 1, 2000);
	writel(link->info->ip_reset_mask,
	       link->reg_base + link->info->status_base);
	writel(link->info->irq_mask, link->reg_base + link->info->irq_base);
	writel(link->info->status_mask,
	       link->reg_base + link->info->status_base);
	writel(link->info->ip_en_val,
	       link->reg_base + link->info->ip_en_base);
	dec->need_workaround = true;
	if (ret)
		dev_err(mpp->dev, "decoder reset timed out: %#x\n", status);
	return ret;
}

static int vdpu383_request_range(struct mpp_request *req, u32 base, u32 size)
{
	u32 offset;

	if (req->offset < base || !IS_ALIGNED(req->offset, sizeof(u32)) ||
	    !IS_ALIGNED(req->size, sizeof(u32)))
		return -EINVAL;
	offset = req->offset - base;
	if (offset > size || req->size > size - offset)
		return -EINVAL;
	return 0;
}

static int rkvdec2_extract_task_msg(struct mpp_session *session,
				  struct rkvdec2_task *task,
				  struct mpp_task_msgs *msgs)
{
	u32 i;
	int ret;

	for (i = 0; i < msgs->req_cnt; i++) {
		struct mpp_request *req = &msgs->reqs[i];
		u32 base;
		u32 size;

		if (!req->size)
			continue;
		switch (req->cmd) {
		case MPP_CMD_SET_REG_WRITE:
			ret = vdpu383_request_range(req, 0, sizeof(task->reg));
			if (ret || task->w_req_cnt >= ARRAY_SIZE(task->w_reqs))
				return -EINVAL;
			if (copy_from_user((u8 *)task->reg + req->offset,
					   req->data, req->size))
				return -EFAULT;
			task->w_reqs[task->w_req_cnt++] = *req;
			break;
		case MPP_CMD_SET_REG_READ:
			base = req->offset >= RKVDEC_PERF_SEL_OFFSET ?
				RKVDEC_PERF_SEL_OFFSET : 0;
			size = base ? sizeof(task->reg_sel) : sizeof(task->reg);
			ret = vdpu383_request_range(req, base, size);
			if (ret || task->r_req_cnt >= ARRAY_SIZE(task->r_reqs))
				return -EINVAL;
			task->r_reqs[task->r_req_cnt++] = *req;
			break;
		case MPP_CMD_SET_REG_ADDR_OFFSET:
			if (req->size % sizeof(struct reg_offset_elem))
				return -EINVAL;
			ret = mpp_extract_reg_offset_info(&task->off_inf, req);
			if (ret)
				return ret;
			break;
		case MPP_CMD_SET_RCB_INFO:
			/* The HAL already supplies DRAM RCB DMA-BUF addresses. */
			if (req->size > sizeof(((struct rkvdec2_rcb_info *)0)->elem) ||
			    req->size % sizeof(struct rcb_info_elem))
				return -EINVAL;
			break;
		default:
			break;
		}
	}
	for (i = 0; i < task->off_inf.cnt; i++) {
		if (task->off_inf.elem[i].index >= RKVDEC_REG_NUM)
			return -EINVAL;
	}
	return task->w_req_cnt ? 0 : -EINVAL;
}

static void *rkvdec2_alloc_task(struct mpp_session *session,
			      struct mpp_task_msgs *msgs)
{
	struct mpp_dev *mpp = session->mpp;
	struct rkvdec2_task *task;
	struct mpp_task *mpp_task;
	u32 fmt;
	int ret;
	int i;

	task = kzalloc_obj(*task);
	if (!task)
		return NULL;
	mpp_task = &task->mpp_task;
	mpp_task_init(session, mpp_task);
	mpp_task->hw_info = mpp->var->hw_info;
	mpp_task->reg = task->reg;
	ret = rkvdec2_extract_task_msg(session, task, msgs);
	if (ret)
		goto fail;
	fmt = RKVDEC_GET_FORMAT(task->reg[mpp_task->hw_info->reg_fmt]);
	if (fmt >= ARRAY_SIZE(rkvdec_vdpu383_trans)) {
		ret = -EINVAL;
		goto fail;
	}
	if (session->trans_count < 0 ||
	    session->trans_count > ARRAY_SIZE(session->trans_table)) {
		ret = -EINVAL;
		goto fail;
	}
	for (i = 0; i < session->trans_count; i++) {
		if (session->trans_table[i] >= RKVDEC_REG_NUM) {
			ret = -EINVAL;
			goto fail;
		}
	}
	if (!(msgs->flags & MPP_FLAGS_REG_FD_NO_TRANS)) {
		ret = mpp_translate_reg_address(session, mpp_task, fmt,
						task->reg, &task->off_inf);
		if (ret)
			goto fail;
		mpp_translate_reg_offset_info(mpp_task, &task->off_inf, task->reg);
	}
	task->strm_addr = task->reg[RKVDEC_REG_RLC_BASE_INDEX];
	return mpp_task;

fail:
	dev_err(mpp->dev, "invalid decoder task: %d\n", ret);
	mpp_task_finalize(session, mpp_task);
	kfree(task);
	return NULL;
}

static int rkvdec_vdpu38x_run(struct mpp_dev *mpp, struct mpp_task *mpp_task)
{
	struct rkvdec2_task *task = to_rkvdec2_task(mpp_task);
	struct rkvdec2_dev *dec = to_rkvdec2_dev(mpp);
	u32 timing_en = mpp->srv->timing_en;
	struct rkvdec_link_dev *link = dec->link_dec;

	/* set cache size */
	u32 reg = RKVDEC_CACHE_PERMIT_CACHEABLE_ACCESS |
		  RKVDEC_CACHE_PERMIT_READ_ALLOCATE;
	u32 i;
	int ret;

	mpp_debug_enter();

	if (dec->need_workaround) {
		mpp_iommu_flush_tlb(mpp->iommu_info);
		ret = rk3576_workaround_run(mpp);
		if (ret) {
			vdpu383_reset(mpp);
			WRITE_ONCE(mpp->recovery_error, ret);
			return ret;
		}
		dec->need_workaround = false;
	}

	if (!mpp_debug_unlikely(DEBUG_CACHE_32B))
		reg |= RKVDEC_CACHE_LINE_SIZE_64_BYTES;

	mpp_write_relaxed(mpp, RKVDEC_REG_CACHE0_SIZE_BASE, reg);
	/* clear cache */
	mpp_write_relaxed(mpp, RKVDEC_REG_CLR_CACHE0_BASE, 1);
	/* init max outstanding read */
	mpp_write_relaxed(mpp, RKVDEC_REG_MAX_READS, 0x1c);

	/* set registers for hardware */
	for (i = 0; i < task->w_req_cnt; i++) {
		u32 s, e;
		struct mpp_request *req = &task->w_reqs[i];

		s = req->offset / sizeof(u32);
		e = s + req->size / sizeof(u32);
		mpp_write_req(mpp, task->reg, s, e, -1);
	}

	/* flush tlb before starting hardware */
	mpp_iommu_flush_tlb(mpp->iommu_info);

	/* init current task */
	mpp->cur_task = mpp_task;

	mpp_task_run_begin(mpp_task, timing_en, MPP_WORK_TIMEOUT_DELAY);

	/* set ip time out threshold, ip watch-dog */
	writel_relaxed(0x7fffff, link->reg_base + link->info->ip_time_base);

	/* set ip func to def val */
	writel_relaxed(link->info->ip_en_val, link->reg_base + link->info->ip_en_base);

	/* Flush the register before the start the device */
	wmb();
	/* enable hardware */
	writel(RKVDEC_START_EN, link->reg_base + link->info->en_base);

	mpp_task_run_end(mpp_task, timing_en);

	mpp_debug_leave();

	return 0;
}

static int rkvdec_vdpu38x_irq(struct mpp_dev *mpp)
{
	struct rkvdec2_dev *dec = to_rkvdec2_dev(mpp);
	struct rkvdec_link_dev *link = dec->link_dec;
	u32 irq_val;
	u32 irq_bits = link->info->irq_mask >> 16;
	u32 status_bits = link->info->status_mask >> 16;

	/* read and clear irq */
	irq_val = readl_relaxed(link->reg_base + link->info->irq_base);
	if (!(irq_val & irq_bits))
		return IRQ_NONE;
	writel(link->info->irq_mask, link->reg_base + link->info->irq_base);

	/* read and clear status */
	mpp->irq_status = readl_relaxed(link->reg_base + link->info->status_base);
	writel(link->info->status_mask, link->reg_base + link->info->status_base);

	mpp_debug(DEBUG_IRQ_STATUS, "irq_status: %08x : %08x\n", irq_val, mpp->irq_status);

	/* wake isr to handle current task */
	if (mpp->irq_status & status_bits)
		return IRQ_WAKE_THREAD;

	return IRQ_NONE;
}

static int rkvdec_vdpu38x_isr(struct mpp_dev *mpp)
{
	u32 err_mask;
	struct rkvdec2_task *task = NULL;
	struct mpp_task *mpp_task = mpp->cur_task;
	struct rkvdec2_dev *dec = to_rkvdec2_dev(mpp);

	if (!mpp_task) {
		dev_err(mpp->dev, "no current task\n");
		return IRQ_HANDLED;
	}
	mpp_task->hw_cycles = mpp_read(mpp, RKVDEC_PERF_WORKING_CNT);
	mpp_task->hw_time = mpp_task->hw_cycles / (dec->clock_hz / 1000000);
	mpp_time_diff_with_hw_time(mpp_task, dec->clock_hz);
	mpp->cur_task = NULL;
	task = to_rkvdec2_task(mpp_task);
	task->irq_status = mpp->irq_status;

	mpp_debug(DEBUG_IRQ_STATUS, "irq_status: %08x\n", task->irq_status);

	/* handle error */
	err_mask = dec->link_dec->info->err_mask;
	if (err_mask & task->irq_status) {
		atomic_inc(&mpp->reset_request);
		if (mpp_debug_unlikely(DEBUG_DUMP_ERR_REG)) {
			mpp_debug(DEBUG_DUMP_ERR_REG, "irq_status: %08x\n", task->irq_status);
			mpp_task_dump_hw_reg(mpp);
		}
	}

	mpp_task_finish(mpp_task->session, mpp_task);

	mpp_debug_leave();
	return IRQ_HANDLED;
}

static int rkvdec2_read_perf_sel(struct mpp_dev *mpp, u32 *regs, u32 s, u32 e)
{
	u32 i;
	u32 sel0, sel1, sel2, val;

	for (i = s; i < e; i += 3) {
		/* set sel */
		sel0 = i;
		sel1 = ((i + 1) < e) ? (i + 1) : 0;
		sel2 = ((i + 2) < e) ? (i + 2) : 0;
		val = RKVDEC_SET_PERF_SEL(sel0, sel1, sel2);
		writel_relaxed(val, mpp->reg_base + RKVDEC_PERF_SEL_BASE);
		/* read data */
		regs[sel0] = readl_relaxed(mpp->reg_base + RKVDEC_SEL_VAL0_BASE);
		mpp_debug(DEBUG_GET_PERF_VAL, "sel[%d]:%u\n", sel0, regs[sel0]);
		if (sel1) {
			regs[sel1] = readl_relaxed(mpp->reg_base + RKVDEC_SEL_VAL1_BASE);
			mpp_debug(DEBUG_GET_PERF_VAL, "sel[%d]:%u\n", sel1, regs[sel1]);
		}
		if (sel2) {
			regs[sel2] = readl_relaxed(mpp->reg_base + RKVDEC_SEL_VAL2_BASE);
			mpp_debug(DEBUG_GET_PERF_VAL, "sel[%d]:%u\n", sel2, regs[sel2]);
		}
	}

	return 0;
}

static int rkvdec2_finish(struct mpp_dev *mpp, struct mpp_task *mpp_task)
{
	u32 i;
	u32 dec_get;
	u32 dec_length;
	u32 reg_ret_status;
	struct rkvdec2_task *task = to_rkvdec2_task(mpp_task);
	struct mpp_request *req;
	u32 s, e;

	mpp_debug_enter();

	/* read register after running */
	for (i = 0; i < task->r_req_cnt; i++) {
		req = &task->r_reqs[i];
		/* read perf register */
		if (req->offset >= RKVDEC_PERF_SEL_OFFSET) {
			u32 off = req->offset - RKVDEC_PERF_SEL_OFFSET;

			s = off / sizeof(u32);
			e = s + req->size / sizeof(u32);
			rkvdec2_read_perf_sel(mpp, task->reg_sel, s, e);
		} else {
			s = req->offset / sizeof(u32);
			e = s + req->size / sizeof(u32);
			mpp_read_req(mpp, task->reg, s, e);
		}
	}
	/* revert hack for irq status */
	reg_ret_status = mpp->var->hw_info->reg_ret_status;
	task->reg[reg_ret_status] = task->irq_status;

	/* revert hack for decoded length */
	dec_get = mpp_read_relaxed(mpp, RKVDEC_REG_RLC_BASE);
	dec_length = dec_get - task->strm_addr;
	task->reg[RKVDEC_REG_RLC_BASE_INDEX] = dec_length << 10;
	mpp_debug(DEBUG_REGISTER, "dec_get %08x dec_length %u\n", dec_get, dec_length);

	if (mpp->srv->timing_en) {
		s64 time_diff;

		mpp_task->on_finish = ktime_get();
		set_bit(TASK_TIMING_FINISH, &mpp_task->state);

		time_diff = ktime_us_delta(mpp_task->on_finish, mpp_task->on_create);

		if (mpp->timing_check && time_diff > (s64)mpp->timing_check)
			mpp_task_dump_timing(mpp_task, time_diff);
	}

	mpp_debug_leave();

	return 0;
}

static int rkvdec2_result(struct mpp_dev *mpp, struct mpp_task *mpp_task,
		   struct mpp_task_msgs *msgs)
{
	u32 i;
	struct mpp_request *req;
	struct rkvdec2_task *task = to_rkvdec2_task(mpp_task);

	for (i = 0; i < task->r_req_cnt; i++) {
		req = &task->r_reqs[i];

		if (req->offset >= RKVDEC_PERF_SEL_OFFSET) {
			u32 off = req->offset - RKVDEC_PERF_SEL_OFFSET;

			if (copy_to_user(req->data,
					 (u8 *)task->reg_sel + off,
					 req->size)) {
				mpp_err("copy_to_user perf_sel fail\n");
				return -EIO;
			}
		} else {
			if (copy_to_user(req->data,
					 (u8 *)task->reg + req->offset,
					 req->size)) {
				mpp_err("copy_to_user reg fail\n");
				return -EIO;
			}
		}
	}

	return 0;
}

static int rkvdec2_free_task(struct mpp_session *session, struct mpp_task *mpp_task)
{
	struct rkvdec2_task *task = to_rkvdec2_task(mpp_task);

	mpp_task_finalize(session, mpp_task);
	kfree(task);

	return 0;
}

static int rkvdec2_control(struct mpp_session *session, struct mpp_request *req)
{
	switch (req->cmd) {
	case MPP_CMD_SEND_CODEC_INFO: {
		u32 i;
		u32 cnt;
		struct codec_info_elem elem;
		struct rkvdec2_session_priv *priv;

		if (!session || !session->priv) {
			mpp_err("session info null\n");
			return -EINVAL;
		}
		priv = session->priv;

		cnt = req->size / sizeof(elem);
		cnt = (cnt > DEC_INFO_BUTT) ? DEC_INFO_BUTT : cnt;
		mpp_debug(DEBUG_IOCTL, "codec info count %d\n", cnt);
		for (i = 0; i < cnt; i++) {
			if (copy_from_user(&elem, req->data + i * sizeof(elem), sizeof(elem))) {
				mpp_err("copy_from_user failed\n");
				continue;
			}
			if (elem.type > DEC_INFO_BASE && elem.type < DEC_INFO_BUTT &&
			    elem.flag > CODEC_INFO_FLAG_NULL && elem.flag < CODEC_INFO_FLAG_BUTT) {
				elem.type = array_index_nospec(elem.type, DEC_INFO_BUTT);
				priv->codec_info[elem.type].flag = elem.flag;
				priv->codec_info[elem.type].val = elem.data;
			} else {
				mpp_err("codec info invalid, type %d, flag %d\n",
					elem.type, elem.flag);
			}
		}
	}
		break;
	default: {
		mpp_err("unknown mpp ioctl cmd %x\n", req->cmd);
	}
		break;
	}

	return 0;
}

static int rkvdec2_free_session(struct mpp_session *session)
{
	if (session && session->priv) {
		kfree(session->priv);
		session->priv = NULL;
	}

	return 0;
}

static int rkvdec2_init_session(struct mpp_session *session)
{
	struct rkvdec2_session_priv *priv;

	if (!session) {
		mpp_err("session is null\n");
		return -EINVAL;
	}

	priv = kzalloc(sizeof(*priv), GFP_KERNEL);
	if (!priv)
		return -ENOMEM;
	session->priv = priv;

	return 0;
}

static int vdpu383_init(struct mpp_dev *mpp)
{
	static const char * const names[] = {
		"aclk_vcodec", "hclk_vcodec", "clk_core", "clk_cabac",
		"clk_hevc_cabac",
	};
	struct rkvdec2_dev *dec = to_rkvdec2_dev(mpp);
	int i;
	int ret;

	for (i = 0; i < ARRAY_SIZE(dec->clocks); i++)
		dec->clocks[i].id = names[i];
	ret = devm_clk_bulk_get(mpp->dev, ARRAY_SIZE(dec->clocks), dec->clocks);
	if (ret)
		return ret;
	dec->resets = devm_reset_control_array_get_exclusive(mpp->dev);
	if (IS_ERR(dec->resets))
		return PTR_ERR(dec->resets);
	mpp->grf_info = &mpp->srv->grf_infos[MPP_DRIVER_RKVDEC2];
	return reset_control_deassert(dec->resets);
}

static int vdpu383_exit(struct mpp_dev *mpp)
{
	struct rkvdec2_dev *dec = to_rkvdec2_dev(mpp);

	rk3576_workaround_exit(mpp);
	return reset_control_assert(dec->resets);
}

static int vdpu383_clk_on(struct mpp_dev *mpp)
{
	struct rkvdec2_dev *dec = to_rkvdec2_dev(mpp);
	int ret;

	ret = clk_bulk_prepare_enable(ARRAY_SIZE(dec->clocks), dec->clocks);
	if (ret)
		return ret;
	dec->clock_hz = clk_get_rate(dec->clocks[0].clk);
	if (dec->clock_hz < 1000000) {
		clk_bulk_disable_unprepare(ARRAY_SIZE(dec->clocks), dec->clocks);
		return -EINVAL;
	}
	return 0;
}

static int vdpu383_clk_off(struct mpp_dev *mpp)
{
	struct rkvdec2_dev *dec = to_rkvdec2_dev(mpp);

	clk_bulk_disable_unprepare(ARRAY_SIZE(dec->clocks), dec->clocks);
	return 0;
}

static struct mpp_hw_ops vdpu383_hw_ops = {
	.init = vdpu383_init,
	.exit = vdpu383_exit,
	.clk_on = vdpu383_clk_on,
	.clk_off = vdpu383_clk_off,
	.reset = vdpu383_reset,
};

static struct mpp_dev_ops vdpu383_dev_ops = {
	.alloc_task = rkvdec2_alloc_task,
	.run = rkvdec_vdpu38x_run,
	.irq = rkvdec_vdpu38x_irq,
	.isr = rkvdec_vdpu38x_isr,
	.finish = rkvdec2_finish,
	.result = rkvdec2_result,
	.free_task = rkvdec2_free_task,
	.ioctl = rkvdec2_control,
	.init_session = rkvdec2_init_session,
	.free_session = rkvdec2_free_session,
};

static const struct mpp_dev_var vdpu383_data = {
	.device_type = MPP_DEVICE_RKVDEC,
	.hw_info = &rkvdec_vdpu383_hw_info,
	.trans_info = rkvdec_vdpu383_trans,
	.hw_ops = &vdpu383_hw_ops,
	.dev_ops = &vdpu383_dev_ops,
};

static int vdpu383_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct rkvdec2_dev *dec;
	struct resource *regs;
	int ret;

	dec = devm_kzalloc(dev, sizeof(*dec), GFP_KERNEL);
	if (!dec)
		return -ENOMEM;
	ret = dma_set_mask_and_coherent(dev, DMA_BIT_MASK(32));
	if (ret)
		return ret;
	dec->mpp.var = &vdpu383_data;
	dec->link.info = &vdpu383_link_info;
	dec->link.reg_base = devm_platform_ioremap_resource_byname(pdev, "link");
	if (IS_ERR(dec->link.reg_base))
		return PTR_ERR(dec->link.reg_base);
	dec->link_dec = &dec->link;
	dec->need_workaround = true;
	regs = platform_get_resource_byname(pdev, IORESOURCE_MEM, "regs");
	if (!regs || resource_size(regs) < RKVDEC_REG_NUM * sizeof(u32))
		return -EINVAL;
	if (!devm_request_mem_region(dev, regs->start, resource_size(regs),
				     dev_name(dev)))
		return -EBUSY;
	platform_set_drvdata(pdev, &dec->mpp);
	ret = mpp_dev_probe(&dec->mpp, pdev);
	if (ret)
		return dev_err_probe(dev, ret, "MPP decoder initialization failed\n");
	if (!rkvdec_vdpu383_hw_info.hw_id ||
	    rkvdec_vdpu383_hw_info.hw_id == U32_MAX) {
		ret = -ENODEV;
		goto remove_device;
	}
	if (dec->mpp.task_capacity != 1) {
		ret = -EINVAL;
		goto remove_device;
	}
	ret = rk3576_workaround_init(&dec->mpp);
	if (ret)
		goto remove_device;
	ret = devm_request_irq(dev, dec->mpp.irq, mpp_dev_irq, IRQF_SHARED,
			       dev_name(dev), &dec->mpp);
	if (ret)
		goto remove_device;
	dec->mpp.is_irq_startup = true;
	dec->mpp.session_max_buffers = 64;
	mpp_dev_register_srv(&dec->mpp, dec->mpp.srv);
	dev_info(dev, "RK3576 VDPU383 MPP decoder ready, hardware ID %#x\n",
		 rkvdec_vdpu383_hw_info.hw_id);
	return 0;

remove_device:
	mpp_dev_remove(&dec->mpp);
	return ret;
}

static void vdpu383_remove(struct platform_device *pdev)
{
	struct mpp_dev *mpp = platform_get_drvdata(pdev);

	clear_bit(MPP_DEVICE_RKVDEC, &mpp->srv->hw_support);
	mpp->srv->sub_devices[MPP_DEVICE_RKVDEC] = NULL;
	mpp_dev_remove(mpp);
}

static int __maybe_unused vdpu383_runtime_resume(struct device *dev)
{
	struct mpp_dev *mpp = dev_get_drvdata(dev);
	struct rkvdec2_dev *dec = to_rkvdec2_dev(mpp);

	/* The workaround state is lost when the VDEC domain is powered off. */
	dec->need_workaround = true;
	return 0;
}

static const struct dev_pm_ops vdpu383_pm_ops = {
	SET_RUNTIME_PM_OPS(NULL, vdpu383_runtime_resume, NULL)
	SET_SYSTEM_SLEEP_PM_OPS(pm_runtime_force_suspend, pm_runtime_force_resume)
};

static const struct of_device_id vdpu383_dt_ids[] = {
	{ .compatible = "rockchip,rkv-decoder-rk3576" },
	{ }
};
MODULE_DEVICE_TABLE(of, vdpu383_dt_ids);

struct platform_driver rockchip_rkvdec2_driver = {
	.probe = vdpu383_probe,
	.remove = vdpu383_remove,
	.shutdown = mpp_dev_shutdown,
	.driver = {
		.name = "mpp_rkvdec2",
		.suppress_bind_attrs = true,
		.of_match_table = vdpu383_dt_ids,
		.pm = &vdpu383_pm_ops,
	},
};
