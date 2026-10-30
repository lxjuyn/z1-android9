# Z1 媒体素材部署报告（播放器 / TTS / FM）

> 2026-10-30 媒体素材部署轮。基线 `out/z1_r5_system/system.img.r5i_final`（sha `35616bfd…`）。
> **本轮未产出新 system.img**（按派单要求，避免与 r5j 撞车）。产出=可重放脚本 + 素材 + 本报告。
> 所有结论均来自本机离线取证；**未上板、未刷写、未碰设备**。凡未确定处均显式标注。

---

## 一句话结论

**能放音频的路已经打通到"只差一次刷写"——但要装的不只是播放器，还必须有 1.7 MB 的 RenderScript 支撑库，
否则 Eleven 会在启动瞬间 FC；TTS 只对英文有意义（PicoTts 无中文，且本机所有离线素材都无中文）；
FM 判定为"需大工程"，卡点是内核 FM 驱动完全不存在（无源码可移植）。**

推荐动作：跑 `out/z1_media_stage/deploy_media.sh`，产出叠加了媒体层的 system.img，与 r5j 合批刷。

---

## 0. 部署包（本报告的交付物）

| 件 | 路径 |
|---|---|
| 可重放脚本 | `/home/lxj/claude/6572/out/z1_media_stage/deploy_media.sh` |
| 素材树（按 Android 路径排好） | `/home/lxj/claude/6572/out/z1_media_stage/stage/` |
| 素材校验和 | `/home/lxj/claude/6572/out/z1_media_stage/SHA256SUMS` |
| 入库副本（脚本+校验和，无二进制） | `z1a9_gpt/scripts/media_deploy/` |

脚本用法（**必须 sudo**，非原地、幂等、带备份、写完回读）：

```bash
cd /home/lxj/claude/6572
sudo bash out/z1_media_stage/deploy_media.sh                    # r5i -> r5i.media.img
sudo bash out/z1_media_stage/deploy_media.sh <in.img> <out.img> # 显式
sudo bash out/z1_media_stage/deploy_media.sh --no-tts           # 省 TTS
sudo bash out/z1_media_stage/deploy_media.sh --no-rs            # 对照实验（Eleven 必 FC）
```

**脚本已实测跑通**（不是"写完就交"）：本轮在 `/tmp` 临时镜像上完整跑过 3 遍，
覆盖全新部署 / 重复部署（幂等）/ `--no-tts` / `--no-rs` / `--no-audio` 五条路径，
产出镜像 `e2fsck -fn` clean、逐项 sha256 独立 debugfs 回读一致。临时镜像已删。

**已知一处脚本瑕疵（不影响功能）**：阶段 5 的"权限断言"注释说在 ro 挂载期间执行，
但 `chkperm` 循环实际排在 `umount` 之后，所以该循环没有真正生效（`$MP$p` 已不挂载 → `[ -e ]` 假 → 直接 return）。
属主/权限本身由 `install -o 0 -g 0 -m 0644` 在阶段 3 保证，已用 debugfs 独立验过（644/0/0），故无功能影响。留待下次修正。

---

## 1. 逐项清单

镜像根 == `/system`（本镜像 system-as-root，镜像内**没有** `/system` 目录；证据：`debugfs -R "stat /system"` → `File not found`，
而 `stat /etc/init/audioserver.rc` 存在，板载日志 `init: Parsing file /system/etc/init/audioserver.rc...`）。
故下表"镜像内路径"在设备上就是 `/system/<路径>`。

| # | 镜像内路径 | 大小 (B) | 权限 | SELinux 标签 | 来源 | 必要性 |
|---|---|---|---|---|---|---|
| 1 | `/priv-app/Eleven/Eleven.apk` | 8,679,786 | 0644 0:0 | system_file | LOS16 base `jflt_ota_v2` | **核心** |
| 2 | `/priv-app/Eleven/lib/arm/librsjni.so` | 23（软链） | 链接 | 无（见 §5） | LOS16 base | **核心** |
| 3 | `/lib/librsjni.so` | 59,984 | 0644 0:0 | system_file | LOS16 base | **核心** |
| 4 | `/lib/libRSSupport.so` | 1,730,048 | 0644 0:0 | system_file | LOS16 base | **核心** |
| 5 | `/lib/libblasV8.so` | 986,392 | 0644 0:0 | system_file | LOS16 base | 保险（§播放器选型） |
| 6 | `/priv-app/Recorder/Recorder.apk` | 2,599,105 | 0644 0:0 | system_file | LOS16 base | 高 |
| 7 | `/priv-app/AudioFX/AudioFX.apk` | 3,014,629 | 0644 0:0 | system_file | LOS16 base | 中 |
| 8 | `/app/PicoTts/PicoTts.apk` | 23,379 | 0644 0:0 | system_file | **los14**（LOS16 已删） | 低（仅英文） |
| 9 | `/lib/libttspico.so` | 240,572 | 0644 0:0 | system_file | los14 | 低 |
| 10 | `/lib/libttscompat.so` | 22,324 | 0644 0:0 | system_file | los14 | 低 |
| 11-14 | `/tts/lang_pico/{en-US_lh0_sg,en-US_ta,en-GB_kh0_sg,en-GB_ta}.bin` | 777,396 / 650,668 / 584,436 / 412,248 | 0644 0:0 | system_file | los14 | 低 |
| 15 | `/vendor/etc/media_profiles_V1_0.xml` | 14,235 | 0644 0:0 | **vendor_configs_file** | LOS16 base | 装饰性（§4） |

**净增约 19.6 MB**（镜像本来 129.6 MB 空闲块，`dumpe2fs` 实测 33,177×4096 B）——**空间充足，不是瓶颈**。

### 依赖是否闭合

| 依赖 | 结论 | 证据 |
|---|---|---|
| Eleven 的 NEEDED/符号 | **闭合** | 独立验证：`librsjni.so`/`libRSSupport.so`/`libblasV8.so` 三个候选件对目标 `/lib` 全部符号逐个核对（含**符号版本**），`FAILURES=0`（24/195/53 个未定义符号全解析）。三者均带 `BIND_NOW`，全部符号必须加载期解析——没有惰性绑定兜底 |
| `libjnigraphics.so`（`librsjni.so` 硬 NEEDED） | **不是问题**（曾疑） | base 15,952 B vs target 15,944 B 的 8 字节差是系统性重建差（每个重建件都有同型差异），**非 API 变化**；SONAME 一致；`librsjni.so` 需要的 3 个符号（`AndroidBitmap_getInfo/_lockPixels/_unlockPixels`）在 target 里全为 `Base` 无版本全局——与 `librsjni.so` 的无版本 `UND` 精确匹配 |
| `libRSSupportIO.so` | **不需要** | base 镜像自己也没有它（debugfs 确认），而 base 的 Eleven 能跑；`librsjni.so` dlopen 它失败时是 log-and-continue（`Couldn't load libRSSupportIO.so, librsjni version: %d`，日志级别 6），app 侧对应串是 `Unable to load libRSSupportIO.so, USAGE_IO not supported` 即显式降级 |
| `/etc/public.libraries.txt` / `ld.config.txt` | **无需修改** | base 与 target 这两个文件**逐字节相同**（`fd836f0a…` / `f26fd0c9…`），且**都没列** `librsjni.so`。base 自身的可用配置就是先例。机制也不是清单生效：base 的 Eleven 自带 app-local 软链 `lib/arm/librsjni.so -> /system/lib/librsjni.so`，`System.loadLibrary("rsjni")` 从 app 自己目录命中，根本不走系统命名空间。且本镜像 `ld.config.txt` 是 **pre-O 式**（`namespace.default.isolated = false`，零 `classloader-namespace` 段） |
| priv-app 权限白名单 | **已覆盖，无需动** | `debugfs -R "cat /etc/permissions/privapp-permissions-lineage.xml"` 里三条**恰好 1:1 对应**我们选的三个 app：`org.lineageos.eleven`→`WRITE_MEDIA_STORAGE`；`org.lineageos.recorder`→`CAPTURE_SECURE_VIDEO_OUTPUT`,`CAPTURE_VIDEO_OUTPUT`；`org.lineageos.audiofx`→`CHANGE_COMPONENT_ENABLED_STATE`,`MEDIA_CONTENT_CONTROL`,`MODIFY_AUDIO_ROUTING`。三个 APK 全部用**平台密钥**签名（指纹 `C8:A2:E9:BC:…:19:2A:B8`，与镜像内 `Settings.apk` **逐字节相同**），signature 级授予也成立 |
| `ro.control_privapp_permissions` | 未设（值在运行时算） | 所有 prop 文件里 0 命中。AOSP 默认按 `Build.IS_DEBUGGABLE` 推（`LOG` 或 `ENFORCE`）。**对本批无实义**——三个包名都已在白名单里 |

### 不需要动的（明确排除）

- `audio_effects.xml`：**A9 仍认 `.conf`，不是缺口**（详见 §4）
- manifest.xml 补条目：三个 app 都不引入 HIDL 服务
- `/vendor/etc/init/hw/` 目录：历史已澄清无需建

---

## 2. 播放器选型对比（核心）

### 候选总表

| | **Eleven (LOS16)** | Eleven (LOS14) | **Factory Music.apk** | Recorder | Snap |
|---|---|---|---|---|---|
| 路径 | `out/z1_media_stage/src_los16/priv-app/Eleven/` | `los14/system/priv-app/Eleven/` | `out/z1_media_stage/src_factory/app/Music.apk` | `src_los16/priv-app/Recorder/` | LOS16 `/priv-app/Snap` |
| 大小 | 8,679,786 | — | 1,126,192 | 2,599,105 | 8,450,512 |
| package | `org.lineageos.eleven` | — | `com.android.music` | `org.lineageos.recorder` | — |
| minSdk / targetSdk | **26 / 28** | — | **19 / 19** | 24 / 28 | — |
| 部署总成本 | 8.68 MB + **1.7 MB RS** | 同 | 1.13 MB（若可用） | 2.60 MB | 8.45 MB |
| RenderScript | **必需** | 必需 | 不需要 | 不需要 | — |
| A9 兼容 | **判定可行** | 需另验 | **判定不可用** | 判定可行 | 是相机，不做 |
| 结论 | ✅ **选它** | ✗ 不用 | ✗ 弃 | ✅ 选 | ✗ 无相机硬件 |

### 为什么选 LOS16 的 Eleven 而不是 LOS14 的

1. **ABI 与平台精确同源**：target 就是 LOS16/jfltxx 血统（`ro.build.flavor=lineage_z1`，`ro.build.version.sdk=28`）。LOS16 Eleven 是 `minSdk=26, targetSdk=28` 的 A9 原生件，LOS14 的是 `platformBuildVersion 25`（7.1.2）件。
2. **零 API 移除风险（逐字节码验证，非字符串猜）**：把 Eleven 的 22,775 条平台引用（classes2.dex 全覆盖）逐条解析到目标镜像自己的 `framework.jar`，沿**父类链+接口链**解析 —— **`TRULY MISSING METHODS: 0` / `TRULY MISSING FIELDS: 0`**。33 个"owner 类缺失"经核对**全部是 APK 自带**（support-v7/v8 等）。API 25→28 的移除项（`RemoteControlClient` / `Notification.setPriority` / `RemoteController` / `getExternalStorageDirectory`）在 app 代码里**逐个 ABSENT**；它走的是现代的 `MediaSession` + `MediaStyle` + `NotificationChannel`，全部存在。
3. **privapp 白名单已按 `org.lineageos.eleven` 写好**（见上）。LOS14 的 Eleven 包名同为 `org.lineageos.eleven`，这点不构成区分，但配合第 1 条仍以 LOS16 为准。
4. **一个坑要记**：`out/z1_media_stage/eleven_extract/` 这个前轮半成品 **不是 LOS16 件**（`AndroidManifest.xml` sha `534bc606…`、`classes.dex` sha `907a0eac…` 对不上），实为 los14 产物。**用 `src_los16/`，别用 `eleven_extract/`。**

### ⚠ 关键发现：Eleven 必须搭配 RenderScript 支撑库，否则启动即 FC

这是本轮最重要的技术结论，**推翻了"只搬一个 APK 就行"的朴素预期**。

**机制（两轮独立证伪/证实，第二轮为对抗性复核）**：

```
HomeActivity.onCreate            tries=0
 → SlidingPanelActivity.onCreate tries=0
 → BaseActivity.onCreate         tries=0, 无条件调 ImageFetcher.getInstance
 → ImageFetcher.getInstance      tries=0
 → ImageFetcher.<init>           tries=0
 → ImageWorker.<init>            tries=0  ← 整个方法没有 try 表
 → android.support.v8.renderscript.RenderScript.create()   @insn 0x00f
```

- `ImageWorker.<init>` 的 `tries_size = 0` —— **不是"invoke 落在 try 范围外"，是根本没有 try 表**。
- 前面的 `if-nez` 守卫是"非空才跳"，而 `sRenderScript` 在 `<clinit>` 里被显式置 **null**，所以**首次构造必走 create**。
- 缺库时 `internalCreate` 在 `System.loadLibrary('rsjni')` 处抛 **`UnsatisfiedLinkError`（是 `Error`，不是 `Exception`）**；其 handler 是 `throw`（不吞）。另一条 `catch Exception` 分支才包成 `RSRuntimeException`（`→ RuntimeException`），但缺 `.so` 这条走的是前者。
- **全 dex 内 `catch Ljava/lang/Error;` = 0 处**；链路各方法全无 catch；app 从不注册 `setDefaultUncaughtExceptionHandler`（classes2.dex 0 命中）。

**为什么"只把 `librsjni.so` 换个名"不行**：target 里**已有** `librs_jni.so`（注意下划线位置不同！），SONAME `librs_jni.so`，注册的是**平台类** `android/renderscript/RenderScript`；而 support 版 `librsjni.so` SONAME 不同、注册的是**support 类** `android/support/v8/renderscript/RenderScript`。两者是**不同 soname + 不同 Java 类**，必须**按原名并存**，且 support 代码会显式 `System.load("/librsjni.so")`。

**副作用**：`MusicPlaybackService` 声明了 `android:process=":main"`（全 manifest 唯一的 `android:process`），
它的 `onCreate` 也走 `ImageFetcher.getInstance` → 同样无 catch。所以缺 RS 库时**播放进程也会挂**，不只是 UI。
换言之：**没有 RS 库，Eleven 不只是"不模糊"，是完全不可用。**

**模糊功能本身是优雅降级的**（这点要说清）：`BlurBitmapWorkerTask.doInBackground` 有
`try[0x066,0x090) → catch RuntimeException @0x096`，9 个 RS 调用全在保护内，失败时 log `Cannot blur image. ` 并返回原图。
坏的是**构造函数**，不是模糊。

### 为什么弃用原厂 Music.apk

原厂件看起来是"免费午餐"（1.13 MB、就是设备出厂的播放器、与平台**同签名**、无 RS 依赖），
但它 **`classes.dex` 引用 21 个 `Lcom/mediatek/*` 类型，其中 11 个 APK 内未定义**：
`MediaPlayerEx` / `OmaDrmClient` / `OmaDrmUiUtils` / `PluginManager` / `Plugin` / `Xlog` /
`IVoiceCommandManagerService` / `IVoiceCommandListener`（含 `$Stub`）。
目标镜像 **无 `mediatek-framework.jar`**，且在 `framework.jar`/`ext.jar`/`core-oj.jar` 全部 dex 里
`com/mediatek` **0 命中**。而调用点**无保护**：

- `MusicLogUtils.{d,e,i,v,w}` → `Lcom/mediatek/xlog/Xlog;`，**tries=0**，被 **264 个方法 / 332 处**调用（整个 app 的日志门面）
- `MediaPlaybackService$MultiPlayer.setDataSourceImpl` → `MediaPlayerEx.setContextForSecureFlag`，所在 try 只捕 `IOException`/`IllegalArgumentException`/`IllegalStateException`，**无 catch-all**
- `MusicBrowserActivity.onPrepareOptionsMenu` → `Extensions.getPluginObject`，无保护

`NoClassDefFoundError` 是 `Error`，全都漏出去 ⇒ **启动即挂 + 首次播放再挂**。
抢救需要 `mediatek-framework.jar`（没有）或 baksmali 掉 122 处调用点。**判定不可用，弃。**

> 顺带发现：**`MusicFX.apk` 零 MTK 引用、零 RS 引用**，是原厂 app 集里唯一干净可复用件（
> 但我们的 AudioFX 走 LOS16 更好，故不用）。

### `libblasV8.so` 装不装？

**默认装（保险，+986 KB）**。证据：`libRSSupport.so` 里 `loadBLASLib` 全库**只有一个调用者**
（`RsdCpuScriptIntrinsicBLAS::invokeForEach`），dlopen 失败时返回 0 且只 log（**不中断**），
模糊路径**完全不碰 BLAS**。但 Eleven 的 dex 里确实有 16 处 `ScriptIntrinsicBLAS` 引用，
**无法离线判定**可达 UI 路径是否会走到它 ⇒ 装上直到上板 logcat 证明无用。
若要省这 986 KB，把 `put lib/libblasV8.so` 那行注释掉即可（RS 模糊不受影响）。

---

## 3. TTS 判定

### 结论：**低优先，且只对英文有意义** —— 中文这条路在本机现存素材里不存在

**PicoTts 无中文，三重独立证据**：
1. 语音库目录只有 6 个拉丁语系（`de-DE / en-GB / en-US / es-ES / fr-FR / it-IT`），**无 zh/cmn/chi**
2. `libttspico.so` 二进制里编译进的 locale 表同样只有这 6 个（`strings | grep -oE '^[a-z][a-z]-[A-Z][A-Z]'`）——**数据与代码两处都没有**
3. APK manifest 声明的 7 个 `tts_lang.*` category 里**无中文**

**最强反证在设备自己身上**：原厂 4.4.2 固件在 `ro.product.locale.language=zh` / `region=CN` 的机器上
**出厂就装了 PicoTts 且无中文**（原厂 `/app/PicoTts.apk` 36,344 B + `/tts/lang_pico/` 12 个 .bin，
同样只有那 6 个语系）。**厂商有全套源码、有中国区机器，却给中文听力机配了个不中文的 TTS** ——
这强烈说明**原厂听力机的音频内容本来就是预录音文件，不是 TTS**。

**本机离线无中文 TTS 出路**：唯一有真普通话能力的是 `espeak-ng`
（实测 `espeak_SetVoiceByName('cmn')` 返回 0=成功，数据含 `cmn_dict` 1.65 MB），
但**只有 x86_64 构建、无 Android/ARM 版、无源码**（`/usr/src`、apt 缓存里都没有），
ARM 交叉编译器倒是有。要做得先下载源码（联网）+ 自写 Android TTS 引擎壳 ⇒ **是立项目不是部署**。

### 英文 TTS：可行且便宜，但排在音频之后

native 链接**干净**：两个 `.so` 对目标 `/lib` 的未定义符号**全解析**（`libstlport`/`libicuuc`/`libgnustl`/`libbinder` 全 ABSENT；
`libttspico.so` 需要 `libc++.so`（target 有，636,332 B）而非工厂版的 `libstdc++`）。
manifest 只用 `SynthesisRequest`/`SynthesisCallback`/`TextToSpeechService`（API 25 稳定、API 28 仍在）。
APK 自包含（`SynthProxy` 在自己 dex 里，不在 framework）。语音数据与工厂件**逐字节相同**，两个来源都行。

**⚠ 但必须先验音频**。链路是 `PicoTts → SynthesisCallback(PCM) → AudioTrack → AudioFlinger → primary HAL → ASoC → 喇叭`，
**PicoTts 的职责止于"给出一段 PCM"**。而且失败是**静默的**：`out_write()` 失败时 AudioFlinger 只记写错误，
**不向引擎回传**，于是 `speak()` 仍返回 `SUCCESS`、`onDone()` 仍触发、logcat 干净 —— **喇叭不响**。
所以**绝不能用"app 报成功"证明 TTS 好了**。顺序不能颠倒：先证明音频出声，再上 TTS。

**本轮默认只装 en-US/en-GB 两个语系**（省 4.3 MB；机器目标语言是中文，英文够做"英文跟读"）。
需要全 6 语系就改脚本里那个 for 循环的列表。

### 部署清单（若做）

见 §1 第 8–14 行。额外两件可选：
- `/etc/public.libraries.txt` 追加上 `libttspico.so` / `libttscompat.so` —— 保险（A8+ 命名空间收紧）。
  base 的 LOS14 自己也没列而能用，且本镜像是 **pre-O 式 ld.config**（见 §1），故**默认不改**。
- 补一个 `handheld_core_hardware.xml` 声明 `android.software.tts` + `android.hardware.audio.output`。
  本镜像**没有** `handheld_core_hardware.xml`，所以 `hasSystemFeature(FEATURE_TTS)` 预期为 false，
  以该 feature 门禁的 app 会在到引擎前就拒绝。**本镜像不装任何 TTS app**，故暂不需要。

---

## 4. FM 收音判定

### 结论：**(c) 需大工程 —— 卡点是内核 FM 驱动完全不存在，且无源码可移植**

**硬证据**：
- **mainline 树里零 FM 驱动**：`fork_linux/drivers/misc/mediatek/connectivity/` 下 `find -iname '*fm*'` 空；
  `common/core/linux/pub/` 有 `stp_chrdev_bt.c` / `stp_chrdev_gps.c` / `wmt_chrdev_wifi.c`，
  **没有 `stp_chrdev_fm.c`**；`common/core/` 只有 `wmt_ic_6620.c` / `wmt_ic_6628.c`，**无 6627 表**
- **FM 驱动源码全盘不存在**：`grep -rl 'do_fm_drv_init|MT6627fm_low_ops_register|mt_fm_probe'` 只命中 3 份**报告文档**，
  **0 个源文件**。原厂驱动只以机器码存在于 `out/boot_extracted/kernel.bin`（5 个符号各 1 命中）
- **mainline `drivers/media/radio/` 无一可改**：全是 USB/ISA/并口或 I2C 调谐器（Si470x 是 I2C/USB）。
  MT6627 FM **不是 I2C 芯片**，是 CONNSYS 协处理器内的 DSP 块，走 WMT/STP 邮箱 ⇒ 只能重写不能移植
- **交付镜像里唯一那个 FM 件是死文件**：`/lib/hw/radio.fm.mt6572.so`（42,628 B，与 los14 件 sha `9a7487db…` 逐字节相同）
  **无 loader、无兄弟库、无驱动**。原厂靠 `/system/bin/afm_server` 加载，该二进制**不在镜像里**。
  且它**没有 `HAL_MODULE_INFO_SYM` 串**，看不出有谁用 `hw_get_module("fm")` 加载它

### 好消息：CONNSYS 半边已经就绪

- `common/core/include/wmt_func.h:33-34` **硬编码** `#if 1 //defined(CONFIG_MTK_COMBO_FM)` → `#define CFG_FUNC_FM_SUPPORT 1`
- `common/core/wmt_func.c:458-463` 里 `mtk_wcn_wmt_func_on(WMTDRV_TYPE_FM)` 路径存在且已编译进
- 固件由 FM 驱动自己读 `/etc/firmware/mt6627/mt6627_fm_vN_{patch,coeff}.bin`；userspace loader
  （`6620_launcher` / `wmt_loader` / `6620_wmt_concurrency` / `6620_wmt_lpbk`）**已在镜像里**
- `/etc/firmware/WMT_SOC.cfg` 存在，但**没有 `wmt_fm_*` 键**（MT6627 共天线切换通常靠这里）

### 要什么才可行（按工时排序）

1. **内核 FM 驱动 —— 整个工程的 90%**。需要：`mtk_fm_drv.c`（MT6627 FM 核心）+ EINT/GPIO 平台胶水
   （MTK 树路径 `drivers/misc/mediatek/connectivity/fm/`），以及 `/dev/fm` chardev（major 193，
   `register_chrdev_region` + **务必 `device_create`**，本项目 `mtk_stp_gps_soc.ko` 就踩过"注册了 region 但没 device_create"的坑）、
   MTK `FM_IOCTL_*` ioctl 集、RDS 的 read/poll，以及接进 WMT 仲裁器。
   **源码渠道（按可能性）**：① 另一台 MT6572/MT6582 Android 4.4 BSP dump；② GitHub 上 MT6572 设备的
   `android_kernel_mediatek` GPL 释放；③ 本地 `wifi_recon/drsami`、`aquaris_bq`（**已查：无 FM**）。
   **若找不到源码 ⇒ 对着 `kernel.bin` 逆向 ioctl ABI + HMI vtable 重写，是"周"级不是"天"级。**
2. **音频通路（决定"能不能听见"，不决定"能不能搜台"）**：mainline 里已有 AWB/I2S-in 骨架
   （`mt6572-mt6323.c:8`、`mt6572-afe-pcm.c:6-7,76-77` 及 352/400 行已编程 `AFE_CONN2_I2S_IN_O05/O06`），
   但 `sound` 节点是 **disabled**（注释说要等 pwrap write-deny 解除），且 `mt6323.c` 的 DAPM widget
   **没有 FM/LINE 输入路径**。**这条与全项目的 P0-A"音频真出声"是同一个依赖** —— 音频不通，FM 有声也无从谈起。
3. **硬件前提：耳机就是天线**。两个 FMRadio app 的 dex 里都有
   `isAntennaAvailable` / `HEADSET_PLUG` / `NoAntennaDialog` / `no_headset.xml` ——
   **不插 3.5mm 耳机，app 直接拒绝运行**。所以 FM **没有耳机就无法验证**。`accdet` 驱动两边都有。
4. **userspace 栈（简单，约 5 个文件 + 1 个 app）**：`libfmjni.so` + `libfmmt6627.so`（**或**已有的
   `radio.fm.mt6572.so`，二者等价）+ `libfmcust.so` + `/etc/fmr/mt6627_fm_cust.cfg` + `/etc/firmware/mt6627/*.bin`
   + `FMRadio.apk`（需进 `/priv-app` + privapp 权限 + SELinux）。注意 **`libfmmt6627.so`/`libfmcust.so` 都
   `UND __xlog_buf_printf`**，需要 `libxlog.so`（**镜像里已有**，r5 的 A4 项补的）。
   ⚠ **两族不能混**：工厂族是 API-17/`libstdc++`，LOS14 族是 API-25/`libc++`，`libfmjni.so` 的 NEEDED 不同。
5. **AOSP 路走不通**：镜像里 `services.jar` 有 `com.android.server.broadcastradio.BroadcastRadioService`、
   `/lib` 有 `android.hardware.broadcastradio@1.0/1.1.so`，**但 `/vendor/bin/hw` 无 `*broadcastradio*` 服务、
   manifest 里 `fm|broadcast` 0 条** ⇒ AOSP tuner 路是死的，只能走 MTK app 路。

### 离线无法判定的两件（诚实列出）

- mainline 的 CONNSYS 固件 blob（`WIFI_RAM_CODE_SOC`，160,464 B）里**是否含 MT6627 的 FM patch handler**。
  本机是 6627(FM)/6620(WiFi) 混合，mainline 只有 `wmt_ic_6620.c`。需要上板读 `/dev/wmtdetect` 或 dmesg。
- LOS14 的 FMRadio app 调 `AudioManager.registerFmRadioListener(...)`，**该方法的字符串在 LOS14 与交付
  两个 framework 里都 0 命中**（只在 app 自己 dex 里）—— 推测走反射，是优雅降级还是崩**无法离线判定**，需上板 logcat。

### 顺带核对的低价值项（两条都判"不用做"）

- **`media_profiles_V1_0.xml` 缺 → 装饰性日志噪声，无功能影响。** `MediaProfiles` 在本镜像里
  **只有 native C++ 类**（`libmedia.so` 导出 `_ZN7android13MediaProfiles11getInstanceEv` 等），
  **Java 类整镜像 0 命中**（`grep -abo 'Landroid/media/MediaProfiles;'` 对 943 MB 全镜像 → 未找到）。
  `MediaRecorder.setAudioEncoder/setVideoEncoder/setOutputFormat` 反汇编后各**只有 1 个调用 `__android_log_print`**，
  **零 MediaProfiles 引用**（纯 flag 设置器）。板载日志里该告警正好 2 次，且上下文是
  **Zygote 预载类的静态初始化**（`preloaded 6534 classes` 旁），**无任何相邻失败**，
  `CamcorderProfile`/`MediaRecorder` 在整份日志 0 命中。音频编码由 `/vendor/etc/media_codecs_google_audio.xml`
  （`OMX.google.aac.encoder` 等）管，**与 MediaProfiles 是两回事**。**默认仍顺手装上**（一条 14 KB 的写入，
  零风险，消掉告警），但**它不解决任何真实功能**。

- **`audio_effects.xml` 缺 → A9 仍认 `.conf`，是 fallback 不是缺口。** 反汇编取证：
  `libaudiopolicyservice.so` `.rodata@0x10928` 有字面量 `Failed to load XML effect configuration, fallback to .conf`，
  `@0x10963` `/vendor/etc/audio_effects.conf`、`@0x10982` `/system/etc/audio_effects.conf`；
  且它仍**导入** legacy 解析器 `config_load`/`config_node`/`config_find`/`config_free`，
  而 `libcutils.so` 仍**导出** `config_load` ⇒ **是活的调用路径，不是死串**。
  `libaudioflinger.so` 与此**完全无关**（effect-config 相关串 0 命中，也不 link `libeffectsconfig.so`；
  涉及格式判断的是 `libaudiopolicyservice.so`）。板载日志逐字：
  `E EffectsConfig: Could not parse effect configuration in any of the default locations.`
  → `W AudioPolicyEffects: Failed to load XML effect configuration, fallback to .conf`（各 2 处）。
  而 `.conf` 解析器**唯一可能的错误串** `Effect config is partially invalid` 在整份日志 **0 命中** ⇒ 回落成功。
  目标 `/etc/audio_effects.conf` 与 base 的**逐字节相同**（sha `550ca26f…`），它引用的 6 个 effect 库
  （`libbundlewrapper`/`libreverbwrapper`/`libvisualizer`/`libdownmix`/`libldnhncr`/`libdynproc`）
  **全部在 `/vendor/lib/soundfx/`**（8 个件）⇒ **零缺失**。**结论：不改。**
  （若哪天真要切 XML：base 的 1,806 B `audio_effects.xml` 可直接拷，但会需要
  `libqcomvoiceprocessing.so` —— 那是 target 里**唯一缺**的 effect 库，会静默丢掉 `<effect name="aec">`。
  另外 target 的 `.conf` 还多接了 `libdynproc.so`，base 的 XML 里没有。**收益为负，不做。**）
  ⚠ 顺带：真正的音频拦路虎**不是** effects —— 板载是三连
  `MTK Audio loadLibrary() failed to open dlopen failed: library "libaudio.primary.default.so" not found`
  → `loadHwModule() error -22` → `Failed to open primary output`。而该 `.so` **是存在的**
  （`/vendor/lib/libaudio.primary.default.so` 20,844 B，自身依赖全可解析）——
  失败在 `audio.primary.mt6572.so` 做 dlopen 时的**命名空间/搜索路径**（日志：
  `Loading /vendor/lib/hw/audio.primary.mt6572.so from current namespace instead of sphal namespace`）。
  **这是 r5g/r5i 音频线要解决的问题，与 effects 配置无关。**

---

## 5. 未决风险与诚实说明

| # | 风险 | 状态 |
|---|---|---|
| 1 | **RS 库与目标 libc 的符号版本** | 已尽力核到**符号版本**一级，`FAILURES=0`。但**未上板实测**——静态解析通过不能保证 dlopen 成功（TEXTREL/重定位等运行时因素无法离线排除） |
| 2 | **Eleven 的 FC 是静态推断，非实测** | 两轮独立字节码分析（第二轮 20,403/20,403 方法 100% 解析覆盖，且修正了三个自身解析器 bug）结论一致。但**未在设备上观察到 FC 本身** |
| 3 | **`libblasV8.so` 是否真被用到** | 离线无法判定；默认装上作保险。上板后 grep `Failed to load the BLAS lib` |
| 4 | **Eleven 在 240×320 屏上的 UI 表现** | **未验**，需上板看 |
| 5 | **音频本身能不能出声** | **这是最大的未决项，且完全不在本轮 scope 内**。`audio.primary.mt6572.so` 是手写 tinyalsa PoC（8,196 B），`grep -c "mixer_"` = **0**（从不碰任何 ALSA mixer 控制），默认 48,000 Hz 而 `audio_policy` 的 mixPort 只声明 `samplingRates="44100"`。**播放器/录音/TTS 三者全部依赖它** |
| 6 | **Eleven 的软链没打 SELinux 标签** | `lsetxattr` 对 ext4 fast symlink **无效**（实测 setfattr 静默不生效）。base 镜像里该软链带 `system_file` 标签，但那是 `mke2fs -d` 建镜像时的产物。**permissive 下无标签无害**（cmdline `androidboot.selinux=permissive`，实测） |
| 7 | **`/tts` 目录无先例可循** | 老镜像里 `/tts` 不存在，`plat_file_contexts` 里也无 `tts` 规则；A9 sepolicy 里 `tts_file` 类型 **0 命中** ⇒ 用 `system_file`（对齐 `plat_file_contexts:45 /system(/.*)?`）。**新目录、无先例，这是最"发明"的一条标签**，但 permissive 下无风险 |
| 8 | **privapp 强制模式未知** | `ro.control_privapp_permissions` 未设，AOSP 按 `ro.debuggable` 运行时推。**对本批无实义**（三个包名都在白名单） |
| 9 | **TTS 中文不可能** | 不是风险，是**确定结论**。若产品必须中文朗读，需新引擎（espeak-ng 移植或联网 TTS），属立项 |
| 10 | 脚本权限断言循环位置有误 | 见 §0。无功能影响 |

### 明确"绝不猜"的几处

- 上述 1/2/3/4/5 全部标为**未上板实测**，不要当已验证结论引用。
- FM 的两处（CONNSYS 固件是否含 FM handler；`registerFmRadioListener` 反射是否崩）标为**离线无法判定**。
- Eleven 在 A9 上的**首次运行**行为，我只能证明"缺 RS 库必崩"与"补上后**零**未解析符号"，
  **不能**证明"补上后一定起得来"。

---

## 6. 上板验证步骤

### 前提

```bash
# 0. 先确认音频通了（顺序不可颠倒 —— 见 §3 静默失败说明）
#    这一步不通，后面 1/2/3 的"有没有声音"全无意义
dmesg | grep Z1Audio
cat /proc/asound/cards          # 期望出 card0
ls -l /dev/snd/pcmC0D0p         # 期望存在
tinyplay /sdcard/test.wav       # 期望真出声
```

### 1. 播放器（本轮主目标）

```bash
# 放一个测试音频（WAV 最稳，MP3 也可）
adb push test.mp3 /sdcard/Music/
adb shell am start -n org.lineageos.eleven/.ui.activities.HomeActivity
```

| 期望 | 失败分叉 |
|---|---|
| HomeActivity 打开，能看到曲目列表 | **立刻闪退** → `adb logcat -d \| grep -E 'UnsatisfiedLinkError\|RSRuntimeException\|librsjni'`。若见 `UnsatisfiedLinkError` ⇒ RS 库没落盘或没生效，查 `ls -l /system/lib/librsjni.so /system/lib/libRSSupport.so`（应 59,984 / 1,730,048） |
| 点播放，喇叭出声 | 出声但**界面正常** ⇒ 只是音频 HAL 问题，转 §6.0 |
| 专辑封面背景**不模糊**（纯色）但不崩 | 这是**预期内的优雅降级**？不 —— 装了 RS 库就该模糊。若 logcat 有 `Cannot blur image. ` ⇒ RS 库在但不工作，查 `libblasV8.so` 与 `/lib/libRSSupport.so` 的 dlopen |
| — | 若 logcat 见 `Failed to load the BLAS lib` ⇒ 确认 `libblasV8.so` 落地；若确认无用可下轮回退省 986 KB |

```bash
# 磁带式取证（先抓再判，别只看一眼）
adb logcat -d > /tmp/media_eleven.log
grep -E 'UnsatisfiedLinkError|RSRuntimeException|Cannot blur|eleven' /tmp/media_eleven.log
```

### 2. 录音机 + 音效

```bash
adb shell am start -n org.lineageos.recorder/.RecorderActivity
# 期望: 能录能放；失败看 logcat 里 MediaRecorder / MediaProfiles 相邻错误
adb shell dumpsys media.audio_flinger | head -40     # AudioFX 是否有 session
```

### 3. TTS（英文）

```bash
adb shell settings get secure tts_default_synth      # 期望 com.svox.pico
adb shell am start -a android.speech.tts.engine.CHECK_TTS_DATA -n com.svox.pico/.CheckVoiceData
```

> **最重要的判据**：**用耳朵**。`speak()` 返回 SUCCESS、`onDone()` 触发、logcat 干净 —— **三者都不能证明出声了**（见 §3）。
> `TextToSpeech.speak()` 之后**必须实际听**。

### 4. 回归（确认没弄坏既有功能）

```bash
adb shell getprop ro.hardware.egl                  # 应仍是 mesa（r5h 成果）
adb shell dumpsys SurfaceFlinger | grep -i renderer # 应 Mali400
# 触摸/按键/WiFi 按 out/HANDOFF_20261030.md §4 走
```

---

## 7. 本轮改了/建了哪些文件

### 新建（产出）

| 路径 | 说明 |
|---|---|
| `/home/lxj/claude/6572/out/z1_media_stage/deploy_media.sh` | 可重放部署脚本（幂等/非原地/带备份/回读断言），已实测跑通 5 条路径 |
| `/home/lxj/claude/6572/out/z1_media_stage/stage/` | 素材树，按 Android 路径排好（15 个文件 + 1 软链） |
| `/home/lxj/claude/6572/out/z1_media_stage/SHA256SUMS` | 素材校验和（22 条） |
| `/home/lxj/claude/6572/out/z1_media_stage/src_los16/priv-app/{Eleven,Recorder,AudioFX}/` | 从 LOS16 base 抽出的 APK 源 |
| `/home/lxj/claude/6572/out/z1_media_stage/src_factory/app/{Music,MusicFX,SoundRecorder}.{apk,odex}` | 从原厂 `1/system.img` 抽出的对照件（Music 判定不可用） |
| `/home/lxj/claude/6572/out/MEDIA_DEPLOY.md` | 本报告 |
| `z1a9_gpt/scripts/media_deploy/{deploy_media.sh,SHA256SUMS}` | 入库副本（**仅脚本+校验和，无二进制**，遵守仓库规则） |

### 修改

**无。** 本轮**没有改动镜像**（未产出新 system.img）、**未改既有交付件**、**未刷写、未碰设备**。
唯一对项目树的"写"是上述新建文件。

### 备份

- `/home/lxj/claude/6572/bridge_backups/system.img.r5i_final.pre_media_20261030_124805.bak`
  —— 内容即原 r5i（sha256 `35616bfd…` 已回读确认一致）。脚本每次运行都会建带时间戳的备份（同 inode 硬链，不额外占盘）。

### 临时文件（已清理）

`/tmp/z1_media_test.img`、`/tmp/z1_media_idem.img`、`/tmp/z1_media_prev.img`、`/tmp/z1_flag_test.img`、
`/tmp/elev`、`/tmp/rs`、`/tmp/rs2`、`/tmp/rsstk`、`/tmp/rdc` —— **全部已删**。
测试过程中产生的 6 个 `/tmp/*.pre_media_*.bak` 已删（释放 1.7 GB）。

---

## 8. 待合批说明

按派单要求，**本轮不产出新 system.img**，等主会话决定合批顺序。当前状态：

- 脚本默认输入 = `out/z1_r5_system/system.img.r5i_final`（sha `35616bfd…`）
- 若 r5j（WiFi HAL + 充电器 + GPS）已出片，把 r5j 的产出当输入喂给本脚本即可叠加：
  `sudo bash out/z1_media_stage/deploy_media.sh <r5j.img> <r5j_media.img>`
- **两者无文件路径冲突**（r5j 动 WiFi/充电/GPS 的 lib/rc、本层动 app/priv-app/lib 媒体件 + `/lib/librsjni.so` 等），
  但**都要写 `/build.prop` 之外各自的 SELinux 标签**，且 r5j 会动 `/vendor/lib`——
  本层也写 `/vendor/etc/media_profiles_V1_0.xml`（**不同目录**，无冲突）。
  唯一需注意：**先跑哪个都行，但不要并发跑**（同一镜像两次 mount rw）。

刷写命令（合批后）：

```bash
cd /home/lxj/claude/6572
Z1_VERIFY=0 tools/flash_with_retry.sh android <合批后的img> out/z1_r5_system/system.img.r5h_final 8
```

---

*报告生成于 2026-10-30。所有"确认"项均给到了可复现的命令与原始输出出处；
所有"未上板"项均已显式标注，请勿当作已验证结论引用。*
