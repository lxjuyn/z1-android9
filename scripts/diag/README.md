# Z1 上板一次性取证脚本套件 (z1_diag_*)

Z1 (MT6572 / Android 9 / arm32) **刷机后立刻取证**用的一套 shell 脚本。目标：一次收集就把
所有子系统的"证据"落盘成可传回的 tar 包，避免"刷完发现问题、又要重刷才能再看一眼"。

全部脚本：

- 只写 `/data/local/z1diag/`，**绝不写 /system、不碰 boot.img**
- 纯 POSIX sh（Android 9 的 `/system/bin/sh` = mksh + toybox），不用 bash 数组/`[[ ]]`/`local`
- 每条外部命令都带 `2>/dev/null || true` 兜底，缺工具不会中断
- 缺哪个工具就记一条 `NOTE`，不影响其余小节

---

## 1. 文件清单

| 脚本 | 作用 | 触发动作 |
|---|---|---|
| `z1_diag_all.sh` | 一键全量取证，17 个分节 + `facts.txt` + tar 包 | 无（严格只读） |
| `z1_diag_wifi.sh` | WiFi/CONNSYS 深挖 | `trigger` 时 `/dev/wmtWifi` func-on + `iw scan`；`insmod` 可加载模块栈 |
| `z1_diag_bt.sh` | 蓝牙深挖 | `trigger` 时开 BT，观察栈，结束**还原**原状态 |
| `z1_diag_gps.sh` | GNSS 深挖 | `trigger` 时开定位，盯 60s，结束**还原** location_mode |
| `z1_diag_audio.sh` | 音频深挖 | `trigger` 时 tinyplay 噪声 WAV / tinycap / 音量键出声（三路按可用性依次试） |
| `z1_diag_keys.sh` | 按键逐键抓事件（引导式） | `all` 逐个键提示你按，每键一个窗口 |
| `z1_diag_onboot.sh` | 开机 90s 后自动跑 `z1_diag_all.sh`（无人值守） | 无 |
| `z1_diag_summarize.py` | **主机侧**：读包出判定表，不进镜像 | — |

行数（`wc -l`，设备侧 7 个脚本 1741 行 + 主机侧判定器 488 行）：

```
z1_diag_all.sh        616
z1_diag_keys.sh       274
z1_diag_wifi.sh       223
z1_diag_audio.sh      187
z1_diag_bt.sh         156
z1_diag_gps.sh        152
z1_diag_onboot.sh     133
z1_diag_summarize.py  488   (主机侧，不进镜像)
```

---

## 2. 部署

### 2.1 手动用（最快，不刷机）

```sh
# 主机侧
adb push scripts/diag/z1_diag_all.sh /data/local/z1diag/
adb shell chmod 755 /data/local/z1diag/z1_diag_all.sh
```

没有 adb 时走 C3 桥 + ACM：串口 console 上直接

```sh
mkdir -p /data/local/z1diag
# 然后在 console 里粘贴脚本内容，或 ttyGS0 传过去
```

### 2.2 进 system.img / ramdisk（正式版，主线程部署）

本项目已有的惯例（见 `device.mk` 的 `PRODUCT_COPY_FILES`）：给 rc 执行的脚本放**ramdisk 根**，
开机即可用、`u:r:init:s0` 直接 exec，路径是 `/z1_xxx.sh` 而不是 `/system/bin/z1_xxx.sh`
（对照 `stock/z1_fbcon_takeover.sh:root/z1_fbcon_takeover.sh`）。

两种放法按需选：

**A. ramdisk 根（推荐，rc 里 `/z1_diag_onboot.sh`）** — 加进 `device.mk` 两个变体各自的
`PRODUCT_COPY_FILES` 块（stock 变体和 mainline 变体都有，别只加一边）：

```make
    $(DEVICE_PATH)/scripts/diag/z1_diag_all.sh:root/z1_diag_all.sh \
    $(DEVICE_PATH)/scripts/diag/z1_diag_wifi.sh:root/z1_diag_wifi.sh \
    $(DEVICE_PATH)/scripts/diag/z1_diag_bt.sh:root/z1_diag_bt.sh \
    $(DEVICE_PATH)/scripts/diag/z1_diag_gps.sh:root/z1_diag_gps.sh \
    $(DEVICE_PATH)/scripts/diag/z1_diag_audio.sh:root/z1_diag_audio.sh \
    $(DEVICE_PATH)/scripts/diag/z1_diag_keys.sh:root/z1_diag_keys.sh \
    $(DEVICE_PATH)/scripts/diag/z1_diag_onboot.sh:root/z1_diag_onboot.sh
```

7 个脚本合计约 85 KB，对 6 MB boot 分区可以接受；但**如果这轮 ramdisk 已经很紧，就只放
`z1_diag_onboot.sh` + `z1_diag_all.sh`（约 33 KB）**，其余深挖脚本用 `adb push` 临时丢到
`/data/local/z1diag/` 即可（`z1_diag_onboot.sh` 也会去那儿找 collector）。

**B. /system/bin（持久可用）** — 改成 `:$(TARGET_COPY_OUT_SYSTEM)/bin/z1_diag_all.sh`
等对应目标；注意 system 是只读挂载，权限由 build 保证 755。

> `z1_diag_onboot.sh` 会自动在"自己所在目录 / `/` / `/system/bin` / `/system/etc` /
> `/data/local/z1diag` / `/cache/z1diag`"里找 `z1_diag_all.sh`，放哪都能跑。

> 输出目录 `/data/local/z1diag` 不是新造的：`stock/z1_logpersist.sh` 已经在往那儿写
> `beats.txt`/`dmesg.txt`/`snap.txt`，所以 init 域写这个目录在本机**已被证明不会被 avc 挡**。
> 采集器不会覆盖这些文件，反而会把它们的内容（tail）作为 `17_logpersist_history.txt`
> 纳入包里——那是"我们这次采集之前"唯一的历史记录。`/data` 不可用时自动退回 `/cache/z1diag`
> （与 logpersist 的兜底一致）。

### 2.3 开机自动取证（无人值守）

`rootdir/init.z1.rc` / ramdisk rc 里加：

```
service z1_diag_onboot /system/bin/sh /z1_diag_onboot.sh
    class late_start
    user root
    group root system
    oneshot
    disabled

on property:sys.boot_completed=1
    start z1_diag_onboot
```

（脚本在 `/system/bin` 就把路径改成 `/system/bin/z1_diag_onboot.sh`。）

关掉：`touch /data/local/z1diag/onboot.disabled`
改延迟：`setprop` 不管用，直接改脚本里的 `DELAY` 或用 `Z1DIAG_ONBOOT_DELAY=180`
保留份数：默认只留最新 6 份 `collect_*`，防止 /data 塞满。

> **SELinux 提醒**：Enforcing 下 `init` 域写 `/data/local/z1diag/` 可能被 avc 拒绝
> （症状：`collect_*` 目录建不出来，`15_selinux.txt`/dmesg 里能看到 avc）。
> 两条路：给该 service 加 `seclabel u:r:su:s0`，或补一条 sepolicy 允许 init 写
> `shell_data_file`。刷之前建议先用 `sh /data/local/z1diag/z1_diag_all.sh` 手动跑一遍
> 确认没被挡。

---

## 3. 取回 tar

### 3.1 adb

```sh
adb pull /data/local/z1diag/collect_20261027_120000.tar.gz .
tar -xzf collect_20261027_120000.tar.gz
```

### 3.2 只有串口 / ACM（无 adb）

先把体积最小的 `facts.txt` 打出来（1KB 左右，一眼能看出各子系统生死）：

```sh
cat /data/local/z1diag/collect_<ts>/facts.txt
```

要全量 tar 就 base64 过串口（921600 下 ~500KB 包 ≈ 十秒）：

```sh
# 设备侧
base64 /data/local/z1diag/collect_<ts>.tar.gz
# 主机侧：把 serial log 里 base64 段落存成 b64.txt
base64 -d b64.txt > collect_<ts>.tar.gz && tar -xzf collect_<ts>.tar.gz
```

设备上没有 `base64` 时用 `toybox base64`。日志通道本身（`z1_bootdiag` 的 ACM relay）
也能看到 `[z1diag]` 标记，能确认采集开始/结束。

### 3.3 只挑一节看

```sh
cd /data/local/z1diag/collect_<ts>
cat facts.txt            # 先看这个
sed -n '1,80p' 06_wireless.txt
```

### 3.4 主机侧一键判定（推荐）

`z1_diag_summarize.py` 把下面第 5 节的判定表变成可执行规则，直接把包读成结论：

```sh
./z1_diag_summarize.py collect_20261027_120000.tar.gz      # 直接读 tar.gz
./z1_diag_summarize.py /path/to/collect_20261027_120000/   # 或解开的目录
./z1_diag_summarize.py bundle.tar.gz -v                    # 附带打印 facts 全量
./z1_diag_summarize.py bundle.tar.gz --json > verdict.json # 机器可读
```

输出示例：

```
 Z1 diag verdict    FAIL=2 WARN=1 OK=7 INFO=2
FAIL   WiFi      func-on 写入了但 wlan0 一直没出现
                  -> dmesg | grep -iE 'wlan|hif|sdio'；查固件目录与 wlan_mt6620 加载
...
 P0 先解决（按顺序）:
  1. [Kernel/modules] 模块 vermagic 不匹配（insmod 失败）
```

也可以直接指向单子系统深挖目录（`wifi_*`/`bt_*`/…）：没覆盖到的子系统会报
`INFO 本包未覆盖`，不会假装成 FAIL。

退出码：`0` 无 FAIL、`1` 有 FAIL、`2` 包打不开。纯 stdlib，无第三方依赖。

---

## 4. `z1_diag_all.sh` 分节说明

| 文件 | 内容 | 怎么读 |
|---|---|---|
| `00_index.txt` | 每节行数/字节数 | 先扫大小，异常小的节=该子系统基本没起来 |
| `01_props.txt` | `getprop` 全量 + egl/lmk/init.svc 过滤 | init service 是否 running；`ro.lmk.*` 是否生效 |
| `02_kernel_identity.txt` | `/proc/version`、`/proc/cmdline`、fingerprint | 内核版本、cmdline 是否含 `console=ttyGS0`/`earlycon` |
| `03_dmesg.txt` | dmesg 全量（上限 4 万行） | 一切内核侧问题的源头 |
| `04_modules.txt` | `/proc/modules`、模块目录、每个 .ko 的 `vermagic` | 模块没加载/vermagic 不匹配一眼看出 |
| `05_devices.txt` | `/proc/devices`、`/dev/stp*` / `wmtWifi` / `snd` / `dri` 节点 | 设备节点有没有被驱动建出来 |
| `06_wireless.txt` | netdev、iw、CONNSYS 节点、固件目录、wmt_dbg、dmesg/logcat 过滤 | WiFi 卡在哪一环 |
| `07_bluetooth.txt` | `/dev/stpbt`、模块、hciconfig、libbluetoothdrv、dumpsys | 同上 |
| `08_gps.txt` | `/dev/stpgps`、mnld、GPS NVRAM 目录、HAL、location 设置 | 同上 |
| `09_audio.txt` | asound、`/dev/snd`、tinymix、HAL、audio_flinger/policy | 同上 |
| `10_display_gpu.txt` | fb0、DRI、EGL props、SurfaceFlinger/HWC | 同上 |
| `11_input.txt` | `/proc/bus/input/devices`、`getevent -lp`、keylayout 全文 | 哪个 input 设备在、有哪些键 |
| `12_battery.txt` | 每个 power_supply 的 uevent、dumpsys battery | 电量/电压有没有数 |
| `13_memory_perf.txt` | free、meminfo、zram、lmkd 参数、cpuinfo/meminfo dumpsys | 设置闪退/lmkd 杀前台看这里 |
| `14_crash_logcat.txt` | tombstones/anr/dropbox 列表、`-b crash`、`-b all` | 谁崩了、为什么 |
| `15_selinux.txt` | `getenforce`、avc 去重计数 top40 | 权限拒绝是否卡住某个 HAL |
| `16_system_snapshot.txt` | ps、mount、df、interrupts、partitions | 兜底全景 |
| `17_logpersist_history.txt` | `z1_logpersist.sh` 早先写的 `beats.txt`/`snap.txt`/`dmesg.txt` 的尾部 | **本次采集之前**发生了什么（冻结、被杀、重启） |
| `facts.txt` | `KEY=VALUE` 机器可读判定表 | **先看它**，再看分节 |

`facts.txt` 关键键：`DEV_STPBT/DEV_STPGPS/DEV_WMTWIFI/DEV_TTYGS0`、`WLAN0_EXISTS`、
`BT_STPBT`、`GPS_*`（含 `GPS_MNLD_SVC`、`GPS_CHIP_CFG`）、`AUDIO_CARDS/AUDIO_SND_NODES/
AUDIO_HAL_LIB/AUDIO_LOADER`、`FB0_NAME/DRI_NODES`、`INPUT_KPD/INPUT_TPD/INPUT_CST836`、
`BATTERY_CAPACITY`、`PSI_PRESENT`、`SELINUX/AVC_COUNT`、`LOGPERSIST_*`，
以及深挖包才会有的 `PRE_*` / `TRIGGER_*`（`TRIGGER_RESULT`、`TRIGGER_SCAN_ATTEMPTS`、
`TRIGGER_SCAN_SSIDS`、`TRIGGER_WLAN0_CARRIER`、`TRIGGER_WLAN0_UP_PROP`…）。

---

## 5. 判定表（现象 → 结论 → 下一步）

### 5.1 WiFi（MT6620 / wlan_gen2）

| 现象 | 结论 | 下一步 |
|---|---|---|
| `DEV_WMTWIFI=no`，`06_wireless` 里 `/dev/stp*` 全空 | WMT 平台驱动没建出功能开关节点，WiFi 无从谈起 | 看 `04_modules.txt` 有没有 `mtk_stp_wmt`；`init.z1.rc` 的 `mknod /dev/wmtWifi c 153 0` 兜底是否执行；`dmesg | grep wmt` |
| `DEV_WMTWIFI=yes` 但 `WLAN0_EXISTS=no`，`trigger` 后仍 no | func-on 写了但 `wlan_mt6620` 没起来 | 看 dmesg `wlan`/`hif`/`sdio`，固件目录 |
| **`TRIGGER_RESULT=scan_empty_no_carrier`** | wlan0 在但 carrier 一直没 up | 射频没起来：固件下载/NVRAM/校准；`dmesg | grep -i wmt` |
| **`TRIGGER_RESULT=scan_empty_but_carrier_up`** | 网卡 up 了、3 次 scan 都空 | 典型是固件/NVRAM(`wifi-nvram-WIFI.bin`)/校准没到位；注意 **"wlan0 出现" 本身不是就绪**（v10 审计结论：netdev 在 insmod 时就出现，早于固件下完） |
| `TRIGGER_RESULT=scan_ok` | 射频+固件+校准链路通 | 剩下是连接/认证 |
| dmesg 有 `request_firmware ... failed` | 固件 blob 缺或路径不对 | `06_wireless` 的 `firmware trees`；核对文件名大小写 |
| `insmod` 报 `Invalid module format` / dmesg `unknown symbol` | vermagic 或符号不匹配 | 对比 `facts` 里 `MOD_VERMAGIC_KERNEL` 与 `MOD_VERMAGIC_RUNNING` |
| `PRE_WLAN0_UP_PROP` 有值（`sys.wlan0.up`） | v10 探针认为 WLAN 真正可用 | 比"wlan0 存在"更可信的就绪信号 |

### 5.2 蓝牙

| 现象 | 结论 | 下一步 |
|---|---|---|
| `BT_STPBT=no` | 没有 BT transport，用户态栈必然起不来（根因在内核） | `grep stp_bt /proc/modules`；`dmesg | grep -i stpbt` |
| `BT_STPBT=yes` 但 `dumpsys bluetooth_manager` 显示未启用、无 `com.android.bluetooth` | 用户态：libbluetoothdrv / libstdc++ / HIDL 缺 | `07_bluetooth` 的 `vendor/HIDL libraries`；logcat `dlopen failed` |
| `hciconfig -a` 无 `hci0` | BTIF 没建 controller（patch 没下发/固件缺） | dmesg `btif`，`/system/etc/firmware` 里 BT patch |
| logcat 报 BD_ADDR 全 0 / vendor 拿不到地址 | NVRAM 地址读不到 | `ro.bt.vendor_owns_bdaddr` 是否设了 |
| 栈起了但连不上任何设备 | 射频/天线/校准 | 需要真机对测，脚本只能证明链路通 |

### 5.3 GPS / GNSS

| 现象 | 结论 | 下一步 |
|---|---|---|
| `GPS_MNLD_BINARY=0` 且 `GPS_HAL_BINARIES=0`、`GPS_HAL_LIB=0` | **这一版没有 GPS 用户态**（mnld/HAL/libgps 都没有） | 先把 HAL+mnld+libnvram 部署进去再谈，否则系列现象都无意义 |
| `PRE_MNLD_SVC` 无值 / `TRIGGER_RESULT=mnld_absent` | mnld 起不来 | 看 `init.svc.z1_mnld`；确认 `scripts/ramdisk_v9/z1_gps_prep.sh` 跑过（它建目录、种 `GPS_CHIP.cfg`、`start z1_mnld`） |
| `DEV_STPGPS=no`（`TRIGGER_RESULT=stpgps_missing`） | 内核 `mtk_stp_gps` 没起来 | `04_modules` / dmesg `stp_gps`；gps_prep 等 80s 仍无节点就不会 start mnld |
| `GPS_CHIP_CFG=0` | `/data/misc/GPS_CHIP.cfg` 没种上 | gps_prep 的 mkdir/chown 是否被 avc 挡（`15_selinux.txt`） |
| 节点都在、mnld 在跑、无卫星 | NVRAM/XTRA 网络/冷启动时间 | `08_gps` 的 NVRAM 目录 + logcat `mnld`；户外冷启动要几分钟 |

### 5.4 音频

| 现象 | 结论 | 下一步 |
|---|---|---|
| `AUDIO_CARDS=0`（`/proc/asound/cards` 是 "no soundcards"） | ASoC 机器驱动没注册（gabin8/MT6572 未 bind） | 先确认 `AUDIO_LOADER=1`（`/system/lib/modules/load_audio.sh` 存在并跑过）；再看 dmesg `snd`/`asoc`/`dai` |
| cards>0 但 `AUDIO_SND_NODES=0` | 卡在但 PCM 没注册 | `/proc/asound/pcm`、dts 里 DAI link |
| `tinymix` 有控件但 `tinyplay` 完成却无声 | codec 供电/路由/外部 PA、MT6323 电源 | `tinymix` 里 DAC/PA 是否 unmute；PMIC regulator |
| `dumpsys media.audio_flinger` 无 output track / HAL load 失败 | 用户态 HAL 没加载 | `09_audio` 的 HAL 清单——注意 HAL 有两个名字：`audio.primary.mt6572.so`（legacy）和 `android.hardware.audio@2.0-impl.so`（HIDL），脚本两个都查 |
| dmesg 无任何 `snd`/`afe` 行 | 音频驱动压根没编进内核 | 先确认内核 config/模块 |

> 脚本能证明"内核 PCM 通不通"，**不能**证明喇叭出声——最后一步永远要人站在机器前面。

### 5.5 显示 / GPU

| 现象 | 结论 | 下一步 |
|---|---|---|
| `FB0_NAME=mtkfb`，`DRI_NODES=0` | 还走 vendor fbcon，lima 没起 | `grep lima /proc/modules`、dmesg `drm` |
| `DRI_NODES>0` 但 logcat EGL/gralloc 报错 | gralloc/composer 与内核 DRM 不匹配 | `10_display_gpu` 的 HWC/gralloc 清单 |
| SurfaceFlinger 反复重启 | 渲染链路炸 | `14_crash_logcat` + tombstones |

### 5.6 按键 / 触摸

| 现象 | 结论 | 下一步 |
|---|---|---|
| `INPUT_KPD=0` | keypad 驱动没注册 | dts keymap/行列、dmesg `kpd` |
| kpd 在但 `keys_*` 里某键**无 EV_KEY** | 该键没走 Linux input（可能走 MCU/并口屏） | 这是重要结论：需要另一条抓取通路 |
| 按每个键都有事件但 Android 无反应 | keylayout 映射不对 | `00_keylayouts.txt` 对照 `SUMMARY.txt` 的码值 |
| `INPUT_CST836=0` | 触摸驱动没注册 | I2C/EINT/RST、dmesg `cst836` |
| 触摸有点但无点击 | 坐标/上报时序 | `getevent -lt` 里 `ABS_MT_*` 是否有、`BTN_TOUCH` 是否 DOWN |

### 5.7 内存 / 崩溃（设置闪退）

| 现象 | 结论 | 下一步 |
|---|---|---|
| `PSI_PRESENT=no` | **正常**，PSI 要 4.20+，本机 3.4.67 | 忽略 |
| `14_crash` 里 `am_kill ... foreground` | lmkd 仍在杀前台 app | 对照 `PROP_RO_LMK`、`LMKD_MINREE`；调 `ro.lmk.*` |
| `TOMBSTONES_COUNT>0` | 有 native 崩溃 | 直接看 tombstone 栈 |
| `LOGCAT_LINES` 很小 | logd 掉数据或刚开机 | 抓早一点（onboot 脚本就是干这个） |

### 5.8 SELinux

| 现象 | 结论 |
|---|---|
| `AVC_COUNT` 几十条以上且带 `gps`/`bt`/`stpwmt` | 权限问题在挡 HAL，先加 sepolicy 再谈功能 |
| `SELINUX=Permissive` | 不会被挡，但要在同一版本上复测 Enforcing |

---

## 6. 刷机后的推荐顺序

```sh
sh /data/local/z1diag/z1_diag_all.sh afterflash     # 1) 全量（只读）
cat /data/local/z1diag/collect_*/facts.txt          # 2) 先看判定表
sh /data/local/z1diag/z1_diag_keys.sh all           # 3) 按键（要人在机器前）
sh /data/local/z1diag/z1_diag_wifi.sh trigger       # 4) WiFi func-on + scan
sh /data/local/z1diag/z1_diag_bt.sh trigger         # 5) BT 开关（会还原）
sh /data/local/z1diag/z1_diag_gps.sh trigger        # 6) 定位 60s（会还原）
sh /data/local/z1diag/z1_diag_audio.sh trigger      # 7) 音频三路（人听）
```

一键顺序跑完（没有人值守时第 3/7 步意义有限）：

```sh
for s in all wifi bt gps audio; do sh /data/local/z1diag/z1_diag_$s.sh trigger; done
```

```sh
sh /data/local/z1diag/z1_diag_keys.sh all     # 引导式，20 个窗口
sh /data/local/z1diag/z1_diag_keys.sh list    # 只看设备清单
sh /data/local/z1diag/z1_diag_keys.sh raw 30  # 自由抓 30s
sh /data/local/z1diag/z1_diag_keys.sh one 复读 10
KEYWIN=15 sh /data/local/z1diag/z1_diag_keys.sh all       # 每键 15s 窗口
Z1DIAG_DEVS="/dev/input/event2" sh /data/local/z1diag/z1_diag_keys.sh all  # 只抓一个设备
```

输出：每键一个 `key_NN_<名字>.txt`（`getevent -lt` 原始流）、`SUMMARY.txt`
（每键出现的 `KEY_*`/`BTN_*` 码值统计）、`MISSING_KEYS.txt`（完全没事件的键）、
`00_input_devices.txt` / `00_getevent_caps.txt` / `00_keylayouts.txt`。

---

## 7. 已知限制 / 坑

1. **dmesg 需要 root**。`dmesg_restrict=1` 时非 root 拿不到内核日志；`z1_diag_all.sh` 会先尝试
   一次 `su -c` 自升权，失败也会继续，但 `03_dmesg.txt` 会是空的（`facts` 里 `RUN_UID` 能看出来）。
2. **`/proc/pressure/*` 在本机不存在是正常的**（内核 3.4.67，PSI 要 4.20+）。
3. **`getevent` 的采样窗口用 `timeout`**；若这版 toybox 没有 `timeout`，脚本自动退化成
   后台跑 + `sleep N` + `kill`。
4. **并发保护**：`/data/local/z1diag/.running` 是锁。上一次异常退出会留锁，提示里给了
   `rm -rf` 的清理命令。
5. **状态改变只发生在 `trigger` 模式**：
   - WiFi：func-on 打开后**保持开启**直到重启（脚本会明确提示）；
   - BT / 定位：测试结束会还原原值；
   - `all` 模式全程只读，可随时跑。
6. **日志有行数上限**（dmesg 4 万行、logcat 6000 行），目的是防 /data 塞满和串口传太慢。
   要更全就单独 `dmesg > /data/local/z1diag/full_dmesg.txt`。
7. **不改 system.img / boot.img**，脚本只落 `/data/local/z1diag/`。

---

## 8. 本机验证记录

在主机上用 dash + `busybox sh` 双跑过：

- 7 个脚本 `sh -n` 语法全过（dash 与 busybox 各一遍）
- `z1_diag_all.sh` 端到端跑完：17 个分节 + `00_index.txt` + `facts.txt` + tar 打包全部生成
- wifi/bt/gps/audio 四个被动模式跑完无中断（宿主机缺 `dumpsys`/`settings` 只记 NOTE）
- `z1_diag_keys.sh all` 用假 `getevent` 跑通完整引导流程（21 个窗口、`SUMMARY.txt`、
  `MISSING_KEYS.txt` 均正确生成）
- 音频噪声 WAV 头逐字节核对：`file` 判定 "RIFF WAVE, Microsoft PCM, 16 bit, stereo 44100 Hz"
- 四个**触发**分支也用 fake 工具链实测走完（不只是被动模式）：WiFi `func-on→wlan0→carrier→
  scan(含 3 次重试)`、BT `开关→观察栈→还原`、定位 `开→60s→还原`、音频
  `tinyplay 后台 + PCM status 并发采样 → tinycap → 音量键`
- `z1_diag_summarize.py` 三档实测：健康合成包 `OK=8/FAIL=0`；故障合成包 `FAIL=11` 且每条 hint
  都对得上（Invalid module format / request_firmware failed / am_kill foreground / avc 120）；
  单子系统深挖包只报 1 真 FAIL + `INFO 未覆盖`，不再误报
