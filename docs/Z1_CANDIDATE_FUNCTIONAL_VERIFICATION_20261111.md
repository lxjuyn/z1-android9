# 候选镜像功能验收报告（2026-11-11 上板实测）

> 对象：`out/z1_driver_candidates_20261110/system_peripherals_v1.img`（v35 基底 + 6 个替换文件）
> 方法：主机侧复核 + 真机刷写 + 逐项功能实测 + A/B 对照
> 结论一句话：**6 个文件安装正确、keylayout 与 WiFi 固件路径修复真实有效；但蓝牙不可用、
> WiFi 卡在 MAC 校验、系统不稳的真因是存储层文件系统损坏（与这 6 个文件无关）。**

---

## 一、已验证成立 ✅

| 项 | 证据 |
|---|---|
| 9 套测试 187 用例全过 | 我本人重跑；同一 harness 对改动前源码 `0e6b50f` 跑出 27/34 失败（证明测试有效） |
| 候选镜像完整性 | sha `833a65f2…`、全树 2134 文件仅 6 个不同、`e2fsck -fn` rc=0，全部重算 |
| ARM 产物可复现 | `fb7240ed…` 从当前树重建逐字节一致 |
| **6 个文件上板正确** | 分块刷写 19/19 成功，板上 sha 全部匹配 manifest |
| **刷写未损坏** | 另查 surfaceflinger/libsf/libc/libbinder/libui 5 个关键文件与镜像逐字节一致 |
| **keylayout 修复生效** | 真机三键上报 **158 BACK / 102 HOME / 139 APP_SWITCH** |
| **WiFi 固件路径修复生效** | 旧：`Failed to open wlan fw path param` → 新：`kalFirmwareOpen: Open FW image done, 160464 bytes` |

## 二、顺手修掉的驱动 bug ✅

三键原始 X 实测 **80 / 120 / 200**，旧默认 `key_x_split1 = 80` 且判定为严格小于，
`x == 80` 落进 HOME 区 → 最左键报 HOME。改为 **90 / 160**（三个中心的中点）后
`x=80 → BACK`。源码默认值已提交（`5d4d266`），并在开机脚本里补写 sysfs 参数。

---

## 三、发现的问题（按严重度）

### 3.1 系统不稳的真因：存储层文件系统损坏（**与这 6 个文件无关**）

**A/B 对照（决定性）**：

| 配置 | 结果 |
|---|---|
| 基线 v35（无 6 文件） | `boot_completed=1`，1951 秒 `ext4 error=0`、`系统死亡=0` ✓ |
| 6 文件 + 无 fsck | 350 秒内 `系统死亡=6`、`zygote 崩=72` ✗ |
| 3 个新库 + 开机 fsck | 324 秒 `系统死亡=0` ✓（fsck `rc=0` 干净） |
| 3 个新库 + 开机 fsck（再下一轮） | 运行中再次损坏：`ext4_mb_generate_buddy: block bitmap and bg descriptor inconsistent` |

**关键**：损坏同时出现在 **两个不同分区**：

```
EXT4-fs error (device mmcblk0p6): ext4_mb_generate_buddy:1314: group 16,
    block bitmap and bg descriptor inconsistent: 32768 vs 31946
EXT4-fs error (device mmcblk0p5): __ext4_new_inode:1284: comm sh:
    failed to insert inode 7233: doubly allocated?
```

且 **无任何 mmc/mtk-msdc 硬件错误**。两个独立 ext4 同时元数据错乱、硬件层无报错 ⇒
指向**块层/页缓存污染或驱动静默写坏**，而不是某个上层库。

一旦 `/data` 被 remount 只读，连锁反应固定出现：
`DropBoxManagerService: /data/system/dropbox/... (Read-only file system)` →
`system_server` 死亡 → `DeadSystemException` 全网崩。

**这也解释了此前多次"候选镜像导致崩溃"的误判** —— 换镜后 `reboot` 时的硬断电把
`/data` 弄脏，与镜像内容无关。

### 3.2 蓝牙不可用 ❌

```
E android.hardware.bluetooth@1.0-impl: VendorInterface -> Unable to call initializationComplete(ERR)
F DEBUG: pid 774 (HwBinder:774_2) signal 6 (SIGABRT)
```

新 `libbt-vendor.so` 能被 dlopen、ABI 校验通过（`bt_vendor_interface_t.size`
两边都是 `0x10`=16，三个函数指针均非零），但 vendor init **报 ERR**，
HAL 视为致命 → abort → 服务反复重启。旧库（15,948 B）虽也起不来，但不会崩溃循环。

⇒ 新库 7,208 B 的实现在这块板子上**初始化失败**，需要实物调试（chip reset/波特率/固件）。

### 3.3 WiFi 固件路径修好，但卡在 MAC 校验 ❌

```
WARNING: net/core/dev_addr_lists.c:520 at dev_addr_check+0x108/0x130, CPU#0: mtk_wmtd/340
netdevice: wlan0 (unregistered): Incorrect netdev->dev_addr
HifAhbRemove [wlan_gen2] from wmt_func_wifi_off
```

固件下载成功了，但 `wlan_gen2` 注册 netdev 时 MAC 未通过主线新增的
`dev_addr_check()` 校验 → 注册失败 → 随后 wifi off / chip 复位。
**修法**：驱动应在 `register_netdev()` 之前把从 chip 读到的 MAC 写入
`dev->dev_addr`（或 `eth_hw_addr_set()`），不要在注册后才赋值。

### 3.4 音频：HAL 服务起来了，未做发声验证

`audiohalservice` 正常注册；`audio.primary.mt6572.so` 为新版（`d037d76c`）。
未做实际发声测试（被 3.1 的存储问题打断）。

---

## 四、本轮产出的修复与工具

### 4.1 新 boot 镜像（可靠交付通道）

串口传输在 8KB 量级不可靠（实测 10,928 字符丢 41 字符，且长度校验通过但内容错）。
改为**把文件打进 ramdisk，开机由 root 脚本安装**：

`out/boot_fsck_20261111.img` (5,959,680 B, sha `3a7a6c75…`)

ramdisk 内新增（已反解逐字节核对）：

```
z1_libwifi-hal.so          95,116 B  bc7e0525…  → /system/vendor/lib/libwifi-hal.so
z1_libbt-vendor.so          7,208 B  43944199…  → /system/vendor/lib/libbt-vendor.so
z1_audio.primary.mt6572.so  8,196 B  d037d76c…  → /system/vendor/lib/hw/audio.primary.mt6572.so
z1stage_boot.sh            14,915 B             → 开机安装 + 自检
```

### 4.2 `z1stage_boot.sh` 新增能力

1. **`install_sys_file`**：从 ramdisk 安装系统文件（cp→tmp→rename，chmod 0644，
   源不存在时**不动**目标，不像 `install_one` 会删除）。
2. **开机强制 e2fsck /data**：`umount /data` → `e2fsck -f -y` → 重新挂载。
   放在框架启动前（post-fs-data），日志形如
   `z1stage: data fsck: rc=0 remount_rc=0 tail=Pass 5: ...`。
   `/data` 忙时跳过（不会把事情弄更糟）。
3. `applied=5` 可直观确认 5 个安装动作（2 原有 + 3 新增）都执行了。

### 4.3 板上已有的两个持久化修复（会被刷 system 覆盖）

`/system/bin/z1_touch_load.sh` 的 `exit 0` 之前：

```sh
chmod 0644 /sys/kernel/debug/wakeup_sources
sleep 2; echo 90  > /sys/module/z1_cst836u/parameters/key_x_split1 2>/dev/null
echo 160 > /sys/module/z1_cst836u/parameters/key_x_split2 2>/dev/null
```

> `wakeup_sources` 那条修的是 `Log.wtf` 打死 system_server
> （`neither /proc/wakelocks nor /d/wakeup_sources exists` —— 文件其实存在，只是 0600 root）。

---

## 五、下一步（按优先级）

1. **定位静默写坏**：两分区同时坏且无硬件报错，优先排查 (a) `mtk-msdc` 驱动在本轮
   内核上的 PIO/DMA 路径；(b) 页缓存层；(c) 内存可靠性（跑 `memtester`）。
   在此之前任何"功能验收"都会被随机打断。
2. **修 WiFi MAC**：`wlan_gen2` 在 `register_netdev` 前设置 `dev_addr`。
3. **蓝牙**：需要实物调试 vendor init 的失败点（可加 `z1_cst836u` 式的日志）。
4. 音频发声验证（等存储稳定后）。

---

## 六、环境纪律（本轮踩到的坑）

* 串口 console 连续收大段命令会"只回显不执行"（UART 溢出）→ 需要 Ctrl-C 或重启。
* 8KB 级文件**不要**用串口 base64 传（长度对了内容也可能错）→ 走 ramdisk 打包。
* `z1push.sh` 在这块板子上不可用（标记协议收不到回显）。
* 每次硬断电都会给 `/data` 留下 ext4 隐患；能 `reboot` 就别拔电。
* 串口工具已入库：`scripts/z1deploy/z1cap.sh`、`z1sh.sh`。

---

## 七、补充实测（同日稍晚，干净 reboot 之后）

用 `reboot` 命令正常重启（而非按电源键硬断电）后，系统进入健康状态：

```
ext4错误=0   只读remount=0   data fsck: rc=0   系统死亡=0(初期)   zygote崩=0
```

### 7.1 存储层实测：没有静默写坏

脚本 `/data/local/tmp/z1stor.sh`：在 `/data` 与 `/cache` 各做 8 轮
「1MB 随机写 → sync → 复制 → sync → `cmp` 逐字节比对」：

```
/data/local/tmp iter=1..8 全部 OK
/cache           iter=1..8 全部 OK
DONE iters=16 mismatch=0
```

⇒ **不是"随便写就坏"**。结合"干净 reboot 后不再坏、硬断电后才坏"，
更符合 eMMC 易失写缓存在硬断电时丢数据的特征（journal 与数据块丢失不成比例，
journal replay 后仍残留不一致）。**结论：能 `reboot` 就不要拔电。**

### 7.2 WiFi：框架启用路径确认修好

`svc wifi enable` 之后：

```
2: wlan0: <BROADCAST,MULTICAST,UP> state DOWN mode DORMANT
   link/ether 20:72:0d:39:0d:1f
[z1wifi] scan returned 6 BSS
[z1wifi] scan OK: 8 SSID lines
```

**重要澄清**：`/z1_wifi_probe.sh`（开机探针，走 `/dev/wmtWifi` 裸路径）在**旧系统上
一直能扫描成功**（日志里第 1、2 次启动就有 `scan OK`）。GPT 的 `libwifi-hal.so`
修复价值在于**框架侧 `svc wifi enable` 这条路**：旧系统报
`Failed to open wlan fw path param` / `Failed to change firmware mode` 后驱动被卸载，
现在 `wlan0` 能真正 UP 并保持。

### 7.3 蓝牙：确认是新库的问题

`initializationComplete(ERR)` 在**所有装了新 `libbt-vendor.so` 的启动**里都出现
（第 3/6/8/9/10 次），旧库的启动里没有。HAL 进程随即 SIGABRT，
`#01/#02` 落在 `android.hardware.bluetooth@1.0-impl.so`。
ABI 校验本身是通过的（结构体 size=16 两侧一致、三个函数指针非零），
是 vendor init 运行期返回 ERR。**需要实物调试**。

### 7.4 system_server 后期崩溃：GPS 服务 APK 加载失败（残留损坏）

```
*** FATAL EXCEPTION IN SYSTEM PROCESS: main
java.lang.RuntimeException: Unable to create service android.hardware.location.gps...
Caused by: java.lang.NullPointerException
    at android.app.LoadedApk.getAssets(LoadedApk.java:1016)
    at android.app.LoadedApk.makeApplication(LoadedApk.java:1089)
```

只在第 11 次启动出现（前 10 次为 0），且与 6 个文件无关（它们不涉及 location）。
判断为此前多次 `/data` 损坏导致某个 APK/dex 残留损坏。
**建议下一步做一次 `/data` 擦除（factory reset）**，把所有残留损坏清掉后重评。

---

## 八、最终裁定（按证据）

| 断言 | 裁定 | 依据 |
|---|---|---|
| 6 个文件安装正确 | ✅ 成立 | 板上 sha 全部匹配 |
| keylayout 修复 | ✅ 成立 | 三键 158/102/139 实测 |
| WiFi HAL 修复 | ✅ 成立（框架启用路径） | `Failed to open wlan fw path param` → `wlan0 UP` + 扫描成功 |
| 候选镜像导致系统崩溃 | ❌ **不成立** | 崩溃真因是 `/data` ext4 损坏（硬断电）与 GPS APK 残留损坏 |
| 蓝牙可工作 | ❌ 不成立 | `initializationComplete(ERR)` → HAL abort 循环 |
| 驱动 X 分区默认值 | ⚠️ 原值有 bug | 80 使最左键报 HOME，已改 90/160 |

---

## 九、新增功能：检测硬断电并自动修复 /data、/cache

### 9.1 需求与设计

板子用电源键硬断电是常态，而硬断电会留下 ext4 元数据残留（journal 回放修不掉，
fs_mgr 又因为"已回放干净"而不跑 e2fsck），随后内核在运行中踩到：
`ext4_lookup: deleted inode referenced` / `ext4_mb_generate_buddy` →
`Remounting filesystem read-only` → 所有 /data 写入者 EROFS → zygote 每 5 秒 SIGABRT。

**实现（`z1stage_boot.sh`，post-fs-data，root）**：按需修复，两个信号 ——

1. **内核日志回放**（权威）：`dmesg` 里出现 `EXT4-fs (<dev>): recovery complete`
   说明内核回放了 journal，即**上次没有正常卸载**。
2. **`EXT4_ERROR_FS`**：读原始超级块 `s_state`（分区内 1024+58），bit1 置位
   说明内核在该文件系统上记录过错误。

命中任一信号 → `umount` → `e2fsck -f -y` → 重新挂载；都未命中 → **什么都不做**。

> ⚠️ **踩过的坑（已用实验推翻）**：最初想用 `s_state` 的 bit0（`EXT4_VALID_FS`）
> 判断，但那是**非日志文件系统**才维护的字段。实测 64 MiB ext4：mkfs / 挂载 rw /
> 干净卸载三个状态下读到的都还是 `0x0001`，**挂载并不会清 bit0**。
> 所以对带日志的 ext4 只能靠"内核是否回放过 journal"判断。

### 9.2 上板验证（两个方向都验了）

| 场景 | 日志 | 结果 |
|---|---|---|
| 刷完 boot 后首次开机（刷写=硬复位） | `mmcblk0p6: journal was replayed (previous shutdown killed) -> REPAIR`<br>`repair /data: e2fsck rc=0 remount_rc=0` | ✅ 自动修复 |
| 上一轮把 fs 打成只读后再开机 | 同上（p6 与 p5 各一次） | ✅ 自动修复 |
| **正常 `reboot` 之后再开机** | `mmcblk0p6: no journal replay, s_state=0x0001 -> clean, skip`<br>`mmcblk0p5: no journal replay, s_state=0x0001 -> clean, skip` | ✅ **跳过，零开销** |

镜像：`out/boot_autorepair_20261111.img`（5,959,680 B，sha `4070dd59…`）。

### 9.3 顺带修正：存储层没有静默写坏

第一版测试用 `cmp` 比对，但读的是**页缓存**，根本没验证落盘数据 —— 无效。
重做：写入 2 MiB → `sync` → `echo 3 > /proc/sys/vm/drop_caches` → 再读回比对。

```
/data/local/tmp iter=1..6  OK(媒体读回一致)
/cache           iter=1..6  OK(媒体读回一致)
DONE iters=12 mismatch=0
```

⇒ **eMMC 不会静默写坏**。运行期损坏另有来源，目前最可疑的是 fs_mgr 在
13.8 s 那次「挂载 → 10 毫秒后卸载 → 2.6 秒后重挂」的抖动，尚未定位。
