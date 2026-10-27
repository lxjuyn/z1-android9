# ramdisk_v10_wifi — Z1 WiFi 用户空间链路修复（2026-10-27）

接 `scripts/ramdisk_v8/`。本轮只换 ramdisk 侧 3 个文件 + 加 2 个脚本，**内核段（zImage+DTB）除 dtb 里 2 字节 initrd-end 外逐字节不变**。

## 改了什么（对应 z1_wifi_plan.md 里编号的 bug）

| 文件 | 修的问题 |
|---|---|
| `init.z1.network.rc` | **B1** 服务 exec 路径 `/system/bin/z1_wpa_start.sh` → `/z1_wpa_start.sh`（v8 那个路径在 system.img 里不存在，supplicant 永远起不来）；**B2** `seclabel u:r:su:s0` → `u:r:init:s0`（ramdisk sepolicy 里没有 `su` 域，permissive 也起不来）；新增 post-fs 同步预置步骤 |
| `z1_wifi_pre.sh`（新） | **B3** 驱动用绝对路径 `/etc/firmware/nvram/WIFI` 读校准（rootfs 的 /etc），v8 却拷到 `/system/etc/firmware/nvram/WIFI` → 永远读不到；顺带把 `regulatory.db`（**B5**）和 `WIFI_RAM_CODE_MT6582`/`WIFI_RAM_CODE` 别名放进 `/lib/firmware` 兜底 |
| `z1_wifi_probe.sh` | **B4** v8 用「wlan0 存在」当就绪，但 netdev 在 insmod 阶段就已注册（固件还没下载）；改为先 `ifconfig up` 再用「扫描有输出」判就绪，失败时把决定性 dmesg 行回灌 kmsg |
| `z1_wpa_start.sh` | 修路径自适配；**B10** 改写 `/etc/wifi` 带来的 `ctrl_interface=wlan0`（会让 supplicant 建一个叫 `wlan0` 的目录，wpa_cli 永远连不上）→ `/data/misc/wifi/sockets` |
| `z1_wifi_connect.sh`（新） | 手工连 AP + 静态 IP + ping（镜像里没有 dhcpcd/dhcptool，A9 的 DHCP 在框架里） |

## 复现打包（从任意基线 boot.img 出发）

```sh
REPO=/home/lxj/claude/6572
W=$REPO/z1a9_gpt/scripts/ramdisk_v10_wifi
T=/tmp/z1_wifi_build && rm -rf $T && mkdir -p $T

# 1) 拆出内核段（zImage+appended DTB）与 ramdisk
python3 $REPO/z1a9_gpt/scripts/z1_split_boot_img.py <base_boot.img> $T
#    → $T/zImage $T/dtb $T/ramdisk.rootfs，并打印「initrd-end 必须是 0x…」

# 2) 换/加 ramdisk 记录（未触及的记录逐字节保持原样）
python3 $REPO/z1a9_gpt/scripts/z1_ramdisk_edit.py $T/ramdisk.rootfs $T/ramdisk.new \
  --replace init.z1.network.rc=$W/init.z1.network.rc \
  --replace z1_wifi_probe.sh=$W/z1_wifi_probe.sh \
  --replace z1_wpa_start.sh=$W/z1_wpa_start.sh \
  --add     z1_wifi_pre.sh=$W/z1_wifi_pre.sh \
  --add     z1_wifi_connect.sh=$W/z1_wifi_connect.sh \
  --mode z1_wifi_pre.sh=0755 --mode z1_wifi_connect.sh=0755
#    输出行会给出新的 initrd-end

# 3) 同步 dtb 的 linux,initrd-end（否则打包脚本会拒绝）
cp $T/dtb $T/dtb.patched
fdtput -t x $T/dtb.patched /chosen linux,initrd-end <上一步的 initrd-end 十六进制>

# 4) 重新打包（脚本会校验 magic/地址/cmdline/kernel=zImage+dtb）
python3 $REPO/z1a9_gpt/scripts/z1_make_android9_bootimg.py \
  $T/ramdisk.new out/<your>/boot.img --zimage $T/zImage --dtb $T/dtb.patched

# 5) 逐字节核对（内核段应只差 initrd-end 那 2 字节；ramdisk 只差本目录这几个记录）
python3 $REPO/z1a9_gpt/scripts/z1_verify_boot_payload.py <base_boot.img> out/<your>/boot.img
```

## 合并到别的 agent 的镜像

本目录的 5 个文件是**纯 ramdisk 记录级改动**，可以 rebase 到任何一个新的基线 boot.img：
用上面的第 2 步对**最新基线**再跑一遍即可（`--replace` 会保留别家新增的记录）。
不要直接把 `v10_wifi` 的 boot.img 当最终刷写件——那是基于 `out/z1_v8/boot.img` 的。

## 上板验证顺序

见 `z1_wifi_plan.md` §8（T0~T5）。最短闭环：
```sh
dmesg | grep -aiE "MTK-WIFI|initWlan|HifAhb|nvram|Open FW image|No Firmware"
echo 1 > /dev/wmtWifi ; sleep 3 ; dmesg | tail -40
ifconfig wlan0 up ; iw dev wlan0 scan | grep SSID
sh /z1_wifi_connect.sh        # 需先写 /data/misc/wifi/z1_target.conf
```
