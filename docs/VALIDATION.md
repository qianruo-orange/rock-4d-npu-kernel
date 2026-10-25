# 实机验证记录

板卡：Radxa ROCK 4D（RK3576），Armbian trixie，内核 `7.3.0-npu`。
验证方式：部署后整机重启，检查开机自启全链路。

## 1. 引导

`boot.scr` 正常加载 `/boot/Image` → `vmlinuz-7.3.0-npu`（未压缩 Image），
`uInitrd` 正常，无 `Bad Linux ARM64 Image magic!`。

## 2. NPU

| 检查 | 结果 |
|---|---|
| `rknpu` 驱动绑定 | `rknpu iommu is enabled` |
| MMU 使能 | `kiln mmu enable_all st=0x19/0x19/0x19/0x19` |
| 设备节点 | `/dev/dri/renderD128`、`/dev/dri/renderD129` 均存在 |
| 模型加载 | `rknn_init` 加载 `yolo26.rknn` 成功 |
| 官方诊断 | `kiln-doctor`：`[WARN] 2 warning(s), 14 ok, 0 critical -- the NPU core looks healthy.` |

## 3. WiFi（aic8800）

| 检查 | 结果 |
|---|---|
| 模块 | `aic8800_fdrv`、`aic_load_fw`、`aic_btusb` 均加载 |
| 联网 | `wlan0` 自动获得 `192.168.1.5/24`（DHCP，netplan + systemd-networkd + wpa_supplicant） |
| 驱动错误 | dmesg 中 rwnx / aic8800 相关 error 计数为 0 |
| 日志量 | 开机 AICWFDBG 由 402 条降至 4 条（仅 LOGERROR 级） |

## 4. 未验证

- 端到端视频检测：板上 `/dev/video0` 是 rkvdec 解码器软链，非真实摄像头，需外接 USB 摄像头。
- P2P / offchannel 功能：cookie 全场景修复后编译与联网验证通过，但未做真实 P2P 对端测试。
