# aic8800 USB WiFi 驱动：适配 mainline 7.3

板载 WiFi 为 AIC8800（USB，VID 0xA69C），Armbian 以 DKMS 包 `aic8800-usb/5.0-kiln`
分发源码（`/usr/src/aic8800-usb-5.0-kiln`）。mainline 7.3 的 cfg80211 接口变更 +
GCC 14 的严格检查导致其无法编译，以下是已实测通过的修改。

## 用法

把本目录 `files/` 下的文件覆盖到 DKMS 源对应路径，然后：

```bash
sudo dkms build  aic8800-usb/5.0-kiln -k 7.3.0-npu --force
sudo dkms install aic8800-usb/5.0-kiln -k 7.3.0-npu --force
```

> DKMS 判定“已构建过”会直接跳过，改源码后必须加 `--force`。

## 修改清单

### 1) `rwnx_msg_tx.c`
- 首行补 `#include <linux/string.h>`
- `strncpy` → `strscpy`
  原因：GCC 14 把 `implicit-function-declaration` 升级为 error，且 7.3 内核头不再声明 `strncpy`。

### 2) `rwnx_main.c` / `rwnx_main.h`
- `cfg80211_probe_status()` 调用插入新参数 `link_id`（传 0）
- 删除 `cfg80211_ops` 中 `.probe_client` 初始化行（7.3 已移除该成员）
- `rwnx_cfg80211_probe_client()` 原型与函数体同步改为传值（`u64 *cookie` → `u64 cookie`），
  否则编译报 `conflicting types` 与 `invalid type argument of unary '*'`
- 默认日志级别由 `LOGERROR|LOGINFO|LOGFW` 降为 `LOGERROR|LOGFW`（见第 4 点）

### 3) cookie 全场景修复（7.3 的 cookie 语义由“出参指针”改为“传值”）
新内核由 cfg80211 生成 cookie 并传入驱动，驱动必须在事件中原样回传，否则用户空间
（wpa_supplicant / hostapd）无法匹配事件，P2P、offchannel 场景失效。原驱动用的是
内部计数器 / skb 地址，与 cfg80211 的 cookie 无关。

| 链路 | 文件 | 修改 |
|---|---|---|
| RoC | `rwnx_defs.h` | `struct rwnx_roc_elem` 增加 `u64 cookie` 字段 |
| RoC | `rwnx_main.c` | `rwnx_cfg80211_remain_on_channel_()` 改传值并存 `roc_elem->cookie`；wrapper 与 `mgmt_tx` 内部 ROC 分支透传 cfg80211 的 cookie（原先丢弃为 `&(u64){0}`） |
| RoC 事件 | `rwnx_msg_rx.c` | `cfg80211_ready_on_channel()` / `cfg80211_remain_on_channel_expired()` 共 6 处改回传 `roc_elem->cookie` |
| mgmt_tx | `rwnx_tx.h` / `rwnx_tx.c` | `rwnx_start_mgmt_xmit()` 改传值；`struct rwnx_sw_txhdr` 增加 `u64 cookie`；`cfg80211_mgmt_tx_status()` 回传 `sw_txhdr->cookie`（原为 `(unsigned long)skb`） |
| TDLS | `rwnx_tdls.c` | 驱动内部路径无 cfg80211 cookie，用源 skb 地址自生成 `(u64)(unsigned long)skb` |

> 注意：`rwnx_main.c` 的 `add_key` 回调里也有 `void *cookie`，那是密钥 API 的另一套
> 机制，**不要**改动。

### 4) 日志降噪
- `rwnx_main.c`：`aicwf_dbg_level = LOGERROR|LOGFW`
- `aic_load_fw/aic_bluetooth_main.c`：`aicwf_dbg_level = LOGERROR`
  两个模块各有独立的一份级别变量，都要改。开机 AICWFDBG 日志从 402 条降到 4 条。
  运行时临时调整：`echo 513 | sudo tee /sys/module/aic8800_fdrv/parameters/aicwf_dbg_level`

## 已验证

重启后 WiFi 自动连接（netplan + systemd-networkd + wpa_supplicant），
dmesg 无驱动错误，NPU 同时正常。
