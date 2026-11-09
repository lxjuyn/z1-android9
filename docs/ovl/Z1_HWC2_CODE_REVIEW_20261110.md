# Z1 HWC2 HAL 对抗式代码审查（2026-11-10）

> 目的：找出「只会在真板上暴露」的那一类 bug。上一次审查已抓到 `MODE` vs `MODE_ID`（"内核 ABI 字符串"类）；
> 本次按同一标准逐行重查 8 个方面（属性名 / 结构体布局 / HWC2 契约 / DRM atomic 用法 / 资源生命周期 / 并发 / 健壮性 / 性能陷阱）。
> 结论：**属性名这一类已经干净了**（板上 `z1drmtest` 实测也证实），真正会咬人的是**资源生命周期**和**提交路径的失败语义**。

## 0. 审查对象、方法与免责

| 项 | 值 |
|---|---|
| HAL 源 | `z1a9_gpt/scripts/z1hwc2/z1hwc2.cpp` |
| **被审版本** | **md5 `59a943a7fab1b3c3991c63711b80c8c3`，2696 行** |
| 策略头 | `z1a9_gpt/scripts/z1hwc2/z1_policy.h`（204 行，本次未改） |
| 内核 | `fork_linux/`（7.0-rc7 + mt6572 fork 驱动） |
| 客户端契约 | `out/Z1_HWC2_CONTRACT_20261110.md` + `/tmp/aosp9/{hidl,sf}`（A9 源码，已本地下载） |
| 板级证据 | `docs/ovl/Z1_HANDOFF_20261109_HWC2.md` §2（KMS 门禁 PASS 的 `z1drmtest` dump） |

**版本漂移警告（必读）**：审查期间该文件被同时改写，我实际读到了三个版本：
`2512 行 / r2` → `2682 行 / r5` → `2691 行 / r6` → 定稿 `2696 行`（本报告的行号对应最后一个）。
其中 **两处我原本判定为 P1 的问题在定稿前已被改掉**（见 §3.6），说明有人在并行加固；因此
**每一条都同时给了"函数名 + 唯一代码片段"作为锚点**，行号漂了也能 `grep -n` 定位。
如果文件再次改动，请以锚点为准。

### 板上已确认的事实（不是推测）

`Z1_HANDOFF_20261109_HWC2.md` §2 的 `z1drmtest` 实测输出：

```
---- /dev/dri/card1 fd=4 driver=mediatek ----     UNIVERSAL_PLANES -> 0, ATOMIC -> 0
resources: crtcs=1 encoders=1 connectors=1
crtc[0] id=59 ; connector[0] id=34 type=DSI status=connected modes=1 240x320@60
planes: count=4   id=35/41/47/53 possible_crtcs=0x1
formats: XR24 AR24 BX24 BA24 AB24 XB24 RG24 BG24 RG16 UYVY YUYV
plane[3] type=Cursor zpos=3 rotation=1 alpha=65535 blend=Pre-multiplied
```

⇒ `plane_count == 4`、`alpha`/`blend`/`zpos`/`rotation` **在板上确实存在**，格式集与 `hal_to_drm()` 的映射完全对得上。
**"属性名写错"这一类风险已经在板的层面排除**；剩下的全部集中在资源与提交语义。

---

## 1. 结论摘要

| 级别 | 数量 | 一句话 |
|---|---|---|
| **P0** | **1** | acquire fence 的 fd 每帧每层泄漏 1 个，composer 服务约 10–30 秒内 `EMFILE`，**合成器进程必死** |
| **P1** | **5** | 提交失败被吞成 `NONE`（静默冻屏）；`client_expected` 误置 true（旧 target 复现）；`modeset_done` 误锁存（永久黑屏）；present/release fence 恒 `-1`（撕裂）；`open()` 失败无回退（composer 整个不存在 → SF `LOG_ALWAYS_FATAL`） |
| **P2** | **13** | blob 泄漏、失败帧不清 pending、sw_sync 节点权限、klogf 尾字符、dumb bpp/句柄泄漏、offset 未处理、缓存键、错误码、无 NOT_VALIDATED、色彩矩阵被忽略、无矩形兜底 |
| **P3** | **7** | strdup 泄漏、layer id 截断、DPI 硬编码、dump 长度、destroyLayer 忽略 display、未用的 rotation、dump 无锁 |

**最危险的一条**：`z1_set_client_target()`（`z1hwc2.cpp:1605`）与 `z1_set_layer_buffer()`（`:1638`）把传入的
acquire fence `dup()` 一份保存后，**从不关闭传入的那个 fd**。按 AOSP 的所有权语义，HAL 返回
`HWC2_ERROR_NONE` 时该 fd 已经归 HAL 所有（证据链见下），所以这是**每帧每层 1 个 fd 的硬泄漏**。

---

## 2. 缺陷表

### P0 — 必然搞死首启

| 级别 | file:line | 问题 | 为什么错（引用） | 最小修法 |
|---|---|---|---|---|
| **P0** | `z1hwc2.cpp:1605`、`1638`<br>锚点：`= acquire_fence >= 0 ? dup(acquire_fence) : -1;` | acquire fence **dup 后不 close 传入的 fd** | ① composer 包装层在 HAL 返回 `NONE` 时把 fd 所有权交给 HAL：`/tmp/aosp9/hidl/ComposerCommandEngine.h:183-215`（`executeSetClientTarget`）与 `:356-381`（`executeSetLayerBuffer`）里 `closeFence=false`；② `/tmp/aosp9/hidl/HwcHal.h:319` 原文 "unlike in setClientTarget, releaseFence is owned by us" ⇒ **反过来 setClientTarget/setLayerBuffer 的 fence 是"设备"的**；③ `hwcomposer2.h:1527-1568 / 1980-2010` 只要求"HAL 保证 client 随时可关它"，即 HAL 必须自己留一份、并自己负责关 | 删掉 `dup()`：`l->acquire_fence = acquire_fence;`（`h->client_acquire_fence = acquire_fence;`）。现有"替换时 close / present 里 close / close() 里 close"的代码立刻变成正确 |
| | **板上症状** | 每帧每层一次 ⇒ 60 fps × 3 层 ≈ 180 fd/s。composer 的 `.rc`（`/mnt/z1sys/vendor/etc/init/android.hardware.graphics.composer@2.1-service.rc`）只有 `user system`、`group graphics drmrpc`、`capabilities SYS_NICE`，**没有 rlimit**；`out/boot_extracted/rd/init.rc:789` 只设了 `setrlimit 13 40 40`（`RLIMIT_NICE`）⇒ 软限走内核默认 **1024**。约 300 帧（≈10 s 动画）后 `dup()` 与 binder 收 fd 开始 `EMFILE`：logcat 出现 SF 的 `Failed to set buffer` / `error posting framebuffer`，随后 `failed to get hwcomposer service`（`/tmp/aosp9/sf/ComposerHal.cpp:172` 的 `LOG_ALWAYS_FATAL`）→ SF abort → init 重启 → 环。**板上 10 秒可证伪**：`cat /proc/$(pidof android.hardware.graphics.composer@2.1-service)/limits` 看 `Max open files`，再 `ls /proc/<pid>/fd | wc -l` 连看三次 | | |

### P1 — 板上肉眼可见 / 静默失效

| 级别 | file:line | 问题 | 为什么错（引用） | 最小修法 |
|---|---|---|---|---|
| **P1** | `z1hwc2.cpp:2353`（`z1_present_display`）<br>锚点：`return HWC2_ERROR_NONE; /* keep SurfaceFlinger alive */` | 两次 `drmModeAtomicCommit` 都失败时**返回 `NONE`**，不报错、不重试、不降级 | `hwcomposer2.h:1471-1505` 规定 present 失败必须返回错误码；返回 `NONE` 等于告诉 SF"这帧已上屏"。SF 不会因为画面没变而重试（它只看返回值与 fence） | 返回 `HWC2_ERROR_NO_RESOURCES`；至少保证"失败次数"进入那行每 600 帧的汇总日志 |
| | **板上症状** | 屏幕停在最后一帧，logcat 只有一行 `z1hwc2: atomic commit failed: Invalid argument (flags=0x2 planes_used=..)`，之后 `frame N: ...` 照常打印 ⇒ 看起来在出帧，实际没提交 | | |
| **P1** | `z1hwc2.cpp:2086`（`z1_validate_display` 第二遍决策）<br>锚点：`h->client_expected = true;` | 只要第一遍没过 `layer_can_be_background()`，第二遍就**无条件**置 `client_expected=true`；即使第二遍一个层都没降级（`used == n`） | 该标志唯一用途是 `z1hwc2.cpp:2197` 决定要不要把 `h->client_target` 放到平面 0。SF 只在**存在 CLIENT 层**时才 GL 合成并产出新 target（`DisplayDevice.cpp:367-373` + `SurfaceFlinger.cpp:2078-2096`）；`used == n` 时 SF 不做 GL，`h->client_target` 仍是上一帧内容 ⇒ 把旧帧画到屏上。同时第 4 个平面被白留 | 第二遍之后再判一次：`h->client_expected = (used != n);`，`used == n` 时允许用满 `h->plane_count` |
| | **板上症状** | 触发条件：可见层全部可上硬件，但最底层不是满屏/不透明（只有一个 Toast/小 SurfaceView、或底部是悬浮层）。此时屏上会在底层显示**上一帧的 GL 合成画面**，且 `dumpsys` 里 4 号平面空着 | | |
| **P1** | `z1hwc2.cpp:2413`（`z1_present_display` 回退路径 + 锁存）<br>锚点：`h->modeset_done = true;` | 首帧带 `ALLOW_MODESET` 失败后，退化成纯平面提交再试；**只要第二次返回 0 就锁死 `modeset_done`**，此后再不 modeset | 若 CRTC 当时并未 active（fbdev 没接管/已被关），纯平面提交会因为 `crtc_state->enable == false` 让 `drm_rect_clip_scaled()` 返回 false（`drm_atomic_helper.c:922-950`）⇒ 提交返回 0 但**一个平面都没点亮**；而模式设置分支（`z1hwc2.cpp:2224-2240`，条件 `!h->modeset_done`）再不会执行 | 只在读到 `crtc ACTIVE == 1` 时才允许走回退；或把锁存条件改成"本帧带了 ALLOW_MODESET 或 ACTIVE==1" |
| | **板上症状** | 永久黑屏，日志只有一行 `z1hwc2: plane-only commit accepted (no modeset)`，之后再无错误；`dumpsys SurfaceFlinger` 显示 HWC 合成、屏不亮 | | |
| **P1** | `z1hwc2.cpp:2162`（`*out_retire_fence = -1`）、`:2389-2410`（release fence 段） | present fence 恒 `-1`；release fence 默认关（`debug.z1.hwc.fences=0`，且开了也打不开节点，见 P2） | `hwcomposer2.h:1433-1468`、`:1471-1505`。SF 侧后果：① 上一块 client target 立即放回 BufferQueue（`FramebufferSurface.cpp:162-180` 只在 `fence->isValid()` 时 addReleaseFence）⇒ 撕裂；② `DispSync` 永远收不到 present 样本（`SurfaceFlinger.cpp:1758-1764`），无法锁相。属注释里承认的 "Phase 1" 取舍，但确实违反契约 | 短期把 `debug.z1.hwc.fences` 默认改 1 并修节点权限；正解是 `OUT_FENCE_PTR`（`drm_mode_config.c:305-311`，内核有） |
| **P1** | `z1hwc2.cpp:2634-2679`（`z1_open` 失败路径）+ `:858-900`（等节点循环）<br>锚点：`return -ENODEV;` / `for (i = 0; i < 120; i++)` | `z1_open()` 一旦失败（card1 60 s 没出现 / `drmSetMaster` 一直失败 / probe 失败），**composer 服务就彻底不存在**，且**没有 fb0 回退** | `HwcLoader::loadModule()` 只在 `hw_get_module()` 失败时才回退 gralloc/fb0（`/tmp/aosp9/hidl/HwcLoader.h:60-70`）；`open()` 失败只打一行 `failed to open hwcomposer device`（`:109`）就返回 nullptr。当前板上能显示的路径正是"没有 `hwcomposer.z1.so` ⇒ 回退 fb0 适配器"（handoff §2：`composer 打开 fb0 成功`）。一旦 init 把 `.so` 装进 `/vendor/lib/hw`（`out/z1_v83/embedded_init.z1.rc:42`），**open 失败 = 屏彻底没驱动** | ① 失败必须能回退：脚本里检测失败后 `mv /vendor/lib/hw/hwcomposer.z1.so{,.off}` 并重启 composer；② 等节点预算压到 ≤ 35 s 并在日志里明确写"超时会放弃"（现在 120×500 ms = 60 s，可能超过 SF 的 `getService` 预算） |
| | **板上症状** | `E ComposerHal: failed to open hwcomposer device: No such device`（v79 已出现过一次）→ composer 反复退出 → SF 在 `ComposerHal.cpp:172` abort → 重启环，屏幕停在二屏/黑屏 | | |

### P2 — 健壮性 / 特定条件才炸

| 级别 | file:line | 问题 | 为什么错（引用） | 最小修法 |
|---|---|---|---|---|
| P2 | `z1hwc2.cpp:2230`<br>锚点：`h->mode_blob = blob;` | 每次重试都新建 blob 并覆盖旧指针，旧 blob 永不销毁 | 内核 blob 是带引用的对象，只有 `drmModeDestroyPropertyBlob` 才释放；首帧持续失败时每帧泄漏一个 68 B blob + 一个 kernel object | 建之前先销毁旧的，或把 blob 缓存在 `modeset_done` 之外只建一次 |
| P2 | `z1hwc2.cpp:2353`（同上）<br>锚点：失败分支的 `return` | 失败分支**跳过了 `pending_release` 清理**（清理已挪到提交之后） | 提交持续失败时 `pending_release` 只增不减：`std::vector` 内存 + 内核 fb 对象 + 被 `drmCloseBufferHandle` 卡住的 gem 引用全部累积 | 失败分支里也把 pending 清一遍（顺序无所谓，反正新状态没生效） |
| P2 | `z1hwc2.cpp:540`/`:549`<br>锚点：`open("/sys/kernel/debug/sync/sw_sync", O_RDWR \| O_CLOEXEC)` | sw_sync 节点以 `O_RDWR` 打开，但该 debugfs 文件是 `root:root 0644` | `sync_debug.c:110-120`：`debugfs_create_file_unsafe("sw_sync", 0644, dbgfs, ...)`；composer 是 `user system`、无 `CAP_DAC_OVERRIDE` ⇒ **必然 EACCES**，`fences_on` 永远 false ⇒ P1-4 的"短期方案"也走不通 | 用 `OUT_FENCE_PTR`；或在 init.rc 里 `chmod 0666 /sys/kernel/debug/sync/sw_sync`（注意 debugfs 挂载时序） |
| P2 | `z1hwc2.cpp:258`<br>锚点：`if (buf[n - 1] == '\n')` 之后的 `buf[n - 1] = '\n';` | 无条件把最后一个字符改成 `\n` 再写 kmsg：**格式化串不以 `\n` 结尾时最后一个字符被吃掉** | 纯逻辑错误。现在所有调用点都带 `\n`，所以不显形，但很容易埋雷 | 记下 `had_nl`，只有 `had_nl` 时才做"去掉-写回" |
| P2 | `z1hwc2.cpp:721`<br>锚点：`create.bpp = hal_bpp(fourcc == DRM_FORMAT_RGB565 ? ... : ...RGBA_8888) * 8;` | dumb 的 bpp 只区分 RGB565/其它：RGB_888 也建 32bpp dumb，但 `AddFB2` 用 `DRM_FORMAT_BGR888`(24bpp) | `drmModeAddFB2` 不校验 pitch（核心只按 `fb->format->cpp` 解释），错位不会被拒 ⇒ 花屏 | 把 HAL format 传进 `dumb_ensure`，`create.bpp = hal_bpp(fmt) * 8` |
| P2 | `z1hwc2.cpp:736-757`（`dumb_ensure` 失败路径） | `CREATE_DUMB` 成功但 `MAP_DUMB`/`AddFB2` 失败时直接 return，`create.handle` **泄漏**（`d->handle` 仍为 0） | 每帧重试就每帧泄漏一个内核 dumb buffer | 失败路径 `DRM_IOCTL_MODE_DESTROY_DUMB(create.handle)` |
| P2 | `z1hwc2.cpp:619-680`（`fb_import`）、`:797-804`（`copy_into_dumb`） | 完全忽略 `private_handle_t.offset`（布局第 28 字节）；拷贝路径尺寸校验 `src_pitch*h > size + src_pitch` 多放过一行 | 当前 gralloc 恒 `offset=0`（`gralloc.cpp` 构造时置 0；`pixel_copy.h:13-36` 的 `size = columns*rows*bpp+4` 也够大），所以现在不出事；一旦有人改 gralloc 复用大 buffer 就整体错位 | `offsets[0] = gh->offset`；拷贝源地址加 `gh->offset`；尺寸校验改 `src_pitch*height > size` |
| P2 | `z1hwc2.cpp:1230`<br>锚点：`h->client_fb.handle == h->client_target` | client target 的 fb 缓存只比 `handle` 指针，层却比 `handle + src_fd`（`z1hwc2.cpp:2268-2270`） | 同一个 `buffer_handle_t` 指针在 slot 重新分配后指向新 dma-buf（fd 变），此时会继续用旧 fb ⇒ 显示旧内容 | 与层一致，加上 `src_fd` 比较 |
| P2 | `z1hwc2.cpp:1415-1417` | 未知 `getDisplayAttribute` 返回 `HWC2_ERROR_BAD_PARAMETER` | `hwcomposer2.h:1253-1274` 只列 `BAD_DISPLAY`/`BAD_CONFIG`，未知属性应 `*out_value=-1` + `NONE`。SF 只问 5 个属性，当前无实害 | 改 `*out_value = -1; return HWC2_ERROR_NONE;` |
| P2 | `z1hwc2.cpp:1420-1448` | `getDisplayConfigs` / `getActiveConfig` / `setActiveConfig` 全都 `(void)display;` | 契约要求 `HWC2_ERROR_BAD_DISPLAY` | 三处加 `if (display != 0) return HWC2_ERROR_BAD_DISPLAY;` |
| P2 | `z1hwc2.cpp:2158-2164`（`z1_present_display` 开头） | 不检查 `h->validated` 就提交 | `hwcomposer2.h:1471-1505` 要求 present 前必须成功 validate，否则返回 `NOT_VALIDATED`；契约文档 §8.3 也列了这条 | 加检查，并在每次 `setLayer*` 后清 `validated` |
| P2 | `z1hwc2.cpp:1563-1580`（`z1_set_color_transform`） | 返回 `NONE` 但完全不应用矩阵 | SF 的判据是 `applyColorMatrix = !hasDeviceComposition && !skipClientColorTransform`（`/tmp/aosp9/sf/SurfaceFlinger.cpp:2917-2921`）：**只要有 Device 合成，SF 就不在 GL 里做矩阵**，并认为 HAL 做了。注意：返回 `UNSUPPORTED` **不是**修法（`HWComposer::setColorTransform` 只看错误码打日志，`applyColorMatrix` 不看它） | 非单位矩阵时把所有层降 CLIENT（复用 `mode_force_client` 那条路），或在 CRTC 上实现 CTM |
| P2 | `z1hwc2.cpp:2264-2289` | `program_plane` 不做任何矩形兜底：`l->frame.right - l->frame.left` 是 int 减法，恶意/异常 rect（如 `INT_MIN`）会溢出成大正数 | 决策策略会挡住绝大多数（`z1_policy.h:97-113`），但 `program_plane` 是最后一道门；溢出成 `CRTC_W` 巨值会被 `drm_atomic.c:747-757` 判 `-ERANGE` ⇒ 整帧提交失败 ⇒ 落到 P1-1 的静默分支 | 在 `program_plane` 入口对 `x,y,w,h` 做 `<= 0 || > 8192` 的钳位/丢弃 |

### P3 — 风格 / 潜在坑（本轮不建议动）

| 级别 | file:line | 问题 | 最小修法 |
|---|---|---|---|
| P3 | `z1hwc2.cpp:850` | `strdup(prop)` 永不释放 | 用 static 缓冲 |
| P3 | `z1hwc2.cpp:2656` | `next_layer_id` 是 u64，但 `createLayer` 只回 u32（`hwcomposer2.h` 的 `outLayer` 就是 `uint32_t*`）；超过 2^32 后 id 撞车 | 回绕时查 map |
| P3 | `z1hwc2.cpp:1409-1414` | DPI 硬编码 160000（160 dpi）；面板 240×320 实际约 200 dpi | 从属性读，或返回 `-1` 让 SF 用默认 |
| P3 | `z1hwc2.cpp:2443-2457`（`z1_dump`） | 第二次调用时 `*out_size = snprintf()` 的返回值可能大于调用方容量（当前字符串 ~190 B，不触发） | 用 `min(n, cap)` |
| P3 | `z1hwc2.cpp:1368-1385`（`z1_destroy_layer`） | 忽略 `display` 参数 | 加 `BAD_DISPLAY` 检查 |
| P3 | `z1hwc2.cpp:1032-1034` | `p_rotation` 查了就再没用（`transform != 0` 一律降 CLIENT，是正确的） | 删掉或注释说明 |
| P3 | `z1hwc2.cpp:1977` 写 `g_verbose`、`:2443-2450` 读 `h->layers` | HAL 被 4 个 binder 线程调用（`service.cpp:35` `defaultPassthroughServiceImplementation<IComposer>(4)`），`dump`/`getCapabilities` 不走命令队列 ⇒ 与 present 并发读 map；`g_verbose` 也是无锁全局 | 只读统计可接受，但 `dump` 应先拷贝 size 或加锁 |

---

## 3. 已核对、确认正确的部分（覆盖说明）

> 每一条都对着源码核过；"板上已验"指 handoff §2 的 `z1drmtest` 实测。

### 3.1 内核 ABI 字符串 / 对象类型

| 项 | 核对结果 |
|---|---|
| `MODE_ID` | 存在：`drm_mode_config.c:337`；blob 属性挂在 CRTC 上：`drm_crtc.c:320`。`z1hwc2.cpp:968-973` 先查 `MODE_ID` 再退 `MODE`，**正确**（上一次的致命 bug 已修） |
| `ACTIVE` | `drm_mode_config.c:330`；CRTC 挂载 `drm_crtc.c:319` |
| `CRTC_ID`（connector） | `drm_mode_config.c:317`；connector 挂载 `drm_connector.c:316` |
| `FB_ID` / `CRTC_ID` / `CRTC_X,Y,W,H` / `SRC_X,Y,W,H` | `drm_mode_config.c:251-317`，全部存在 |
| `zpos` | `drm_blend.c:381`（可变）/`:420`（immutable）；mtk 用 immutable：`mtk_plane.c:340` |
| `alpha` | `drm_blend.c:229`；mtk 在 `mtk_plane.c:354` 创建。**板上已验** |
| `pixel blend mode` | `drm_blend.c:597`；mtk 在 `mtk_plane.c:359` 创建，且 `mt6572_ovl_driver_data.blend_modes` 三种齐全（`mtk_disp_ovl.c:707-709`）⇒ 属性存在。**板上已验** |
| `rotation` | `drm_blend.c:296`；mtk `mtk_plane.c:346-352`。**板上已验** |
| `OUT_FENCE_PTR` / `IN_FENCE_FD` | `drm_mode_config.c:305-311` 都存在（当前未用；以后要真 fence 可直接用） |
| `DRM_MODE_OBJECT_*` | 用的 `PLANE/CONNECTOR/CRTC` 都是 libdrm 标准值，与内核 `drm.h` 一致 |
| sw_sync UAPI | 结构体/ioctl 号逐字段一致（`sw_sync.c:44-58`）；`sync_timeline_signal(obj, inc)` 是 `obj->value += inc`（`sw_sync.c:209-238`）⇒ "INC 1 推进 1" 成立；节点路径 `/sys/kernel/debug/sync/sw_sync` 正确（`sync_debug.c:110-120`） |

### 3.2 结构体 / 布局假设

- `hw_device_t` = 64 B、`close@60`；`hwc2_device_t` 的 `getCapabilities@64` / `getFunction@68`：与 `include/hardware/hardware.h`、`hwcomposer2.h` 一致，且**已有编译期 `static_assert` 兜底**（`z1hwc2.cpp:169-174`）——这类 bug 以后不可能再静默存在。
- gralloc `private_handle_t`：`gralloc_priv.h` 的字段顺序/类型 = `z1_handle`，`sizeof==64`、`fd@12`、`width@44`、`height@48`、`stride@52`、`format@56` 全部对得上（`z1hwc2.cpp:175-185` 已 static_assert）。
- `stride` 语义是**像素**（`pixel_copy.h:22-35`），HAL 的 `pitch = stride * bpp`（`:660`）正确。
- `hwc_rect_t/hwc_frect_t/hwc_color_t/hwc_region_t` 布局与 AOSP 一致（`hwcomposer_defs.h:54-84`）。
- `hw_device_t.version = HWC_DEVICE_API_VERSION_2_0 = 0x02000001`（`hwcomposer_defs.h:42`）⇒ `HwcLoader.h:113` 的 `major = version>>24 == 2` 通过，走原生 HWC2 而非 HWC1 适配器。

### 3.3 HWC2 契约

- `getFunction()` 对 `HwcHal::initDispatch()`（`/tmp/aosp9/hidl/HwcHal.h:521-584`）要求的 **42 个描述符全部返回非 NULL**（含 `CREATE_VIRTUAL_DISPLAY`/`DESTROY_VIRTUAL_DISPLAY`/`GET_MAX_VIRTUAL_DISPLAY_COUNT`/`GET_CLIENT_TARGET_SUPPORT`/`DUMP`/`SET_OUTPUT_BUFFER`/`SET_LAYER_COLOR`/`SET_CURSOR_POSITION`）；`>= SET_LAYER_FLOAT_COLOR`（=46）的 2.2 描述符返回 NULL 也正确（`hwcomposer2.h:265-273`）。
- `getCapabilities` 返回 0 个 cap：既避开 `SKIP_VALIDATE`（会让层永远停在 INVALID），也避开 `PRESENT_FENCE_IS_NOT_RELIABLE`（它在 `HwcHal.h:75-91` 会让 `initWithDevice(requireReliablePresentFence=true)` **直接拒绝本设备**）。
- hotplug：`registerCallback` 内同步回调 `(data,0,CONNECTED)` 是安全的——`HwcHal::registerEventCallback` 先赋 `mEventCallback` 再 `mDispatch.registerCallback`（`HwcHal.h:110-124`），`ComposerClient::registerCallback` 也是先建 `mHalEventCallback`（`ComposerClient.h:119-124`）。
- `validateDisplay` 返回码自洽：`*out_num_types = changed.size()` 且 `NONE ⇔ changed.empty()`（`:2143`）；`*out_num_requests = 0` 与 `getDisplayRequests` 的 0 一致。
- 三个计数查询接口都判了 NULL 数组（`getChangedCompositionTypes:1868` 附近、`getDisplayRequests`、`getReleaseFences`），与 `HwcHal.h:340-410` 的"先传 NULL 取数量"模式匹配。
- 组合类型方向：只报 `→CLIENT`，从不把 CLIENT 改 DEVICE（`z1_policy.h:146-153`），符合 `hwcomposer2.h:124-131` 与 `HWComposer.cpp:109-126`。
- "CLIENT 连续且在底部"：`z1_policy_decide` 从顶部往下走，遇到第一个降级就 `client_above=1` 把下面全拉成 CLIENT（`z1_policy.h:141-199`），与契约文档 §1.4 算法一致。
- INVALID 首帧：策略给 DEVICE/CLIENT 并置 `report=1`，`validateDisplay` 返回 `HAS_CHANGES`；而 SF 每帧会因自身缓存值不同重新 `setCompositionType(Device)`（`BufferLayer.cpp:653-661` + `Layer.cpp:730-748`），所以"把 requested 覆盖成 validated"不会让层被永久钉在 CLIENT（这一点我专门验证过，**不是** bug）。
- `getActiveConfig` 恒返回 0、`VSYNC_PERIOD` 恒 > 0（`:1405-1408`）⇒ 不会触发 `SurfaceFlinger.cpp:1287-1290` 的空指针，也不会让 `DispSync` 周期为 0。
- `acceptDisplayChanges` 清 pending 并置 validated（`:1319-1331`），符合"等价于 set+re-validate"的效果。
- `destroyLayer` 会 `fb_forget` + close 两个 fence（`:1368-1385`）；`getReleaseFences` 交所有权即置 -1（`:1887-1900`），无双重 close。
- `plane_count < 2` 才拒绝（`:1053`）；软色/侧带层被降 CLIENT（`z1_policy.h:154-167`）。

### 3.4 DRM atomic 用法

- 首帧三件套 `connector CRTC_ID + crtc MODE_ID + crtc ACTIVE=1` + `ALLOW_MODESET` 是内核要求的最小集合；`drm_atomic_helper_commit_crtc_enable()`（`drm_atomic_helper.c:1652-1689`）会因此真正调用 `mtk_crtc_atomic_enable()`，即使 fbdev 没接管也能把 DDP 初始化起来。
- 模式 blob 用 `drmModeModeInfo`（68 B，与内核 `drm_mode_modeinfo` 同构），内核要求的长度校验刚好通过（`drm_atomic_uapi.c` 的 mode blob 分支）。
- 多余平面显式 `FB_ID=0 + CRTC_ID=0` 关闭（`:1151-1158`）：应对"未提交的平面继续扫描旧 buffer"的 mtk 行为，写得对。
- 负坐标安全：`CRTC_X/Y` 是 `DRM_MODE_PROP_SIGNED_RANGE, INT_MIN..INT_MAX`（`drm_mode_config.c:275-281`）；内核 `state->crtc_x = U642I64(val)`（`drm_atomic_uapi.c:560-563`），HAL 的 `(uint64_t)(uint32_t)x` 截断后正好还原成负数；越界由 `drm_atomic.c:747-757` 的 `INT_MAX - crtc_w` 兜住。
- 无缩放/裁剪：`SRC_*` 必须落在 fb 内（`drm_atomic.c:759-781`），策略保证 `crop ≤ buffer`，不会吃 `-ENOSPC`；`hscale/vscale` 检查在 `drm_atomic_helper.c:932-938`，策略保证 `src == dst`；分数 crop 会被策略判成 scaling 而降 CLIENT，所以不会出现 `src_w>>16 != crtc_w` 的 `-ERANGE`。
- `drmModeAddFB2` 的 pitch/cpp 与 `hal_to_drm()` 的映射逐项对得上 `mt8173_formats[]`（`mtk_disp_ovl.c:116-128`）：`ABGR8888/XBGR8888/ARGB8888/BGR888/RGB565` 全在表内；`RGB565` 用 `DRM_FORMAT_RGB565` 而非 BGR565 是**对的**（该 SoC `fmt_rgb565_is_0=false`，`mtk_disp_ovl.c:705`）。
- 平面 alpha 量化到 0..255 也是对的：驱动 `mtk_disp_ovl.c:543` 是 `con |= state->base.alpha & OVL_CON_ALPHA`，`OVL_CON_ALPHA == 0xff`（`:96`）。
- 平面能力：mt6572 只有 `main_path`（`mtk_drm_drv.c:249-257`）⇒ 1 个 CRTC、`possible_crtcs&1` 成立；RDMA 不贡献平面（`mtk_crtc.c:934-950` 要求 `comp_idx==1` 的组件有 `bgclr_in_on`，`ddp_rdma` 没有，`mtk_ddp_comp.c:371-385`）⇒ **正好 4 个平面**，与板上 `count=4 id=35/41/47/53` 一致；排序后 zpos 0..3（plane 3 是 Cursor 型，当普通层用没问题——只有 async 路径才按类型区分，`mtk_plane.c:92-96`）。
- `drmModeCreatePropertyBlob` 失败会退化成 flags=0 的平面提交（`:2224-2240`），至少不会崩。

### 3.5 资源与并发

- `fb_import` 成功/失败两条路径的 gem 引用都对：成功存进 `z1_fb.gem`，失败立即 `drmCloseBufferHandle`（`:678`）；`pending_release` 成对处理 `(fb, gem)`。
- `dumb_ensure` 尺寸变化时先 `munmap` + RmFB + DESTROY_DUMB 再重建（`:702-716`）。
- `z1_close` 顺序正确：先停 watchdog（`pthread_join`）→ 再进 `fence_lock`（不会死锁）→ 关层/target/pending/shadow/blob/fd，最后 `delete`（`:2570-2631`）。
- 拷贝回退路径先等 acquire fence（`:2179-2193` 在 `client_target_fb` 之前），再用 `DMA_BUF_IOCTL_SYNC(START|READ)/(END|READ)` 包住 memcpy（`:811-830`）——顺序正确。
- `wait_fence()` 用 poll + 3 次递增超时，出错不阻塞主流程；close 后才置 -1。
- 只有 watchdog 一个线程，与 present 的共享状态（`fences_pending`/`timeline_val`）在 `fence_lock` 内修改；`wd_stop` 用 `volatile` 在本实现里够用。

### 3.6 审查期间已被修掉的两条（我原本判 P1，现降级为"已验证正确"）

| 原问题 | 现状 |
|---|---|
| `pending_release` 在**提交前**清（会对仍在扫描的 fb 调 `drmModeRmFB`，触发内核 `atomic_remove_fb()` 强制关平面：`drm_framebuffer.c:1146-1176`、`:999-1130`） | **已修**：清理已挪到 `drmModeAtomicCommit` 成功之后（`z1hwc2.cpp:2360-2376`），并且注释写清了原因。顺序正确 |
| release fence 在**下一帧提交前** signal（撕裂窗口） | **已修**：`inc` 现在位于本帧 commit 之后（`:2389-2410`），语义与注释一致 |

---

## 4. 没有板子无法确认的事项（以及会让哪些结论失效）

| # | 事项 | 影响 |
|---|---|---|
| 1 | `libhidl` 的 `IComposer::getService()` 重试预算到底是多少（AOSP 常见 500×100 ms = 50 s，但本地没有这份源码） | 若 50 s：card1 在 ~35.7 s 出现，只剩 ~14 s 余量，P1-5 的 60 s 等待一旦走满必死；若更短（有的版本 5 s）：**当前设计不可能首启成功** |
| 2 | `/dev/kmsg` 的实际权限（本 ramdisk 的 `ueventd.rc` 无 kmsg 规则，Android 对未列出节点默认 0600 root:root） | 若 0600，uid system 的 HAL 写 kmsg 全 EACCES。代码已把 ALOG 当主通道（`z1hwc2.cpp:223-229` 注释写明），所以只影响"串口/dmesg 能不能直接看到 HAL 日志"。上板 `ls -l /dev/kmsg` 一句话确认 |
| 3 | `drmPrimeFDToHandle`/`drmModeAddFB2` 在本板真实 gralloc 上能否成功（dma-heap system heap 不保证物理连续；`mtk_gem.c:212-231` 会 `-EINVAL`） | 决定"有没有任何层能上硬件"。全失败则一切走 CPU 拷贝回退（能用但慢，且 P1-2/P1-3 的行为不同）；只能上板看 `drmPrimeFDToHandle(N) failed` 那行 |
| 4 | `mtk_ddp_comp_power_on()`（DIS 电源域）是否成功 | `mtk_crtc_atomic_enable()` 失败时**只打一行 `Failed to enable power domain` 就 return**（`mtk_crtc.c:777-800`，`:789`），提交照样返回 0 ⇒ HAL 打印 "first commit done with modeset" 而屏黑。这是"看起来成功但没显示"的最大黑洞 |
| 5 | panel/DSI 在 HAL 接管时是否真的出图（LK 点屏 + mainline `mtk_dsi` 接管） | 决定 P1-3 的"首帧 modeset"是否会被内核静默吞掉 |
| 6 | fbdev（`CONFIG_DRM_FBDEV_EMULATION=y`）在 HAL 首帧时是否已接管 CRTC | 决定首帧是"纯平面提交就能亮"还是"必须靠 ALLOW_MODESET 模式设置"。两条路径代码都覆盖，但实际走哪条只能看日志 |
| 7 | DRM master 竞争的实际结果（gralloc 服务 `z1_heap.cpp:640-664` 会 `DROP_MASTER`；`z1drmtest` 是短命进程） | 若某进程长期持 master，`drmSetMaster` 会 `EACCES`，且**重开 fd 也拿不回来**（`drm_auth.c:240-266`：`if (dev->master) return -EBUSY;`，非 root 先 `EACCES`）⇒ P1-5 |
| 8 | 实际帧率 | 每帧 1 次阻塞提交（`drm_atomic_helper_commit` 末尾等 vblank）+ 可能的 CPU 拷贝，60 fps 估计到不了。需要 `dumpsys SurfaceFlinger --latency` 或帧计数日志 |

---

## 5. 建议的上板验证顺序（把结论变成可证伪的观测）

1. **先验"能不能开起来"**：装 `hwcomposer.z1.so` 后看
   `z1hwc2: opened /dev/dri/card1 fd=.. driver=mediatek (waited NNN ms)` → `probe done: 240x320 planes=4` → `first commit done with modeset`。
   第一条就没有 ⇒ 按 P1-5 处理，**回退 = 把 `/vendor/lib/hw/hwcomposer.z1.so` 改名/删除**（open 失败不会自动回 fb0）。
2. **立刻查 fd 泄漏**：`ls /proc/$(pidof android.hardware.graphics.composer@2.1-service)/fd | wc -l` 连看三次（间隔 5 s）。每 5 s 涨几百 ⇒ P0 命中。
3. **看 `z1hwc2: atomic commit failed:`** 有没有出现；出现且屏不动 ⇒ P1-1 命中（失败被吞）。
4. **看 dmesg 有没有 `Disabling [PLANE:...] because [FB:...] is removed`**：有 ⇒ 清理顺序又退回去了（§3.6 的修复被破坏）。
5. 最后才看 `dumpsys SurfaceFlinger` 的合成类型是否真出现 `Device`（满血 OVL 的目标）。
