# Z1 HWC2 HAL 对抗式代码审查（2026-11-10）

> 目的：找出「只会在真板上暴露」的那一类 bug。上一次审查已抓到 `MODE` vs `MODE_ID`（"内核 ABI 字符串"类）。
> 本次按同一标准逐行重查 8 个方面：属性名 / 结构体布局 / HWC2 契约 / DRM atomic 用法 / 资源生命周期 / 并发 / 健壮性 / 性能陷阱。
>
> **结论：属性名这一类已经干净了**（板上 `z1drmtest` 实测也证实）；真正会咬人的是**资源生命周期**与**提交路径的失败语义**。
> 我找到 1 个 P0、5 个 P1。**审查期间作者在并行修复**，截至终检（`md5 48f17a97`）P0 与 3 个 P1 已被改掉，
> 剩下 2 个 P1 + 6 个 P2 + 7 个 P3 仍存在。每条都给了**机制 + 内核/AOSP 引用 + 板上症状 + 最小修法**，
> 已修的条目保留下来当**回归检查项**（改错了会立刻复现）。

## 0. 版本与时间线（必读）

| 时刻 | md5 | 行数 | 说明 |
|---|---|---|---|
| 21:56 | `38bc7c38` | 2512 | 本轮审查起点 |
| 22:24 | `7668f037` | 2691 | 分析基线 1 |
| 22:27 | `59a943a7` | 2696 | **本报告 §2 的"分析基线"**（P0 在此版本仍存在） |
| 22:31 | `e4a85820` | 2756 | P0/P1 大规模修复 |
| 22:3x | `52da9b2a` | 2771 | 中间复核版本 |
| 22:4x | `48f17a97` | 2831 | **终检版本**：下表 `[仍存在]` 的行号以此版本为准 |

所以：
- **`[已修]`** 行的行号是分析基线（`59a943a7`）的，留着做回归对照；
- **`[仍存在]`** 行的行号是终检版本（`48f17a97`）的。
- 文件仍在被编辑；**用"锚点代码" `grep -n` 定位最可靠**，行号只是辅助。

其它输入：策略头 `z1_policy.h`（204 行，未改）；内核 `fork_linux/`；客户端契约 `out/Z1_HWC2_CONTRACT_20261110.md` + `/tmp/aosp9/{hidl,sf}`（A9 源码）；板级证据 `docs/ovl/Z1_HANDOFF_20261109_HWC2.md` §2。

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

⇒ `plane_count == 4`、`alpha`/`blend`/`zpos`/`rotation` 在板上确实存在，格式集与 `hal_to_drm()` 完全对得上。
**"属性名写错"这一类风险已在板的层面排除**；剩余风险全部集中在资源与提交语义。

---

## 1. 结论摘要

| 级别 | 本轮共发现 | 终检仍存在 | 一句话 |
|---|---|---|---|
| **P0** | 1 | **0** | acquire fence fd 每帧每层泄漏 → composer `EMFILE` → 必死。**已在 22:31 修掉** |
| **P1** | 5 | **2** | 静默冻屏 / 旧 target 复现 / modeset 锁存 / fence 恒 -1 / **open 失败无回退** |
| **P2** | 13 | **6** | blob 泄漏、sw_sync 节点权限、offset 未处理、错误码、无 NOT_VALIDATED、矩形兜底… |
| **P3** | 7 | **7** | strdup 泄漏、id 截断、DPI 硬编码、dump 长度、dump 无锁… |

**本轮最危险的一条（现已修复）**：`z1_set_client_target()` / `z1_set_layer_buffer()` 把传入的 acquire fence
`dup()` 一份保存后**从不关闭传入的 fd**。按 AOSP 的所有权语义，HAL 返回 `HWC2_ERROR_NONE` 时该 fd 已归 HAL 所有，
这是**每帧每层 1 个 fd 的硬泄漏**：composer 服务（`user system`，`.rc` 无 rlimit ⇒ 软限 1024）约 10 秒连续动画后
`EMFILE`，binder 连 buffer handle 的 fd 都收不进来 → SF 失去 composer → `LOG_ALWAYS_FATAL` → 重启环。
**这条如果没修，首启必挂**；修法就是删掉 `dup()`（现在的代码 `l->acquire_fence = acquire_fence;` 即正确）。

**终检仍存在的最危险一条**：`z1_open()` 失败时**没有任何回退**（见 §2 P1-e）。它会把"HAL 的某个 probe 失败"
放大成"整块屏没有驱动"，而且回退必须人工把 `/vendor/lib/hw/hwcomposer.z1.so` 改名/删除。

---

## 2. 缺陷表

> 状态标记：`[已修]` = 终检时已被作者改掉（保留作回归检查）；`[仍存在]` = 终检版本仍有。
> **行号 = md5 `48f17a97`（2831 行）**；`[已修]` 行给的是分析基线（`59a943a7`）行号。
> 作者仍在编辑，行号会漂——**以每格的"锚点"为准**，一条 `grep -n "<锚点>"` 即可定位。

### P0

| 级别 | 状态 | file:line | 问题 | 为什么错（引用） | 最小修法 |
|---|---|---|---|---|---|
| **P0** | **`[已修]`**<br>22:31 | base `z1hwc2.cpp:1605`（client target）、`:1638`（层）<br>锚点：`= acquire_fence >= 0 ? dup(acquire_fence) : -1;` | acquire fence **dup 后不 close 传入的 fd** | ① composer 包装层在 HAL 返回 `NONE` 时把 fd 所有权交给 HAL：`/tmp/aosp9/hidl/ComposerCommandEngine.h:183-215`（`executeSetClientTarget`）与 `:356-381`（`executeSetLayerBuffer`）里 `closeFence=false`；② `/tmp/aosp9/hidl/HwcHal.h:319` 原文 "unlike in setClientTarget, releaseFence is owned by us" ⇒ 反过来说 setClientTarget/setLayerBuffer 的 fence 是"设备"的；③ `hwcomposer2.h:1527-1568 / 1980-2010` 只要求"HAL 保证 client 随时可关它" | 删掉 `dup()`，直接存传入的 fd（现在 `z1hwc2.cpp:1669` 的 `h->client_acquire_fence = acquire_fence;` 与 `:1704` 的 `l->acquire_fence = acquire_fence;` 即为正确实现） |
| | | **板上症状** | 180 fd/s 泄漏 ⇒ 约 300 帧后 `EMFILE`：logcat 出现 SF 的 `Failed to set buffer` 或 composer 的 `failed to get hwcomposer service`（`ComposerHal.cpp:172`）；`ls /proc/$(pidof android.hardware.graphics.composer@2.1-service)/fd | wc -l` 每 5 s 涨几百。**回归检查点**：这个数字必须稳定 | | |

### P1

| 级别 | 状态 | file:line | 问题 | 为什么错（引用） | 最小修法 |
|---|---|---|---|---|---|
| **P1-a** | **`[已修]`**<br>22:31 | base `:2353`<br>锚点：`return HWC2_ERROR_NONE; /* keep SurfaceFlinger alive */` | 两次 `drmModeAtomicCommit` 都失败时返回 `NONE`，不报错、不重试 | `hwcomposer2.h:1471-1505` 规定 present 失败必须返回错误码；返回 `NONE` 等于告诉 SF"这帧已上屏"，SF 不会重试 | 已改为 `HWC2_ERROR_NO_RESOURCES`（现版本正确） |
| | | **板上症状** | 屏停在最后一帧，日志只有一行 `atomic commit failed: Invalid argument`，之后 `frame N: ...` 照常打印 ⇒ 看着在出帧，实际没提交 | | |
| **P1-b** | **`[已修]`**<br>22:31 | base `:2086`<br>锚点：`h->client_expected = true;`（else 分支无条件） | 第二遍决策只要没走"全上硬件"分支就置 `client_expected=true`，即使一个层都没降级（`used == n`） | 该标志决定要不要把 `h->client_target` 放平面 0（`z1hwc2.cpp:2205` 附近）。SF 只在有 CLIENT 层时才 GL 合成并产出新 target（`DisplayDevice.cpp:367-373` + `SurfaceFlinger.cpp:2078-2096`）；`used == n` 时 target 是上一帧内容 ⇒ 旧帧被画到屏上 | 已改为 `h->client_expected = (used != n);`（现版本正确，且 `used==n` 时允许用满 4 个平面） |
| | | **板上症状** | 触发条件：可见层全部可上硬件、但最底层不是满屏/不透明（只有 Toast/小 SurfaceView、或底部悬浮层）。此时底层出现上一帧的 GL 合成内容 | | |
| **P1-c** | **`[已修]`**<br>22:31 | base `:2413`<br>锚点：`h->modeset_done = true;` | 首帧带 `ALLOW_MODESET` 失败后，纯平面回退只要返回 0 就锁死 `modeset_done`，此后再不 modeset | CRTC 未 active 时纯平面提交照样返回 0：`crtc_state->enable == false` ⇒ `drm_rect_clip_scaled()` 判不可见（`drm_atomic_helper.c:922-950`）⇒ 一个平面都没点亮，而模式设置分支（`!h->modeset_done`）永不再执行 | 现版本已改成"只有本帧真带 ALLOW_MODESET（或无可设置）才锁存"，并有重试上限（正确） |
| | | **板上症状** | 永久黑屏，日志只有 `plane-only commit accepted (no modeset)`，之后再无错误 | | |
| **P1-d** | **`[仍存在]`** | `z1hwc2.cpp:2255`<br>锚点：`*out_retire_fence = -1;`；release fence 段在 `:2496` 的 `if (h->fences_on)` | present fence 恒 `-1`；release fence 默认关（`debug.z1.hwc.fences=0`），且**即便置 1 也打不开节点**（见 P2-c） | `hwcomposer2.h:1433-1468`、`:1471-1505`。SF 侧：① 上一块 client target 立即放回 BufferQueue（`FramebufferSurface.cpp:162-180` 只在 `fence->isValid()` 时 addReleaseFence）⇒ 撕裂；② `DispSync` 收不到 present 样本（`SurfaceFlinger.cpp:1758-1764`），无法锁相。属注释承认的 Phase-1 取舍，但确实违反契约 | 短期：先修 P2-c 再默认开 fences；正解：`OUT_FENCE_PTR`（`drm_mode_config.c:305-311`，内核有） |
| **P1-e** | **`[仍存在]`** | `z1hwc2.cpp:2808`（`return -ENODEV;`）+ `:887`（`for (i = 0; i < 120; i++)`） | `z1_open()` 失败（card1 60 s 没出现 / `drmSetMaster` 一直失败 / probe 失败）⇒ **composer 服务彻底不存在，且没有 fb0 回退** | `HwcLoader::loadModule()` 只在 `hw_get_module()` 失败时才回退 gralloc/fb0（`/tmp/aosp9/hidl/HwcLoader.h:60-70`）；`open()` 失败只打一行 `failed to open hwcomposer device`（`:109`）返回 nullptr。当前板上能显示的路径正是"没有 `hwcomposer.z1.so` ⇒ 回退 fb0"（handoff §2）。一旦 init 把 `.so` 装进 `/vendor/lib/hw`（`out/z1_v83/embedded_init.z1.rc:42`），**open 失败 = 屏彻底没驱动** | ① 等节点预算从 60 s 压到 ≤ 35 s，日志明确写"超时会放弃"；② **回退必须写进脚本**：失败则 `mv /vendor/lib/hw/hwcomposer.z1.so{,.off}` 并重启 composer（这是唯一能回到今天可用状态的办法） |
| | | **板上症状** | `E ComposerHal: failed to open hwcomposer device: No such device`（v79 已出现过）→ composer 反复退出 → SF 在 `ComposerHal.cpp:172` abort → 重启环，屏停在二屏/黑屏 | | |

### P2

| 级别 | 状态 | file:line | 问题 | 为什么错（引用） | 最小修法 |
|---|---|---|---|---|---|
| **P2-a** | **`[仍存在]`** | `z1hwc2.cpp:2329`<br>锚点：`h->mode_blob = blob;` | 每次重试都新建 blob 并覆盖旧指针，旧 blob 永不销毁 | 内核 blob 带引用计数，只有 `drmModeDestroyPropertyBlob` 才释放；首帧持续失败时每帧泄漏 68 B blob + 一个 kernel object | 建之前先销毁旧的，或把 blob 缓存到 `modeset_done` 之外只建一次 |
| **P2-b** | **`[已修]`**<br>22:31 | base `:2353` | 失败分支跳过了 `pending_release` 清理（清理已挪到提交之后）→ 提交持续失败时 fb/gem 引用无限累积 | 现在是 `if (ret) { ...; h->pending_release.clear(); }`（正确） | — |
| **P2-c** | **`[仍存在]`** | `z1hwc2.cpp:557`<br>锚点：`open("/sys/kernel/debug/sync/sw_sync", O_RDWR \| O_CLOEXEC)` | 该 debugfs 文件是 `root:root 0644`，composer 是 `user system`、无 `CAP_DAC_OVERRIDE` ⇒ **必然 EACCES**，`fences_on` 永远 false | `sync_debug.c:110-120` 的 `debugfs_create_file_unsafe("sw_sync", 0644, ...)` | 用 `OUT_FENCE_PTR`；或在 init.rc 里 `chmod 0666 /sys/kernel/debug/sync/sw_sync`（注意 debugfs 挂载时序）。**P1-d 的短期方案依赖这条** |
| **P2-d** | **`[已修]`**<br>22:31 | base `:258` | klogf 无条件把最后一个字符改成 `\n`：格式化串不以 `\n` 结尾时最后一个字符被吃掉 | 现版本已引入 `had_nl`（正确） | — |
| **P2-e** | **`[已修]`**<br>22:31 | base `:721` | dumb 的 bpp 只区分 RGB565/其它：RGB_888 也建 32bpp dumb，但 `AddFB2` 用 24bpp 的 `BGR888` ⇒ pitch 与内容布局不一致 | 现版本 `create.bpp = hal_bpp(hal_format) * 8;`（正确） | — |
| **P2-f** | **`[已修]`**<br>22:31 | base `:736-757` | `CREATE_DUMB` 成功但 `MAP_DUMB`/`mmap`/`AddFB2` 失败时直接 return，`create.handle` 泄漏 | 现版本三处失败路径都 `DESTROY_DUMB(create.handle)`（正确） | — |
| **P2-g** | **`[仍存在]`** | `z1hwc2.cpp:672`、`:771`<br>锚点：`uint32_t offsets[4] = { 0, 0, 0, 0 };`；拷贝路径在 `:826` 一带用 `gh->fd` + 偏移 0 | 完全忽略 `private_handle_t.offset`（布局第 28 字节）；拷贝路径尺寸校验 `src_pitch*h > size + src_pitch` 多放过一行 | 当前 gralloc 恒 `offset=0`（`gralloc.cpp` 构造置 0；`pixel_copy.h:13-36` 的 `size = columns*rows*bpp+4` 也够大），所以现在不出事；一旦 gralloc 改成复用大 buffer 就整体错位 | `offsets[0] = gh->offset`；拷贝源地址加 `gh->offset`；尺寸校验改 `src_pitch*height > size` |
| **P2-h** | **`[已修]`**<br>22:31 | base `:1230` | client target 的 fb 缓存只比 `handle` 指针（层却比 `handle + src_fd`） | 现版本已加上 `&&` 的第二个条件（与层一致） | — |
| **P2-i** | **`[已修]`**<br>22:3x | base `:1415-1417` | 未知 `getDisplayAttribute` 返回 `HWC2_ERROR_BAD_PARAMETER`（`hwcomposer2.h:1253-1274` 只列 `BAD_DISPLAY`/`BAD_CONFIG`，未知属性应 `-1` + `NONE`） | 终检版本 `return HWC2_ERROR_BAD_PARAMETER` 已不存在 | — |
| **P2-j** | **`[仍存在]`** | `z1hwc2.cpp:1454`（`get_display_configs`）、`:1468`（`get_active_config`）、`:1478`（`set_active_config`） | 三个函数全都 `(void)display;`，不返回 `HWC2_ERROR_BAD_DISPLAY` | 契约要求 `BAD_DISPLAY`（`hwcomposer2.h` 对应段落） | 三处加 `if (display != 0) return HWC2_ERROR_BAD_DISPLAY;` |
| **P2-k** | **`[仍存在]`** | `z1hwc2.cpp:2241-2262`（`z1_present_display` 开头：`*out_retire_fence = -1` 之后只有 `display`/`powered_on` 检查，直接进 wait 循环） | 不检查 `h->validated` 就提交 | `hwcomposer2.h:1471-1505` 要求 present 前必须成功 validate，否则返回 `NOT_VALIDATED`；契约文档 §8.3 也列了这条 | 加检查，并在每次 `setLayer*` 后清 `validated` |
| **P2-l** | **`[已修]`**<br>22:31 | base `:1563-1580` | `setColorTransform` 返回 `NONE` 但完全不应用矩阵 ⇒ 有 Device 合成时 SF 不做 GL 矩阵（`SurfaceFlinger.cpp:2917-2921` 的 `applyColorMatrix = !hasDeviceComposition && !skipClientColorTransform`）⇒ 夜间模式/色彩校正静默失效。注意：返回 `UNSUPPORTED` **不是**修法（`applyColorMatrix` 不看返回码） | 终检版本已引入 `h->color_transform` 并把它 OR 进降级条件（`mode_force_client \|\| color_transform`），等价于"有矩阵时全走 CLIENT"（正确） | — |
| **P2-m** | **`[仍存在]`** | `z1hwc2.cpp:1170`（`program_plane`）及调用点 | `program_plane` 不做任何矩形兜底：`l->frame.right - l->frame.left` 是 int 减法，异常 rect（如 `INT_MIN`）会溢出成大正数 | 决策策略挡住绝大多数（`z1_policy.h:97-113`），但 `program_plane` 是最后一道门；溢出成巨值 `CRTC_W` 会被 `drm_atomic.c:747-757` 判 `-ERANGE` ⇒ 整帧提交失败 | 入口对 `x,y,w,h` 做 `<= 0 \|\| > 8192` 的钳位/丢弃 |

### P3

| 级别 | 状态 | file:line | 问题 | 最小修法 |
|---|---|---|---|---|
| P3 | **`[仍存在]`** | `z1hwc2.cpp:879` | `strdup(prop)` 永不释放 | 用 static 缓冲 |
| P3 | **`[仍存在]`** | `z1hwc2.cpp:2791` | `next_layer_id` 是 u64，但 `createLayer` 只回 u32（`hwcomposer2.h` 的 `outLayer` 就是 `uint32_t*`）；超 2^32 后 id 撞车 | 回绕时查 map |
| P3 | **`[仍存在]`** | `z1hwc2.cpp:1440` | DPI 硬编码 160000（160 dpi）；面板 240×320 实际约 200 dpi | 从属性读，或返回 `-1` 让 SF 用默认 |
| P3 | **`[仍存在]`** | `z1hwc2.cpp:2578`（`z1_dump`） | 第二次调用时 `*out_size = snprintf()` 的返回值可能大于调用方容量（当前字符串 ~190 B，不触发） | 用 `min(n, cap)` |
| P3 | **`[仍存在]`** | `z1hwc2.cpp:1399` | `destroyLayer` 忽略 `display` | 加 `BAD_DISPLAY` 检查 |
| P3 | **`[仍存在]`** | `z1hwc2.cpp:1061` | `p_rotation` 查了就再没用（`transform != 0` 一律降 CLIENT，是正确的） | 删掉或注释说明 |
| P3 | **`[仍存在]`** | `z1hwc2.cpp:2578`（`dump` 读 `h->layers`）、`validate` 里写 `g_verbose` | HAL 被 4 个 binder 线程调用（`service.cpp:35` `defaultPassthroughServiceImplementation<IComposer>(4)`），`dump`/`getCapabilities` 不走命令队列 ⇒ 与 present 并发读 map；`g_verbose` 是无锁全局 | 只读统计可接受；`dump` 应先拷贝 size 或加锁 |

---

## 3. 已核对、确认正确的部分（覆盖说明）

> 每一条都对着源码核过；"板上已验"指 handoff §2 的 `z1drmtest` 实测。

### 3.1 内核 ABI 字符串 / 对象类型

| 项 | 核对结果 |
|---|---|
| `MODE_ID` | 存在：`drm_mode_config.c:337`；blob 属性挂在 CRTC：`drm_crtc.c:320`。代码先查 `MODE_ID` 再退 `MODE`，**正确**（上次的致命 bug 已修） |
| `ACTIVE` | `drm_mode_config.c:330`；CRTC 挂载 `drm_crtc.c:319` |
| `CRTC_ID`（connector） | `drm_mode_config.c:317`；connector 挂载 `drm_connector.c:316` |
| `FB_ID` / `CRTC_ID` / `CRTC_X,Y,W,H` / `SRC_X,Y,W,H` | `drm_mode_config.c:251-317`，全部存在 |
| `zpos` | `drm_blend.c:381`（可变）/`:420`（immutable）；mtk 用 immutable：`mtk_plane.c:340` |
| `alpha` | `drm_blend.c:229`；mtk `mtk_plane.c:354`。**板上已验** |
| `pixel blend mode` | `drm_blend.c:597`；mtk `mtk_plane.c:359`，且 `mt6572_ovl_driver_data.blend_modes` 三种齐全（`mtk_disp_ovl.c:707-709`）⇒ 属性存在。**板上已验** |
| `rotation` | `drm_blend.c:296`；mtk `mtk_plane.c:346-352`。**板上已验** |
| `OUT_FENCE_PTR` / `IN_FENCE_FD` | `drm_mode_config.c:305-311` 都有（当前未用，以后要真 fence 可直接用） |
| `DRM_MODE_OBJECT_*` | 用的 `PLANE/CONNECTOR/CRTC` 都是 libdrm 标准值，与内核一致 |
| sw_sync UAPI | 结构体/ioctl 号逐字段一致（`sw_sync.c:44-58`）；`obj->value += inc`（`sw_sync.c:209-238`）⇒ "INC 1 推进 1" 成立；节点路径正确（`sync_debug.c:110-120`） |

### 3.2 结构体 / 布局假设

- `hw_device_t`=64 B、`close@60`；`hwc2_device_t` 的 `getCapabilities@64`/`getFunction@68`：与 `hardware.h`/`hwcomposer2.h` 一致，且**已有编译期 `static_assert`**（`z1hwc2.cpp` 的 ABI 自检段）——这类 bug 以后不可能再静默存在。
- gralloc `private_handle_t`：`gralloc_priv.h` 的字段顺序/类型 = `z1_handle`，`sizeof==64`、`fd@12`、`width@44`、`height@48`、`stride@52`、`format@56` 全对（同样有 static_assert）。
- `stride` 语义是**像素**（`pixel_copy.h:22-35`），`pitch = stride * bpp` 正确。
- `hwc_rect_t/hwc_frect_t/hwc_color_t/hwc_region_t` 与 AOSP 布局一致（`hwcomposer_defs.h:54-84`）。
- `hw_device_t.version = HWC_DEVICE_API_VERSION_2_0 = 0x02000001`（`hwcomposer_defs.h:42`）⇒ `HwcLoader.h:113` 的 `major = version>>24 == 2` 通过，走原生 HWC2。

### 3.3 HWC2 契约

- `getFunction()` 对 `HwcHal::initDispatch()`（`HwcHal.h:521-584`）要求的 **42 个描述符全部返回非 NULL**（含 `CREATE_VIRTUAL_DISPLAY`/`DESTROY_VIRTUAL_DISPLAY`/`GET_MAX_VIRTUAL_DISPLAY_COUNT`/`GET_CLIENT_TARGET_SUPPORT`/`DUMP`/`SET_OUTPUT_BUFFER`/`SET_LAYER_COLOR`/`SET_CURSOR_POSITION`）；`>= SET_LAYER_FLOAT_COLOR`(=46) 的 2.2 描述符返回 NULL 也正确（`hwcomposer2.h:265-273`）。
- `getCapabilities` 返回 0 个 cap：避开 `SKIP_VALIDATE`（会让层永远停在 INVALID），也避开 `PRESENT_FENCE_IS_NOT_RELIABLE`（`HwcHal.h:75-91` 会因此**拒绝本设备**）。
- hotplug：`registerCallback` 内同步回调 `(data,0,CONNECTED)` 安全——`HwcHal::registerEventCallback` 先赋 `mEventCallback` 再 dispatch（`HwcHal.h:110-124`），`ComposerClient::registerCallback` 也是先建 `mHalEventCallback`（`ComposerClient.h:119-124`）。
- `validateDisplay` 返回码自洽（`NONE ⇔ changed.empty()`，`*out_num_types==changed.size()`）；`*out_num_requests=0` 与 `getDisplayRequests` 的 0 一致。
- 三个计数查询接口都判 NULL 数组，与 `HwcHal.h:340-410` 的"先 NULL 取数量"模式匹配。
- 组合类型方向：只报 `→CLIENT`，从不把 CLIENT 改 DEVICE（`z1_policy.h:146-153`），符合 `hwcomposer2.h:124-131` 与 `HWComposer.cpp:109-126`。
- "CLIENT 连续且在底部"：`z1_policy_decide` 从顶往下走，遇到第一个降级就 `client_above=1` 把下面全拉 CLIENT（`z1_policy.h:141-199`），与契约文档 §1.4 算法一致。
- INVALID 首帧：策略给类型并 `report=1` → `HAS_CHANGES`；而 SF 每帧会因自身缓存值不同重新 `setCompositionType(Device)`（`BufferLayer.cpp:653-661` + `Layer.cpp:730-748`），所以"把 requested 覆盖成 validated"**不会**让层永久钉在 CLIENT（这点我专门验证过）。
- `getActiveConfig` 恒成功、`VSYNC_PERIOD` 恒 > 0 ⇒ 不会触发 `SurfaceFlinger.cpp:1287-1290` 的空指针，也不会让 `DispSync` 周期为 0。
- `acceptDisplayChanges` 清 pending 并置 validated，符合"等价于 set+re-validate"。
- `destroyLayer` 会 `fb_forget` + close 两个 fence；`getReleaseFences` 交所有权即置 -1 ⇒ 无双重 close。
- `plane_count < 2` 才拒绝；SOLID_COLOR/SIDEBAND 一律降 CLIENT（`z1_policy.h:154-167`）。

### 3.4 DRM atomic 用法

- 首帧三件套 `connector CRTC_ID + crtc MODE_ID + crtc ACTIVE=1` + `ALLOW_MODESET` 是内核最小集合；`drm_atomic_helper_commit_crtc_enable()`（`drm_atomic_helper.c:1652-1689`）会真正调用 `mtk_crtc_atomic_enable()`，即使 fbdev 没接管也能把 DDP 初始化。
- 模式 blob 用 `drmModeModeInfo`（68 B，与内核 `drm_mode_modeinfo` 同构），长度校验刚好通过。
- 多余平面显式 `FB_ID=0 + CRTC_ID=0` 关闭：应对"未提交平面继续扫描旧 buffer"的 mtk 行为，写得对。
- 负坐标安全：`CRTC_X/Y` 是 `DRM_MODE_PROP_SIGNED_RANGE, INT_MIN..INT_MAX`（`drm_mode_config.c:275-281`）；内核 `state->crtc_x = U642I64(val)`（`drm_atomic_uapi.c:560-563`）⇒ `(uint64_t)(uint32_t)x` 截断后正好还原成负数；越界由 `drm_atomic.c:747-757` 的 `INT_MAX - crtc_w` 兜住。
- 无缩放/裁剪：`SRC_*` 必须落在 fb 内（`drm_atomic.c:759-781`），策略保证 `crop ≤ buffer`；`hscale/vscale` 检查在 `drm_atomic_helper.c:932-938`，策略保证 `src == dst`；分数 crop 会被策略判成 scaling 而降 CLIENT ⇒ 不会吃 `-ERANGE`。
- `drmModeAddFB2` 的 pitch/cpp 与 `hal_to_drm()` 逐项对得上 `mt8173_formats[]`（`mtk_disp_ovl.c:116-128`）：`ABGR8888/XBGR8888/ARGB8888/BGR888/RGB565` 全在表内；`RGB565` 用 `DRM_FORMAT_RGB565` 而非 BGR565 是**对的**（`fmt_rgb565_is_0=false`，`:705`）。
- 平面 alpha 量化到 0..255 正确：驱动 `mtk_disp_ovl.c:543` 是 `con |= state->base.alpha & OVL_CON_ALPHA`，`OVL_CON_ALPHA==0xff`（`:96`）。
- 平面能力：mt6572 只有 `main_path`（`mtk_drm_drv.c:249-257`）⇒ 1 个 CRTC、`possible_crtcs&1` 成立；RDMA 不贡献平面（`mtk_crtc.c:934-950` 要求 `comp_idx==1` 有 `bgclr_in_on`，`ddp_rdma` 没有，`mtk_ddp_comp.c:371-385`）⇒ **正好 4 个平面**，与板上 `count=4 id=35/41/47/53` 一致；排序后 zpos 0..3（plane 3 是 Cursor 型，当普通层用没问题——只有 async 路径才按类型区分，`mtk_plane.c:92-96`）。
- `drmModeCreatePropertyBlob` 失败会退化成 flags=0 的平面提交，不会崩。

### 3.5 资源与并发

- `fb_import` 成功/失败两条路径的 gem 引用都对（成功存进 `z1_fb.gem`，失败立即 `drmCloseBufferHandle`）；`pending_release` 成对处理 `(fb, gem)` 且已挪到提交之后（顺序正确）。
- `dumb_ensure` 尺寸变化时先 `munmap` + RmFB + DESTROY_DUMB 再重建；失败路径现在都销毁 dumb handle。
- `z1_close` 顺序正确：先停 watchdog（`pthread_join`）→ 再进 `fence_lock`（不会死锁）→ 关层/target/pending/shadow/blob/fd → `delete`。
- 拷贝回退先等 acquire fence（在 `client_target_fb` 之前），再用 `DMA_BUF_IOCTL_SYNC(START|READ)/(END|READ)` 包住 memcpy——顺序正确。
- `wait_fence()` 用 poll + 3 次递增超时，出错不阻塞主流程；close 后才置 -1。
- 只有 watchdog 一个线程，与 present 的共享状态（`fences_pending`/`timeline_val`）在 `fence_lock` 内修改；`wd_stop` 用 `volatile` 在本实现里够用。
- release fence 的 signal 时机已挪到**本帧 commit 之后**（注释与代码一致，语义正确）。
- `pending_release` 清理已挪到 commit 成功之后（否则会对仍在扫描的 fb 调 `drmModeRmFB`，内核 `drm_framebuffer_remove()` 会强制关平面：`drm_framebuffer.c:1146-1176`、`:999-1130`）——顺序正确，**这是本轮修得最漂亮的一处**。

---

## 4. 没有板子无法确认的事项（以及会让哪些结论失效）

| # | 事项 | 影响 |
|---|---|---|
| 1 | `libhidl` 的 `IComposer::getService()` 重试预算到底多少（AOSP 常见 500×100 ms = 50 s，但本地无此源码） | 若 50 s：card1 在 ~35.7 s 出现，只剩 ~14 s 余量；若更短（有的版本 5 s）：**当前设计不可能首启成功** |
| 2 | `/dev/kmsg` 实际权限（本 ramdisk 的 `ueventd.rc` 无 kmsg 规则，Android 对未列出节点默认 0600 root:root） | 若 0600，uid system 的 HAL 写 kmsg 全 EACCES；代码已把 ALOG 当主通道，只影响"串口/dmesg 能否直接看到 HAL 日志"。上板 `ls -l /dev/kmsg` 一句话确认 |
| 3 | `drmPrimeFDToHandle`/`drmModeAddFB2` 在本板真实 gralloc 上能否成功（dma-heap 不保证物理连续；`mtk_gem.c:212-231` 会 `-EINVAL`） | 决定"有没有任何层能上硬件"。全失败则一切走 CPU 拷贝回退；看 `drmPrimeFDToHandle(N) failed` 那行 |
| 4 | `mtk_ddp_comp_power_on()`（DIS 电源域）是否成功 | `mtk_crtc_atomic_enable()` 失败时**只打一行 `Failed to enable power domain` 就 return**（`mtk_crtc.c:777-800`，`:789`），提交照样返回 0 ⇒ HAL 打印 "first commit done with modeset" 而屏黑。这是"看起来成功但没显示"的最大黑洞 |
| 5 | panel/DSI 在 HAL 接管时是否真的出图 | 决定首帧 modeset 是否会被内核静默吞掉 |
| 6 | fbdev（`CONFIG_DRM_FBDEV_EMULATION=y`）在 HAL 首帧时是否已接管 CRTC | 决定首帧走"纯平面提交"还是"必须模式设置"。两条路径代码都覆盖，实际走哪条只能看日志 |
| 7 | DRM master 竞争的实际结果（gralloc 服务 `z1_heap.cpp:640-664` 会 `DROP_MASTER`；`z1drmtest` 短命） | 若某进程长期持 master，`drmSetMaster` 会 `EACCES`，重开 fd 也拿不回来（`drm_auth.c:240-266`）⇒ P1-e |
| 8 | 实际帧率 | 每帧 1 次阻塞提交（`drm_atomic_helper_commit` 末尾等 vblank）+ 可能的 CPU 拷贝，60 fps 估计到不了。需 `dumpsys SurfaceFlinger --latency` |

---

## 5. 建议的上板验证顺序（把结论变成可证伪的观测）

1. **先验"能不能开起来"**：装 `hwcomposer.z1.so` 后看
   `z1hwc2: opened /dev/dri/card1 fd=.. driver=mediatek (waited NNN ms)` → `probe done: 240x320 planes=4` → `first commit done with modeset`。
   第一条就没有 ⇒ 按 P1-e 处理；**回退 = 把 `/vendor/lib/hw/hwcomposer.z1.so` 改名/删除**（open 失败不会自动回 fb0）。
2. **查 fd 是否稳定**（P0 回归）：`ls /proc/$(pidof android.hardware.graphics.composer@2.1-service)/fd | wc -l` 连看三次（间隔 5 s）。必须基本不涨。
3. **看 `z1hwc2: atomic commit failed:`** 有没有出现；出现且屏不动 ⇒ P1-a 类问题回来了。
4. **看 dmesg 有没有 `Disabling [PLANE:...] because [FB:...] is removed`**：有 ⇒ `pending_release` 的清理顺序又退回去了。
5. 最后看 `dumpsys SurfaceFlinger` 的合成类型是否真出现 `Device`（满血 OVL 的目标）。
