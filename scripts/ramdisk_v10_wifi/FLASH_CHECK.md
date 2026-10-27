# Z1 WiFi v10 集成件 (2026-10-27 21:45)

## 产物（**尚未刷写**）
- `out/z1_wifi_v10/boot.img` 6,207,488 B（≤6MB ✅, ANDROID! ✅, dt_size=0, page 2048）
  sha256 `0dcbf89a12e7dd3b076478731daf475b95f060ccd2aebc96560a97185d43ad90`
- 基线 = `out/z1_v8/boot.img`（6205440 B, sha256 `202d67ec1653…`）
- 归档 `bridge_backups/products/products_wifi_v10_20261027_211853/`（boot.img + 两个 ramdisk + 打过 initrd-end 的 dtb + zImage + 方案文档）

> ⚠️ **命名冲突提醒**：`out/z1_v10/boot.img`（6205440 B, 21:27:52）是**另一个 agent** 同时刻产出的，不是本件。本件在 `out/z1_wifi_v10/`。最终刷写件应把本目录的 5 个 ramdisk 记录 rebase 到最新基线后再打（配方见 `z1a9_gpt/scripts/ramdisk_v10_wifi/README.md`）。

## 与基线的差异（逐字节核对过）
- 内核段（zImage+appended DTB）：**只差 1 处 2 字节** —— offset 0x4072ba 的 `163e→25fa`，即 dtb `/chosen/linux,initrd-end` 0x842e163e→0x842e25fa（ramdisk 变大 4028 B）。
- ramdisk：74 条记录（基线 72），**69 条逐字节相同**；改动 =
  - `init.z1.network.rc` 3858→4914（B1 路径 / B2 seclabel / 新增预置 exec）
  - `z1_wifi_probe.sh` 2086→3144（B4 就绪判据 + ifconfig up + 失败回灌 dmesg）
  - `z1_wpa_start.sh` 1856→2828（B10 ctrl_interface 改写）
  - 新增 `z1_wifi_pre.sh`（B3 NVRAM 路径 / B5 regulatory.db / 固件名别名）
  - 新增 `z1_wifi_connect.sh`（手工连接 + L3 验证）
- cmdline / 加载地址 / page size / dt_size 全部与基线一致（未动）。

## 上板验证（按顺序，任何一步失败就停）
```sh
# T0 模块链
dmesg | grep -aiE "MTK-WIFI|WMT-CONSYS|initWlan|HifAhb|cfg80211|regulatory"
#   期望：WIFI_init major 155 / initWlan / createWirelessDevice success / HifAhbPltmProbe
#   期望新增：[z1wifipre] NVRAM staged at /etc/firmware/nvram/WIFI (512 bytes)
#             [z1wifipre] regulatory.db staged at /lib/firmware/regulatory.db
ls -l /etc/firmware/nvram/WIFI /lib/firmware/     # 512B / regulatory.db(+别名)

# T1 function-on（固件下载）
echo 1 > /dev/wmtWifi ; sleep 5 ; dmesg | tail -40
#   期望：(INIT INFO)Open FW image done, N bytes
#   失败关键字：No Firmware found! (固件名) / nvram_read failed (B3 未生效) / WMT turn on WIFI fail (patch/供电)

# T2 RF
ifconfig wlan0 up ; iw dev wlan0 scan | grep -E "SSID|signal" | head
#   ← 这是「链路通」的唯一可信判据（v8 的 z1_wifi_probe 已改成自动做同样的事）

# T3 supplicant
getprop init.svc.z1_wpa_supplicant          # 期望 running（v8 里此步必挂）
ls -l /data/misc/wifi/sockets/              # 期望有 wlan0 socket

# T4 连接（先写凭据）
cat > /data/misc/wifi/z1_target.conf <<'EOF'
SSID=你的AP
PSK=密码
IP=192.168.1.50/24
GW=192.168.1.1
DNS=192.168.1.1
EOF
chmod 600 /data/misc/wifi/z1_target.conf ; sh /z1_wifi_connect.sh
#   期望：wpa_state=COMPLETED → ifconfig 有 IPv4 → ping 通网关

# T5 框架（当前**预期失败**，仅取证）
logcat -d | grep -iE "wifi|wificond|IWifi" ; dumpsys wifi | head
#   期望：无 android.hardware.wifi@1.0 服务 → 框架 WiFi 不可用（A9 缺 wifi HAL，见方案 §2/§10）
```

## 关于框架 WiFi（重要）
`wlan.driver.status=ok` 对 Android 9 **无效**（那是 KitKat 时代属性）。设置里的 WiFi 开关要能用，必须补
`android.hardware.wifi@1.0-service` + `libwifi-hal.so` + HIDL 版 wpa_supplicant（Pie 已删 legacy 回退），
MT6620-gen2 无 A9 时代现成 HAL。本轮目标是「驱动+supplicant 链路验证」，框架 WiFi 见方案 §10 路线 B/C。

## 回滚
刷回 `out/z1_v8/boot.img`（sha256 `202d67ec…`）或原厂 `~/claude/6572/1/boot.img`。
