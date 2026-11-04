# Z1 (MT6572 / LineageOS 16 / Android 9) 「所有 app FATAL EXCEPTION: main 死循环」根因研究

日期: 2026-11-04
作者: 根因研究 agent（只读分析，未改任何镜像/源码）
分析对象:
- 日志 `mainline_recon/z1_boot_mainline_20261104_224629.log`（含 3 次开机，`Booting Linux on physical CPU` 在 663 / 12900 / 19502 行）
- 镜像 `out/z1_fix3_system/system_fix3.img`（`debugfs` 读取，sudo 密码 `1`）
- boot ramdisk 里的 `/z1_klog_relay.sh`（源: `z1a9_gpt/rootdir/z1_klog_relay.sh`，v4 版）
- AOSP 9.0.0_r61 源码（联网抓取，见 §7 来源表）

> 本文与 22:35 的 `out/APP_CRASH_FIX_20261104.md` **不重复**：那篇立的是
> 「过滤器吞堆栈 + build.prop 首次生效」这个框架；本篇补的是**从那同一份日志里
> 之前没人读出来的 5 条硬证据**，其中一条直接推翻了「dropbox 里没有崩溃文件」这个前提。

---

## 0. 一句话结论

**「34 个不同 PID 各报一次 FATAL EXCEPTION: main」= 同一个进程被 AMS 反复重启 34 次，
而第一个崩溃的 PID（4903）在同一份日志里被 ANR 堆栈 dump 明确标注为 `com.android.phone`。**
而本机 **`/vendor/lib/hw` 里根本没有任何 radio HAL**（`android.hardware.radio@1.0-impl.so` 不存在、
`rild` 不存在），日志里 `Could not find service hosting interface
android.hardware.radio@1.0::IRadio/slot1` 出现 **238 次**。com.android.phone 是 `persistent`
进程，AMS 会无限重启它 —— 这就是「2→6 秒一次、PID 一直变、永远不停」的教科书形态。

**并且「dropbox 里没有 data_app_crash」是查法错了，不是文件不存在**：AOSP 9 的
`ActivityManagerService.processClass()` 对 `FLAG_SYSTEM` 的包返回 `system_app`，
所以 Settings / Home / phone 这类系统 app 的崩溃文件名是 **`system_app_crash@<ts>.txt`**，
不是 `data_app_crash@*.txt`。relay v4 的 `deep_dump` 只 glob 了后者
（`z1a9_gpt/rootdir/z1_klog_relay.sh` 里 `ls -t /data/system/dropbox/data_app_crash@*.txt`），
所以「找不到」是必然的。**改一行 glob 就能拿到全部堆栈。**

---

## 1. 从 22:46 日志里新读出来的 5 条硬证据

### 1.1 崩溃风暴是 **34 次**、**34 个不同 PID**、**76.6 秒**（不是 26 次）

去重复读（同一批 crash buffer 被 relay 复读了 4 轮）：

```
10-30 17:53:43.307 4903      ← 第 1 次
10-30 17:53:47.359 5574
10-30 17:53:51.344 5716
...
10-30 17:54:56.854 7466
10-30 17:54:58.442 7571
10-30 17:54:59.954 7621      ← 第 34 次
```

- 跨度 17:53:43.307 → 17:54:59.954 = **76.6 s**
- 间隔 **4.0 s 起，逐步缩到 1.4–2.6 s**（越崩越快，不是越崩越慢）
- 34 个 PID **全不相同** → AMS 每次重启给新 PID，是**同一个进程**在崩，不是 34 个 app
- 复现命令：
  ```sh
  grep -a 'FATAL EXCEPTION' /tmp/boot2.log \
    | sed -E 's/.*\] ([0-9-]+ [0-9:.]+) +([0-9]+) .*/\1 \2/' | sort -u | wc -l   # → 34
  ```

### 1.2 第一个崩溃的 PID 4903 = `com.android.phone`（日志内自证）

同一份日志、同一个 PID，在崩溃前 23 秒被 ANR 机制 dump 过：

```
[  519.788455] 10-30 17:53:19.944  4903  4914 I m.android.phon: Thread[3,tid=4914,
    WaitingInMainSignalCatcherLoop,...,"Signal Catcher"]: reacting to signal 3
[  520.449190] 10-30 17:53:21.024  4903  4914 I m.android.phon: Wrote stack traces to '[tombstoned]'
...
[  557.163560] 10-30 17:53:43.307  4903  4903 E AndroidRuntime: FATAL EXCEPTION: main
```

（`m.android.phon` 是 ART 对进程名 `com.android.phone` 的截断 tag；同一份日志里
`ndroid.systemu` = `com.android.systemui`、`le.android.hom` = `com.example.android.home`，
截断规则一致。）

紧接着 17:53:48.101 出现：

```
W SurfaceFlinger: Attempting to destroy on removed layer:
  ... ActivityRecord{ecc1905 u0 com.android.settings/.FallbackHome t1}
```

—— 这是 **FallbackHome 让位**（provisioned 之后 Settings 的兜底 Home 退出），
时间点落在第 2、3 次崩溃之间，**不能**据此判定「崩的是 FallbackHome」。

### 1.3 radio HAL 完全缺失，`Could not find service hosting interface` 刷屏 238 次

```
$ grep -ac "Could not find service hosting interface android.hardware.radio" /tmp/boot2.log
238
```

镜像侧核实（`debugfs -R "ls -l /vendor/lib/hw"`）：只有 graphics / audio / keymaster /
configstore / bluetooth / power / lights / vibrator / gralloc，**没有任何 radio HAL**：

```
android.hardware.graphics.composer@2.1-impl.so
android.hardware.audio@2.0-impl.so
android.hardware.graphics.allocator@2.0-impl.so
... （共 20 个 .so，无 android.hardware.radio*）
```

`/bin` 里也**没有 `rild`**（只有 `z1_linux7_radio_init.sh`，那是 WMT/STP 的 WiFi-BT-GPS
联合驱动脚本，跟 cellular RIL 无关）。

`com.android.phone` 是 `persistent=1` 的进程，`PhoneApp.onCreate()` 拿不到
`android.hardware.radio@1.0::IRadio/slot1` 就会在 main 线程抛异常 → FATAL EXCEPTION: main →
AMS 立刻重启（persistent 进程不遵守崩溃退避）→ 再崩。**这条链完全闭合。**

### 1.4 崩溃风暴之后，system_server 被自己的 Watchdog 杀掉（这条之前没人发现）

```
[  595.458774] sysrq: Show Blocked State
[  595.467169] sysrq: Show backtrace of all active CPUs
[  595.467880] CPU: 0 UID: 1000 PID: 4800 Comm: watchdog ...
[  595.482640] CPU: 1 UID: 1000 PID: 6221 Comm: Binder:2515_1B ...
             PC is at _raw_spin_unlock_irqrestore+0x1c/0x20
             LR is at sock_def_readable+0x34/0x58
             sock_def_readable from unix_dgram_sendmsg+0x464/0x788
[  596.094463] init: Service 'zygote' (pid 338) received signal 9
[  596.095341] init: Sending signal 9 to service 'zygote' (pid 338) process group...
[  596.449231] init: starting service 'zygote'...
```

解读（对着 AOSP 9 源码逐条对得上）：

1. `Comm: watchdog` + `UID 1000` = **system_server 的 Watchdog 线程**（system_server 的 uid 就是 1000）。
2. AOSP 9 `Watchdog.java:514-515` 超时时正是 `doSysRq('w'); doSysRq('l');`，
   `573-579` 行写 `/proc/sysrq-trigger` —— 和日志里 `sysrq: Show Blocked State` 完全对应。
3. 然后 `Watchdog.java:562-565`：`"*** WATCHDOG KILLING SYSTEM PROCESS"` + `Process.killProcess(myPid())`。
4. system_server 一死，zygote 的 `ZygoteInit` 检测到子进程退出 → zygote 自杀 →
   init 走 `onrestart`（`write /sys/power/state on` 失败 → 重启 audioserver/cameraserver/media/netd/wificond）
   → **596.449 秒 zygote 重启**。日志里这一整套 8 行一条不差。

**结论：这次开机里 system_server 至少被 Watchdog 杀过一次**，而且 NMI 抓到
`Binder:2515_1B` 卡在 `sock_def_readable` / `unix_dgram_sendmsg` 的 spinlock 上 ——
这是 **mainline 内核（7.0.0-rc7）与 Android binder/unix socket 负载下的内核级卡死**，
是独立于 app 崩溃的第二个缺陷。boot3 里 560.99 s 和 953.16 s 又各触发一次
（boot3 的 0 条 FATAL，说明 Watchdog 单发**不会**直接产生 FATAL）。

### 1.5 全程 **0 条 native abort** —— 可以彻底排除图形/EGL/gralloc 方向

```
$ grep -ac 'SIGABRT\|signal 6\|Abort message\|Fatal signal' <整个日志>
0
```

hwui 的 `EglManager.cpp` 在 `eglCreateWindowSurface` 失败时是
`_FATAL_IF(surface == EGL_NO_SURFACE, "Failed to create EGLSurface for window %p")`
→ `LOG_ALWAYS_FATAL` → `abort()` → logcat 里必然是 `Fatal signal 6 (SIGABRT)` +
`Abort message: 'Failed to create EGLSurface...'`，而且 relay v1-v3 的过滤器
**明确包含** `Abort message|signal [0-9]|SIGABRT` —— 一条都没有。
所以 `gralloc: invalid gralloc handle (at 0x0)` 那一堆**没有**把 app 崩掉，
它是 hwcomposer HAL 自己刷的噪声（boot1 里它确实把 zygote 拖进了 131 次重启，见 §1.6）。

### 1.6 附：boot1 的 131 次 zygote 重启 = 图形 HAL 用错（可解释「早期版本开不了机」）

boot1（日志 663–12899 行）里 `starting service 'zygote'` 出现 **131 次**，
每次都伴随：

```
E AllocatorHal: failed to open gralloc0 device: Invalid argument
E hwcomposer: ranchu_hw_composer hwc_open could not open framebuffer
E ComposerHal: failed to open hwcomposer device: Invalid argument
```

`ranchu_hw_composer` 是**模拟器（goldfish/ranchu）的 HWC**，真机上必然打不开 framebuffer。
boot1 是旧配置（`/vendor/lib/hw` 里选了 ranchu 那套），boot2/boot3 换成
`android.hardware.graphics.composer@2.1-impl.so` 后 zygote 只重启 1 次就稳定了。
**这条是历史包袱，不是当前问题**，但说明「图形栈选错 → zygote 全灭」这条因果链在本机是**真实存在**的。

---

## 2. 根因排序（每条带来源 + 证据强度 + 可证伪方式）

### H1 ★★★★★ `com.android.phone` 因 radio HAL 缺失而 main 线程崩溃 → persistent 进程无限重启

| 项 | 内容 |
|---|---|
| 证据 | §1.2 首崩 PID 4903 = `m.android.phon`（同 PID ANR dump 自证）；§1.3 radio HAL 0 个实现 + 238 次 `interface_start` 失败；34 个 PID / 76.6 s / 间隔递减 = persistent 进程重启特征 |
| 机理 | `persistent=1` → `ActivityManagerService` 不管崩溃次数都会 `startProcessLocked` 重拉；`PhoneApp.onCreate()` 里 telephony 初始化失败抛 RuntimeException |
| 反证 | 若真是它，dropbox 里应有 `system_app_crash@*.txt` 且 `Process: com.android.phone` |
| 可证伪 | `dumpsys dropbox --print system_app_crash \| head -80`（**不用刷机**） |

### H2 ★★★☆☆ system_server 卡死 → 全体 app `DeadSystemException`

| 项 | 内容 |
|---|---|
| 证据 | §1.4 Watchdog 触发 + NMI 抓到 `sock_def_readable` spinlock + zygote 596 s 重启；AOSP 9 `RuntimeInit` 对 system_server 死亡会在**每个** app 的 main 线程抛 `DeadSystemException: The system died; earlier logs will point to the root cause` |
| 外部佐证 | Sailfish OS 论坛实测：system_server 被 `IllegalArgumentException` 打死之后，Firefox 和 `com.android.phone` **同时**报 `FATAL EXCEPTION: main / DeadSystemException`（见 §7 来源 [5]）；StackOverflow 同现象 [6] |
| 反证 | 本机崩溃风暴（557–589 s）**早于** Watchdog（595 s）和 zygote 重启（596 s），所以风暴本身不是 DeadSystemException；但**用户手动点开 Settings/Home 时**（更晚、system_server 已经重启或半死）很可能是这条 |
| 结论 | 不能解释 34 连崩，但**能解释「用户点什么都闪退」**这一半现象，且它是本机最严重的系统级缺陷（内核 spinlock 卡死） |

### H3 ★★☆☆☆ `ro.config.low_ram=true`（11-04 首次真正生效）改变框架分支

| 项 | 内容 |
|---|---|
| 事实（AOSP 9 源码核对） | `ActivityManager.isLowRamDeviceStatic()` = `ro.config.low_ram`；它连锁影响三处：<br>① `ActivityManager.isHighEndGfx()` 返回 **false**（`ActivityManager.java:961-966`）<br>② `DropBoxManagerService` 的 `mMaxFiles` 从 1000 降到 **300**（`DEFAULT_MAX_FILES_LOWRAM`）<br>③ `lmkd.c:1281-1283` 走 Go 分支：`find_and_kill_processes(level, level_oomadj[level], 0)`，**每次压力事件只杀 1 个进程**，并且**完全绕过 `use_minfree_levels` 那段** |
| 实际影响面 | 我逐个 grep 了 AOSP 9 的 `ActivityManagerService.java` / `ActivityManagerConstants.java` / `ProcessList.java` / `WindowManagerService.java`：**`ro.config.low_ram` 在 AOSP 9 的服务端几乎没有硬行为**（AMS 里只有 dump 用；WMS 不用；ProcessList 不用；ActivityManagerConstants 不用）。真正的分支在 **app 层**（SystemUI / Settings / Launcher 读 `ActivityManager.isLowRamDevice()` 换低内存 UI）和 **lmkd** 里 |
| 判断 | 老报告说「low_ram 一开就多出一堆没验证过的分支」方向对，但**它本身几乎不可能直接抛 Java FATAL**。它最现实的危害是：Settings/SystemUI 走进 Go 版 UI 分支（本机没有 Go 版资源裁剪，反而更吃资源），以及 `isHighEndGfx()=false` 让 SystemUI 关掉部分硬件加速路径 |
| 证据强度 | ★★（相关性有，因果链没有） |
| 可证伪 | 删掉这一行单独刷一版；或用 `setprop` 不行（ro.* 只读），必须重打 system.img |

### H4 ★★☆☆☆ `resources.arsc` 36 MB + `framework-res.arsc` 30 MB

| 项 | 内容 |
|---|---|
| 事实（实测） | `Settings.apk` = 49,835,131 B，其中 `resources.arsc` = **36,394,168 B**、`classes.dex` = 5,461,320 B；`framework-res.apk` 的 `resources.arsc` = **30,805,156 B** |
| 关键纠正 | arsc 是 **mmap** 的（`ResTable` 直接映射文件），**不占 Java 堆**。所以「36 MB arsc + 96 MB heap → OutOfMemoryError」这个推理链**不成立**。它的真实代价是每次资源查找要在 532 个 config 上做匹配，在双核 A7 + 240×320 上会明显变慢（→ ANR 风险），但不会自己抛 FATAL |
| 外部检索 | 没有找到「arsc 大 → 崩溃」的可信实例；找到的都是 arsc 损坏/未对齐导致的安装失败（§7 [7][8]），与本机无关（`aapt dump resources` 已通过） |
| 证据强度 | ★★（降级：从「可疑主因」降为「性能负担」） |
| 可证伪 | 无需证伪；若想看真实影响，用 `dumpsys package com.android.settings` 看加载耗时 |

### H5 ★☆☆☆☆ ART oat/vdex 不可用 → 回退解释执行

| 项 | 内容 |
|---|---|
| 事实 | 镜像里 `/framework/arm.noimage` 存在（= 无 boot image 模式），有 `boot.vdex` / `boot-framework.vdex`（18.9 MB）等一整套 vdex，**配置是自洽的**；日志里 322 s 有 `dex2oat` 进程在跑（`/data/dalvik-cache/arm/system@framework@...`），说明 dexopt 链路通 |
| 机理 | dex2oat 失败时 installd 会删掉 oat，ART 回退到 **解释执行 + JIT**（AOSP art `dex2oat.cc` 注释原文：*"If dex2oat fails, installd will remove the oat files causing the app to run in interpreter mode"*）。**这是慢，不是崩** |
| 证据强度 | ★（明确排除为主因；只作为「为什么一切都这么慢 / ANR 遍地」的背景） |

### H6 ★☆☆☆☆ WebView / SystemUI / Launcher 连锁

- 本机 `com.android.webview` 没有出现在 §1.3 的 priv-app 列表里；但 Settings / Home / phone 的
  **启动路径都不碰 WebView**，所以「WebView 缺失 → 全崩」不成立。
- SystemUI 确实在 ANR（17:52:40、17:53:13 两次 signal 3），但那是**慢**导致的，不是崩。
- `com.example.android.home` 在 boot3 里 ANR 过一次（`le.android.hom` 17:53:34），
  说明它**能跑起来**，只是慢。
- 证据强度 ★（排除）

### H7 ★☆☆☆☆ `ro.sf.lcd_density=160` 与资源桶不匹配

- 240×320 @ 160 dpi 正好是 **mdpi**，是 Android 最基础的一档；AOSP 每个 app 都有 default 桶兜底。
- `aapt dump resources` 通过、`unzip -t` 通过。
- 证据强度 ★（排除）

### H8 ★★☆☆☆ 老报告的「heapgrowthlimit 96m 太大 → 降到 64m」

**这条建议方向是反的**，必须纠正：

- `ActivityManager.staticGetMemoryClass()` 直接读 `dalvik.vm.heapgrowthlimit`（`ActivityManager.java:892-900`），
  所以 96m 就是 app 的「正常堆上限」。1 GB 机器 200–445 MB 空闲，96 m 完全合理（AOSP Go 设备是
  32–64 m，但那是给 512 MB 机器的）。
- **如果**堆栈最后显示 `OutOfMemoryError`，正确做法是**调高** growthlimit（→128m）并把
  `heapsize` 从 256m 降到 192m（256m 的 largeHeap 上限在 1 GB 上确实危险，会招来内核 OOM-killer），
  而不是降到 64m。

---

## 3. 怎么把 Java 堆栈抓出来（4 条路，按可靠度排序）

### 3.1 ★★★★★ `dumpsys dropbox --print system_app_crash`（**新发现，最可能一击命中**）

AOSP 9 `ActivityManagerService.addErrorToDropBox()`（源码 15740 行起）：

```java
final String dropboxTag = processClass(process) + "_" + eventType;
if (dbox == null || !dbox.isTagEnabled(dropboxTag)) return;
```

`processClass()`（源码 15716 行）：

```java
if (process == null || process.pid == MY_PID) return "system_server";
else if ((process.info.flags & ApplicationInfo.FLAG_SYSTEM) != 0) return "system_app";
else return "data_app";
```

→ **`/system/priv-app/Settings`、`/system/app/Home`、`/system/priv-app/TeleService`
（com.android.phone）全部是 FLAG_SYSTEM → 文件名叫 `system_app_crash@<epoch>.txt`。**

板上 root shell（relay 就是 root 跑的，`init.z1.rc:95 user root`）执行：

```sh
# 1) 看目录里到底有什么（root 才读得到，shell 用户会 Permission denied）
ls -lt /data/system/dropbox | head -30

# 2) 走 binder 服务拿，最稳，不受 DAC 权限影响
dumpsys dropbox --print system_app_crash | head -120
dumpsys dropbox --print data_app_crash   | head -120
dumpsys dropbox --print system_server_crash | head -120
dumpsys dropbox --print system_server_watchdog | head -120

# 3) 直接读文件
cat /data/system/dropbox/system_app_crash@*.txt | head -150
```

`dumpsys dropbox --print` 输出格式（AOSP `DropBoxManagerService.dump()`）：

```
========================================
2026-10-30 17:53:43 system_app_crash (text, 8123 bytes)
Process: com.android.phone
Flags: 0x...
Package: com.android.phone v28 (9)
Build: Jiabo/lineage_z1/z1:9/...
Foreground: Yes
java.lang.RuntimeException: ...
	at ...
Caused by: ...
```

**这就是包名 + 异常类 + 完整堆栈，一次全有。**

> 注意：22:46 那次日志里有人以 `console:/ $`（uid 2000，非 root）跑过
> `ls -l /data/system/dropbox/` → `Permission denied`（boot2 第 5176-5177 行）。
> `/data/system/dropbox` 是 0700 root，**非 root 一定读不到**。所以「dropbox 里没有」
> 这个结论本身就可能是**权限问题造成的假阴性**。

### 3.2 ★★★★☆ 修 relay 的 dropbox glob（下次开机自动带出）

`z1a9_gpt/rootdir/z1_klog_relay.sh` 的 `deep_dump()` 里：

```sh
for f in $(ls -t /data/system/dropbox/data_app_crash@*.txt 2>/dev/null | head -4); do
```

改成同时抓 system_app / system_server：

```sh
for f in $(ls -t /data/system/dropbox/*app_crash@*.txt \
                  /data/system/dropbox/system_server_crash@*.txt 2>/dev/null | head -8); do
```

另外 `z1_klog_relay.sh` v4 的 `deep_dump` 里已经有
`logcat -d -b main -b system -t 4000 | grep -aE 'FATAL|AndroidRuntime|Process:|Exception|Caused by|^[[:space:]]+at '`
—— **这条是对的**，v4 一上板就能拿到堆栈。**确认板上跑的确实是 v4**
（v4 会打 `relay v4 start` 这行）。

### 3.3 ★★★☆☆ `/data/anr/`（ANR 堆栈，本机一定非空）

`build.prop:65 dalvik.vm.stack-trace-dir=/data/anr` 已经指过去了，而且 §1.2 证明
ANR 的 signal 3 确实被处理了（`Wrote stack traces to '[tombstoned]'`）。
Android 9 默认把 trace 写进 tombstoned，但只要 `/data/anr` 可写就会同时落盘：

```sh
ls -lt /data/anr | head
cat /data/anr/anr_* | head -80
```

即使只有 ANR trace 也很有价值：`com.android.phone` 的 ANR trace 会显示它卡在哪
（很可能卡在 `RIL`/`HIDL` 的 binder 调用上，直接印证 H1）。

### 3.4 ★★★★☆ 只抓 crash buffer（不用改 relay，一条命令）

```sh
logcat -d -b crash -v threadtime | tail -400
```

Android 的 `logd` 对 `AndroidRuntime` 的 E 级日志**强制同时写进 crash buffer**
（日志里 17:53:43 前那行 `--------- beginning of crash` 就是证据），
所以**整段 Java 堆栈本来就在 crash buffer 里**，只是 relay 没读它。
`logcat -d -b crash` 不受任何过滤器影响。

> 补充：`/data/tombstones/tombstone_*` 只放 **native** 崩溃；本机 0 条 native abort
> （§1.5），所以 tombstone 里不会有这次的 Java 堆栈，别再在那儿找了
> （boot3 的 `tombstone_43` 是 netd 的 `SIGSEGV in RefBase::incStrong`，另一件事）。

---

## 4. 最小改动清单（按「最可能先试」排序，一次只改一个变量）

### 第 0 步（0 改动、0 刷机）：先把堆栈拿出来

见 §3.1。**在做任何修改之前做完这一步**——它决定后面所有实验的顺序。

---

### 第 1 步（改 1 行 build.prop，重打 system.img）：`ro.config.low_ram`

文件: `<system.img>:/build.prop` 第 **52** 行

```diff
-ro.config.low_ram=true
+ro.config.low_ram=false
```

理由：
- 它是 11-04 首次真正生效的变量里**最"框架级"**的一个；
- AOSP 9 里它连锁 `isHighEndGfx()=false`（`ActivityManager.java:961`）+ lmkd Go 分支
  + DropBox 上限 300；
- 1 GB 机器上 `low_ram=true` 是 Android Go 的用法，**但 Go 需要配套的 Go 版 app**；
  本机装的是全套 AOSP app，属于「开了 Go 开关却没装 Go 包」的错配。

预期：即使不是主因也不会有回归（`false` 是绝大多数非 Go 设备的默认值）。
验证：`getprop ro.config.low_ram` 应为 `false`；`dumpsys activity settings | grep -i low` 无 low-ram 标记。

---

### 第 2 步（改 1 行 build.prop）：`ro.hardware.egl`

文件: `<system.img>:/build.prop` 第 **51** 行

```diff
-ro.hardware.egl=mesa
+ro.hardware.egl=swiftshader
```

理由（镜像实测）：`/vendor/lib/egl` 里只有

```
libEGL_swiftshader.so
libGLESv1_CM_swiftshader.so
libGLESv2_swiftshader.so
```

**没有 `libEGL_mesa.so`**。EGL loader 按 `ro.hardware.egl` 找 `libEGL_<name>.so`，
`mesa` 必然 dlopen 失败再回落（日志 17:56:50 的
`vndloading /vendor/lib/egl/libEGL_swiftshader.so` 就是回落成功）。
每次 app 冷启动多一次失败 dlopen，在双核 A7 上是实打实的时间。
这条**不是崩溃主因**，但是**零风险**的纯收益修复。

---

### 第 3 步（若 §3.1 确认是 com.android.phone）：把 cellular 栈停掉

本机 `/vendor/lib/hw` 没有任何 radio HAL，cellular 本来就**永远不可能工作**，
让它继续 crash-loop 只会拖垮 system_server。三种做法，从轻到重：

**3a. 只禁用包（可逆、不用刷机）**

```sh
pm disable-user --user 0 com.android.phone
pm disable-user --user 0 com.android.telephony  # 若有
```
注意：禁用后 `TelephonyManager` 相关 API 会抛异常，但 AOSP 框架对
`com.android.phone` 缺失是容错的（`PhoneInterfaceManager` 会返回空）。
**风险中等，先在串口上试，不行 `pm enable` 回滚。**

**3b. 让 PhoneFactory 走"无 RIL"分支（build.prop 加一行）**

```properties
ro.radio.noril=yes
```
（AOSP 老版本 `PhoneFactory` 认这个属性；A9 里行为需上板验证，
验证点：`dumpsys telephony.registry` 不再刷 radio 错误）

**3c. 干脆把 TeleService / MmsService 从镜像里删掉**（重打 system.img）
—— 最干净，但属于「改产品定义」，需用户确认。

> 这三条都**必须**在 §3.1 确认 `Process: com.android.phone` 之后再做。
> 如果堆栈显示是 `com.android.settings` 或 `com.example.android.home`，跳过第 3 步。

---

### 第 4 步（诊断用，1 行）：让 lmkd 说话

文件: `<system.img>:/build.prop` 第 **111** 行

```diff
-ro.lmk.debug=false
+ro.lmk.debug=true
```

理由：`lmkd.c:1529` `debug_process_killing = property_get_bool("ro.lmk.debug", false);`
打开后 lmkd 每次判定/杀进程都会 ALOGI，能把「是不是 lmk 干的」一次性钉死。
**纯诊断，不改行为，验证完可以改回去。**

---

### 第 5 步（**只在堆栈显示 OOM 时**才做）：堆参数

文件: `<system.img>:/build.prop` 第 **88–89** 行

```diff
-dalvik.vm.heapgrowthlimit=96m
+dalvik.vm.heapgrowthlimit=128m
-dalvik.vm.heapsize=256m
+dalvik.vm.heapsize=192m
```

理由：
- `heapgrowthlimit` 就是 `ActivityManager.getMemoryClass()`（源码 892–900 行直读该属性）。
  96m → 128m 是**给 app 更多正常堆**，不是更少。
- `heapsize=256m` 是 largeHeap 上限；1 GB 机器上单个 app 吃到 256 MB 会直接把内核逼到
  OOM-killer，192m 更安全。
- **不要**按老报告的建议降到 64m —— 那会让 OOM 更早发生。

---

### 第 6 步（可选）：关掉纯噪声 app

只列**确定不影响系统**、且本机用不到的（`pm disable-user --user 0 <pkg>`）：

```
com.android.email             # 历史日志里反复 WidgetProvider NPE（10-25 那批）
com.android.printspooler
com.android.printservice.recommendation
com.android.dreams.basic      # BasicDreams
com.android.traceur
com.android.htmlviewer
com.android.calculator2       # ExactCalculator
com.android.deskclock
com.android.calendar
com.android.gallery3d
com.android.camera2
org.lineageos.updater
org.lineageos.lineageparts   # 关掉后 Settings 里的 Lineage 选项消失，但 Settings 本身还在
com.android.wallpaperbackup
```

**不要关**：`com.android.settings`、`com.android.systemui`、`com.android.providers.*`、
`com.android.shell`、`com.android.bluetooth`（BT 栈在 `/vendor/lib/hw` 里有实现）。

---

### 第 7 步（独立缺陷，不在本次 app 崩溃范围内，但必须记录）

**system_server Watchdog 超时**（§1.4）。NMI 显示 `Binder:2515_1B` 卡在
`sock_def_readable` / `unix_dgram_sendmsg` 的 spinlock 上，这是 mainline 内核层面的问题。
缓解（不改内核的前提下）：`frameworks/base` 里 `Watchdog.DEFAULT_TIMEOUT` 默认 60 s，
本机因为太慢可以调到 120–180 s；但那是治症状。真因需要单独查
mainline 7.0-rc7 在 MT6572 双核上的 `unix_dgram` + binder 负载。

---

## 5. 上板验收判据

按顺序，**每一步都要有明确的通过/不通过信号**：

### A. 抓堆栈阶段（不刷机）
| # | 命令 | 通过判据 |
|---|---|---|
| A1 | `logcat -d -b crash \| tail -400` | 至少看到 1 条完整的 `Process: <pkg>` + 异常类 + `at ...` |
| A2 | `dumpsys dropbox --print system_app_crash \| head -120` | 出现 `Process: com.android.phone`（或 Settings/Home），带 `java.lang.*` |
| A3 | `ls -lt /data/anr` | 非空，且有与 A2 同一时间窗的文件 |
| A4 | `getprop ro.config.low_ram` | 记录当前值（用于对比） |

**A1/A2 任意一条拿到堆栈 = 本次研究的核心目标达成。**

### B. 改 build.prop 后（每次只刷一版）
| # | 检查 | 通过判据 |
|---|---|---|
| B1 | `getprop ro.config.low_ram` | `false`（若做了第 1 步） |
| B2 | `getprop ro.hardware.egl` | `swiftshader`（若做了第 2 步） |
| B3 | 开机后 `logcat -d -b crash \| grep -c 'FATAL EXCEPTION'` | **0**，或数量从 34 显著下降 |
| B4 | `logcat -d \| grep -c 'Could not find service hosting interface android.hardware.radio'` | 与基线（238）对比；若做了第 3 步应大幅下降 |
| B5 | `dumpsys dropbox --print system_app_crash` | 不再出现新的 `com.android.phone` 条目 |
| B6 | 手动点开「设置」→ 停留 10 s | 不闪退，`adb shell dumpsys activity activities \| grep mResumed` 仍是 Settings |
| B7 | 桌面（`com.example.android.home`） | 能显示，且 `logcat -b crash` 无新增 |
| B8 | `dmesg \| grep -c 'sysrq: Show Blocked State'` | 若做第 7 步，应比基线（每次开机 1–2 次）少 |
| B9 | `cat /proc/meminfo \| grep MemAvailable` | ≥ 150 MB（说明不是内存压力） |

### C. 回归判据（不能接受的副作用）
- `system_server` 不再重启（`logcat -b events | grep -c am_proc_died.*system` 应为 0）
- zygote 重启次数 = 1（`logcat -d | grep -c "starting service 'zygote'"` 应为 1）
- 开机时间不劣化（基线：`Boot is finished (462999 ms)`，任何一版都应 ≤ 这个量级）
- `dumpsys window | grep mCurrentFocus` 有正常的窗口，不是 `null`

---

## 6. 关键纠正汇总（对 22:35 那份报告）

| 老报告结论 | 本篇修正 | 依据 |
|---|---|---|
| 26 次崩溃 | **34 次**（34 个唯一 PID，76.6 s） | 去重统计 |
| 不知道崩的是谁 | **首崩 PID 4903 = com.android.phone** | 同 PID 的 ANR signal-3 dump |
| dropbox 里没有 `data_app_crash` → 拿不到堆栈 | **系统 app 的文件名是 `system_app_crash@*.txt`**；而且那次 `ls` 是**非 root** 跑的，Permission denied 是假阴性 | AOSP `processClass()` + 日志 5176 行 `console:/ $` |
| `resources.arsc` 36 MB → OOM 土壤 | arsc 是 **mmap**，不占 Java 堆；降级为「性能负担」 | AOSP `ResTable` |
| `heapgrowthlimit` 96m → 降到 64m | **方向反了**；若真是 OOM 应该**升到 128m** 并把 heapsize 降到 192m | `ActivityManager.staticGetMemoryClass()` |
| lmkd 是嫌疑 | 本机 `low_ram=true` 时 lmkd 走 Go 分支，每次只杀 1 个 cached 进程，且日志 0 条 kill；**排除** | `lmkd.c:1281` |
| — | **新增**：system_server Watchdog 超时 → zygote 重启（596 s），NMI 显示内核 spinlock 卡死 | 日志 + AOSP `Watchdog.java` |
| — | **新增**：`ro.hardware.egl=mesa` 但只有 swiftshader 实现 | 镜像 `/vendor/lib/egl` |
| — | **新增**：radio HAL 完全缺失（238 次 interface_start 失败） | 镜像 `/vendor/lib/hw` + 日志 |

---

## 7. 来源

**AOSP 9.0.0_r61 源码（直读，最权威）**
1. `DropBoxManagerService.java` — `DEFAULT_MAX_FILES_LOWRAM`、`isTagEnabled()`、
   `trimToFit()`、`createEntry()`：
   https://raw.githubusercontent.com/aosp-mirror/platform_frameworks_base/android-9.0.0_r61/services/core/java/com/android/server/DropBoxManagerService.java
2. `ActivityManagerService.java` — `processClass()`（15716 行）、`addErrorToDropBox()`
   （15740 行，"5 logs per 10 second" 限流）：
   https://raw.githubusercontent.com/aosp-mirror/platform_frameworks_base/android-9.0.0_r61/services/core/java/com/android/server/am/ActivityManagerService.java
3. `Watchdog.java` — `doSysRq('w')/doSysRq('l')`（514-515）、`WATCHDOG KILLING SYSTEM PROCESS`（562-565）：
   https://raw.githubusercontent.com/aosp-mirror/platform_frameworks_base/android-9.0.0_r61/services/core/java/com/android/server/Watchdog.java
4. `ActivityManager.java` — `isLowRamDeviceStatic()`（938-941）、`isHighEndGfx()`（961-966）、
   `staticGetMemoryClass()`（892-900）：
   https://raw.githubusercontent.com/aosp-mirror/platform_frameworks_base/android-9.0.0_r61/core/java/android/app/ActivityManager.java
5. `lmkd.c` — `low_ram_device` 分支（1281-1283）、`use_minfree_levels`（1199）、
   `ro.lmk.*` 默认值（1524-1544）：
   https://raw.githubusercontent.com/aosp-mirror/platform_system_core/android-9.0.0_r61/lmkd/lmkd.c

**外部实例 / 文档**
6. Sailfish OS 论坛 — system_server 死亡后 Firefox + com.android.phone 同时
   `FATAL EXCEPTION: main / DeadSystemException`（与本机形态高度一致）：
   https://forum.sailfishos.org/t/android-app-support-crashes-after-starting-because-vm-runs-out-of-heap/24823?page=3
7. StackOverflow — `DeadSystemException: The system died; earlier logs will point to the root cause`：
   https://stackoverflow.com/questions/70146887/deadsystemexception-the-system-died-earlier-logs-will-point-to-the-root-cause
8. AOSP hwui `EglManager.cpp` — `_FATAL_IF(surface == EGL_NO_SURFACE, "Failed to create EGLSurface for window %p")`
   （用于证明「EGL 失败 = native abort，不是 Java FATAL」）：
   https://android.googlesource.com/platform/frameworks/base/+/682573c/libs/hwui/renderthread/EglManager.cpp
9. AOSP art `dex2oat.cc` — *"If dex2oat fails, installd will remove the oat files causing the app to run in interpreter mode"*：
   https://android.googlesource.com/platform/art/+/master/dex2oat/dex2oat.cc
10. `dumpsys dropbox --print system_app_crash / data_app_crash` 用法：
    https://gh0st.tistory.com/40 ；AOSP `incident.proto` 里同样按
    `dropbox --proto data_app_crash` / `system_app_crash` 分两路采集：
    https://android.googlesource.com/platform/frameworks/base/+/master/core/proto/android/os/incident.proto
11. Android 官方 lmkd 文档（lowmemorykiller 驱动 vs 用户态 lmkd）：
    https://source.android.com/docs/core/perf/lmkd
12. XDA — 低内存属性补丁「厂商实现不当会导致崩溃甚至不开机」（low_ram 的风险佐证）：
    https://xdaforums.com/t/mod-low-ram-property-patcher-for-android.3737373/page-19

---

## 8. 建议的执行顺序（给主会话）

1. **今天**：板上 root shell 跑 §3.1 的 4 条命令（不刷机、不改镜像、5 分钟）→ 拿到堆栈
2. 按堆栈分叉：
   - `com.android.phone` → 做第 3 步（先 3a，可逆）
   - `com.android.settings` / `com.example.android.home` → 做第 1 步（low_ram）
   - `OutOfMemoryError` → 做第 5 步（升 growthlimit）
   - `DeadSystemException` → 转去查 §1.4 的内核 spinlock（第 7 步）
   - `Resources$NotFoundException` → 回到资源方向（H4），那时才值得动 arsc
3. 每刷一版只改一个变量，按 §5 的 B 表逐项记录
4. 顺手做第 2 步（`ro.hardware.egl`）—— 零风险纯收益
