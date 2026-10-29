# Z1 UI 卡顿与 GPU 加速 —— 根因判定与修复方案

日期: 2026-10-29
作者: UI 卡顿/GPU 加速工程师 (subagent)
范围: 只读分析 + 出脚本；**未刷写、未碰设备、未原地改任何交付镜像**。

---

## 0. 结论 (TL;DR)

**UI 卡顿的主因不是"没用 GPU"这个待办，而是一次 GPU 加速回归。**

板子**曾经**用 lima/Mali-400 硬件渲染并且 `ro.hardware.egl=mesa` 就是那个状态。
`out/z1_r5_system/system.img.r5d_final` (当前交付基线) 把 GPU 栈**换成了不兼容的一套**，
导致 SurfaceFlinger 退回 SwiftShader 纯软件渲染 —— 这才是 32/32 帧 >1s、SystemUI
反复 ANR、system_server 被 Watchdog 杀掉的直接原因。

具体是三个**互相独立**、必须同时修好的缺陷：

| # | 缺陷 | 证据 | 后果 |
|---|---|---|---|
| **A** | DRI 驱动搜索路径错 | r5d 的 `libEGL_mesa.so` 内 `DEFAULT_DRIVER_DIR` = `/usr/local/lib/dri`；该目录在镜像里**不存在**（只有 `/usr`,`/usr/local` 无）。而 `lima_dri.so` 放在 `/vendor/lib/dri/`。 | mesa 找不到 `lima_dri.so`；`droid_load_driver` 失败 → 整个 mesa 栈不可用 |
| **B** | `ro.hardware.egl=swiftshader` | r5d `/build.prop:51` | 即使 mesa 修好也不会被加载；SF 走 SwiftShader |
| **C** | 缺 `libGLES_mesa.so` | r5d `/vendor/lib/egl/` 只有 `libEGL_mesa.so`；**没有** `libGLES_mesa.so`。系统 `libEGL.so` 用固定名 `libGLES_<tag>.so`（tag=`mesa`）| 有 GLES blob 也没有；且 r5d 的 libEGL 是从 mesa 构建的，而 GLES 栈是 swiftshader 的 —— 两者不混用 |

**另有一条被证伪的线索**：`/dev/dri/renderD128` **不是**问题。它有专门规则、
有 AVC 标签、7 月实测能 `open()` 成功（详见 §2）。

---

## 1. 三条线索的判定

### 线索① `ro.hardware.egl=swiftshader` / 框架在用纯软件渲染 —— **判定: 真, 且是回归**

板载 log `mainline_recon/z1_android9_acm_20261025_105535_309533.log`:
```
978: Loading /vendor/lib/egl/libEGL_swiftshader.so
979: libEGL  : loaded /vendor/lib/egl/libEGL_swiftshader.so
995: SurfaceFlinger: vendor    : Google Inc.
996: SurfaceFlinger: renderer  : Google SwiftShader
997: SurfaceFlinger: version   : OpenGL ES 2.0 SwiftShader 4.0.0.4
```

**但 10-20 的镜像里 GPU 是好的。** `mainline_recon/z1_android9_acm_20261020_180334_356207.log`:
```
937: Loading /vendor/lib/egl/libGLES_mesa.so
938: libEGL  : loaded /vendor/lib/egl/libGLES_mesa.so
951: SurfaceFlinger: vendor    : lima
952: SurfaceFlinger: renderer  : Mali400
953: SurfaceFlinger: version   : OpenGL ES 2.0 Mesa 19.1.8
```
→ **lima/Mali-400 硬件渲染在本板已成过事实**。这不是"待实现"，是"被弄坏了"。

对应源镜像: `out/A9_flashpkg_p12_r2/system.img`
- `/build.prop:51` = `ro.hardware.egl=mesa`
- `/vendor/lib/egl/libGLES_mesa.so` (94,488 B)
- `/vendor/lib/dri/{swrast,kms_swrast,lima}_dri.so` → 符号链接 → `gallium_dri.so` (4,662,060 B, **Mesa 19.1.8**)

### 线索② `/dev/dri/renderD128` 疑似不存在 —— **判定: 否, 节点存在且可用**

主会话的疑点（log 里 `renderD128` 0 命中）**是 grep 假象**，三点铁证:

1. **有专门 ueventd 规则**（else 分支不会给标签）:
   `z1a9_gpt/rootdir/ueventd.z1.rc:2` → `/dev/dri/renderD128 0660 system graphics`
   （默认 AOSP `ueventd.rc` 里是 `/dev/dri/* 0666 root graphics`）

2. **SELinux 已经给目录打了 `gpu_device` 标签**（neverallow 会编译期拒掉给不存在的路径打标签）:
   `z1_android9_acm_20261020_180334_356207.log:3064`:
   `avc: denied { read } for name="dri" dev="tmpfs" ino=86 ... tcontext=u:object_r:gpu_device:s0 tclass=dir`
   → `/dev/dri` **已经创建**（tmpfs 上），进程读它被 SELinux 拦。
   注: permissive=1，所以**拦截不是失败原因**（这正是排除主会话选项 (b) 权限不对的关键）。

3. **7 月实测 open 成功**:
   `mainline_recon/z1_lima_runtimepm_rendernode_20260717_220932.log:13`:
   ```
   renderD128_open_rw=PASS
   renderD128_close=PASS
   ```
   同 log `:47-49` 给出主次设备号（精确 mknod 值）:
   ```
   226:0     ← /sys/class/drm/card0/dev
   226:128   ← /sys/class/drm/renderD128/dev
   drm 1.1.0 20060810
   ```
   内核态 `CONFIG_DRM=y` 早注册好了（`devtmpfs: initialized`，log:75）。

**对主会话三选项的回答**:
- (a) "建了但没 log" —— 部分对，但**有 log**：10-20 log 是**真的 open 失败**，
  183 次 `EGL-MAIN: droid_open_device() Failed to open DRM device /dev/dri/renderD128`。
  失败在 **10-05 时段**，那是 app 进程（`RenderThread`/`untrusted_app`）—— 被 SELinux 拦或
  当时镜像状态不同；**不是主因**（见 §3）。
- (b) 权限 —— **排除**。permissive=1，denial 只是审计记录，`open()` 照样进行。
- (c) 真没建 —— **排除**（上述三点）。

其余 A9 log（`_185436_`、`_194944_`）里 `renderD128` 命中少，是采集窗口没覆盖到
SF 初始化段，不代表节点缺失。

### 线索③ GPU 时钟 25.39 MHz —— **判定: 真, 但与本卡顿无关（是第 4 个独立问题）**

- 10-25 log `:169,172`: `lima 13010000.gpu: bus rate = 25390` / `mod rate = 25390`
  （`lima_device.c:417-418` 打的是 `clk_get_rate()`，单位 Hz → **25.39 MHz**）
- 7 月同节点（同内核树，见下）: `bus rate = 416000000` / `mod rate = 416000000` → **416 MHz**

**关键结论：这不是 dts 缺配置。**
- `mt6572.dtsi` 的 gpu 节点在 `a9_wt` 与 `fork_linux` 两树**逐字节相同**，
  都只有 `clocks = <&mfgcfg CLK_MFG_BG3D>, <&mfgcfg CLK_MFG_BG3D>`，**都没有** `assigned-clock-rates`
- `clk-mt6572-topckgen.c` / `clk-mt6572-mfg.c` / `apmixedsys.c` 两树 `diff -rq` **零差异**
- `mt6572-z1.dts` 的 `assigned-clocks` 只有 PMIC SPI 那条，无 GPU

→ 同样的源码 / 同样的 dts，7 月出 416 MHz、10 月出 25.39 MHz。**可用时钟树本来就到 416**
（`mfg_gf_parents[]` 默认父 `upll_d3` = 416 MHz，见 `clk-mt6572-topckgen.c:90-91`，
`upll_d3` = `univpll/3` = 1248/3，`:35`）。所以差异来自**运行期状态**（initrd/早期时钟初始化
或 July→Oct 间某个未提交的运行环境差异），**不是静态配置缺失**。
→ **不建议为它改 dts**：改了大概率无效（dts 本来就没限制），而且会动 dtb/initrd-end 风险面。
应在 §5 的验证步骤里**顺手测量**，确定后再单独处理。
→ **注意：25.39 MHz 不影响本次修复**。卡顿是"完全没走 GPU"，不是"GPU 慢"。

---

## 2. 为什么"改 `ro.hardware.egl=mesa` 就行"是错的

主会话的原方案（只改属性）**在 r5d 上无效**。因为 A9 的 EGL loader 用**固定名字**:

`libEGL.so`(system) 内字符串证实:
```
/vendor/lib/egl/libGLES_android.so
libGLES_android.so
_swiftshader.so            ← 后缀拼接
libEGL.so / libEGL          ← 基础名
```
即 loader 读 `ro.hardware.egl=<tag>`，然后 `dlopen("libEGL_<tag>.so")` 和
`dlopen("libGLES_<tag>.so")`。

r5d `/vendor/lib/egl/` 实际内容（`debugfs ls`，只读）:
```
libEGL_mesa.so            259,992 B   ← 是 mesa 的
libEGL_swiftshader.so     119,464 B
libGLESv1_CM_swiftshader.so 1,218,856 B
libGLESv2_swiftshader.so  1,624,792 B
```
- **没有 `libGLES_mesa.so`** → tag=mesa 时 GLES 侧加载失败
- 而且 `libEGL_mesa.so` **是 Mesa 24.0.9**（sha256 与 10-27 产物一致:
  `5c4d6243d0c8f7cce18c480d03ad967a42b0386f893b86f4f0a7cdcd056f9857`），
  而唯一的 `libGLESv*` 是 swiftshader 的 —— mesa 24 的 EGL 和 swiftshader 的 GLES **不可混用**。
- 更要命的是 **DRI 路径**: r5d 的 `libEGL_mesa.so` 里**唯一**的 DRI 目录字符串是
  `/usr/local/lib/dri`，`/usr/local` 在镜像里**不存在**。
  对照工作版本 `libGLES_mesa.so`(p12_r2) 内的字符串是 **`/vendor/lib/dri`** ——
  正好对上 `lima_dri.so` 的实际落点。

这就是任务里点名"**最容易漏的一环**"：**DRI 落点与 EGL 内编译死的搜索路径不匹配**。

---

## 3. 方案

### 方案 A（推荐，低风险）: 恢复 10-20 验证过的 mesa 19.1.8 栈

把 `out/A9_flashpkg_p12_r2/system.img` 里那套**已经实测出 `renderer: Mali400`** 的文件
覆盖到 r5d 副本上。这是**还原一个已知可用状态**，不是新发明。

脚本: `z1a9_gpt/scripts/r5f_gpu/apply_gpu_mesa.sh`

改动清单（源 = `out/A9_flashpkg_p12_r2/system.img`，**只读**）:

| 目标路径 (r5d) | 动作 | 内容 |
|---|---|---|
| `/build.prop` | 改 1 行 | `ro.hardware.egl=swiftshader` → `mesa` |
| `/vendor/lib/egl/libGLES_mesa.so` | **新增** | 94,488 B（来自 p12_r2） |
| `/vendor/lib/egl/libEGL_mesa.so` | 删除 | r5d 的 mesa 24 版，不兼容 |
| `/vendor/lib/dri/gallium_dri.so` | **新增** | 4,662,060 B（Mesa 19.1.8，含 lima） |
| `/vendor/lib/dri/{lima,swrast,kms_swrast}_dri.so` | **新增** | 符号链接 → `gallium_dri.so` |
| `/vendor/lib/dri/lima_dri.so` | 删除 | r5d 的 mesa 24 单文件版（13.5 MB）替代 |
| swiftshader 4 个 .so | **保留** | 回滚用；不占多少空间 |

注意: r5d 的 `/vendor/lib/dri/` 下**只有** `lima_dri.so`（无 swrast/kms_swrast/gallium），
而 p12_r2 有全套。符号链接是必需的 —— p12_r2 里三个名字都是指向 `gallium_dri.so` 的软链
（`Fast link dest` 实测），mesa loader 按 `<kernel_driver>_dri.so` 找，这个 gallium 单文件
内部按名字分发。

`libglapi.so` 两边同尺寸 173,184 B，**不动**。`libc++.so`、`libdrm.so`、`libsync.so`
r5d 都有（p12_r2 的 GLES blob NEEDED 里有 `libc++.so`，已确认存在）。

**磁盘**: 净增约 4.5 MB（删 13.5 MB 的 mesa24 lima_dri，加 4.66 MB gallium + 94 KB）。
可在 900 MB system.img 的 ext4 余量内完成，但**必须先 `df`** —— 本项目反复因盘满导致
debugfs 静默失败。

**回滚**: 脚本把原 `/build.prop` 和删掉的文件备份到镜像内 `/.r5f_gpu_backup/`，
并提供 `--undo`（幂等）。

### 方案 B（备选，风险高）: 重编 mesa 24 补齐

`mesa_android/lima-build-egl/` 是已配置的构建目录（86 MB，`build.ninja` 在），
meson/ninja 都在主机上。需要:
1. `-Ddri-search-path=/vendor/lib/dri` 重配（或给 `libEGL_mesa.so` 补路径），
2. 构建并部署 `libGLES_mesa.so`（mesa 的 GLES 前端），

**但当前 meson 配置是 `gles1 = disabled` / `gles2 = enabled`，
两棵源码树都找不到 `DRI_DIR_PATH` 的定义** —— 说明有个自定义补丁层（`z1_gralloc_dmabuf.c`
那批同源改动）没进 git，重编**可重现性没保证**。
**不建议优先走这条**；除非方案 A 被否。

---

## 4. 附带发现：`/dev/dri` 大目录（独立问题，低风险）

10-20 log 里那条:
```
avc: denied { read } for name="dri" dev="tmpfs" ino=86 ... tclass=dir
```
读**目录本身**被拒，说明 app 侧（`untrusted_app`/`platform_app`）**没有** `dir read`
权限，只有进程在**枚举** `/dev/dri` 时才会触发。
`blkid`/gralloc/EGL 若改成"列出目录找节点"就会踩到。
当前 mesa 是直接 `open("/dev/dri/renderD128")`（见 `platform_android.c:1089`
`loader_open_device(device->nodes[node_type])`），**不枚举目录**，所以现在不是阻塞点。
**不动**，仅记录：若将来切到枚举式查找，需要给 app 域加 `/dev/dri` 的 `dir read`。

---

## 5. 不依赖 GPU 的兜底（`out/z1_perf_stage/`，6 个文件一个都没应用）

先说**优先级**：这些是"锦上添花"，**不能替代 §3 的 GPU 修复**。
帧耗时 32/32 > 1s 是"完全没有 GPU"级别的灾难，省掉开机动画不会改变这个量级。

| 文件 | 判定 | 理由 |
|---|---|---|
| `build_prop_append.prop` 里 `debug.sf.nobootanimation=1` | **建议应用（纯收益）** | 已验证 `/bin/bootanimation` 内含该字符串（`strings` 命中 1 次）。省掉整段 60 fps 软渲染开机动画 |
| 同上 `dalvik.vm.dex2oat-threads=2` | **建议应用（低风险）** | ramdisk `default.prop` 现值是 `=1`（强制单线程）；dex2oat 帮助文本默认就是硬件线程数（双核=2）。只影响 AOT 墙钟，不改功能 |
| 同上 `pm.dexopt.disable_bg_dexopt=true` | **谨慎，建议先不应用** | 副作用真实存在：新装应用冷启动变慢（走 JIT）。且 `default.prop` 已是 `pm.dexopt.bg-dexopt=speed-profile`（非 speed），后台风暴本来就轻。**建议 GPU 修好后再单独评估** |
| 同上 `ro.lmk.debug=false` | **建议应用（无害）** | 仅关日志 |
| `z1_perf.rc` + `z1_perf_tune.sh` | **建议应用（纯收益）** | ①动画缩放=0（已有 `z1_pm_guard.sh` 双保险）②vm/cpuset/eMMC 参数。注意 rc 里 `write /dev/cpuset/...` 会在 GPU 修复后更有用（渲染线程不再和后台抢核）③抓基线快照便于对比 |
| `disable_list.txt`（8 服务） | **建议应用（低风险）** | 见下 |

`disable_list.txt` 默认要停的 8 个，逐条评估:
1. `drm` — 无 Widevine/TV，**停**（省内存，无功能损失）
2. `mediadrm` — 同上，**停**
3. `vendor.cas-hal-1-0` — 无 CAS 硬件，**停**
4. `vendor.media.omx` — 无 HW codec（Z1 只有软解），**停**
5. `incidentd` — 纯遥测，**停**
6. `statsd` — 纯遥测，**停**
7. `storaged` — 遥测，且 rc 里已把 `iostats` 关掉，**停**
8. `perfprofd` — 采样分析器，**停**

文件里还默认停了 `vendor.sensors-hal-1-0`（jfltexx 移植的 sensor HAL，Z1 无任何 sensor）—— **停，正确**。
可选段（注释掉的 `cameraserver`/`thermalservice`/`gatekeeperd`/`webview_zygote`）**建议保持注释**，
理由文件里已写清。

---

## 6. 上板单变量验证步骤

**前提**: 按项目纪律，先备份、别原地改、`df -h /`、一次只改一个变量。

### 步骤 1 — 先只修 GPU（方案 A），其它都不动
```
cd /home/lxj/claude/6572
df -h /
z1a9_gpt/scripts/r5f_gpu/apply_gpu_mesa.sh \
    out/z1_r5_system/system.img.r5d_final \
    out/z1_gpu_stage/system.img.r5f_gpu
sha256sum out/z1_gpu_stage/system.img.r5f_gpu   # 记录
```
（刷写由主会话决定，本 agent 不刷。）

### 步骤 2 — 刷完开机抓 log，看**这三行**（缺一即失败）
```bash
grep -nE "SurfaceFlinger: (vendor|renderer|version)" <log>
```
**期望**:
```
SurfaceFlinger: vendor    : lima
SurfaceFlinger: renderer  : Mali400
SurfaceFlinger: version   : OpenGL ES 2.0 Mesa 19.1.8
```
同时**应该消失**的:
- `libEGL  : loaded /vendor/lib/egl/libEGL_swiftshader.so`（改为 `...libGLES_mesa.so`）
- `EGL-MAIN: droid_open_device() Failed to open DRM device /dev/dri/renderD128`
- `libEGL  : eglInitialize(...) failed (EGL_NOT_INITIALIZED)`

### 步骤 3 — 帧耗时（真正的验收指标）
```bash
logcat -d | grep "Davey! duration" | tail -40
```
**期望**: 中位数从 **5,112 ms** 降到**三位数 ms 乃至 <100 ms**。
判据: 若中位仍 >1s，说明 GPU 仍未生效，**别继续调其它东西**，回步骤 2 分叉。

### 步骤 4 — ANR / Watchdog 是否止住
```bash
logcat -d | grep -cE "ANR in com.android.systemui"
logcat -d | grep -c "WATCHDOG KILLING SYSTEM PROCESS"
```
**期望**: 两者都应为 0（或显著下降）。这是"机器能开但一直卡"是否根治的判据。

### 失败分叉
| 现象 | 判定 | 下一步 |
|---|---|---|
| SF 仍 `SwiftShader` | 属性没生效或文件没到位 | 查 `getprop ro.hardware.egl`；在镜像里 `debugfs ls /vendor/lib/egl` 核对 |
| `loaded ...libGLES_mesa.so` 但 `eglInitialize failed` | DRI 路径/依赖问题 | 查 `MESA-LOADER: failed to open ...: ... (search paths ...)` 会打印实际搜索路径 |
| `Failed to open DRM device /dev/dri/renderD128` | 节点/权限 | 上板 `ls -l /dev/dri/`；应见 `renderD128` 226,128；若无 → 回到线索②，查 ueventd |
| SF 正常但帧仍慢 | GPU 生效但被限频 | 此时才处理**线索③**：量 `bus rate`/`mod rate` |
| 起不来/黑屏 | 栈不匹配 | 用 `--undo` 回滚（脚本支持），或刷回 r5d |

### 步骤 5（仅当步骤 3 达标后）— 再叠加兜底
按 §5 表格，先加 `debug.sf.nobootanimation=1` + `dalvik.vm.dex2oat-threads=2` + `z1_perf.rc`，
再测一次帧耗时，确认是**净改善**而非回归。

---

## 7. 未决风险 / 未确定项

1. **10-20 的 log 是哪个镜像刷出来的？** 我推断是 `out/A9_flashpkg_p12_r2/system.img`
   （`ro.hardware.egl=mesa` + 唯一的 `libGLES_mesa.so` + `/vendor/lib/dri` 路径全对上），
   **但没有 boot.img/system.img 的 sha 与 log 直接绑定**。缺: 该次刷写的日志或 sha 记录。
   这不影响方案 —— 无论如何都要**恢复一个已验证可用的栈**，而这是唯一一套。
2. **10-20 那次 mesa 生效时 GPU 时钟是多少？** 该 log（`_180334_`）里
   `bus rate = 25390`（25.39 MHz）**和 mesa 生效是同时发生的**。也就是说
   **25.39 MHz 下 Mesa 19.1.8 也能出 `renderer: Mali400`**。
   → 进一步支持"**线索③ 与本次卡顿无关**"的判断。
3. **10-25 log 里 183 次 app 侧 open 失败** 的原因未完全定论。
   可能是 SELinux 域（`untrusted_app` 无 `dir read`）或当时 SF 状态不同。
   但**不是主因** —— 主因是 SF 自己走了 swiftshader（§1 线索①）。
   缺: 一次带 `RenderThread` 完整域的实测。
4. **方案 A 是否会触发 SELinux 文件标签不匹配** —— 新增的
   `/vendor/lib/egl/libGLES_mesa.so`、`/vendor/lib/dri/gallium_dri.so` 需要正确的
   `file_contexts` 标签，且要有 `vendor_file` 域可读。
   当前是 `permissive=1`（`androidboot.selinux=permissive`），**不会阻塞**；
   但**若将来转 enforcing，这是必须先解的**。缺: enforcing 下的验证。
5. **`linux,initrd-end` 同步问题** —— `mt6572-z1.dts` 里 R5 刚改成 `0x842e2a64`
   并注明"与 R4.1 ramdisk 对齐"。**本方案不碰 dts/dtb，所以不影响 initrd-end。**
   但若为线索③去改 dts，就会踩到这个 —— 这是**不建议现在动 clocks 的另一个理由**。

---

## 8. 本次改了/建了哪些文件

**新建**:
- `z1a9_gpt/scripts/r5f_gpu/apply_gpu_mesa.sh` — 方案 A 的可重放脚本（幂等、非原地、含 `--undo`）

  已在**合成小镜像**上端到端验证（用真实的 `libGLES_mesa.so` 94,488 B 与
  `gallium_dri.so` 4,662,060 B 作恢复源，按 p12_r2 / r5d 两种布局搭建）:
  - `bash -n` 语法通过
  - apply: 部署后两份文件的 sha256 与源**逐字节相同**
  - 幂等: 二次运行输出"已是 mesa，跳过"，结果一致
  - `--undo`: `build.prop` 回到 `swiftshader`，被覆盖的两个文件内容**逐字节还原**，新增文件已删
  - 空间预检、输入=输出拦截、大小校验均生效
  - **未在真实 900 MB `system.img` 上跑过**（避免占用磁盘）；上板前请先跑一次并核对 sha
  - 已知限制: `--undo` 用 `debugfs dump/write` 还原，若原文件是**符号链接**会被落成普通文件
    （脚本会打印此警告）。r5d 的 `lima_dri.so` 是普通文件，故本项目不受影响。
- `out/z1_gpu_stage/` — 预留产物目录（**本次未生成 system.img**，等主会话决定合批）
- `out/UI_JANK_GPU.md` — 本报告

**未改动**（严格遵守只读纪律）:
- `out/z1_r5_system/system.img.r5d_final` — 只 `debugfs` 只读访问
- `out/z1_r5_system/system.img.r5_final` — 未访问
- `out/z1_r5c_boot/boot.img` — 只读解析（解到 `/tmp`，已删）
- `fork_linux/arch/arm/boot/dts/` — 只读；线索③**只给结论不改 dts**
- `out/A9_flashpkg_p12_r2/system.img` — 只读（作为恢复源）

**临时文件**: `/tmp/z1rd`、`/tmp/z1sys`、`/tmp/z1v` 已全部删除；`df -h /` 复查通过。
