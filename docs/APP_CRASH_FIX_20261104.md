# Z1 (MT6572 / Android 9 LineageOS 16) 内置 app 闪退 —— 排查报告

日期: 2026-11-04 22:35
分析对象: `out/z1_fix3_boot/boot.img` + `out/z1_fix3_system/system_fix3.img`
(依据 `out/Z1_FLASHABLE_20261104.md`「步骤 1」= 22:08 生成、22:10 上板；
 11-04 日志里 EGL 走 `libEGL_swiftshader.so`、`z1prov` 存在、`z1_sf_dump` 只跑一次，
 三处都指向 fix3 那套，不是「步骤 2」GPU 套)

> 注：任务里指定的 `out/z1_v2_system/system_v2.img` 在本轮分析途中被主会话删掉了
> （22:18 生成、22:30 前后消失）。它的 `/build.prop` 与 `system_fix3.img` 的
> **逐字节相同**（`diff` 空输出），`/priv-app/Settings/Settings.apk`、`/app/Home/Home.apk`、
> `/framework/*` 也都来自同一个 10-05/10-06 的 ROM build，所以结论不受影响。
> 本报告所有镜像侧证据均取自 `system_fix3.img`。

---

## 0. 一句话结论

**能证明的**：本次开机存在一个 **Java 层崩溃死循环**——81 秒内 26 个不同 PID 各报一次
`FATAL EXCEPTION: main`（不是被 OOM 杀，不是 native 崩溃）。

**不能证明的**：崩的是哪个包、异常是什么类型。原因不是日志没抓到，而是
`/z1_klog_relay.sh` 第 22 行的 logcat 过滤器**只放行含 `FATAL` 字样的那一行**，
`Process: xxx`、异常类名、`Caused by:`、`at ...` 堆栈行**全部被丢弃**。这是设计使然，不是偶发。

**上一版结论作废**：记忆里 2026-10-27 的「lmkd 杀前台 Settings」在**本次开机完全不成立**——
全日志 0 条 `lowmemorykiller: Kill`、0 条 lmkd 杀进程记录，且空闲内存
473s 时 200,720 kB、694s 时 445,484 kB。本次不是内存压力事件。

**定位成本最低的一步**（不用刷机、不用重编）：串口 root shell 里读
`/data/system/dropbox/`。Android 9 的 `ActivityManagerService.handleAppCrashLocked()`
会把每一次 Java FATAL 连同完整堆栈写进 dropbox，文件名形如
`data_app_crash@<epoch>.txt` / `system_app_crash@<epoch>.txt`。一条 `cat` 就能拿到
包名 + 异常类 + 堆栈。**这一步应该在改任何东西之前做。**

---

## 1. 崩溃证据（原文 + 出处）

### 1.1 崩溃死循环本体

出处: `mainline_recon/z1_boot_mainline_20261104_222254.log`（第 14–51 行，第 124 行起是
同一批 crash buffer 的第二轮 relay 复读；全文件 104 条 FATAL = 26 次崩溃 × 4 轮 relay）

```
[  696.751523] [z1klog] 10-30 17:52:57.276  4337  4337 E AndroidRuntime: FATAL EXCEPTION: main
[  696.798332] [z1klog] 10-30 17:53:05.390  4760  4760 E AndroidRuntime: FATAL EXCEPTION: main
[  696.846139] [z1klog] 10-30 17:53:09.613  4900  4900 E AndroidRuntime: FATAL EXCEPTION: main
[  696.888784] [z1klog] 10-30 17:53:11.206  4952  4952 E AndroidRuntime: FATAL EXCEPTION: main
[  696.934984] [z1klog] 10-30 17:53:12.776  5002  5002 E AndroidRuntime: FATAL EXCEPTION: main
[  696.977519] [z1klog] 10-30 17:53:14.991  5055  5055 E AndroidRuntime: FATAL EXCEPTION: main
[  697.019544] [z1klog] 10-30 17:53:16.422  5126  5126 E AndroidRuntime: FATAL EXCEPTION: main
[  697.060690] [z1klog] 10-30 17:53:18.099  5175  5175 E AndroidRuntime: FATAL EXCEPTION: main
[  697.102510] [z1klog] 10-30 17:53:19.696  5240  5240 E AndroidRuntime: FATAL EXCEPTION: main
[  697.144500] [z1klog] 10-30 17:53:21.337  5288  5288 E AndroidRuntime: FATAL EXCEPTION: main
[  697.187320] [z1klog] 10-30 17:53:26.026  5337  5337 E AndroidRuntime: FATAL EXCEPTION: main
[  697.228602] [z1klog] 10-30 17:53:32.427  5399  5399 E AndroidRuntime: FATAL EXCEPTION: main
[  697.270283] [z1klog] 10-30 17:53:38.146  5463  5463 E AndroidRuntime: FATAL EXCEPTION: main
[  697.312642] [z1klog] 10-30 17:53:47.151  5538  5538 E AndroidRuntime: FATAL EXCEPTION: main
[  697.357002] [z1klog] 10-30 17:53:50.552  5688  5688 E AndroidRuntime: FATAL EXCEPTION: main
[  697.404958] [z1klog] 10-30 17:53:54.202  5751  5751 E AndroidRuntime: FATAL EXCEPTION: main
[  697.449646] [z1klog] 10-30 17:53:56.287  5805  5805 E AndroidRuntime: FATAL EXCEPTION: main
[  697.496421] [z1klog] 10-30 17:54:00.136  5852  5852 E AndroidRuntime: FATAL EXCEPTION: main
[  697.675761] [z1klog] 10-30 17:54:02.652  5909  5909 E AndroidRuntime: FATAL EXCEPTION: main
[  697.731920] [z1klog] 10-30 17:54:04.986  5957  5957 E AndroidRuntime: FATAL EXCEPTION: main
[  697.780086] [z1klog] 10-30 17:54:07.600  4344  4344 E AndroidRuntime: FATAL EXCEPTION: main
[  697.824553] [z1klog] 10-30 17:54:09.241  6006  6006 E AndroidRuntime: FATAL EXCEPTION: main
[  697.871453] [z1klog] 10-30 17:54:11.066  6058  6058 E AndroidRuntime: FATAL EXCEPTION: main
[  697.923422] [z1klog] 10-30 17:54:12.615  6107  6107 E AndroidRuntime: FATAL EXCEPTION: main
[  697.973574] [z1klog] 10-30 17:54:15.576  6162  6162 E AndroidRuntime: FATAL EXCEPTION: main
[  698.016355] [z1klog] 10-30 17:54:18.366  6278  6278 E AndroidRuntime: FATAL EXCEPTION: main
```

读法：
- **26 个互不相同的 PID**（4337 … 6278，其中 4344 是中途复用的老进程）→ AMS 在
  **反复重启同一个崩溃进程**，不是 26 个不同的 app 各崩一次。
- 时间跨度 **17:52:57.276 → 17:54:18.366 = 81.09 秒**，间隔从 2s 逐渐拉长到 6s
  （机器越跑越慢），典型「启动→崩→AMS 立刻重启→再崩」。
- 之后（17:56:50）出现 `vndksupport: Loading /vendor/lib/egl/libEGL_swiftshader.so`
  （pid 6441），说明崩溃循环停下来后有一个带渲染的进程成功起来了。

### 1.2 崩溃起点紧跟 HOME 启动 + boot 完成

`mainline_recon/z1_boot_mainline_20261104_221018.log`：

```
2807:[  464.792341] [z1prov] am start HOME: Starting: Intent { act=android.intent.action.MAIN cat=[android.intent.category.HOME] }
2807:[  464.792341] Warning: Activity not started, intent has been delivered to currently running top-most instance.
2973:[  480.630484] [z1klog] ... 404   666 I SurfaceFlinger: Boot is finished (422854 ms)
2974:[  480.697662] [z1klog] --------- beginning of crash
2975:[  480.751498] [z1klog] ... 4337  4337 E AndroidRuntime: FATAL EXCEPTION: main
...
2983:[  480.961458] [z1klog] ... 404  3980 W SurfaceFlinger: Attempting to destroy on removed layer:
      AppWindowToken{31110d8 token=Token{8c193bb Token ... ActivityRecord{f96984a u0 com.android.settings/.FallbackHome t1}}}#0
```

- `Boot is finished` 之后 **0.12 秒**就出现第一条 FATAL。
- `[z1prov]` 说明 **`z1_provision.sh` 这次第一次生效**（该脚本是 fix3 boot 新增的）：
  置 `device_provisioned=1` / `user_setup_complete=1` 然后 `am start HOME`。
  在这之前 HOME 一直停在 `com.android.settings/.FallbackHome`（纯黑兜底页）。
- `Attempting to destroy on removed layer: ... FallbackHome` 是 WMS 想销毁一个
  SF 侧已经没了的 layer —— **通常是该进程已经死了**。时间点 17:53:06.857 夹在
  第 2 条 FATAL（17:53:05）和第 3 条（17:53:09）之间，与「FallbackHome 进程崩溃」
  自洽。
- **注意**：FallbackHome 正常 finish（provisioned 之后让位给真 launcher）也会移除 layer，
  但那种情况下不会同时出现 26 次 FATAL。所以这里更可能是崩溃。

### 1.3 本次开机是「第一次」同时满足两个新条件

| 条件 | 11-01 / 11-02 / 11-03 21:47 那几版 | 11-03 23:09 (fix3) / 11-04 |
|---|---|---|
| `/build.prop` 权限 | **0664**（init 整份跳过 → 所有 prop 未生效） | **0644**（生效） |
| `z1_provision.sh` | 无（HOME 永远停在 FallbackHome 黑页） | 有（provisioned + 主动拉 HOME） |

镜像侧实证（`debugfs -R "stat /build.prop"`）：

```
out/z1_r5_system/system.img.r5k_sf4.img   Mode: 0664   (11-01 00:21)
out/z1_r5_system/system_all.img           Mode: 0664   (11-02 00:50)
out/z1_r5_system/system_hafm.img          Mode: 0664   (11-03 21:47)
out/z1_fix3_system/system_fix3.img        Mode: 0644   (11-03 23:09)
out/z1_gpu_system/system_gpu.img          Mode: 0644   (11-03 23:09)
```

**build.prop 的内容在三代之间逐字节相同**（`diff /tmp/bp_hafm.txt /tmp/bp_fix3.txt` 空输出），
变的只有文件权限。init 的 `load_properties_from_file()` 见到 `S_IWGRP|S_IWOTH` 就判
insecure 并**整份跳过**，所以 0644 这次是这批 prop（`ro.config.low_ram`、
`dalvik.vm.heapsize`、`ro.lmk.*` 等）**第一次真正生效**。

结论：**11-04 22:10 这次开机是历史上第一次「prop 全部生效 + HOME 真正被拉起」**，
而它同时也是第一次出现 Java 崩溃死循环。这两个变量是并列新增的，靠现有日志无法拆开。

---

## 2. 为什么所有历史日志都挖不出堆栈（根因级证据）

`/z1_klog_relay.sh`（fix3 boot ramdisk 内，已与 `/tmp/x_z1_fix3_boot/z1_klog_relay.sh` 比对一致）：

```sh
21:    /system/bin/logcat -d -b main -b system -b crash -t 2000 2>/dev/null \
22:      | grep -aiE 'FATAL|Abort message|signal [0-9]|SIGABRT|backtrace|libEGL|libGLES|eglInitialize|gralloc|hwcomposer|SurfaceFlinger|tombstone|Beginning of crash' \
23:      | while IFS= read -r L; do printf '<6>[z1klog] %s\n' "$L" > /dev/kmsg 2>/dev/null || true; done
```

`grep` 是**逐行**匹配的。ART 打印一次 Java 崩溃长这样：

```
E AndroidRuntime: FATAL EXCEPTION: main                       ← 命中 FATAL，放行
E AndroidRuntime: Process: com.xxx.yyy, PID: 4337             ← 不命中，丢弃
E AndroidRuntime: java.lang.RuntimeException: Unable to ...   ← 不命中，丢弃
E AndroidRuntime:   at android.app.ActivityThread...          ← 不命中，丢弃
E AndroidRuntime: Caused by: java.lang.NullPointerException   ← 不命中，丢弃
```

所以**每一条历史日志（10-20 起的全部 `mainline_recon/*.log`）都只有那一行 FATAL**。
逐文件核对过：`z1_android9_acm_*.log`、`z1_boot_mainline_20261031_*`、`20261101_*`、
`20261103_*`、`20261104_*` 无一例外。

唯一有完整堆栈的是 10-20～10-25 那批 ACM 全量 logcat，但那几版 ROM 里唯一反复崩的是
**`com.android.email`**（与本问题无关，见 §4.6）：

```
z1_r3b_boot2_20261025.log:289205  Process: com.android.email, PID: 3264
z1_r3b_boot2_20261025.log:289206  java.lang.RuntimeException: Unable to start receiver
    com.android.email.provider.WidgetProvider: java.lang.NullPointerException:
    Attempt to invoke virtual method 'int[] android.appwidget.AppWidgetManager.getAppWidgetIds(...)'
    on a null object reference
```

**全项目历史里没有任何一条 `com.android.settings` 或 `com.example.android.home` 的 FATAL 堆栈。**

---

## 3. 根因判断（按证据强度排序）

### 3.1 ★★★★ 某个 UI 进程（launcher 或 FallbackHome）在 Java 层反复崩溃 —— 已证实存在，未证实身份

证据：§1.1 的 26 次 FATAL + §1.2 的 FallbackHome layer 被移除。
未证实：崩的是 `com.android.settings/.FallbackHome` 还是 `com.example.android.home/.Home`。
两者在 11-04 这次开机里都首次被真正拉起。

为什么这一条可信度高：26 个不同 PID、间隔递增、紧跟 `Boot is finished`，
是 AMS 反复重启同一个前台/持久进程的教科书形态。**不是** OOM（无 lmk 记录），
**不是** native abort（无 `Fatal signal` / `SIGSEGV` / tombstone 行）。

### 3.2 ★★★ build.prop 首次生效带来的行为突变（最可疑的具体项：`ro.config.low_ram=true`）

证据：§1.3 的三代镜像权限对比 + 内容逐字节相同。11-01 20:37 那次开机
`sys.boot_completed=1`（834s）之后**零 FATAL**，而那版 build.prop 是 0664。

`ro.config.low_ram=true` 在 Android 9 里是**改变框架行为**的开关（不只是「省内存提示」）：
`ActivityManagerService.mLowRamDevice=true`、`ActivityManagerConstants` 降低
最大 cached 进程数、`lmkd` 走 Go 分支、Settings/SystemUI 切低内存 UI 变体。
对一台刚上线的移植机，这是「一开就多出一堆没验证过的分支」。

但要注意：**单靠 `low_ram` 本身不应该直接抛 Java FATAL**。所以更可能是它和
另一个新生效项叠加（见 3.3）。

### 3.3 ★★★ 资源/配置类崩溃：超大 `resources.arsc` + 全语言全密度

镜像里 `Settings.apk` = 49,835,131 字节，其中 **`resources.arsc` = 36,394,168 字节**，
`aapt dump resources` 数出 **532 个 config**（`af` `am` `ar` `as` `ast` `az` … 全部语言，
外加 `sw600dp-xxhdpi/xxxhdpi` 全套密度桶）。正常 Android 9 Settings 的 arsc 是 2–5 MB 量级。

这台机器：1 GB RAM、双核 A7、**240×320 屏、`ro.sf.lcd_density=160`**、SwiftShader 软渲染。
把 36 MB arsc 挂进一个 240×320 的进程，配合 `dalvik.vm.heapgrowthlimit=96m`，
是 `Resources$NotFoundException` / `OutOfMemoryError` 的经典土壤。

**但这条只是可疑，没有直接证据**——arsc 结构本身是好的（`aapt dump resources` rc=0，
412,527 行完整解析），APK 也是好的（`unzip -t` 全 OK）。

### 3.4 ★★ `ro.hardware.egl=mesa` 指向不存在的驱动

`/build.prop` 里有 `ro.hardware.egl=mesa`，但 `/vendor/lib/egl/` 只有：

```
libEGL_swiftshader.so
libGLESv1_CM_swiftshader.so
libGLESv2_swiftshader.so
```

**没有 `libEGL_mesa.so`**（mesa 只在「步骤 2 GPU」那套里，且名字是
`libGLES_mesa.so` + `libEGL_swiftshader.so` 的组合）。所以 `ro.hardware.egl=mesa`
生效后 libEGL 会先找 mesa 失败再回落 swiftshader —— 日志 17:56:50 确实看到回落成功
（`Loading /vendor/lib/egl/libEGL_swiftshader.so`）。

回落是好的，但**回落过程要多走一遍 `dlopen` 失败**，在软渲染 + 双核 A7 上每次冷启动
多几百毫秒。不是崩溃主因，但是个应该修掉的自相矛盾配置。

### 3.5 ★★ `z1_mnld` 每 5 秒崩一次的死循环（独立缺陷，非本次主因）

`mainline_recon/z1_boot_mainline_20261104_222254.log` 第 73–121、175–365 行：
`init: starting service 'z1_mnld'` → `Service 'z1_mnld' (pid NNNN) exited with status 1`
每 5 秒一次，从 466s 一直持续到 799s 之后。`/system/vendor/bin/mnld` 起不来
（`execute_no_trans ... path="/system/vendor/bin/mnld"`）。
它不产生 FATAL，但**每 5 秒 fork/exec 一次 + avc audit 洪水**，在双核机器上持续吃 CPU。
建议一并关掉（见 §5.4）。

---

## 4. 排除项（逐条给「有/无证据」）

### 4.1 lmkd 杀前台（2026-10-27 的旧结论）—— **无证据，本次不成立**
- 本次开机全日志 `lowmemorykiller: Kill` **0 条**、`lmkd` 杀进程记录 **0 条**。
- `libprocessgroup: Successfully killed process cgroup` 全是 `uid 1000`（即 `z1_mnld`
  自己的进程组），**没有一条 app uid（10000+）**。
- 空闲内存充足：`[z1hb] beat=29 uptime=473.84s free=200720kB`（221018 第 2897 行）、
  `beat=42 uptime=694.65s free=445484kB`（222254 第 7 行）。
- 附带纠正：旧记忆说「`ro.lmk.low=85` 让 oom_adj 0 的 Settings 成为最重可杀者」，
  这个机制在 AOSP 9 的 `lmkd.c` 里**讲不通**——`find_and_kill_processes()` 是
  `for (i = OOM_SCORE_ADJ_MAX; i >= min_score_adj; i--)`，从 adj 1000 往下扫，
  只会杀 `adj >= min_score_adj` 的进程。`ro.lmk.low=85` 根本选不到 adj 0 的进程。
  要杀 adj 0 只有 `critical`（min_score_adj=0）才行。
  全项目历史日志里也**搜不到任何 `oom_adj 0,` 的 Kill 行**。

### 4.2 dalvik 堆参数不合理 —— **有嫌疑，无直接证据**
当前（`system_fix3.img:/build.prop` 第 87–92 行）：
```
dalvik.vm.heapstartsize=8m
dalvik.vm.heapgrowthlimit=96m
dalvik.vm.heapsize=256m
dalvik.vm.heaptargetutilization=0.75
dalvik.vm.heapminfree=2m
dalvik.vm.heapmaxfree=8m
```
`heapsize=256m` / `heapgrowthlimit=96m` 对 1 GB 设备偏高（AOSP Go 档一般是
growthlimit 32–48m、heapsize 128–192m）。日志里没有 `OutOfMemoryError` 的直接证据
（堆栈被过滤掉了），但也没有反证。属于「修了没坏处」的项。

### 4.3 `com.example.android.home` 与 Settings 的兼容性 —— **静态检查通过，无 ClassNotFound 证据**
- `Home.apk`（258,863 字节，`/app/Home/Home.apk`）是完整 AOSP 示例 launcher：
  `com.example.android.home/.Home`，manifest 声明 `MAIN` + `CATEGORY_HOME` + `DEFAULT`，
  另带 `Wallpaper` activity（`SET_WALLPAPER`）。`unzip -t` 通过。
- 把它 dex 里引用的 **102 个** 外部类逐个对照 `framework.jar` + `core-libart.jar` +
  `org.apache.http.legacy.boot.jar` + `org.lineageos.platform.jar` + `org.lineageos.hardware.jar`
  的字符串表：**只有它自己的 17 个 `Lcom/example/android/home/*` 不在里面**，框架类 0 缺失。
  `android.provider.LiveFolders`（示例 launcher 的老 API）在 framework.jar 里**存在**
  （classes2.dex 命中）。dex 里也**没有任何 `Lcom/android/internal/*` 引用**（不受 hidden API 限制）。
- Settings 同理：dex 引用的 **6,080 个**外部类逐个对照，**缺失 0 个**；
  `uses-library` 只有 `org.apache.http.legacy`（镜像里在）。
- 结论：**`ClassNotFoundException` / `NoClassDefFoundError` 可以排除**。

### 4.4 资源缺失（DPI / 语言）—— **有嫌疑，无直接证据**
见 §3.3。arsc 完整可解析，`ro.sf.lcd_density=160` 与 240×320 屏匹配。
没有 `Resources$NotFoundException` 的直接证据（同样被过滤器吃掉）。

### 4.5 SELinux —— **可排除**
内核 cmdline 明写 `androidboot.selinux=permissive`（221018 第 1 行），
日志里大量 `avc: denied ... permissive=1` 全部是「拒绝但放行」。不会导致崩溃。

### 4.6 32 位 ABI / native lib 缺失 —— **可排除（对 Settings/Home）**
- `ro.product.cpu.abilist=armeabi-v7a,armeabi`，`ro.product.cpu.abilist64=`（空）→ 纯 32 位。
- `Settings.apk` **没有 `lib/` 目录**（`unzip -l | grep lib/` 无命中）→ 不加载任何 native lib，
  不会 `UnsatisfiedLinkError`。
- `Home.apk` 同样没有 `lib/`。
- 日志里 0 条 `UnsatisfiedLinkError` / `Fatal signal` / `SIGSEGV`。

### 4.7 `/data` 空间不足 —— **无证据**
全日志 0 条 `No space left` / `ENOSPC`；同期 `z1diag` 还能成功写出
`collect_20261030_175422_onboot.tar.gz`（151,108 字节）。
（`/data` 实际剩余空间没法离线确认，需要在板上 `df /data`。）

### 4.8 `com.android.email` 的 WidgetProvider NPE —— **存在，但不是本次主因**
10-05/10-06 那批 ROM 里反复出现（17 次），根因是 `AppWidgetManager` 为 null 的
receiver NPE。本次 11-04 日志里**没有** email 的 FATAL（`grep -c` = 0）。
属于该删掉的死 app，顺手处理即可，别当成主因。

### 4.9 ANR / Watchdog —— **可排除**
本次开机日志里 `ANR in` 0 条、`Watchdog` 触发 0 条（只有
`mtk-wdt ... Watchdog enabled` 和 `watchdogd started` 两条启动日志）。

### 4.10 磁盘上 `system_v2.img` 被删 —— **不是崩溃原因**
`out/z1_v2_system/` 是 22:18 生成、22:30 前后被主会话删除的**宿主侧文件**，
与板子上的 /system 无关。本次开机（22:10）刷的是 fix3 那套。

---

## 5. 最小改动集

### 5.1 【先做，0 改动】把堆栈拿出来（唯一能定案的动作）

串口 root shell（CH340 `/dev/ttyUSB0` @921600，系统起来后就是 root）执行：

```sh
ls -lt /data/system/dropbox | head -20
cat /data/system/dropbox/data_app_crash@*.txt  | head -80
cat /data/system/dropbox/system_app_crash@*.txt | head -80
```

Android 9 的 dropbox 崩溃文件头部就是：
```
Process: com.xxx.yyy
Flags: 0x...
Package: com.xxx.yyy v28 (9)
...
java.lang.XXXException: ...
	at ...
```
**包名 + 异常类 + 完整堆栈一次全有。**

备选（同样不用刷机）：
```sh
cat /cache/z1diag/accept_boot_*.txt      # z1_accept.sh 已经存了 logcat -d -t 150
ls -l /data/local/z1diag/collect_*.tar.gz # 里面含全量 logcat 快照
```

> 注意：`z1_diag_onboot` 默认在 boot_completed 后 90s 跑，`/data/local/z1diag/` 下
> 已经有 `collect_20261030_175422_onboot.tar.gz`（生成时刻正好在崩溃循环中间），
> 里面大概率就有完整堆栈。

### 5.2 【必做，需重打 ramdisk + 刷 boot】放开 relay 过滤器，让下次开机自带堆栈

文件：boot ramdisk 里的 `/z1_klog_relay.sh`（源在
`out/z1_fix3_boot/boot.img` 解包目录，例如 `/tmp/x_z1_fix3_boot/z1_klog_relay.sh`）

第 22 行，把
```
| grep -aiE 'FATAL|Abort message|signal [0-9]|SIGABRT|backtrace|libEGL|libGLES|eglInitialize|gralloc|hwcomposer|SurfaceFlinger|tombstone|Beginning of crash' \
```
改成
```
| grep -aiE 'AndroidRuntime|FATAL|Abort message|signal [0-9]|SIGABRT|backtrace|libEGL|libGLES|eglInitialize|gralloc|hwcomposer|SurfaceFlinger|tombstone|Beginning of crash|^\s+at |Caused by:|^Process: ' \
```
（关键是加上 `AndroidRuntime` —— ART 的每一行堆栈 tag 都是 `AndroidRuntime`，
一条就能全收；其余几项是兜底。串口带宽 921600 够用，崩一次约多 30 行。）

### 5.3 【建议，需重刷 system.img】build.prop 参数修正

文件：`<system.img>:/build.prop`（`system_fix3.img` 里行号如下）

| 行 | 现值 | 建议值 | 理由 |
|---|---|---|---|
| 88 | `dalvik.vm.heapgrowthlimit=96m` | `dalvik.vm.heapgrowthlimit=64m` | 1GB 低内存设备 96m 过大 |
| 89 | `dalvik.vm.heapsize=256m` | `dalvik.vm.heapsize=192m` | 同上 |
| 87 | `dalvik.vm.heapstartsize=8m` | `dalvik.vm.heapstartsize=4m` | 冷启动少占 4MB |
| 96 | `ro.lmk.low=128` | `ro.lmk.low=1001` | 关掉 LOW 档杀进程（AOSP 默认就是 1001=禁用） |
| 100 | `ro.lmk.kill_timeout_ms=100` | `ro.lmk.kill_timeout_ms=1000` | 100ms = 最多 10 次/秒，太凶 |
| 101 | `ro.lmk.use_minfree_levels=true` | `ro.lmk.use_minfree_levels=false` | **因为 `ro.config.low_ram=true`，lmkd 走 Go 分支，这行是死代码**，留着只会误导 |

`ro.lmk.low=1001` 的依据（AOSP `android-9.0.0_r61 lmkd/lmkd.c`）：
```c
level_oomadj[VMPRESS_LEVEL_LOW] =
    property_get_int32("ro.lmk.low", OOM_SCORE_ADJ_MAX + 1);   // 默认 1001 = 禁用
level_oomadj[VMPRESS_LEVEL_MEDIUM]  = property_get_int32("ro.lmk.medium", 800);
level_oomadj[VMPRESS_LEVEL_CRITICAL] = property_get_int32("ro.lmk.critical", 0);
low_ram_device = property_get_bool("ro.config.low_ram", false);   // 本机 = true
...
if (low_ram_device) {
    /* For Go devices kill only one task */
    find_and_kill_processes(level, level_oomadj[level], 0);
} else {
    ... if (use_minfree_levels) { ...LMK_TARGET minfree 表... }
}
```
关键点三条：
1. `ro.lmk.low/medium/critical` 是 **oom_score_adj 阈值**，不是百分比、不是 minfree。
2. `ro.config.low_ram=true` → `low_ram_device=true` → **lmkd 走 Go 分支**，
   `use_minfree_levels` 整段被绕过（死代码）。
3. Go 分支 `pages_to_free=0` → `find_and_kill_processes` 的注释写明
   *"If pages_to_free is set to 0 only one process will be killed"* → **每个压力事件只杀 1 个进程**。

`ro.lmk.medium=800` / `ro.lmk.critical=0` / `kill_heaviest_task=false` **保持不动**
（800 只杀 cached，0 是全局兜底，都是 AOSP 默认值）。

`ro.config.low_ram=true` 本身**暂时不动** —— 它是当前唯一能解释「11-04 才出现」的变量，
但也是最需要先拿到堆栈再决定是否回退的变量。如果 §5.1 拿到的堆栈指向
`OutOfMemoryError` 或 `Resources$NotFound`，就把它改回 `false` 再测一次
（这是最干净的 A/B）。

### 5.4 【建议，需重刷 system.img】关掉 `z1_mnld` 死循环

`/system/vendor/bin/mnld` 每 5 秒起一次、立刻 exit 1。在
`<system.img>:/etc/init/` 里找启动它的 rc（`z1_mnld`），改成 `disabled` 或直接注释掉
整个 service 块。**WiFi 现在没在跑，mnld 起不来也没有损失**，关掉能省掉持续的
fork/exec + avc audit 洪水（`audit: rate limit exceeded` 在日志里出现多次）。

### 5.5 【可选，需重刷 system.img】删掉 `com.android.email`

`/priv-app/Email/`（或 `/app/Email/`）—— 它的 `WidgetProvider` 在 Android 9 上必崩
（`AppWidgetManager` 为 null 的 NPE）。日志证据见 §4.6。
注意：删 APK 要在镜像里连 `/data/system/packages.xml` 一起处理，或者接受
首次开机 PMS 清理；更省事的做法是保留 APK 但在 `/data` 里 `pm disable`。

### 5.6 【不建议现在做】回退 build.prop 权限到 0664

能立刻把崩溃「变回不崩」，但同时会让 `dalvik.vm.*`、`ro.lmk.*` 全部失效，
等于把问题盖住而不是修好，而且 fix3 的 allocator rc 修复也依赖这一批改动。
**不要用这个当修复**；只在 §5.1 拿不到堆栈、且需要立刻可用时作为临时手段。

---

## 6. 上板验收判据

### 6.1 判据 A —— 拿到堆栈（第一优先级）
串口执行 `cat /data/system/dropbox/data_app_crash@*.txt`，**期望看到**：
- 明确的 `Process: <包名>`（`com.android.settings` 或 `com.example.android.home`）
- 明确的 `java.lang.XXXException: ...`
- 至少 3 行 `at ...`

**没有这三样 = 没查完，不要改代码。**

### 6.2 判据 B —— 崩溃循环消失
刷入修正后的 boot+system，开机后串口：
```sh
# 在板上
logcat -d -b crash | grep -c 'FATAL EXCEPTION'
```
**期望 `0`**（或只有一次、且不再重复）。
串口侧（宿主）：
```sh
grep -c 'FATAL EXCEPTION' mainline_recon/z1_boot_mainline_<新时间戳>.log
```
**期望 ≤ 1**（当前基线是 104 行 / 26 次）。

### 6.3 判据 C —— HOME 稳定
```sh
dumpsys window | grep mCurrentFocus
```
**期望**：`mCurrentFocus=Window{... u0 com.example.android.home/com.example.android.home.Home}`
（而不是 `com.android.settings/.FallbackHome`，也不是 `null`）。
连续观察 3 分钟，**PID 不变**：
```sh
pidof com.example.android.home     # 连续 3 次，PID 应相同
```

### 6.4 判据 D —— 打开设置不再闪退
```sh
am start -a android.settings.SETTINGS
sleep 5
pidof com.android.settings         # 期望有 PID
dumpsys window | grep mCurrentFocus  # 期望 com.android.settings/.Settings
logcat -d -b crash | grep -c FATAL   # 期望仍为 0
```
然后在**屏幕上用手指点**「设置」图标复现一次（这是用户报告的原场景）。

### 6.5 判据 E —— 内存/杀进程侧确认无回归
```sh
grep -c 'lowmemorykiller: Kill' mainline_recon/z1_boot_mainline_<新时间戳>.log   # 期望 0
grep 'z1hb' mainline_recon/z1_boot_mainline_<新时间戳>.log | tail -3            # free 应 >= 150000kB
```

### 6.6 判据 F —— mnld 不再刷屏（若做了 §5.4）
```sh
grep -c "starting service 'z1_mnld'" mainline_recon/z1_boot_mainline_<新时间戳>.log
```
**期望 0**（当前基线：466s～799s 之间 ≥ 20 次）。

---

## 附录 A —— 本次分析用到的离线证据清单

| 证据 | 来源 / 命令 |
|---|---|
| 26 次 FATAL、PID、时间戳 | `grep -a -n 'FATAL EXCEPTION' mainline_recon/z1_boot_mainline_20261104_222254.log` |
| Boot is finished / FallbackHome layer | `mainline_recon/z1_boot_mainline_20261104_221018.log:2973-2985` |
| `[z1prov] am start HOME` | 同上 `:2807` |
| relay 过滤器 | `/tmp/x_z1_fix3_boot/z1_klog_relay.sh:21-23`（= boot ramdisk `/z1_klog_relay.sh`） |
| build.prop 权限三代对比 | `debugfs -R "stat /build.prop" out/z1_r5_system/*.img out/z1_fix3_system/system_fix3.img` |
| build.prop 内容 | `debugfs -R "cat /build.prop" out/z1_fix3_system/system_fix3.img`（111 行） |
| Settings.apk 完整性 | `unzip -t` / `aapt dump resources`（rc=0，412,527 行）/ 6,080 个外部类缺失 0 |
| Home.apk 完整性 | `unzip -t` / 102 个外部类缺失 0（仅自身 17 个）/ 无 `com/android/internal` 引用 |
| lmkd 语义 | `https://raw.githubusercontent.com/aosp-mirror/platform_system_core/android-9.0.0_r61/lmkd/lmkd.c` |
| SELinux | `androidboot.selinux=permissive`（221018:1） |
| ABI | `ro.product.cpu.abilist=armeabi-v7a,armeabi`；APK 无 `lib/` |
| lmk 杀进程 | 全日志 `lowmemorykiller: Kill` = 0；`Successfully killed ... uid 10xxx` = 0 |

## 附录 B —— 与本问题无关但记录在案的现场噪声

- `interface_start ... android.hardware.radio@1.0::IRadio/slot1` → `Could not find service
  hosting interface` 每 0.5–1s 一次，持续整场。无 radio HAL，属于已知缺失。
- `z1_mnld` 5s 一次 exit 1（§3.5）。
- `audit: rate limit exceeded` 多次（avc 洪水把 audit 缓冲打满，会**吃掉一部分
  selinux 审计日志**，但不影响 kmsg）。
- `dex2oat` 在 uptime ~312s 还在跑（pid 3532），`Boot is finished` 用了 **422,854 ms**
  （7 分钟）。软渲染 + 双核 A7 + 全量 dexopt，启动慢是必然，但与崩溃无直接关系。
