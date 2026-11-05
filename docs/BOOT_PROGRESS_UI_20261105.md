# 开机"正在优化系统"可视化方案调研（AOSP 9 / LineageOS 16 · Z1）

日期：2026-11-05　范围：只读调研（未改任何镜像/文件）。所有结论都带源码行号或板上/镜像内实证。

---

## 0. 一句话结论

1. **A9 的 "Optimizing app N of M" 代码还在，但我们这种场景不会触发**：`PackageManagerService` 只在 **pre-N 升级**
   （`showDialog = mIsPreNUpgrade`）时才向系统 UI 推送这个进度对话框。换 system 镜像导致 dalvik 缓存被清 → 走的是
   `causePrunedCache` 分支，`showDialog=false`，所以**原生 A9 在这种情况下就是"开机动画一直播到系统真正起来"**，
   没有所谓"优化中"的进度页。→ 我们要做的"原生感"= **把开机动画做成优化提示**，这是 A9 真实的行为路径。
2. 现场 1.7 s 退出（40.59 s 起 / 42.33 s 退）**已确证**是 `debug.sf.nobootanimation=1`：
   该属性由 **bootanimation 自己**（不是 SF）在 `BootAnimationUtil.cpp:28-36` 读取，命中后 `main()` 直接 `return 0`。
   v6 已注释掉该行，方向正确。
3. 本次额外发现 3 个硬问题（都会直接影响"看得见"这件事）：
   - 现有 `/system/media/bootanimation.zip` **帧在 zip 里的存放顺序是乱的**（486 个 local header，part0/00114 排在 00000 前面）。
     A9 `preloadZip` 按**归档顺序**追加帧，不按文件名排序 → 即使动画能播，也是乱帧。
   - 没有 boot image 时，zygote 每次开机都会用 dex2oat **现编 boot image**：日志 110 s 开始，**605 s 堆损坏 SIGABRT 崩溃**，
     白烧 ~8 分钟之后才 prune 再重编全部应用。这一段是"十几分钟"里最大的一块。
   - `/system/framework/arm.noimage` 并不是空壳：它是**一整套完整的 boot image**（62 个文件 36.45 MB，10-06 18:23，
     与 framework.jar 同批构建），只是目录被人为改名禁用。恢复它（+ `-Xnorelocate`）是根治"每次换镜像重编译"的最短路径。

---

## 1. Q1：A9 bootanimation 的全部退出路径（源码级清单）

源码：`frameworks/base/cmds/bootanimation/`（android-9.0.0_r61）
- BootAnimation.cpp：https://android.googlesource.com/platform/frameworks/base/+/android-9.0.0_r61/cmds/bootanimation/BootAnimation.cpp
- bootanimation_main.cpp：同目录 `bootanimation_main.cpp`
- BootAnimationUtil.cpp：同目录 `BootAnimationUtil.cpp`

| # | 触发条件 | 代码位置 | 结果 |
|---|---|---|---|
| 1 | `debug.sf.nobootanimation > 0` 或 `ro.boot.quiescent > 0` | `BootAnimationUtil.cpp:28-36`（`property_get("debug.sf.nobootanimation", ...)` 在 **:30**；`ro.boot.quiescent` 在 **:35**）→ `bootanimation_main.cpp:149-151` | `main()` 打印 `"boot animation disabled"` 后 **直接 return 0**，进程活不到 1 帧。**这就是我们 1.7 s 退出的原因。** |
| 2 | 三个路径都找不到 zip | `BootAnimation.cpp:337-345`（`/product/media`、`/oem/media`、`/system/media`）+ `:343 if (mZipFileName.isEmpty())` | 退化成 `android()` 内置 AOSP logo 动画，**无限循环**（`do { ... checkExit(); } while (!exitPending())`，`:390-426`），不算退出。 |
| 3 | zip 打不开 / desc.txt 解析失败 / preloadZip 失败 | `movie()` `:797-801`（`loadAnimation()` 返回 NULL 即 `return false`） | `threadLoop()` 返回 false → 线程结束 → 进程退出（屏幕停在上一帧）。 |
| 4 | `service.bootanim.exit` 被置 1 | `checkExit()` `:429-437`（只在渲染循环里每帧调用）→ `requestExit()` | `playAnimation()` 在 `:895`（`c` 前缀 part 之外立刻 break）和 `:969`（无限 part 至少播完一轮）退出；带 `c` 前缀（playUntilComplete）的 part 会把当前一轮播完再退。随后 `init()` 返回 false → 线程退出 → 进程退出。 |
| 5 | SurfaceFlinger 死亡 | `binderDied()` `:131-139` | `kill(getpid(), SIGKILL)`。 |
| 6 | 关机动画（`sys.powerctl` 非空） | 构造函数 `:107-113` 决定 `mShuttingDown`，改用 shutdownanimation.zip | 与开机无关。 |

**没有其它退出条件**：没有超时、没有 `sys.boot_completed` 检查、没有帧数上限。也就是说——
**只要 `service.bootanim.exit` 不被置 1，`c 0 0 partX` 的无限 part 会一直播下去。**

谁置 `service.bootanim.exit`：
- `WindowManagerService.performEnableScreen()`：`WMS.java:3446-3451` 置 **1**；
  该方法要等 `mSystemBooted`（由 `bootAnimationComplete()` 在 AMS 收尾时调用，`WMS:3362-3374`）或显示 boot message 才走。
  之后 WMS 每 **200 ms** 轮询 bootanim 进程是否退出（`WMS:338`、`:3495-3500`）才真正开屏。
- `SurfaceFlinger::bootFinished()` 也置 1（`frameworks/native/.../SurfaceFlinger.cpp:477`），但它是 WMS 在开屏时
  发 `BOOT_FINISHED` binder 事务触发的（`WMS:3462-3472`），在 bootanim 退出之后。
- `startBootAnim()` 置 **0** 后再 `ctl.start`（SurfaceFlinger 初始化时）。

**时序结论（对我们最有利的一条）**：
`SystemServer.startOtherServices()` 里 `mPackageManagerService.updatePackagesIfNeeded()`（`SystemServer.java:1001-1005`）
发生在 AMS 收尾 `finishBooting()` **之前**，而 `service.bootanim.exit=1` 要等 `finishBooting → bootAnimationComplete`。
所以 **dexopt 的整个窗口都被开机动画覆盖**——这正是"原生安卓"的视觉体验来源。

现场对照（`mainline_recon/z1_boot_mainline_20261105_131920.log`）：
```
1714 [ 40.592206] init: Received control message 'start' for 'bootanim' ... surfaceflinger
1715 [ 40.595968] init: starting service 'bootanim'...
1716 [ 42.332307] init: Service 'bootanim' (pid 757) exited with status 0
```
镜像内二进制核对：`/system/bin/bootanimation`（24,764 B，stripped，DT_NEEDED libOpenSLES/libandroidfw）
`strings` 含 `debug.sf.nobootanimation`、`ro.boot.quiescent`、`boot animation disabled`、`playing clip for part%d`
→ 与 AOSP 9 这份源码**逐字对应**，可以放心按上面的结论推行为。

---

## 2. Q2：Android 9 到底还有没有"Optimizing app N of M"

### 2.1 有代码，但触发条件很窄

调用链（全部 A9 源码，行号为 android-9.0.0_r61）：

```
SystemServer.startOtherServices()
  └─ PMS.updatePackagesIfNeeded()                       SystemServer.java:1001-1005
       ├─ causeUpgrade   = isUpgrade()
       ├─ causeFirstBoot = isFirstBoot() || mIsPreNUpgrade
       ├─ causePrunedCache = VMRuntime.didPruneDalvikCache()      PMS.java:9016-9021
       └─ performDexOptUpgrade(pkgs, mIsPreNUpgrade /*showDialog*/, ...)   PMS.java:9033
            └─ if (showDialog) AMS.showBootMessage("Optimizing app X of Y", true)   PMS.java:9149-9157
                 └─ WMS.showBootMessage()                        WMS.java:3506-3530
                      └─ PhoneWindowManager.showBootMessage()    PhoneWindowManager.java:7649+
                           └─ 创建 ProgressDialog mBootMsgDialog（原生"转圈+文字"对话框）
```

关键闸门：
- `mIsPreNUpgrade = mIsUpgrade && ver.sdkVersion < Build.VERSION_CODES.N`　**PMS.java:2616**
- `mIsUpgrade = !Build.FINGERPRINT.equals(ver.fingerprint)`　**PMS.java:2604-2608**（`ver` 来自 `/data/system/packages.xml`
  的 `<version>` 元素，`Settings.java:3140` 读 `sdkVersion`）
- WMS 侧还有一道：`mAllowBootMessages = showBootMsgs`（`WMS.java:932`），SystemServer 传的是 **`!mFirstBoot`**
  （`SystemServer.java:861-863`）→ **首次开机永远不显示 boot message**。

字符串资源仍在 A9 里（`core/res/res/values/strings.xml`）：
`:3018 android_upgrading_apk = "Optimizing app"`、`:2995 "Phone is updating…"`、`:3009 "Optimizing storage."`。

**结论**：
- 从 M/N 以前升级 → 显示原生 "Optimizing app N of M" 对话框（且必须非首次开机）。
- **我们的场景（新刷 system 镜像 / dalvik 缓存被 prune / 首次开机）→ 不显示，一行都不会打。**
  这不是配置问题，是 A9 的设计：这种情况只靠开机动画。

板上核实：从 v6 镜像里 dump 出 `/framework/oat/arm/services.vdex`（8.7 MB），
`strings` 命中 `showBootMessage`、`updatePackagesIfNeeded` → 我们的 ROM 确实带这套代码（没有被裁掉）。

### 2.2 SurfaceFlinger::showBootMessage 的版本演进

我对 `frameworks/native/services/surfaceflinger/SurfaceFlinger.cpp` 逐版本 grep：

| 版本 | showBootMessage |
|---|---|
| 4.1.2_r1 / 4.2.2_r1 / 4.3_r3 / 4.4.4_r2 / 5.1.1_r38 / 6.0.1_r81 / 7.0.0_r36 / 7.1.2_r36 / 8.0.0_r36 / 8.1.0_r81 / 9.0.0_r61 | **0 处** |

即：**SurfaceFlinger 里早就没有 showBootMessage 了（4.1 起就没有；更早的 Gingerbread 源码路径已迁移，未能取到逐行证据，
但结论不变）**。A9 的 boot message 是 **system_server 自己的一块普通窗口（ProgressDialog）**，不经过 SurfaceFlinger。
`SurfaceFlinger::bootFinished()` 在 A9 里只剩 `property_set("service.bootanim.exit","1")`（`:454-487`）。

所以"让 SF 显示进度"这条路在 A9 上根本不存在；能显示进度的只有 system_server 的对话框，而它被上面两道闸门锁死。

---

## 3. Q3：让等待期有可视反馈的候选方案对比

| 方案 | 做法 | 工作量 | 风险 | 像不像原生 | 结论 |
|---|---|---|---|---|---|
| **A. 修正现有动画，让它一直播** | 去掉 `debug.sf.nobootanimation`（v6 已做）+ 用无限循环 part | 极小（已做） | 低 | 高（A9 原生行为） | **必做**。但现有 zip **帧顺序乱**，会播成乱帧，需重打包或换自制 zip。 |
| **B. 自制 bootanimation.zip（"正在优化系统"）** | desc.txt + 自绘 PNG，240×320 循环 | 小（1 人 1~2 h） | 低 | 高（同一机制，原生观感） | **主方案**（见 §4）。 |
| **C. 消灭等待：恢复/预置 boot image** | 把 `/system/framework/arm.noimage` 恢复为 `arm`；zygote 加 `-Xnorelocate`；或至少加 `-Xnoimage-dex2oat` 跳过注定失败的那次编译 | 中（改镜像 + 改 ramdisk init.rc） | 中（历史上 patchoat 曾"冻死"） | —（不是 UI 方案，但让等待从十几分钟降到几分钟） | **强烈建议做**（见 §5），与 B 并行。 |
| **D. 兜底：往 /dev/graphics/fb0 画静态图** | 开机早期由 init 服务写 fb0 | 小 | 中（simplefb 格式/pitch；SF 起来后被覆盖） | 低（静态、无进度） | **不推荐作为主方案**；只能覆盖 SF 起来前（<40 s）的黑屏，救不了 dexopt 窗口。 |
| **E. 伪造 pre-N 升级，逼出原生对话框** | 改 `/data/system/packages.xml` 的 `<version sdkVersion="23" fingerprint="...">`，使 `mIsUpgrade=true && sdkVersion<24` → `showDialog=true` | 中 | **高**（会跑一整套 pre-N 迁移路径；且首次开机 `showBootMsgs=false` 一定不显示，只能在非首次开机用） | 最高（真·原生 "Optimizing app 12 of 45"） | **实验性**，可以单独做一次验证，别进正式方案。 |
| **F. 改 PMS 源码** | 把 `showDialog` 改成 `causeFirstBoot \|\| causePrunedCache` | 大 | 中 | 最高 | 需要 Android 构建树；本机只有 device tree（`z1a9_gpt/`），**当前不具备条件**。 |

---

## 4. Q3(b) 详解：bootanimation.zip 格式、打包与坑（A9 源码实测）

### 4.1 加载规则（源码依据）
- 路径优先级：`/product/media/bootanimation.zip` → `/oem/media/…` → `/system/media/…`（`BootAnimation.cpp:66-72, 337-345`）。
  我们是 system-as-root，所以镜像内是 `/media/bootanimation.zip`，板上就是 `/system/media/bootanimation.zip`。
  当前 v6 里该文件 **565,395 B**，与 teammate 说的字节数一致。
- `desc.txt` 语法（`parseAnimationDesc()` `:629-694`）：
  ```
  <width> <height> <fps>
  <c|p> <count> <pause> <folder> [#RRGGBB] [clockX clockY]
  ```
  - `c` = `playUntilComplete`（收到 exit 也把当前一轮播完）；`p` = 立即中断。
  - `count = 0` → **无限循环**，直到 `service.bootanim.exit` 被置 1。
- **PNG 必须 STORED（不压缩）**：`preloadZip()` `:697-760` 里只接受 `kCompressStored`，否则报
  `"bootanimation.zip is compressed; must be only stored"` 并跳过该帧 → 打包必须 `zip -0`。
- **帧顺序 = zip 归档顺序**，不是文件名排序：`preloadZip` 用 `zip->nextEntry()` 迭代、按遇到顺序 `part.frames.add()`。
  → 必须按帧号顺序把文件加入 zip。
- 帧尺寸取 `desc` 的 width/height（可被每 part 的 `trim.txt` 覆盖，`:748-775`）；每帧**居中**绘制到屏幕上，
  背景色取 desc 第 5 字段（默认黑）。
- 逐帧 `eglSwapBuffers`，帧率由 desc 的 fps 控制（`:960-975`）；SwiftShader 软渲染下建议 **10–15 fps**，
  240×320 全屏、总大小 ≤ 3 MB 比较稳妥。

### 4.2 现有 zip 的实测缺陷（要修）
```
local-header 数量 486；part0 前 10 个：part0/00114.png, 00045, 00070, 00068, 00099, 00058, 00109, 00088, 00014, 00012 …
part0 sorted? False   part1 sorted? False
```
→ 现有动画即使播出来也是**乱序帧**。重做/重打包时必须显式排序。

### 4.3 推荐的自制动画
- 画布 **240×320**（全屏），黑底 `#000000`，居中显示"正在优化系统…" + 一个不确定进度动画（转圈/往返进度条）。
  想更像原生 A9 的 ProgressDialog：左侧一个转圈、右侧两行字。
- `desc.txt`：
  ```
  240 320 15
  c 1 0 part0
  c 0 0 part1
  ```
  `part0` = 1 帧（首帧立现），`part1` = 循环帧（例如 24 帧 = 1.6 s 一轮）。
- 打包（Python，保证顺序 + stored）：
  ```python
  import zipfile, os
  with zipfile.ZipFile('bootanimation.zip','w') as z:
      z.write('desc.txt', 'desc.txt', compress_type=zipfile.ZIP_STORED)
      for d in ('part0','part1'):
          z.write(d, d+'/', compress_type=zipfile.ZIP_STORED)   # 目录项可选
          for n in sorted(os.listdir(d)):
              z.write(os.path.join(d,n), d+'/'+n, compress_type=zipfile.ZIP_STORED)
  ```
- 打包后自检（顺序 + stored）：
  ```python
  import zipfile; z=zipfile.ZipFile('bootanimation.zip')
  print([i.filename for i in z.infolist()][:5])            # 必须 00000 起递增
  print({i.compress_type for i in z.infolist()})           # 只能是 {0}（ZIP_STORED）
  ```
- 放进镜像：替换 `/media/bootanimation.zip`（debugfs `rm` + `write`，或 loop 挂载），保持 `0644`，
  SELinux 标签 `u:object_r:system_file:s0`（现有文件就是这个标签，替换时别丢）。
- 也可用 `trim.txt` 做局部动画（只画一块区域），能显著省 CPU，但全屏黑底+文字更接近原生观感。

---

## 5. Q3(c) 详解：为什么每次都要重编，以及怎么根除

### 5.1 现场事实（全部可复核）
1. 镜像里 **没有** `/system/framework/arm/`；`/framework/oat/arm/` 里倒是有 services.odex 等预编译产物。
2. **`/system/framework/arm.noimage/` 是一整套完整 boot image**：62 个文件、**36.45 MB**：
   `boot.art` 2,351,104 B、`boot-framework.art` 8,892,416 B、`boot.art.rel` 220,304 B、`boot.oat` 4,004,452 B、
   `boot-framework.oat` 13,064,512 B，以及 boot-core-libart/-ext/-okhttp/-telephony-common/... 的 .art/.oat/.art.rel；
   `*.vdex` 是指向 `/framework/boot*.vdex` 的符号链接（`boot-framework.vdex` 18,980,788 B 等都在 `/framework` 下）。
   全部 **mtime 2026-10-06 18:23**，与 `framework.jar`（18:19）、`core-oj.jar`（10-05 18:32）同批 → **大概率就是为这套 system 编的**。
   历史上 11-01 因为 "patchoat 冻死（41s）" 把目录改名禁用（`out/STATUS_20261031.md:11`）。
3. 无 image 时的启动路径（`art/runtime/gc/space/image_space.cc`，A9）：
   - `CreateBootImage()` `:1498`：Step 0.b 先 `VerifyImage`（跑 patchoat --verify），失败 → **`PruneDalvikCache`** `:1549-1550`；
   - 没有 system image → **Step 3 `GenerateImage(cache_filename, ...)`** `:1655+`，即用 `/system/bin/dex2oat` 现场编到
     `/data/dalvik-cache/arm/boot.art`（参数见 `GenerateImage()` `:100-158`：`--image=`、每个 boot jar 一个 `--dex-file=`、
     `--oat-file=`、`--base=0x…`、`--image-classes`…）；
   - 失败 → 再 prune，返回"无 image"，zygote 纯解释 + JIT 跑。
4. 板上日志：`110.59 s` dex2oat 开始编 boot classpath（`Skipping class … from telephony-common.jar previously found in framework.jar`），
   **`605.21 s` SIGABRT**：`dex2oat` 在 `ConcurrentCopying` 的堆校验里 abort（日志 `:5737-5762`）。
   → 这 **~8 分钟**是纯浪费，每次开机都重演。
5. prune 之后：`PackageManagerService.updatePackagesIfNeeded()` 因为 `VMRuntime.didPruneDalvikCache()==true`（`PMS:9021`）
   对**所有包**重跑 dexopt（日志里 150+ 次 dex2oat，685 s ~ 1062 s），这才是"十几分钟"的主体。
6. `pm.dexopt.*`：v6 的 `/system/build.prop` 与 `/etc/prop.default` 里**都查不到** `pm.dexopt.first-boot`
   （只有 `dalvik.vm.dex2oat-filter=speed`，build.prop:82）。但 A9 的 `PackageManagerServiceCompilerMapping.checkProperties()`
   要求这些属性必须非空（缺失会抛 `IllegalStateException`），所以运行时值一定来自 **boot ramdisk 的 `default.prop`**
   （本次未解包核对；上板 `getprop pm.dexopt.first-boot` 可一眼确认）。无论取值如何，A9 的 `quicken` 都是"无 AOT 代码"级
   （`runtime/compiler_filter.cc` 中 `IsAotCompilationEnabled(kQuicken)==false`），能省时间但不解决"每个包都要跑一遍"的问题。

### 5.2 三个改法（按风险从低到高）

**C1（低风险，立省 ~8 分钟）：zygote 加 `-Xnoimage-dex2oat`**
- 依据：`art/runtime/parsed_options.cc:223-225` 定义 `-Ximage-dex2oat / -Xnoimage-dex2oat`；
  `runtime.cc:1172` `image_dex2oat_enabled_ = GetOrDefault(Opt::ImageDex2Oat)`；`image_space.cc:1535,1565,1633` 用它跳过 GenerateImage。
- 改哪里：boot ramdisk 的 init.rc，zygote 那一行（本地样本 `out/boot_extracted/rd/init.rc:1201`）：
  ```
  service zygote /system/bin/app_process -Xzygote -Xnoimage-dex2oat /system/bin --zygote --start-system-server
  ```
- 效果：不再白跑 8 分钟的 image 编译（也不再触发那次 SIGABRT）；应用重编仍在（因为 prune 已发生）。
- 风险：低。ART 选项是公开支持项；不改任何二进制。

**C2（根因，最值得试）：恢复 boot image + 禁止重定位**
- 做法：镜像里把 `/framework/arm.noimage` **改名回 `/framework/arm`**；zygote 加 `-Xnorelocate`
  （`parsed_options.cc:217-219`；`Runtime::ShouldRelocate()` = `must_relocate_ && CanRelocate()`，`runtime.h:137-139`；
  对应 `image_space.cc` 的 Step 2.a：`found_image && has_system && !relocate` 直接 `Load` 系统镜像，跳过 patchoat）。
- 预期：zygote 直接加载 10-06 那套 boot.art；`VerifyImage` 通过 → **不 prune** → `didPruneDalvikCache()` 为 false →
  `updatePackagesIfNeeded()` 直接 return（非首次开机）→ 应用不再全量重编；开机从十几分钟降到几分钟。
- 风险：中。11-01 记录过 patchoat 冻死；加 `-Xnorelocate` 正是为了绕开它。最坏情况：镜像校验不过 → 回到今天的 prune 路径，
  等于没改（但要防 patchoat 卡住，所以必须先上 `-Xnorelocate` 再恢复目录）。
- 必须单变量上板验证 + 保留回滚镜像。

**C3（host 预生成）：需要同版本 dex2oat / 完整构建树**
- 标准做法是构建时 `WITH_DEXPREOPT` 由 host `dex2oat` 生成 `boot.art/.rel`（`build/make/core/dex_preopt_libart.mk`）。
- 本机没有 Android 全树（只有 device tree `z1a9_gpt/`），也没有同版本 host dex2oat → **当前不可行**。
  备选：在板子上用 `/system/bin/dex2oat` 生成后把 `/data/dalvik-cache/arm/boot.art` 拷回镜像
  （`out/sf6_files/z1_bootimg_build.sh` 就是这个思路，但它没有加 `--image-classes` 之外的坑位校验，且受 5.1-4 的崩溃影响）。
  优先级低于 C2（C2 已经有现成一套，只差改名）。

---

## 6. 推荐方案（主 + 兜底）

### 主方案（可视反馈）
1. **保持 `debug.sf.nobootanimation` 被注释**（v6 已做，`build.prop:113-120`）。
2. **换一个自制的 `/system/media/bootanimation.zip`**：240×320、15 fps、黑底 + "正在优化系统…" + 转圈，
   `c 1 0 part0` + `c 0 0 part1`（无限循环），**全部 STORED、按帧号顺序**打包。
3. 兜底：即使 zip 损坏，A9 会自动退化成内置 AOSP logo 无限循环（`BootAnimation.cpp:343`），依然有画面反馈。

### 并行根因方案（缩短等待）
4. 先上 **C1**（`-Xnoimage-dex2oat`）——立省 ~8 分钟，零风险。
5. 再单变量试 **C2**（`arm.noimage → arm` + `-Xnorelocate`）——成功则等待从十几分钟降到几分钟，
   开机动画照样覆盖这段时间。

### 上板验收判据（逐条可复核）
- `logcat -b all | grep bootanim`：`starting service 'bootanim'` 之后**不再出现 1.7 s 的 exited**，
  直到 `WMS` 收尾（`sys.boot_completed` 前后）才退出。
- 屏幕：从 SF 起来（~40 s）到桌面出现，全程有动画（不是黑屏、不是乱帧）。
- `logcat | grep -E "Optimizing|Preemptively|AnimationShownTiming"`：
  - 主方案成功 → 有 `BootAnimation: AnimationShownTiming start time`；
  - C1 成功 → 不再有 110 s 起、605 s 崩的那次 dex2oat；
  - C2 成功 → 不再出现 `Preemptively pruning the dalvik cache.`，且总 boot 时间显著下降。
- `ps -A | grep bootanim` 在 dexopt 期间应能看到进程存活。

### 如果一定要"原生 Optimizing app N of M"
- 只有两条路：改 PMS（需构建树）或伪造 pre-N 升级（改 `/data/system/packages.xml` 的 `<version>`，
  且必须非首次开机）。后者可作为一次性验证，**不建议进正式镜像**。

---

## 7. 回滚
- 系统镜像：`out/z1_fix3_system/system_fix3.img` 等既有回滚链不受影响；本次只动 `/media/bootanimation.zip`、
  可选 `/framework/arm*` 目录名、ramdisk `init.rc` 一行——三项都可原地还原。
- ramdisk 改动前备份 `out/boot_extracted/rd/init.rc`（项目既有规范：改前带时间戳备份到 `bridge_backups/`）。

---

## 8. 证据索引（复核用命令）

| 结论 | 复核方式 |
|---|---|
| 1.7 s 退出 = nobootanimation | `grep -a -n "bootanim" mainline_recon/z1_boot_mainline_20261105_131920.log`（:1714-1716）；`strings /system/bin/bootanimation` |
| zip 内容/desc | `debugfs -R "cat /media/bootanimation.zip" system_v6.img` → 解 zip 看 desc.txt |
| 帧乱序 | 解析 local header 顺序：part0 首个是 00114.png，`sorted? False` |
| arm.noimage 全量 | `debugfs -R "ls -l /framework/arm.noimage"`（62 文件 / 36.45 MB） |
| 无 /framework/arm | `debugfs -R "ls /framework"` → 只有 `arm.noimage` |
| image 编译崩溃 | 日志 `:5737-5762`（dex2oat SIGABRT, ConcurrentCopying 堆损坏） |
| prune 原因 | 日志 `:1975-1977`；源码 `image_space.cc:1542-1550` |
| A9 无原生优化页 | `PMS.java:2616,9016-9033,9149-9157`；`WMS.java:932,3506-3530`；`SystemServer.java:861-863,1001-1005` |
| SF 无 showBootMessage | 对 4.1.2/4.2.2/4.3/4.4.4/5.1.1/6.0.1/7.0/7.1.2/8.0/8.1/9.0 的 SurfaceFlinger.cpp grep = 0 |
| ROM 带 showBootMessage | `debugfs dump /framework/oat/arm/services.vdex` → `strings | grep showBootMessage` |

## 9. 来源
- BootAnimation.cpp (A9): https://android.googlesource.com/platform/frameworks/base/+/android-9.0.0_r61/cmds/bootanimation/BootAnimation.cpp
- bootanimation_main.cpp (A9): https://android.googlesource.com/platform/frameworks/base/+/android-9.0.0_r61/cmds/bootanimation/bootanimation_main.cpp
- BootAnimationUtil.cpp (A9): https://android.googlesource.com/platform/frameworks/base/+/android-9.0.0_r61/cmds/bootanimation/BootAnimationUtil.cpp
- PackageManagerService.java (A9, showBootMessage/showDialog): https://android.googlesource.com/platform/frameworks/base/+/android-9.0.0_r61/services/core/java/com/android/server/pm/PackageManagerService.java
- WindowManagerService.java (A9, bootanim exit=1 / showBootMessage): https://android.googlesource.com/platform/frameworks/base/+/android-9.0.0_r61/services/core/java/com/android/server/wm/WindowManagerService.java
- PhoneWindowManager.java (A9, mBootMsgDialog): https://android.googlesource.com/platform/frameworks/base/+/android-9.0.0_r61/services/core/java/com/android/server/policy/PhoneWindowManager.java
- SystemServer.java (A9, updatePackagesIfNeeded 调用点): https://android.googlesource.com/platform/frameworks/base/+/android-9.0.0_r61/services/java/com/android/server/SystemServer.java
- art image_space.cc (A9, prune/GenerateImage): https://android.googlesource.com/platform/art/+/android-9.0.0_r61/runtime/gc/space/image_space.cc
- art parsed_options.cc (A9, -X[no]relocate / -X[no]image-dex2oat): https://android.googlesource.com/platform/art/+/android-9.0.0_r61/runtime/parsed_options.cc
- build/make dex_preopt_libart.mk (A9, boot.art/.rel 生成): https://android.googlesource.com/platform/build/+/android-9.0.0_r61/core/dex_preopt_libart.mk
