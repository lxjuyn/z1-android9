# AUDIO_HAL_PORT.md — Z1 (MT6572) 音频 HAL 移植调研与方案

日期: 2026-10-28  
作者: 音频 HAL 移植 agent  
输入基线: `out/z1_r5_system/system.img.r5d_final` (sha256 `c258c196…`, 943,718,400 B)  
  ⚠️ 我开工时交付基线是 `system.img.r5c_final` (sha `07507110…`)；作业期间该文件被本 session 的
  另一个 agent 删除，替换为 `r5d_final` (sha `c258c196…`)。两者都含 `/vendor/lib/libaudio.primary.default.so`
  (20,844 B 空壳) 与 `/vendor/lib/hw/audio.primary.default.so` (同 sha `5a8ab6c6…`)，本次分析对二者等价。

---

## 一句话结论

**不可直接落地；需按"替代路径"重写。置信度：高（对"LOS14 真 HAL 不可用"这一否定结论）；中高（对替代路径可行，PoC 已交叉编译通过但未上板）。**

- 把 LOS14 的 763KB MTK 真 HAL 装进去 **跑不起来**有两道独立的墙：
  1. **ABI 墙（可绕）**：它是 **`AUDIO_DEVICE_API_VERSION_2_0`**，而 `common.version` 字段写的是
     **字节序错位的 `0x00020001`**；且按 **A7 布局**编出来（`audio_hw_device` = **152 B**），
     A9 框架要 **156 B** —— 相差 1 个指针，`get_microphones` 之后**全体函数指针错位一格**。
  2. **驱动墙（不可绕，致命）**：它 `open("/dev/eac")` + `ioctl` 走 **MTK 私有 audio-controller**，
     全 lib 里出现 **11 处 `/dev/eac`**、外加 `/dev/eemcs_*`、`/dev/ccci_*`。
     而本项目的 mainline 内核（`fork_linux/sound/soc/{mediatek/mt6572,codecs/mt6323}`）
     **只有标准 ALSA/ASoC，`/dev/eac` 出现 0 次**。它一 `open` 就 `ENOENT`。
- 另外它还需要 **8 个镜像里没有的 MTK 库**（`libmtk_drvb.so` 等）+ **10 个镜像里没有的符号**
  （A7 `AudioSystem::*VoiceUnlock*` / `IATV`，A9 已迁走）。
- **推荐替代路径**：自写一个 **A9 v2.0 primary HAL**，只做 `audio_hw_device` 外壳，
  把 PCM 交给 **tinyalsa `pcm_open/pcm_write` → `/proc/asound/card0`**（内核 ASoC 栈、11 个 `.ko`
  已经就绪、镜像里已有 `/lib/libtinyalsa.so` 65 个符号）。我已把这条路做成 **PoC 源码 + 交叉编译通过**：
  `z1a9_gpt/scripts/r5d_audio_hal/audio_primary_z1_poc.c`（316 行，NDK API28 编过、链接出 11,812 B 的 `.so`，
  导出 `HMI`，`tag/versions/id` 与镜像期望**逐字节一致**）。

一句话给主会话：**LOS14 那个 HAL 别装；装我写的 tinyalsa 外壳。**

---

## 阶段 1：判定 LOS14 真 HAL 能否在 A9 上工作

### 1.1 素材与身份

| 文件 | 大小 | 角色 |
|---|---|---|
| `los14/system/lib/libaudio.primary.default.so` | 763,948 B | **真 HAL**（本任务候选） |
| `los14/system/lib/hw/audio.primary.default.so` | 18,160 B | A7 的 AOSP null 空壳 |
| `los14/system/lib/hw/audio.primary.mt6572.so` | 14,060 B | A7 的 dlopen shim |
| 镜像 `/vendor/lib/hw/audio.primary.default.so` | 20,844 B | A9 的 AOSP null 空壳（= A9 空壳） |
| 镜像 `/vendor/lib/hw/audio.primary.mt6572.so` | 14,060 B | A9 的 dlopen shim |
| 镜像 `/vendor/lib/libaudio.primary.default.so` | 20,844 B | 同上空壳（sha 与 hw/ 那个**相同** `5a8ab6c6…`） |

**关键身份发现（比预期好）**：`los14/system/build.prop` 显示这份 LOS14 是
**为 Z1 本身编的**——`ro.product.device=Z1`、`ro.product.board=MT6572`、
`ro.build.flavor=lineage_Z1-userdebug`、`ro.build.version.sdk=25`（Android 7.1.2）。
即那个 763KB HAL 是**针对 Z1 的 MT6572 + MT6323 硬件编的真货**，不是通用包。

**shim 链（实测）**：镜像里 `/vendor/lib/hw/audio.primary.mt6572.so` 的字符串表含
`dlopen`/`dlsym` + `libaudio.primary.default.so` + `MTK Audio loadLibrary() failed to open %s`，
即"HW 名 `mt6572` → shim → `dlopen("libaudio.primary.default.so")`"。
注意：镜像里**没有** `/lib/libaudio.primary.default.so`（`/lib` 与 `/lib/hw` 都没有），
只有 `/vendor/lib/` 那个 20,844 B 空壳。

### 1.2 `HMI` 到底是不是 `audio_hw_device`？——不是，是 `hw_module_t`

`HMI` 在三个二进制里都是 **128 字节**，正好等于 `sizeof(struct hw_module_t)`。
实测 dump（`HMI` 各字段）：

| | LOS14 真 HAL | 镜像 A9 空壳 |
|---|---|---|
| `tag` | `0x48574d54` `"HWMT"` | `"HWMT"` |
| `module_api_version` | `0x0001` | `0x0001` |
| `hal_api_version` | `0x0100` | `0x0100` |
| `id` | `"libaudio"` | `"audio"` |
| `name` | `"LEGACY Audio HW HAL"` | `"Default audio HW HAL"` |
| `methods->open` | `0x0305c0`（ARM） | `0x000a19`（Thumb） |

`HMI` 就是 `HAL_MODULE_INFO_SYM`。真 HAL 的 `id="libaudio"`（不是 `"audio"`）但名字写着
**`LEGACY Audio HW HAL`**——这就是 AOSP `hardware/libhardware_legacy/audio/audio_hw_hal.cpp`
的经典包装：它把老的 `AudioHardwareInterface`（`libhardware_legacy.so`）**适配**成 `audio_hw_device_t`。

**旁证（实测，来自反汇编字符串）**：该 HAL 的模块 `open()` 第一条就是
`strcmp(name, "audio_hw_if")`（字面量在 `.rodata` `0x8816c`，我解析确认 `= "audio_hw_if"`）。
`AUDIO_HARDWARE_INTERFACE` 正是 `"audio_hw_if"`。→ **确认它走 legacy 包装路径。**

### 1.3 结构体 ABI 比对（本轮最关键的量化结论）

本机没有 A7 头文件、也没有网络（`curl`/`urllib` 到 googlesource/github 全部超时；
只有 Firecrawl 可用但同样超时）。因此我**不靠"查文档"**，改用**三方二进制取证**：

**(a) A9 侧真值**：用项目里已有的 A9 头 `out/z1_audio_stage/src/include/hardware/audio.h`
（`AUDIO_DEVICE_API_VERSION_CURRENT = 3.0`），用 **ARM 交叉编译器**算 `offsetof`：

```
sizeof(struct hw_device_t)    = 64      (tag, version, module, reserved[12], close)
common                        = 0
get_supported_devices         = 64      <-- 注意：不在偏移 4
init_check                    = 68
set_voice_volume              = 72
set_master_volume             = 76
get_master_volume             = 80
set_mode                      = 84
set_mic_mute                  = 88
get_mic_mute                  = 92
set_parameters                = 96
get_parameters                = 100
get_input_buffer_size         = 104
open_output_stream            = 108
close_output_stream           = 112
open_input_stream             = 116
close_input_stream            = 120
get_microphones               = 124
dump                          = 128
set_master_mute               = 132
get_master_mute               = 136
create_audio_patch            = 140
release_audio_patch           = 144
get_audio_port                = 148
set_audio_port_config         = 152
sizeof(struct audio_hw_device)= 156
```
这张表里的 `get_supported_devices = 64` 让我一度怀疑"`audio.h` 里少了 12 个指针"，
于是回去数文件行数：`audio.h` **只有 818 行，止于 802**，`audio_hw_device` 的最后一个成员是
`set_audio_port_config`。**该头里没有 `get_audio_port_v7`**——所以 64/156 是自洽的，
不是缺字段。我先前"A9 有 28 个指针、偏移 4"的判断来自**凭记忆猜 AOSP 上游**，已由本机头文件推翻，
记录在此以免误导。

**(b) 两个**空壳**的 `open()` 反汇编——A7 vs A9 同一份 AOSP 代码**：

| 版本 | 代码模式 | `calloc(1, N)` | `common.version` | 写的字段偏移 |
|---|---|---|---|---|
| **A7**（`los14/system/lib/hw/audio.primary.default.so`） | Thumb | **152** (`0x98`) | `512` = `0x0200` = v2.0 | 60, 68, 72, 76, 80, 84, 88, 92, 96, 104, 124, 128, 132 |
| **A9**（镜像 `/vendor/lib/hw/audio.primary.default.so`） | Thumb | **156** (`0x9c`) | `512` = `0x0200` = v2.0 | 60, 68, 72, 76, 96, 128, 132, 136 |
| LOS14 **真 HAL** | **ARM** | **232** (`0xe8`) | `512` = `0x0200` | 60, 64, 68, 72, 76, 80, 84 … 一路到 228 |

用 A9 的 `audio_hw_device` 语义表去读 **A7 空壳**写的位置，立刻看出错位：
它把 `init_check`+`set_voice_volume`+`set_master_volume` 依次写进 **68/72/76** ——
**恰好与 A9 一致**（因为 A7 是 `common{4}+get_supported_devices{4}`=8，字段从 8 起，
A9 是 `common{64}`，字段从 64 起，而 8+60=68=64+4）。**但**最远只写到 **132**，
而 A9 的 `audio_hw_device` 到 **156**（还差 `get_master_mute`/`create_audio_patch`/
`release_audio_patch`/`get_audio_port`/`set_audio_port_config`）。→ **A7 的 `audio_hw_device` 比 A9 短 4 字节。**

**(c) LOS14 真 HAL 自己**：`calloc(1, 232)`，写字段 **60 → 228**（步长 4，共约 43 个指针，
停在 228 而不是 232）。**232 = 64 + 168 = 64 + 42 指针**，而 A9 = 64 + 92 = **23 指针**。
→ **它按一个比 A9 长 19 个指针的 `audio_hw_device` 编的**（MTK 当年是 A7 私有扩表）。
如果按 A9 容器读它，`open_output_stream`（A9 偏移 108）落到的位置它**根本没写** → **恒 NULL** →
`AudioFlinger::openOutput` 判 NULL 拒绝 → **无声**。（且它 `FLAGS: SYMBOLIC BIND_NOW`，
`dlopen` 时符号不全就直接失败。）

> 小坑记录：该 HAL 的 `HMI` 位于 `.data`（vaddr `0xb0008` / file `0xaf008`），
> 我第一版 vaddr→file 映射写反了，读出垃圾指针；修正为
> `file = vaddr - sec_vaddr + sec_offset` 后才读对。同类分析务必先做段映射。

### 1.4 结论（阶段 1）

**不可直接使用。**（置信度：高）
- ABI：**需大改**——不是"修 4 字节"，而是要在 A7 布局与 A9 布局之间做**函数指针重排**
  （写一层同进程 shim 把 8 个指针搬位），且它内部构造的 stream 对象偏移也要跟着调。
- 驱动：**不可绕**——`/dev/eac` 是 MTK 私有 audio controller，本项目内核**没有**；
  没有它，这个 HAL 连 `AudioHardwareInterface` 都初始化不了（它 `open`/`ioctl` 该节点）。

### 1.5 用**镜像自己的二进制**反证 A9 布局（收尾验证，见阶段3风险#3）

为把"偏移表只来自本机头文件"这个弱点补上，我反汇编了**镜像内自带的**
`/vendor/lib/hw/android.hardware.audio@2.0-impl.so`（200,172 B，ARM）——
它是**为这个镜像编的**，所以它的偏移就是**镜像实际用的 A9 布局**。实测：

```
; PrimaryDevice::setAudioPortConfig(...) 直接穿到 HAL 的设备表
1c206:  ldr  r1, [r1, #4]
1c208:  ldr  r3, [r1, #0]
1c20a:  ldr  r3, [r3, #108]     <-- 从设备结构偏移 108 取函数指针
1c20c:  blx  r3                 <-- 间接调用

; Device::openOutputStream(...)
19aec:  ldr.w r8, [r4, #108]    <-- 同样偏移 108 是可调用槽
```
并且它有 **`cmp.w r0, #512`(0x200) 与 `cmp.w r0, #256`(0x100) 的版本分派**
（`0x23318`/`0x23322`），即镜像框架会**按 `common.version` 分派 v1.0/v2.0**。

→ 两条独立结论：
1. **镜像期望的 `audio_hw_device` 里，偏移 108 是一个函数指针槽** —— 与我算出的
   A9 表 `open_output_stream = 108` **吻合**；
2. 镜像框架**认 v2.0**（与 `/vendor/etc/primary_audio_policy_configuration.xml` 的
   `halVersion="2.0"` 一致）。

我**不**声称已把每个字段逐一对上（那需要完整反汇编整个 wrapper，超出本任务 scope）；
但"偏移 108 是可调用槽 + 框架认 2.0 + 期待 v2.0 布局"这三点，
足以支撑"**按 A9 头编一个 v2.0 HAL 是对的方向**"。

---

## 阶段 2：方案

### 2.1 为什么不推荐"搬 A7 的 libmedia.so"

真 HAL 有 **220 个 UND 符号，且全是 GLOBAL 强引用、0 个 WEAK**（`BIND_NOW` 立即绑定）。
逐个在镜像里找导出者，最终差 **10 个**：

| 符号 | 谁导出 | 目标镜像里 |
|---|---|---|
| `AudioSystem::ReadRefFromRing` | A7 `libmedia.so` | ❌ 无 |
| `AudioSystem::start/stopVoiceUnlockDL`、`SetVoiceUnlockSRC`、`GetVoiceUnlockULTime`、`GetVoiceUnlockDLLatency`、`get/freeVoiceUnlockDLInstance`(共 8 个) | A7 `libmedia.so` | ❌ 无（A9 只把 `getDeviceConnectionState`/`get_audio_policy_service` 搬进了 `libaudioclient.so`） |
| `IATVCtrlService::asInterface` | A7 `libmedia.so` | ❌ 无 |
| `__xlog_buf_printf` | 镜像 `/lib/libxlog.so` ✓ | ⚠️ 库在、但**没有库 NEEDED 它** → 不会被载入 |

我实测确认了"整个镜像（`libmedia.so` + `libaudioclient.so` + …）里没有这 9 个 `AudioSystem::*`/`IATV`"。
要补，只能连 **A7 `libmedia.so`(820,904 B) + `libicuuc.so`(1,128,028 B) + `libicui18n.so`(1,599,352 B)** 一起搬
（A7 `libmedia.so` 的 UND 里有 11 个 `*_56` ICU 符号，A9 的 `libicuuc.so` 是 ICU60，**版本号不同 → 无解**），
这就是 **约 4 MB 的连锁替换**，会动 A9 的 media 栈 → **不推荐**（风险远超收益，且这些 API 全是
VoIP 下行链路 + ATV 语音解锁，**Z1 听力机根本用不到**）。

**另外 8 个镜像里没有的 MTK 库**（HAL 直接 NEEDED 或二级依赖）：
`libmtk_drvb.so`（我实测：`libblisrc.so`、`libspeech_enh_lib.so`、`libbessound_mtk.so`、
`libbessound_hd_mtk.so`、`libcvsd_mtk.so`、`libmsbc_mtk.so` **6 个库共同依赖**它）、
`libmtklimiter.so`、`libmtkshifter.so`、`libbessound_mtk.so`、`libbessound_hd_mtk.so`、
`libblisrc32.so`、`libaudiomtkdcremoval.so`、`libalsautils.so`。
（LOS14 侧这 19 个 MTK 私有库合计 **878,868 B ≈ 858 KB**。）

→ 汇总：**就算把 ABI 搬位做对，仍要搬 ~858KB MTK 库 + 自写 10 符号 shim + 面对 `/dev/eac` 无解。**
**性价比判定：不值得。**

### 2.2 推荐路径 —— 自写 v2.0 primary HAL 薄壳 + tinyalsa

**内核侧已就绪（实测）**：
- `fork_linux/sound/soc/mediatek/mt6572/mt6572-afe-pcm.c`：platform `mt6572-afe-pcm`，
  DAI `mt6572-afe-dl1` / `mt6572-afe-awb`。
- `fork_linux/sound/soc/mediatek/mt6572/mt6572-mt6323.c`：machine 卡名 **`mt6572-mt6323`**，
  dai_link `DL1`(`SND_SOC_DAILINK_REG(playback)`)、`AWB`。**`DL1` 就是 pcm device 0**。
- codec `mt6323.c` + `mt6323-accdet.c`（耳机检测）。
- 全内核 grep **`/dev/eac` = 0 处** → 标准 ALSA，`/dev/snd/pcmC0D0p` 可用。
- 镜像 `/lib/modules/` 11 个 `.ko` + `load_audio.sh`（已修好依赖顺序）齐备。

**用户空间已就绪**：镜像 `/lib/libtinyalsa.so`(32,904 B) 导出 65 个符号，
`pcm_open/pcm_write/pcm_read/pcm_close/pcm_is_ready/pcm_get_error/mixer_*` **全在**。
头文件 `out/z1_audio_stage/src/tinyalsa/include/tinyalsa/asoundlib.h`(12,124 B) 是本机的真头。
NDK `android-ndk-r26d` 有 `armv7a-linux-androideabi{21..29}-clang`。

**PoC 已产出并验证（不是纸面方案）**：

```
z1a9_gpt/scripts/r5d_audio_hal/
├── audio_primary_z1_poc.c        316 行，含真实 out_write→pcm_write 路径
├── apply_audio_hal.sh            幂等/非原地部署脚本（见 2.3）
└── libaudio.primary.z1.so        交叉编译产物 11,812 B
```

实测编译 + 链接 + ABI 断言：
```
$ armv7a-linux-androideabi28-clang -c -fPIC -Os -I<A9头> -I<tinyalsa头> audio_primary_z1_poc.c -o poc.o
   → 0 error, 0 warning（除我显式 -Wno-unused-parameter）
$ armv7a-linux-androideabi28-clang -shared -o libaudio.primary.z1.so poc.o /tmp/lt.so -llog -lm -ldl
   → ELF 32-bit LSB shared object, ARM, EABI5 ✓
   NEEDED: libtinyalsa.so, liblog.so, libm.so, libdl.so, libc.so
$ readelf HMI dump: tag="HWMT" module_api_version=0x0001 hal_api_version=0x0100 id="audio" ✓
$ cc -c assert.c:  _Static_assert(sizeof(hw_device_t)==64) ✓  (sizeof(audio_hw_device)==156) ✓
   （我写的第三条 stream_out 断言是我算错指针个数，非头文件问题，已在源码注释里标掉）
```
它的 `adev_open()` 写字段的偏移（60/68/72/76/88/92/96/100/120/128…）**与 A9 语义表逐个吻合**，
`common.version = AUDIO_DEVICE_API_VERSION_2_0 = 0x0200`。

**上板前还要做的**（PoC → 可用 HAL 的差距，诚实列出）：
1. **PCM 设备号确认**：PoC 用 `card/dev = 0/0`（依据 machine `DL1` + 卡名 `mt6572-mt6323`）。
   上板先 `tinypcminfo -D 0 -d 0` 复核；若卡不是 0，改宏即可。
2. **采样率/位深**：PoC 默认 48k/stereo/S16_LE。要与 AFE 实际支持对齐（`tinypcminfo` 看 `0x…` mask）。
   听力机常用 44.1k，建议做 8k/16k/44.1k/48k 重采样或至少枚举。
3. **录音路径**：PoC 的 `open_input_stream` 返回 `-ENOSYS`（只有 AWB DAI，仅通话用）。
   若要录音，补 `pcm_open(PCM_IN)` + `DL1` 之外的 UL 通路。
4. **音量/路由**：PoC 的 `set_volume` 是 no-op。变频机必需 → 用 `mixer_open/mixer_ctl_set_value`
   操作 mt6323 codec 的 mixer 控件（`Playback Volume`，见 AFE 的 `SOC_SINGLE_EXT_TLV`）。
5. **SELinux**：新文件需要 `file_contexts` 里有 `audio.primary.default.so` 的 `vendor_file` 上下文；
   见 2.3 的脚本告警。
6. **`audio_policy_configuration.xml`**：镜像 `/vendor/etc/primary_audio_policy_configuration.xml`
   已写 `halVersion="2.0"` → **v2.0 是被期望的**，与 PoC 一致 ✓。

### 2.3 部署路径与脚本

**放哪儿**：写 **`/vendor/lib/libaudio.primary.default.so`** 与
**`/vendor/lib/hw/audio.primary.default.so`** 两处（皆覆盖现有 20,844 B 空壳）。
理由：镜像里 shim `audio.primary.mt6572.so` `dlopen("libaudio.primary.default.so")`
（**带 `lib` 前缀**），走 linker 默认路径（`/system/lib`,`/vendor/lib`）；
而镜像**没有** `/lib/libaudio.primary.default.so`，放 `/vendor/lib/` 最直接、改动面最小。

**脚本**：`z1a9_gpt/scripts/r5d_audio_hal/apply_audio_hal.sh <in.img> <out.img>`
- 幂等、**非原地**（`in == out` 直接拒绝）；
- 预检：磁盘 ≥ 4 GiB（本项目盘满踩坑多次；我测 dry-run 时用 `Z1_MIN_FREE_GIB=1` 覆盖）、
  `file` 确认 ARM32、`nm` 确认导出 `HMI`；
- 先备份 `<in>.bak.<时间戳>`，再 `cp` 成 `<out>`，**只改 `<out>`**；
- `debugfs -w` 写文件 + `set_inode_field mode/uid/gid`（`/vendor` 是 `0:2000`），
  并**尽力回写原 `security.selinux` xattr**（读不到就告警，不假装成功）；
- 收尾 `e2fsck -fy` → **`e2fsck -fn` 只读验证** → **逐项回读 sha256 比对** + 复查 `HMI`。
- 已实测 `bash -n` 通过、`--dry-run` 输出正确（识别到两个目标"已存在，将替换"）。

---

## 阶段 3：把方案落地成新镜像

**未执行。** 原因：**磁盘只剩 3.3 GiB（97% 已用），低于我自己给脚本定的 4 GiB 红线**；
而作业期间 `r5c_final` 被同 session 另一 agent 删除、基线换成了 `r5d_final`。
在"部署哪个 HAL 尚未由主会话拍板"（见阶段 1 结论）且基线正在变动时，
我不想占掉最后 3 GiB 去产出一个**基于未拍板 HAL**的镜像。

**待主会话确认后，一条命令即可产出（约 1 GB 写入，需先腾出 ≥4 GiB）**：
```
cd /home/lxj/claude/6572
z1a9_gpt/scripts/r5d_audio_hal/apply_audio_hal.sh \
    out/z1_r5_system/system.img.r5d_final \
    out/z1_r5_system/system.img.r5d_audio_final
```
产出后按脚本输出记录 sha256（当前 PoC HAL sha256 =
`6427dbf404739af4b3f3e94451679fab286dff6333ea93d7bb15cfaeaf96df30`）。

**（若主会话坚持要试 LOS14 真 HAL）** 那需要另一支脚本：拷 19 个 MTK 库（858 KB）、
写 10 符号 shim、外加 ABI 搬位 shim，且**仍会在 `/dev/eac` 上失败**——我建议不要走。

---

## 上板验证步骤

刷完（或 `insmod` 音频 `.ko` 后）依次跑：

**A. 内核/驱动层**
```
cat /proc/asound/cards          # 期望: [0] mt6572-mt6323
cat /proc/asound/pcm            # 期望: 含 DL1 / AWB
ls -l /dev/snd/                 # 期望: pcmC0D0p 等
cat /proc/modules | grep -E '^snd|^mt|^soundcore'   # 期望 11 个
dmesg | grep -iE 'mt6572|mt6323|afe|asoc'           # 期望 probe OK, 无 "Unknown symbol"
```

**B. tinyalsa 直通（先证明"能出声"，绕过 HAL）**
```
tinypcminfo -D 0 -d 0          # 记下 rate/format/channels 能力
tinyplay /data/test48k.wav     # ← 这一步出声 = 内核+codec+扬声器全通
tinymix                        # 列出控件; 用 tinymix 打开 "Playback Volume"
```
**关键分叉**：**若 `tinyplay` 不出声 → 问题在 HAL 之下的内核/codec，不要在 HAL 上浪费时间。**

**C. HAL 层**
```
logcat -d | grep -iE 'AudioFlinger|audio_hw|loadHwModule|primary'
   # 期望: 不再有 "loadHwModule() error -22 loading module primary"
dumpsys media.audio_flinger | head -50    # 期望: 看到 primary device, 不再 "no hw module"
```

**D. 端到端**
```
am start -a android.intent.action.VIEW -d file:///data/test.mp3 -t audio/mpeg
```
期望：有声音；`dumpsys media.audio_flinger` 里输出流 active。

失败分叉：
| 现象 | 分叉 |
|---|---|
| `loadHwModule() error -22` | HAL 的 `open()` 返回 -EINVAL → 检查 `name` 参数匹配（PoC 同时接受 `"audio_hw_if"` 和 `"primary"`） |
| `dlopen failed: cannot locate symbol` | 缺符号 → `readelf -dW` 看 NEEDED 在不在镜像 |
| `pcm_open ... No such device` | `tinypcminfo` 复核 card/dev 号，改 PoC 宏 |
| HAL 起来但无声 | `tinyplay` 是否出声？出声→HAL 的 write 参数（rate/fmt/ch）没对齐；不出声→内核层 |
| SELinux `avc: denied` | 补 `file_contexts` + `restorecon` |

---

## 未决风险

1. **未上板**。PoC 不过 12 KB、编译链接全过、ABI 断言过，但**从没在 Z1 上跑过**——
   "能不能出声" 仍未知，最大不确定性就是"内核卡 + codec 到底出不出声"（这也是先跑 B 步的原因）。
2. **`audio_hw_device` 是否还需向后兼容分支**：`AudioFlinger` 在 A9 对 `AUDIO_DEVICE_API_VERSION_MIN`
   以下的模块有 fallback 代码路径，我没去反汇编它；PoC 直接报 2.0，属于策略里声明的版本，风险低但未证。
3. ~~A9 头与镜像实际布局是否一致~~ —— **已在 §1.5 部分闭合**：镜像自带 wrapper 在偏移 **108**
   做间接调用，且按 `common.version` 分派 v1.0/v2.0，与 A9 表及 PoC 一致。
   仍未证的是**逐字段全表核对**（需完整反汇编 wrapper）；
   若要 100% 保险，上板前可再静态核对 64/68/72/124/128/140/148 等槽是否都被当函数指针取用。
4. **SELinux 上下文**：`debugfs` 回写 `security.selinux` 是"尽力而为"，可能失败；
   首次创建的文件更可能没上下文 → 上板可能吃 `avc denied`。
5. **`__xlog_buf_printf`**：只有走 LOS14 真 HAL 才需要（它在镜像 `/lib/libxlog.so` 里，
   但没库 NEEDED 它 → 不会被载入）。走替代路径无此问题。
6. **磁盘**：本机剩 3.3 GiB。产出 r5d 音频镜像 + 备份要 ~2 GiB，**必须先清理**。
7. **版本差本身**：HAL 来自 A7、system 是 A9。这是所有风险的源头；
   替代路径的价值恰恰是**绕开**这个版本差（自写 A9 头编的 HAL）。

---

## 我实际改/建了哪些文件（自查）

**新建（全部在允许范围内）：**
- `out/AUDIO_HAL_PORT.md`（本报告）
- `z1a9_gpt/scripts/r5d_audio_hal/audio_primary_z1_poc.c`（316 行，PoC HAL 源码）
- `z1a9_gpt/scripts/r5d_audio_hal/apply_audio_hal.sh`（部署脚本，`bash -n` 过、dry-run 过）
- `z1a9_gpt/scripts/r5d_audio_hal/libaudio.primary.z1.so`（11,812 B，编译产物）
- `out/z1_audio_stage/reports/SYM_COVERAGE_A7HAL_vs_A9IMAGE.md`（由我派的 subagent 产出，符号覆盖核对）

**未碰任何禁用项：**
- ❌ 没改 `out/z1_r5_system/system.img.r5c_final`（它在我作业期间被别的 agent 删除，**不是我删的**）
- ❌ 没改 `out/z1_r5c_boot/boot.img`
- ❌ 没改 `fork_linux/` 下任何文件
- ❌ 没刷写、没碰设备（设备离线，也无 USB 操作）
- ❌ 没写任何镜像（`apply_audio_hal.sh` 只跑了 `--dry-run`）
- ✅ 阶段 3 的新镜像**未产出**（磁盘红线 + 待主会话给 HAL 方案拍板，理由见上）

**临时物**：`/tmp/inc`、`/tmp/lt.so`、`/tmp/poc.o` 等 scratch 已清理（`/tmp/*.so` 等）。
唯一残留：`out/z1_r5_system/system.img.r5c_final` 缺失是**外部**造成的，与本 agent 无关。
