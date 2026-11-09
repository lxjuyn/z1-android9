# Z1 定制 HWC2 HAL 的「客户端契约」——Android 9 (Pie) SurfaceFlinger 侧精确规格

- 文档版本：2026-11-10（v1）
- 目标读者：写 `/vendor/lib/hw/hwcomposer.z1.so`（`hwc2_device_t`，HIDL composer 2.1 passthrough）的实现者
- 结论用途：回答 A–E 共 12 个问题，给出可执行规则 + 风险表 + 检查清单

---

## 证据等级与取证方法（先读）

| 标记 | 含义 |
|---|---|
| **【Q】** | 源码原文引用。本地源给出 `文件:行号`；AOSP 源给出 `tag android-9.0.0_r1` 的精确行号 + URL 构造方式 |
| **【V】** | 由【Q】原文用一步逻辑直接推出（推理链写在文中） |
| **【I】** | 我的推断/工程建议，**无直接源码支撑**，不当作事实 |
| **【未确认】** | 无法在本机确认，明确列出它会让哪些结论失效 |

取证方式（可复核）：

```bash
# AOSP Pie 源码按 tag 精确下载（gitiles ?format=TEXT 是 base64）
curl -sSL "https://android.googlesource.com/platform/frameworks/native/+/refs/tags/android-9.0.0_r1/services/surfaceflinger/SurfaceFlinger.cpp?format=TEXT" | base64 -d > SurfaceFlinger.cpp
# 本报告引用的所有 AOSP 文件均以此方式下载到 /tmp/aosp9/ 后逐行核对
```

**URL 构造约定**（下文用 `U_SF` / `U_HW` 简写；gitiles 的行锚点是 `#<行号>`）：

| 简写 | 基址 |
|---|---|
| `U_SF` | `https://android.googlesource.com/platform/frameworks/native/+/refs/tags/android-9.0.0_r1/services/surfaceflinger/` |
| `U_SFD` | `https://android.googlesource.com/platform/frameworks/native/+/refs/tags/android-9.0.0_r1/services/surfaceflinger/DisplayHardware/`（`HWComposer.cpp`、`HWC2.cpp`、`ComposerHal.cpp`、`FramebufferSurface.cpp` 都在这个子目录里） |
| `U_HW` | `https://android.googlesource.com/platform/hardware/libhardware/+/refs/tags/android-9.0.0_r1/include/hardware/hwcomposer2.h` |
| `U_WIRED` | `https://android.googlesource.com/platform/hardware/interfaces/+/refs/tags/android-9.0.0_r1/graphics/composer/2.1/` |

即：`U_SF + SurfaceFlinger.cpp#2990` = `https://android.googlesource.com/platform/frameworks/native/+/refs/tags/android-9.0.0_r1/services/surfaceflinger/SurfaceFlinger.cpp#2990`。

下文中为表格紧凑起见，有些引用省略了前缀（形如 `` `SurfaceFlinger.cpp#2990` ``），按文件名即可定位：
`SurfaceFlinger.cpp` / `Layer.cpp` / `BufferLayer.cpp` / `BufferLayerConsumer.cpp` / `DisplayDevice.cpp` / `EventThread.cpp` / `DispSync.cpp` → `U_SF`；
`HWComposer.cpp` / `HWC2.cpp` / `ComposerHal.cpp` / `FramebufferSurface.cpp` → `U_SFD`；
`hwcomposer2.h` → `U_HW`；`/tmp/aosp9/hidl/*` → 已下载到本地的 `U_WIRED` 文件。

**两点重要更正/前提**（都已实测核对）：

1. 任务描述说 `z1hwc2/include/hardware/hwcomposer2.h` 比 Pie 新——**实测不成立**：
   `diff -u /tmp/aosp9/hw/hwcomposer2_pie.h .../z1hwc2/include/hardware/hwcomposer2.h` 输出为 **0 行**，
   即本地头文件与 `android-9.0.0_r1` 的 `hardware/libhardware/include/hardware/hwcomposer2.h` 逐字节相同。
   本报告全部行号同时适用于本地头文件。【Q，已执行 diff，复现命令见 §9.3】
2. 真实调用链是三层：**SF → HIDL `IComposerClient`（composer 2.1）→ passthrough 适配器 `HwcHal` → 我们的 `hwc2_device_t`**。
   所以「SF 期望什么」与「`hwc2_device_t` 规格要求什么」并不完全等价：中间适配层对若干错误是**宽容**的（见 §2.1、§2.3），
   但这不构成可以违反 `hwcomposer2.h` 的理由（CTS / 其它客户端 / 后续 Android 版本不宽容）。

本地参考实现 `/tmp/drmhwc/` 已核对为 **Pie 时代 HWC2 版 drm_hwcomposer**（有 `drmhwctwo.cpp`、`separate_rects.h`，`RegisterCallback` 里带 `HWC2::Callback::Hotplug`），下文引用时给 `文件:行号`。

---

## 0. TL;DR —— 一个正确 HAL 必须遵守的 10 条硬规则

1. **生命周期闭环**：`getFunction()` 必须对 HIDL 适配器 `initDispatch()` 列表里的**每一项**返回非 NULL（共 42 个必需项 + 1 个条件项 `SET_LAYER_SIDEBAND_STREAM`，含 `CREATE_VIRTUAL_DISPLAY`、`DESTROY_VIRTUAL_DISPLAY`、`GET_MAX_VIRTUAL_DISPLAY_COUNT`、`GET_CLIENT_TARGET_SUPPORT`、`DUMP`、`SET_OUTPUT_BUFFER`、`SET_LAYER_COLOR`、`SET_CURSOR_POSITION` 这些"看起来用不到"的）；并且在 `registerCallback(HWC2_CALLBACK_HOTPLUG, …)` 之后必须**主动补发一次 `HWC2_CONNECTION_CONNECTED`（主显示）**。缺任何一个 → HAL 加载失败；缺 hotplug → SF 启动即 `LOG_ALWAYS_FATAL`（`U_SF + SurfaceFlinger.cpp#682`；`U_HW + hwcomposer2.h#837-844`；`/tmp/aosp9/hidl/HwcHal.h:520-586`）。
2. **display config 永远可用**：`getDisplayConfigs` 至少返回 1 个 config，每个 config 的 5 个属性（WIDTH/HEIGHT/VSYNC_PERIOD/DPI_X/DPI_Y）都可答，`VSYNC_PERIOD` 必须是**正数纳秒**；`getActiveConfig` **任何时候都不得返回 `HWC2_ERROR_BAD_CONFIG`**——SF 会把 nullptr 直接解引用（`U_SF + SurfaceFlinger.cpp#1287-1290`、`U_SFD + HWComposer.cpp#270-279`）→ SF 段错误。
3. **validate 返回码与计数必须自洽**：不需要改任何层的类型 → 返回 `HWC2_ERROR_NONE` 且 `*outNumTypes==0`；需要改 → 返回 `HWC2_ERROR_HAS_CHANGES` 且 `*outNumTypes>0`，并且 `getChangedCompositionTypes` 必须恰好吐出这 `outNumTypes` 条（`U_HW + hwcomposer2.h#1900-1931`、`#1131-1162`）。
4. **三个 count 查询接口必须支持"数组指针为 NULL"的调用**：`getChangedCompositionTypes`、`getDisplayRequests`、`getReleaseFences` 都会被适配器先以 `(…, NULL, NULL)` 调一次只取数量（`/tmp/aosp9/hidl/HwcHal.h:340-345`、`356-368`、`398-410`）。写成 `layers[i]=…` 不判空 → composer 服务 SIGSEGV → SF 失去 composer。
5. **组合类型只能往下改**：允许 `DEVICE→CLIENT`（以及 `CURSOR/SIDEBAND/SOLID_COLOR→CLIENT`），**绝不允许 `CLIENT→DEVICE`**（`U_HW + hwcomposer2.h#124-131`；`U_SFD + HWComposer.cpp#109-126` 里 `Client` 分支 `valid=false`）。
6. **z 序必须自洽**：SF 不检查"CLIENT 是否都在 DEVICE 之下"；把客户端 target 固定放在最底层平面后，HAL 必须**自己保证 CLIENT 组连续且在最底部**——凡是有一个层要落到 CLIENT，它下面的所有层都必须一起改成 CLIENT（§1.4 给出算法）。否则画面层次错乱。
7. **release fence 必须给**：每一帧里、每个"换了新 buffer 的 DEVICE 层"都要在 `getReleaseFences` 交出"上一块 buffer 已无人读取"的 fence。返回 `-1` 等于告诉 SF"上一块可以立刻写"（`U_HW + hwcomposer2.h#1440-1445`）→ 撕裂（§3.2）。
8. **present fence 必须给真实 fence**：`presentDisplay` 的 `*outPresentFence` 不能恒为 `-1`。它会成为客户端 target buffer 的 release fence（`U_SFD + FramebufferSurface.cpp#162-172`）和 DispSync 的误差输入（`U_SF + SurfaceFlinger.cpp#1758-1764`）；给 `-1` → target 提前被重画（撕裂）+ 显示时钟永不锁定。
9. **acquire fence 必须"等到/交给 KMS"，不能只 close**：`setClientTarget`/`setLayerBuffer` 传进来的 acquire fence，HAL 要 `dup()` 保留并作为该平面的 `IN_FENCE_FD` 提交（或先 wait）；只 `close()` 不等待 = 可能扫描到没画完的 buffer（`U_HW + hwcomposer2.h#1540-1544`、`#1990-1994`）。
10. **不要 advertise `HWC2_CAPABILITY_SKIP_VALIDATE` / `SKIP_CLIENT_COLOR_TRANSFORM` / `PRESENT_FENCE_IS_NOT_RELIABLE`**：`SKIP_VALIDATE` 会让 HIDL 命令引擎先试 `presentDisplay` 并让 SF 跳过 `getChangedCompositionTypes`（`/tmp/aosp9/hidl/ComposerCommandEngine.h:277-289` + `U_SFD + HWComposer.cpp#437-456`）；`SKIP_CLIENT_COLOR_TRANSFORM` 会让 SF 不再在 GL 里做色彩矩阵（`U_SF + SurfaceFlinger.cpp#2915-2921`）。**最小 cap 集 = 空集。**

> 现状：`z1a9_gpt/scripts/z1hwc2/z1hwc2.cpp` 在本次调研期间被**连续改写**（21:13 的 1196 行版 → 21:36 的 2348 行版 → 21:40 的 2302 行版，md5 `e51422b145d94c69128d48410aca1c12`）。**最新版已满足第 1/3/4/5/6/9/10 条，仍缺第 7、8 条（release / present fence）**，逐条快照见 §7。

---

## 1. A —— 组合类型（composition type）与 z 序

### 1.1 一帧里 SF 到底做了什么（顺序）

`SurfaceFlinger::handleMessageRefresh()`（`U_SF + SurfaceFlinger.cpp#1528-1542`）顺序固定：

```
preComposition → rebuildLayerStacks() → setUpHWComposer() → doComposition() → postComposition()
```

- `rebuildLayerStacks()`（`#1814-1886`）：按 z 序遍历当前所有 layer，凡 `visibleNonTransparentRegion ∩ display.bounds` 非空者进入 `layersSortedByZ`（**升序**，index 0 = 最底），否则 `destroyHwcLayer()`（`#1839-1852`）。结果存进 `DisplayDevice::setVisibleLayersSortedByZ`。
- `setUpHWComposer()`（`#1958-2076`）对每个 **亮着** 的 display：
  1. 对 `visibleLayersSortedByZ[i]`：`createHwcLayer()` 失败 → `forceClientComposition()`（`#2004-2006`）；成功则 `setGeometry(display,i)`（`#2010`），i **就是发给 HAL 的 z**（见 1.2）。
  2. 之后再走一遍（`#2018-2055`）：HDR/secure 等强制 CLIENT（`#2030-2046`），否则 `setPerFrameData()`。
  3. `displayDevice->prepareFrame(*mHwc)`（`#2063-2075`，非亮屏 continue）→ `HWComposer::prepare()` → **validate**。
- `doComposition()`（`#2078-2096`）：仅亮屏 display → `doDisplayComposition` → `doComposeSurfaces`（GL 合成）→ `displayDevice->swapBuffers()`（EGL swap + `advanceFrame()` → **`setClientTarget`**）；**`doComposition()` 末尾会调用 `postFramebuffer()`**。
- `postFramebuffer()`（`#2099-2152`，由 `doComposition()` 末尾调用）：**`presentDisplay` + `getReleaseFences`**，再逐层 `onLayerDisplayed(releaseFence)`。
- `postComposition()`（`#1706-1795`，最后一步）：present fence → `FenceTime`/DisplayTimeline/`DispSync::addPresentFence()`、帧延迟统计（**不处理** release fence）。

要点（**这里决定了 HAL 的协议边界**）：

- **validate 发生在前，`setClientTarget` 发生在 validate 之后、present 之前**（`swapBuffers` 里）。所以 validate 阶段 HAL 只能"决定"要用 target，不能读它。
- `setLayerBuffer`（换 buffer）也发生在 validate 之前（`setPerFrameData` → `BufferLayer::setPerFrameData` → `hwcLayer->setBuffer()`，`U_SF + BufferLayer.cpp#677-688`）。所以 **validate 时 HAL 已经能看到这一帧要上屏的 buffer handle**，可以做 import 检查。

### 1.2 z 值的真实含义（必须知道）

`Layer::setGeometry(displayDevice, z)` 中 `z` = 该层在"可见层升序数组"中的下标，直接下发：

```cpp
// U_SF + Layer.cpp#589-594
    error = hwcLayer->setZOrder(z);
```

而 `hwcomposer2.h` 只约定大小关系：

```c
// U_HW + hwcomposer2.h#2312-2329
/* setLayerZOrder(..., z)
 * Sets the desired Z order (height) of the given layer. A layer with a greater
 * Z value occludes a layer with a lesser Z value. */
```

**【V】** z 是"可见层序号"，不是 layer 的绝对 z-order；层被遮挡到不可见时会从列表消失（HWC layer 被 destroy），下次出现时序号会变。HAL 不得把 z 当成稳定 id，也不得用 z 值做数组下标。

**CLIENT 层同样会收到 `setZOrder`**（`setGeometry` 在 `#2010` 对每个可见层都调用），因此 HAL **可以**知道 CLIENT 层相对于 DEVICE 层的位置——这是 §1.4 算法的基础。

### 1.3 SF 是否要求"CLIENT 全部在 DEVICE 之下"？

**SF 侧没有任何强制**：

- SF 每帧默认给所有有 buffer 的层请求 DEVICE（`U_SF + BufferLayer.cpp#654-661`），
- 只有 HDR/PQ/HLG/Y410/secure/变换非法/`createHwcLayer` 失败等才会把某一层 **强制 CLIENT**（`SurfaceFlinger.cpp#2030-2046`、`Layer.cpp#494-496`、`#622-626`），
- **没有任何代码**把"被强制 CLIENT 层下面的层"跟着降级。
- 反过来，HAL 的变更由 `HWComposer::prepare` 直接采信（`U_SFD + HWComposer.cpp#483-500`），`validateChange` 只做"合法性告警"，**不阻断**：

```cpp
// U_SFD + HWComposer.cpp#109-126
void HWComposer::validateChange(HWC2::Composition from, HWC2::Composition to) {
    bool valid = true;
    switch (from) {
        case HWC2::Composition::Client:  valid = false; break;   // CLIENT→任何 非法
        case HWC2::Composition::Device:
        case HWC2::Composition::SolidColor: valid = (to == HWC2::Composition::Client); break;
        ...
    }
    if (!valid) ALOGE("Invalid layer type change: %s --> %s", ...);   // 只打日志
}
```

- 而 SF 的画法是把**所有** CLIENT 层按 z 升序画进**同一块** target：

```cpp
// U_SF + SurfaceFlinger.cpp#2987-3026（节选）
    ALOGV("Rendering client layers");
    for (auto& layer : displayDevice->getVisibleLayersSortedByZ()) {
        const Region clip(bounds.intersect(displayTransform.transform(layer->visibleRegion)));
        ...
        switch (layer->getCompositionType(hwcId)) {
            case HWC2::Composition::Device: case HWC2::Composition::Cursor:
            case HWC2::Composition::Sideband: case HWC2::Composition::SolidColor: { ... break; }
            case HWC2::Composition::Client: { layer->draw(renderArea, clip); break; }
        }
    }
```

**verdict（Q1）**
- 绘制顺序 = `visibleLayersSortedByZ` 升序；DEVICE 层 SF 完全不画，只画 CLIENT 层。
- **SF 不要求、也不保证 CLIENT 在 DEVICE 之下**。规格里没有这条约束（`hwcomposer2.h` 全文关于 target 只有"CLIENT 层会被合成进 target、非 CLIENT 层由 device 与 target 一起合成"，`#120-131`、`#1527-1568`）。
- 因此**保持 z 序正确是 HAL 的责任**。我们用"target 固定在最底层平面 + zpos 不可改"的方案时，唯一自洽的配置是：**DEVICE 层只能是可见层栈最上面的一段连续组，其余全部 CLIENT**。HAL 必须主动把 DEVICE 组下面那些"SF 请求 DEVICE"的层改成 CLIENT（合法变更，见规则 5）。
- 反过来，如果照抄"中间某层不行就它自己变 CLIENT、上下仍 DEVICE"（v1 版 `z1hwc2.cpp` 就是这么写的；21:36 的当前版已改成自顶向下的连续顶段判定），当失败层在**上方**时，它的内容会被画到最底层平面 → **被下面的 DEVICE 层盖住**（视频盖在 UI 下面的典型现象）。

参考实现的做法（drm_hwc，作为"另一种可行方案"的证据）：

```cpp
// /tmp/drmhwc/drmhwctwo.cpp:497-518
  std::map<uint32_t, DrmHwcTwo::HwcLayer *> z_map;
  for (auto &l : layers_) {
    switch (l.second.validated_type()) {
      case HWC2::Composition::Device: z_map.emplace(l.second.z_order(), &l.second); break;
      case HWC2::Composition::Client:
        // Place it at the z_order of the highest client layer
        use_client_layer = true;
        client_z_order = std::max(client_z_order, l.second.z_order());
        break;
      ...
  if (use_client_layer) z_map.emplace(client_z_order, &client_layer_);
```

**【Q】** drm_hwc 把客户端 target 放在"最高的那个 CLIENT 层的 z"上，不是最底层。
**【V】** 这种放法只有在 target 被清成透明、且逐层 alpha 混合时才正确（见 1.4/1.5）；对 zpos 可编程的 DRM 才可行。我们的 zpos 不可编程，只能放最底层，所以**不能**照抄。
**【I】** 附带风险：`std::map::emplace` 在 z 冲突时**静默丢弃**后插入者（若某 DEVICE 层与 client_z_order 相同，该层直接消失）。

### 1.4 客户端 target 应该放在哪？（Q2）

**规格原文：没有任何一句规定 target 的 z 位置。** 全文中提到 client target 的地方只有 5 处（已逐处核对）：

| 位置 | 原文要点 |
|---|---|
| `U_HW + hwcomposer2.h#120-131` | `HWC2_COMPOSITION_CLIENT`：client 把该层合成进 target；**device 不得对该类型请求变更** |
| `#181-191` | `HWC2_DISPLAY_REQUEST_FLIP_CLIENT_TARGET`：即使没有 CLIENT 层，也要求 client 提供新的 target |
| `#1164-1194` | `getClientTargetSupport`：必须对"活动分辨率 + RGBA_8888 + UNKNOWN dataspace"返回支持 |
| `#1527-1568` | `setClientTarget`：target 收 CLIENT 层的合成结果；**非 CLIENT 层要由 device 与 target 一起合成**；可为 null |
| `#276-283` | `HWC2_LAYER_REQUEST_CLEAR_CLIENT_TARGET`：client 应在该层位置清透明 |

**【Q】** 规格**沉默**（silent），不存在"必须放最底层"的条文。

**SF 的实际意图（可验证的行为证据）**：

```cpp
// U_SF + SurfaceFlinger.cpp#2940-2947
        // Never touch the framebuffer if we don't have any framebuffer layers
        if (hasDeviceComposition) {
            // when using overlays, we assume a fully transparent framebuffer
            getBE().mRenderEngine->clearWithColor(0, 0, 0, 0);
        } else { /* 画 wormhole 黑底 */ }
```

**【V】** 有 DEVICE 层时 SF 把 target **清成全透明**、只画 CLIENT 层 → SF 的模型是"target 是一张可混合的半透明图"，而不是"不透明底板"。所以：
- 放最底层：透明处显示……下面什么都没有（屏上是黑色/背景），**正确**；
- 放中间/顶部：透明处透出下面 DEVICE 层，**也正确**，但要求 target 参与 alpha 混合（PREMULTI + plane alpha=1.0，且 buffer 本身是预乘 alpha）。

**verdict（Q2）**
- 规格沉默；drm_hwc 放"最高 CLIENT 层的 z"；SF 的行为允许这两种放法（只要混合正确）。
- 我们的方案（target 固定在平面 0 = 最底层）是**合法且最简单**的，但必须满足 §1.3 的"CLIENT 组在底部"不变量，并且：
  - target 平面用 `PIXEL_BLEND_MODE = PREMULTI`（或至少不能是 `PIXEL_NONE`，除非能保证 target 全屏不透明）；
  - target 的 buffer 是 RGBA_8888（见 §4.1），alpha 预乘；
  - 若某帧没有任何 CLIENT 层，SF 不会调用 `setClientTarget` → 平面 0 **必须关闭**，否则会显示上一帧的旧 target（残影）。**【Q】** `DisplayDevice::swapBuffers` 只在 `hasClientComposition || hasFlipClientTargetRequest` 时才 `mSurface->swapBuffers()`（`U_SF + DisplayDevice.cpp#367-372`），而 `setClientTarget` 只在 `advanceFrame → nextBuffer` 真正取到新 buffer 时才调用（`U_SFD + FramebufferSurface.cpp#93-152`）。

### 1.5 设备层可以被部分裁出屏外 / 被遮挡吗？（Q3）

三条硬证据：

```c
// U_HW + hwcomposer2.h#2185-2192
/* setLayerDisplayFrame(..., frame)
 * Sets the display frame (the portion of the display covered by a layer) of the
 * given layer. This frame will not exceed the display dimensions. */
```

```c
// U_HW + hwcomposer2.h#2294-2308
/* setLayerVisibleRegion(..., visible)
 * Specifies the portion of the layer that is visible, including portions under
 * translucent areas of other layers. The region is in screen space, and will
 * not exceed the dimensions of the screen. */
```

```cpp
// U_SF + Layer.cpp#551-560（display frame 被裁到 viewport，空则清空）
    if (!frame.intersect(displayDevice->getViewport(), &frame)) frame.clear();
    ...
    error = hwcLayer->setDisplayFrame(transformedFrame);
```

另外 `rebuildLayerStacks()` 会把"完全不可见"的层**从可见列表剔除并 destroy 其 HWC layer**（`U_SF + SurfaceFlinger.cpp#1839-1852`），而不是发一个空 frame。

**verdict（Q3）**
- **display frame 不会超出屏幕**（规格 + SF 双重保证），HAL 不需要做越界裁剪，但为稳健仍应裁到 `[0,W)×[0,H)`。
- **可以部分被遮挡**（被更高的不透明/半透明层覆盖），这种层照常出现在可见列表里；`visibleRegion` 只是"哪些屏幕像素最终能看到它"，**不是**"只扫描这块"的指令，也**不能**用来缩小平面矩形（否则半透明覆盖区域会缺内容）。
- 完全不可见的层不会出现在列表里；对应地 HAL 会收到 `destroyLayer`，之后可能再 `createLayer`。**HAL 必须能承受 layer 频繁创建/销毁**（旧实现若在 destroy 后仍保留 plane 映射会出错）。
- 若某 DEVICE 层被上面的 CLIENT 层部分遮挡 → 这正是"target 必须在最底层"的原因之一；在我们方案下不会发生（CLIENT 组永远在下）。

---

## 2. B —— 校验（validate）协议

### 2.1 规格定义的调用序列与返回值（Q4）

```c
// U_HW + hwcomposer2.h#1900-1931（validateDisplay 全文要点）
 * outNumTypes - the number of composition type changes required by the
 *       device; if greater than 0, the client must either set and validate new
 *       types, or call acceptDisplayChanges to accept the changes returned by
 *       getChangedCompositionTypes; must be the same as the number of changes
 *       returned by getChangedCompositionTypes
 * outNumRequests - ... must be equal to the number of layer requests returned
 *       by getDisplayRequests
 * Returns HWC2_ERROR_NONE if no changes are necessary and it is safe to present
 * the display using the current layer state. Otherwise returns ...
 *   HWC2_ERROR_HAS_CHANGES - outNumTypes was greater than 0
```

```c
// U_HW + hwcomposer2.h#1055-1074（acceptDisplayChanges）
 * Accepts the changes required by the device from the previous validateDisplay
 * call ... This function is equivalent to requesting the changed types from
 * getChangedCompositionTypes, setting those types on the corresponding layers,
 * and then calling validateDisplay again.
 * After this call it must be valid to present this display. Calling this after
 * validateDisplay returns 0 changes must succeed with HWC2_ERROR_NONE, but
 * should have no other effect.
```

```c
// U_HW + hwcomposer2.h#1471-1505（presentDisplay）
 * Prior to calling this function, the display must be successfully validated
 * with validateDisplay. ...
 * If this call succeeds, outPresentFence will be populated with a file
 * descriptor referring to a present sync fence object. For physical displays,
 * this fence will be signaled at the vsync when the result of composition of
 * this frame starts to appear ...
```

**HIDL 适配层实际怎么做（决定了"我们能侥幸到什么程度"）**：

```cpp
// /tmp/aosp9/hidl/HwcHal.h:327-377（HwcHalImpl::validateDisplay，节选）
        int32_t err = mDispatch.validateDisplay(mDevice, display, &typesCount, &reqsCount);
        mMustValidateDisplay = false;
        if (err != HWC2_ERROR_NONE && err != HWC2_ERROR_HAS_CHANGES) {
            return static_cast<Error>(err);
        }
        // ↓ 注意：无论 NONE 还是 HAS_CHANGES，都会去取改变列表
        err = mDispatch.getChangedCompositionTypes(mDevice, display, &typesCount, nullptr, nullptr);
        ...
        err = mDispatch.getChangedCompositionTypes(mDevice, display, &typesCount, changedLayers.data(), ...);
        ...
        err = mDispatch.getDisplayRequests(mDevice, display, &displayReqs, &reqsCount, nullptr, nullptr);
        ...
        return static_cast<Error>(err);     // ← 返回的是最后一次 getDisplayRequests 的错误码
```

```cpp
// /tmp/aosp9/hidl/ComposerCommandEngine.h:248-268（executeValidateDisplay，节选）
        auto err = mHal->validateDisplay(mCurrentDisplay, &changedLayers, &compositionTypes,
                                         &displayRequestMask, &requestedLayers, &requestMasks);
        if (err == Error::NONE) {
            mWriter.setChangedCompositionTypes(changedLayers, compositionTypes);
            mWriter.setDisplayRequests(displayRequestMask, requestedLayers, requestMasks);
        } else {
            mWriter.setError(getCommandLoc(), err);
        }
```

```c
// /tmp/aosp9/hidl/types.hal:20-30 —— HIDL 的 Error 枚举里**没有** HAS_CHANGES（5 被注释为 reserved）
enum Error : int32_t {
    NONE = 0, BAD_CONFIG = 1, BAD_DISPLAY = 2, BAD_LAYER = 3, BAD_PARAMETER = 4,
    /* 5 is reserved */ NO_RESOURCES = 6, NOT_VALIDATED = 7, UNSUPPORTED = 8,
};
```

**【V】** 在 Pie 的 composer 2.1 HIDL 路径下：
- `HWC2_ERROR_HAS_CHANGES` 被适配器**吞掉**（不报错），改变列表照样上传；
- SF 侧 `HWC2::Error::HasChanges` 实际上**永远不会出现**（`ComposerHal::validateDisplay` 在 `execute()` 成功后恒返回 `Error::NONE`，`U_SFD + ComposerHal.cpp#599-613`），SF 的 `if (error != HasChanges) …` 分支是死代码；
- SF 通过 `CommandReader::hasChanges()` 的 `compositionTypes.size()` 得到 `numTypes`（`U_SFD + ComposerHal.cpp#1162-1179`），与 HAL 返回码无关。

**所以现实是**：HAL 返回 `NONE` 但 `outNumTypes>0` **在 Pie 上能工作**（drm_hwc 就这么干；`z1hwc2.cpp` 的 v1 也是，当前版已改为返回 `HAS_CHANGES`），但这是**借适配器的宽容**，且与 `hwcomposer2.h` 明文冲突。

**verdict（Q4）**
- 正确序列（HAL 视角）：
  `setLayer*…` → **validateDisplay** → （若 `HAS_CHANGES`）`getChangedCompositionTypes`×2 + `getDisplayRequests`×2 →（SF 应用类型）→ **acceptDisplayChanges** → …（SF 画 target、`setClientTarget`）→ **presentDisplay** → **getReleaseFences**×2。
- 若 HAL 返回 `NONE` 但某层实际不支持（应在 validate 里报 CLIENT 却没报）：**SF 什么都不会做**——它只把 `getChangedCompositionTypes` 返回的那几条应用上去（`U_SFD + HWComposer.cpp#483-500`），该层仍是 DEVICE，SF 不画它，HAL 又没在平面上合成 → **该层内容缺失/花屏，无任何报错**。这就是"必须用 CLIENT 变更来拒绝"的根本原因。
- 若 HAL 返回 `HAS_CHANGES` 但 `outNumTypes==0`：适配器接受，`getChangedCompositionTypes` 返回 0 条，SF 视作"无变更"继续 present。**功能上等价于 NONE，但违反规格**（`HAS_CHANGES` 的定义就是 `outNumTypes>0`）。不要这么写。
- **实现要求**：把"SF 请求的类型（requested）"和"自己验证后的类型（validated）"分开存；`getChangedCompositionTypes` 只报 `requested!=validated` 的层；`acceptDisplayChanges` 才把 validated 落到 requested（`/tmp/drmhwc/drmhwctwo.h:41-55`、`drmhwctwo.cpp:243-249` 是标准写法）。

### 2.2 `getDisplayRequests` 的语义与 SF 的消费方式（Q5）

```c
// U_HW + hwcomposer2.h#1321-1358（要点）
 * Returns the display requests and the layer requests required for the last
 * validated configuration.
 * ... this number may also be obtained from validateDisplay as outNumRequests
 * (outNumElements must be equal to the value returned in outNumRequests ...)
```

```c
// U_HW + hwcomposer2.h#181-191
    HWC2_DISPLAY_REQUEST_FLIP_CLIENT_TARGET = 1 << 0,   // 即使没有 CLIENT 层也要新 target
    HWC2_DISPLAY_REQUEST_WRITE_CLIENT_TARGET_TO_OUTPUT = 1 << 1,  // 仅虚拟显示有效
// U_HW + hwcomposer2.h#276-283
    HWC2_LAYER_REQUEST_CLEAR_CLIENT_TARGET = 1 << 0,    // client 在该层位置清透明
```

SF 侧消费点（全部）：

```cpp
// U_SFD + HWComposer.cpp#501-511 —— layer request
        if (layerRequests.count(hwcLayer) != 0 &&
                layerRequests[hwcLayer] == HWC2::LayerRequest::ClearClientTarget) {
            layer->setClearClientTarget(displayId, true);
        } else {
            if (layerRequests.count(hwcLayer) != 0) { LOG_DISPLAY_ERROR(displayId, ("Unknown layer request " + ...)); }
            layer->setClearClientTarget(displayId, false);
        }
// U_SF + SurfaceFlinger.cpp#2997-3008 —— CLEAR_CLIENT_TARGET 的落地
                    if (layer->getClearClientTarget(hwcId) && !firstLayer &&
                            layer->isOpaque(state) && (state.color.a == 1.0f) && hasClientComposition) {
                        layer->clearWithOpenGL(renderArea);
                    }
// U_SF + DisplayDevice.cpp#367-372 —— FLIP_CLIENT_TARGET
void DisplayDevice::swapBuffers(HWComposer& hwc) const {
    if (hwc.hasClientComposition(mHwcDisplayId) || hwc.hasFlipClientTargetRequest(mHwcDisplayId)) {
        mSurface->swapBuffers();
    }
    ...
```

**verdict（Q5）**
- 我们这套 HAL 应恒返回 `*outDisplayRequests = 0`、`*outNumElements = 0`，且 `validateDisplay` 的 `*outNumRequests` 也恒为 0（两者必须一致）。
- `FLIP_CLIENT_TARGET` 的唯一效果是：**即使没有 CLIENT 层，SF 也会 EGL swap 并再调一次 `setClientTarget`**（`DisplayDevice.cpp#367-372` → `FramebufferSurface::advanceFrame`）。我们没有"必须每帧刷 target"的需求，不要请求它（多一次 GL swap 纯浪费）。
- 未知的 layer request 只会打 `Unknown layer request` 日志（`U_SFD + HWComposer.cpp#505-509`），但不要依赖这点。

### 2.3 `HWC2_CAPABILITY_SKIP_VALIDATE` 能不能用？（Q6）

```c
// U_HW + hwcomposer2.h#106-115
    /* Specifies that a device is able to skip the validateDisplay call before
     * receiving a call to presentDisplay. The client will always skip
     * validateDisplay and try to call presentDisplay regardless of the changes
     * in the properties of the layers. If the device returns anything else than
     * HWC2_ERROR_NONE, it will call validateDisplay then presentDisplay again.
     * For this capability to be worthwhile the device implementation of
     * presentDisplay should fail as fast as possible in the case a
     * validateDisplay step is needed.
     */
    HWC2_CAPABILITY_SKIP_VALIDATE = 4,
```

**SF 侧：完全不看这个能力。** 证据：`grep -rn "SkipValidate" /tmp/aosp9/sf/` 只命中 **1 处**，是 `U_SFD + HWComposer.cpp#458` 的一句日志文本（`ALOGV("SkipValidate failed, Falling back to SLOW validate/present")`），**没有任何 `Capability::SkipValidate` 的读取**。SF 真正读的 capability 只有三个：`SidebandStream`（`U_SFD + HWC2.cpp#917`）、`PresentFenceIsNotReliable`（`SurfaceFlinger.cpp#725-731`、`#801-805`）、`SkipClientColorTransform`（`SurfaceFlinger.cpp#2915-2921`）；cap 集合来自 `HWC2::Device::loadCapabilities()`（`U_SFD + HWC2.cpp#205-213` → `mComposer->getCapabilities()`）。
而且 HIDL 的 `IComposer::getCapabilities` **只转发 3 个** cap，SKIP_VALIDATE 根本到不了 SF：

```cpp
// /tmp/aosp9/hidl/Composer.h:56-73（composer-hal, IComposer 2.1 实现）
        const std::array<IComposer::Capability, 3> all_caps = {{
            IComposer::Capability::SIDEBAND_STREAM,
            IComposer::Capability::SKIP_CLIENT_COLOR_TRANSFORM,
            IComposer::Capability::PRESENT_FENCE_IS_NOT_RELIABLE,
        }};
```

**唯一消费者是 HIDL 命令引擎**：

```cpp
// /tmp/aosp9/hidl/ComposerCommandEngine.h:271-310（executePresentOrValidateDisplay，节选）
        // First try to Present as is.
        if (mHal->hasCapability(HWC2_CAPABILITY_SKIP_VALIDATE)) {
            int presentFence = -1; ...
            auto err = mHal->presentDisplay(mCurrentDisplay, &presentFence, &layers, &fences);
            if (err == Error::NONE) { mWriter.setPresentOrValidateResult(1); ... return true; }
        }
        // Present has failed. We need to fallback to validate
        auto err = mHal->validateDisplay(mCurrentDisplay, ...);
        if (err == Error::NONE) { mWriter.setPresentOrValidateResult(0); ... }
```

而 SF **每帧都会发 `PRESENT_OR_VALIDATE_DISPLAY`**（只要上一帧没有 CLIENT 组合），**与是否 advertise 该能力无关**：

```cpp
// U_SFD + HWComposer.cpp#437-456
    displayData.validateWasSkipped = false;
    if (!displayData.hasClientComposition) {
        sp<android::Fence> outPresentFence;
        uint32_t state = UINT32_MAX;
        error = hwcDisplay->presentOrValidate(&numTypes, &numRequests, &outPresentFence , &state);
        ...
        if (state == 1) { //Present Succeeded.
            ...
            displayData.validateWasSkipped = true;
            ...
            return NO_ERROR;
        }
        // Present failed but Validate ran.
    } else {
        error = hwcDisplay->validate(&numTypes, &numRequests);
    }
```

**verdict（Q6）**
- **不要 advertise**。理由（均【V】自上述源码）：
  1. 一旦 advertise，命令引擎会在**没有 validate 的情况下**直接调用我们的 `presentDisplay`；我们就不再有"检查 buffer/格式/平面数并改类型"的机会。
  2. 一旦 present 成功，SF 走 `validateWasSkipped` 分支（`U_SFD + HWComposer.cpp#444-455`）→ **`getChangedCompositionTypes` 根本不会被调用**（SF 直接 return），我们任何类型变更都送不上去，SF 对组合类型的认知会永久停留在它请求的值。
  3. 若 present 失败，引擎会 fallback 到 validate（多一帧延迟），此时"快速失败"的要求意味着 `presentDisplay` 必须在**动硬件之前**判断能不能用当前状态提交——对本 HAL 是额外的复杂度，收益为零。
- 附带：**也不要 advertise `SKIP_CLIENT_COLOR_TRANSFORM`**（否则 SF 不再自己 apply 色彩矩阵：`SurfaceFlinger.cpp#2915-2921`，而 `setColorTransform` 我们不实现 → 色彩校正在夜间模式/无障碍下失效）；**也不要 `PRESENT_FENCE_IS_NOT_RELIABLE`**（它会让 SF 认为 present 时间戳不可靠：`SurfaceFlinger.cpp#725-731`）。
- 历史注意：`z1hwc2.cpp` 的 v1（1196 行版）曾 advertise `SKIP_VALIDATE` + `SKIP_CLIENT_COLOR_TRANSFORM`；当前版（2348 行，21:36）已改为 0 个 cap，不要退回。

---

## 3. C —— Fence

### 3.1 `presentDisplay` 的 present/retire fence（Q7）

```c
// U_HW + hwcomposer2.h#1471-1505（要点）
 * If this call succeeds, outPresentFence will be populated with a file
 * descriptor referring to a present sync fence object. For physical displays,
 * this fence will be signaled at the vsync when the result of composition of
 * this frame starts to appear (for video-mode panels) or starts to transfer to
 * panel memory (for command-mode panels).
```

SF 拿到后当 `sp<Fence>`（`U_SFD + HWC2.cpp#571-582`：`*outPresentFence = new Fence(presentFenceFd);`，`-1` 即 `Fence::NO_FENCE`），然后：

```cpp
// U_SF + SurfaceFlinger.cpp#1730-1765（postComposition，节选）
    sp<Fence> presentFence = getBE().mHwc->getPresentFence(HWC_DISPLAY_PRIMARY);
    auto presentFenceTime = std::make_shared<FenceTime>(presentFence);
    getBE().mDisplayTimeline.push(presentFenceTime);
    ...
    mDrawingState.traverseInZOrder([&](Layer* layer) {
        bool frameLatched = layer->onPostComposition(glCompositionDoneFenceTime,
                presentFenceTime, compositorTiming); ... });
    if (presentFenceTime->isValid()) {
        if (mPrimaryDispSync.addPresentFence(presentFenceTime)) enableHardwareVsync();
        else disableHardwareVsync(false);
    }
```

以及**最要命的一处**——它还是客户端 target buffer 的 release fence：

```cpp
// U_SFD + FramebufferSurface.cpp#162-176
void FramebufferSurface::onFrameCommitted() {
    if (mHasPendingRelease) {
        sp<Fence> fence = mHwc.getPresentFence(mDisplayType);
        if (fence->isValid()) {
            status_t result = addReleaseFence(mPreviousBufferSlot, mPreviousBuffer, fence);
            ...
        }
        status_t result = releaseBufferLocked(mPreviousBufferSlot, mPreviousBuffer);
```

**verdict（Q7）**
- present fence 会被用于：① 客户端 target 上一块 buffer 的释放（`U_SFD + FramebufferSurface.cpp#162-176`）；② `FenceTime` → DisplayTimeline / FrameTracker（帧延迟统计）；③ `DispSync::addPresentFence()` 的误差输入（→ 决定是否继续 resync 硬件 vsync）。
- **返回 `-1` 不会崩，但有三个真实后果**：
  1. target buffer 上一块**不带 fence 直接放回 BufferQueue** → SF/GPU 可以立刻重画 → **画面撕裂**（尤其全屏动画时明显）；
  2. `DispSync` 永远收不到 present 样本 → `mModelUpdated` 永不成立 → 每帧都 `enableHardwareVsync()`（我们若实现 vsync 就会被反复开关）；
  3. 帧延迟统计/`onPostComposition` 的 `frameLatched` 全部失真（`SIGNAL_TIME_INVALID`），`SurfaceFlinger::getCompositorTiming` 给出垃圾值（影响 `Choreographer` 的 frame deadline 估算）。
- 正确做法：KMS 原子提交带 `OUT_FENCE_PTR`，把 out-fence 直接作为 present fence；**如果这条老内核没有 out-fence**（【未确认】，见 §9.2 第 1 条），用 `sw_sync` timeline：每帧创建一个 fence，提交完成（下一次提交成功 or page-flip 事件）时 signal 它，把同一 timeline 上的 fence 也给 release 用。参考实现就是这么干的（`/tmp/drmhwc/drmdisplaycomposition.cpp:222-282` 的 `CreateAndAssignReleaseFences()` + `drmdisplaycompositor.cpp:825-854` 在**下一次提交成功后**才 `SignalCompositionDone()`）。
- 参考实现还有一个**语义偏差**值得知道：它返回的 present fence 实际是**上一帧**的完成 fence：

```cpp
// /tmp/drmhwc/drmhwctwo.cpp:573-576
  // The retire fence returned here is for the last frame, so return it and
  // promote the next retire fence
  *retire_fence = retire_fence_.Release();
  retire_fence_ = std::move(next_retire_fence_);
```

**【V】** 这与 `hwcomposer2.h` 的"本帧开始显示时 signal"不符；对 SF 的用途（释放上一块、统计延迟）是可接受的近似，但**延迟恒定多一帧**。我们若能拿到真实 out-fence，应给真实 out-fence。

### 3.2 `getReleaseFences`（Q8）

```c
// U_HW + hwcomposer2.h#1433-1468（节选）
 * Retrieves the release fences for device layers on this display which will
 * receive new buffer contents this frame.
 *
 * A release fence is a file descriptor referring to a sync fence object which
 * will be signaled after the device has finished reading from the buffer
 * presented in the prior frame. ... If a given layer's fence is not returned
 * from this function, it will be assumed that the buffer presented on the
 * previous frame is ready to be written.
 *
 * The fences returned by this function should be unique for each layer (even if
 * they point to the same underlying sync object), and ownership of the fences
 * is transferred to the client, which is responsible for closing them.
```

**HAL 会在什么时刻被调用**：在 `presentDisplay` 内部（同一个 HIDL 调用里），先取数量再取内容：

```cpp
// /tmp/aosp9/hidl/HwcHal.h:385-425（presentDisplay，节选）
        *outPresentFence = -1;
        int32_t err = mDispatch.presentDisplay(mDevice, display, outPresentFence);
        if (err != HWC2_ERROR_NONE) return static_cast<Error>(err);
        uint32_t count = 0;
        err = mDispatch.getReleaseFences(mDevice, display, &count, nullptr, nullptr);
        if (err != HWC2_ERROR_NONE) { ALOGW("failed to get release fences"); return Error::NONE; }
        outLayers->resize(count); outReleaseFences->resize(count);
        err = mDispatch.getReleaseFences(mDevice, display, &count, outLayers->data(), outReleaseFences->data());
        if (err != HWC2_ERROR_NONE) { ...clear...; return Error::NONE; }
```

注意：`getReleaseFences` 返回错误只会被 **ALOGW**，SF 收到空列表 → 按"全部可写"处理。**报错不会救你，只会静默撕裂。**

**SF 拿到 fence 之后做什么**：

```cpp
// U_SF + SurfaceFlinger.cpp#2114-2149（postFramebuffer，节选）
            getBE().mHwc->presentAndGetReleaseFences(hwcId);         // ← present + getReleaseFences
        ...
        for (auto& layer : displayDevice->getVisibleLayersSortedByZ()) {
            auto hwcLayer = layer->getHwcLayer(hwcId);
            sp<Fence> releaseFence = getBE().mHwc->getLayerReleaseFence(hwcId, hwcLayer);
            // If the layer was client composited in the previous frame, we
            // need to merge with the previous client target acquire fence. ...
            if (layer->getCompositionType(hwcId) == HWC2::Composition::Client) {
                releaseFence = Fence::merge("LayerRelease", releaseFence,
                        displayDevice->getClientTargetAcquireFence());
            }
            layer->onLayerDisplayed(releaseFence);
        }
```

```cpp
// U_SF + BufferLayer.cpp#256-258
void BufferLayer::onLayerDisplayed(const sp<Fence>& releaseFence) {
    mConsumer->setReleaseFence(releaseFence);
}
// U_SF + BufferLayerConsumer.cpp#227-243
void BufferLayerConsumer::setReleaseFence(const sp<Fence>& fence) {
    if (!fence->isValid()) { return; }                 // ← -1 直接丢弃，等价于"立即可写"
    auto slot = mPendingRelease.isPending ? mPendingRelease.currentTexture : mCurrentTexture;
    ...
    auto err = addReleaseFence(slot, buffer, fence);   // 合并进 BufferQueue 该 slot 的 release fence
```

**verdict（Q8）**
- 返回值要求：**对每个"本帧换上了新 buffer 的 DEVICE 层"返回一条 fence**（`hwcomposer2.h#1437-1438` 原文是 "device layers … which will receive new buffer contents this frame"）。
- **CLIENT 层不需要返回值**：SF 对 CLIENT 层会自己把 target 的 acquire fence 合并进去（`U_SF + SurfaceFlinger.cpp#2128-2133`）。给也不算错，但必须有效；给了 `-1` 无意义，给了错误/永不 signal 的 fence 会**卡死 producer**（App 永久 dequeue 不到 buffer → 掉帧/ANR）。
- **如果对"仍在扫描中的 buffer"返回 `-1`**：SF 认为它已可写（`U_SF + BufferLayerConsumer.cpp#228-230` 直接 return），producer 下一帧就能往里写 → **撕裂**；如果 App 三缓冲，通常表现为偶尔的水平错位条带。
- 未返回 fence 的层：SF 的 `getLayerReleaseFence` 返回 `Fence::NO_FENCE`（`U_SFD + HWComposer.cpp#556-570`），`setReleaseFence` 忽略 → 同"立即可写"。
- **返回了不认识的 layer id**：SF `getReleaseFences` 会 `ALOGE` 并 `BadLayer`（`U_SFD + HWC2.cpp#538-568`），`presentAndGetReleaseFences` 报错（帧已经 present 了，所以只是日志 + 该帧所有 release fence 丢失）。
- 实现建议【I】：用一个 `sw_sync` timeline 每帧生成一个 fence，同一帧里所有"直接被硬件扫描"的层共用**同一 timeline 上不同 fence 对象**（满足"unique fence"要求：各自 `sync_merge` 自己一份），在下一次提交成功（或本帧提交的 completion）时 signal。

### 3.3 `setClientTarget` / `setLayerBuffer` 的 acquire fence（Q9）

```c
// U_HW + hwcomposer2.h#1527-1568（setClientTarget 节选）
 * Also provides a file descriptor referring to an acquire sync fence object,
 * which will be signaled when it is safe to read from the client target buffer.
 * If it is already safe to read from this buffer, -1 may be passed instead.
 * The device must ensure that it is safe for the client to close this file
 * descriptor at any point after this function is called.
```

```c
// U_HW + hwcomposer2.h#1980-2010（setLayerBuffer 节选）
 * Also provides a file descriptor referring to an acquire sync fence object,
 * which will be signaled when it is safe to read from the given buffer. ...
 * The device must ensure that it is safe for the client to close this file
 * descriptor at any point after this function is called.
```

SF 侧对 target fence 是**先 dup 再传**（所以 HAL 拿到的是独立 fd，必须自己关）：

```cpp
// U_SFD + HWC2.cpp#596-603
Error Display::setClientTarget(uint32_t slot, const sp<GraphicBuffer>& target,
        const sp<Fence>& acquireFence, Dataspace dataspace)
{
    // TODO: Properly encode client target surface damage
    int32_t fenceFd = acquireFence->dup();
    auto intError = mComposer.setClientTarget(mId, slot, target, fenceFd, dataspace, std::vector<...>());
    return static_cast<Error>(intError);
}
```

**verdict（Q9）**
- **必须等待**（或把 fd 交给 KMS 作为该平面的 `IN_FENCE_FD`，由硬件在扫描时等待）。规格里那句 "must ensure that it is safe for the client to close this fd" 的意思是"你不能依赖 client 帮你留着 fd"，**不是**"你可以不等待就使用 buffer"。
- **只 `close()` 不等待 = 违反"安全读 buffer"的语义**：CPU 侧立刻去读（import/转发）或让 KMS 立刻扫描，都可能读到还没渲染完的内容 → 撕裂/半帧/上一帧内容。`z1hwc2.cpp` 的 v1 版就是这样（present 里遍历 `close(acquire_fence)`）；当前版已改为 `z1_set_client_target()` 里 `dup()` 保存 + `z1_present_display()` 提交前 `wait_fence(fd, 100ms)`，符合本条。
- 正确模式（drm_hwc 的做法：dup 保存，随 composition 一起提交）：

```cpp
// /tmp/drmhwc/drmhwctwo.h:71-73
    void set_acquire_fence(int acquire_fence) { acquire_fence_.Set(dup(acquire_fence)); }
// /tmp/drmhwc/drmhwctwo.cpp:619-630
  UniqueFd uf(acquire_fence);                 // 关闭调用者给的 fd（我们已 dup 过，安全）
  client_layer_.set_acquire_fence(uf.get());
// /tmp/drmhwc/drmhwctwo.cpp:826-830
  OutputFd release_fence = release_fence_output();
  layer->acquire_fence = acquire_fence_.Release();     // 交给 DRM 提交
  layer->release_fence = std::move(release_fence);
```

- 也要注意：`setLayerBuffer` 对 **CLIENT/SOLID_COLOR/SIDEBAND** 层必须"返回 NONE 且无副作用"（`hwcomposer2.h#1996-2000`）——即不要为 CLIENT 层 import/占平面。
- 若 buffer handle 变化（三缓冲轮转），HAL 应缓存 import 结果（`handle → fb_id`），每帧新建 `bo` 会明显掉帧【I】。

---

## 4. D —— Buffer / 平面导入与显示属性

### 4.1 无法 import 的 DEVICE 层能不能拒绝？最低支持集？（Q10）

```c
// U_HW + hwcomposer2.h#124-131（CLIENT 的定义，注意最后一句）
    /* The client will composite this layer into the client target buffer
     * (provided to the device through setClientTarget).
     * The device must not request any composition type changes for layers of
     * this type. */
```

```c
// U_HW + hwcomposer2.h#1164-1194（getClientTargetSupport 的硬性最低要求）
 * This function must return true for a client target with width and height
 * equal to the active display configuration dimensions,
 * HAL_PIXEL_FORMAT_RGBA_8888, and HAL_DATASPACE_UNKNOWN. It is not required to
 * return true for any other configuration.
```

```c
// U_HW + hwcomposer2.h#1980-2010（setLayerBuffer 的错误码只有两种）
 * Returns HWC2_ERROR_NONE or one of the following errors:
 *   HWC2_ERROR_BAD_LAYER - an invalid layer handle was passed in
 *   HWC2_ERROR_BAD_PARAMETER - the buffer handle passed in was invalid
```

**SF 对 `setLayerBuffer` 失败的反应：只打日志**（不会回退 CLIENT）：

```cpp
// U_SF + BufferLayer.cpp#677-688
    auto acquireFence = mConsumer->getCurrentFence();
    error = hwcLayer->setBuffer(hwcSlot, hwcBuffer, acquireFence);
    if (error != HWC2::Error::None) {
        ALOGE("[%s] Failed to set buffer %p: %s (%d)", ...);
    }
```

**【V】** SF 不会因为 `setLayerBuffer` 返回错误而改变组合类型 → 该层仍然是 DEVICE 且没有可显示内容。

**verdict（Q10）**
- **允许拒绝**：唯一的合法拒绝手段是**在 `validateDisplay` 里把该层报成 `HWC2_COMPOSITION_CLIENT`**（`DEVICE→CLIENT` 是 `U_SFD + HWComposer.cpp#115-117` 明确允许的变更）。这才是 Android 的"回退 GL 合成"机制。
- **禁止**用 `setLayerBuffer` 返回 `BAD_PARAMETER` 来"拒绝"——它会被静默吞掉，结果是丢层。
- 最低支持集：
  - **客户端 target 必须支持**：活动分辨率 + `RGBA_8888`（HAL_PIXEL_FORMAT_RGBA_8888 = 1）+ `HAL_DATASPACE_UNKNOWN`（`hwcomposer2.h#1176-1179`）。SF 的 RenderEngine 也以 RGBA_8888 创建（`U_SF + SurfaceFlinger.cpp#669`），我们的平面必须能吃 ARGB8888/ABGR8888（DRM 四字符码按本机驱动）。
  - **DEVICE 层没有任何法定最低格式集**；规格只说"不支持就改 CLIENT"。对 Z1：`RGBA_8888`/`RGBX_8888`/`RGB_565` 应走 DEVICE；YUV/其它（视频）先一律 CLIENT【I】——但注意这会把"CLIENT 组"抬高，见 §1.4 算法。
- `getClientTargetSupport` 在 Pie 的 SF 里**从未被调用**（`grep -rn getClientTargetSupport /tmp/aosp9/sf/*.cpp` → 0 命中），但 HIDL 适配器 `initDispatch()` **要求该函数指针非 NULL**（`/tmp/aosp9/hidl/HwcHal.h:532-533`），所以必须实现（返回 NONE/UNSUPPORTED 都可以）。【Q】

### 4.2 `getDisplayAttribute` / config / type / power（Q11）

```c
// U_HW + hwcomposer2.h#36-53
    /* Dimensions in pixels */        HWC2_ATTRIBUTE_WIDTH = 1, HWC2_ATTRIBUTE_HEIGHT = 2,
    /* Vsync period in nanoseconds */ HWC2_ATTRIBUTE_VSYNC_PERIOD = 3,
    /* Dots per thousand inches (DPI * 1000). ... If the DPI ... is unavailable
     * or is considered unreliable, the device may return -1 instead */
                                      HWC2_ATTRIBUTE_DPI_X = 4, HWC2_ATTRIBUTE_DPI_Y = 5,
// U_HW + hwcomposer2.h#1253-1274
 * Any attribute which is not supported or for which the value is unknown by the
 * device must return a value of -1.
 * Returns ... HWC2_ERROR_BAD_DISPLAY / HWC2_ERROR_BAD_CONFIG (config 无效)
```

SF 侧：DPI 按 **/1000** 换算成 float，`-1` 走默认密度；`VSYNC_PERIOD` **原样当纳秒**用：

```cpp
// U_SFD + HWC2.h#158-165（Config::Builder::setDpiX，节选）
            Builder& setDpiX(int32_t dpiX) {
                if (dpiX == -1) { mConfig->mDpiX = getDefaultDensity(); }
                else { mConfig->mDpiX = dpiX / 1000.0f; }
                return *this; }
```

`getActiveConfig` 的致命性：

```c
// U_HW + hwcomposer2.h#1108-1128
 * If no display configuration is currently active, this function must return
 * HWC2_ERROR_BAD_CONFIG ... It is the responsibility of the client to call
 * setActiveConfig with a valid configuration before attempting to present
 * anything on the display.
```
```cpp
// U_SFD + HWComposer.cpp#300-319 —— BAD_CONFIG → 返回 nullptr
    auto error = mDisplayData[displayId].hwcDisplay->getActiveConfig(&config);
    if (error == HWC2::Error::BadConfig) { LOG_DISPLAY_ERROR(displayId, "No active config"); return nullptr; }
```
```cpp
// U_SF + SurfaceFlinger.cpp#1283-1290 —— 直接解引用，无判空
    const auto& activeConfig = getBE().mHwc->getActiveConfig(HWC_DISPLAY_PRIMARY);
    const nsecs_t period = activeConfig->getVsyncPeriod();
    mPrimaryDispSync.reset();
    mPrimaryDispSync.setPeriod(period);
```
```cpp
// U_SFD + HWComposer.cpp#270-279 —— 同样无判空
    auto vsyncPeriod = getActiveConfig(displayId)->getVsyncPeriod();
```

**[V]** `getActiveConfig` 返回 `BAD_CONFIG` 或返回一个"不在 config 列表里"的 id（→ `HWC2::Display::getActiveConfig` 返回 nullptr，`U_SFD + HWC2.cpp#315-340`）会导致 **SF 空指针解引用 → 崩溃/开机循环**。SF 启动流程中 `setActiveConfig` **不会被调用**（只有 `SurfaceFlinger::setActiveConfigInternal` 在显示模式切换时才调：`U_SF + SurfaceFlinger.cpp#945-961`），所以 HAL 必须**自带一个默认 active config**，hotplug 之后立刻可用。

`getDisplayConfigs`：SF 在 hotplug connected 时调用一次并缓存（`U_SFD + HWC2.cpp#685-693` → `loadConfigs()` → `getDisplayConfigs` + 5 个 `getDisplayAttribute`）。返回 0 个 config 或属性全 `-1` 的后果：`mConfigs` 为空/`mVsyncPeriod=-1` → `DispSync::setPeriod(-1)` → 软件 vsync 时钟错乱（`1e9 / -1` 得负 fps 等）。

`getDisplayType`（`hwcomposer2.h#1361-1376`）：内置屏必须返回 `HWC2_DISPLAY_TYPE_PHYSICAL = 1`；SF 用它区分内置/虚拟（`SurfaceFlinger.cpp#2413-2470`）。

`setPowerMode`（`hwcomposer2.h#1729-1752`）：

```c
 * Sets the power mode of the given display. The transition must be complete
 * when this function returns. It is valid to call this function multiple times
 * with the same power mode.
 * All displays must support HWC2_POWER_MODE_ON and HWC2_POWER_MODE_OFF.
```

**SF 在 OFF 时不会 present**（三处都跳过）：

```cpp
// U_SF + SurfaceFlinger.cpp#2063-2075（setUpHWComposer 里 prepare 前）
        if (!displayDevice->isDisplayOn()) { continue; }
        status_t result = displayDevice->prepareFrame(*getBE().mHwc);
// U_SF + SurfaceFlinger.cpp#2078-2096（doComposition）
        const sp<DisplayDevice>& hw(mDisplays[dpy]);
        if (hw->isDisplayOn()) { ... doDisplayComposition(hw, dirtyRegion); ... }
// U_SF + SurfaceFlinger.cpp#2099-2116（postFramebuffer）
        if (!displayDevice->isDisplayOn()) { continue; }
```

**verdict（Q11）**
- WIDTH/HEIGHT 像素；VSYNC_PERIOD **纳秒且 > 0**；DPI 是 `DPI*1000`，不确定就给 `-1`（SF 用默认密度）；其余未知属性返回 `-1` + `HWC2_ERROR_NONE`（不要返回 `BAD_PARAMETER`，规格只列了 BAD_DISPLAY/BAD_CONFIG 两种错误）。
- `getDisplayConfigs` ≥ 1，且必须包含 `getActiveConfig` 会返回的那个 id；`getActiveConfig` **永远成功**。
- `setPowerMode(ON/OFF)` 必须同步生效（"transition must be complete when this function returns"）；OFF 之后 SF 不再 prepare/present，可以安全关闭所有平面；ON 时 SF 会 `resyncToHardwareVsync(true)`（`SurfaceFlinger.cpp#3727-3735`）→ 又回到"active config 必须可用"。
- `getDozeSupport` 返回 0（我们不支持 DOZE）：SF 会把 DOZE 请求降级为 ON（`U_SFD + HWComposer.cpp#630-641`）。
- 热插拔语义：`getDisplayConfigs`/`getDisplayAttribute` 会在 **hotplug 回调线程**里被调用（`hwcomposer2.h#846-850` 明确允许 client 在同一线程回调进来），HAL 必须线程安全。

---

## 5. E —— Vsync（Q12）

### 5.1 HAL 从不报 vsync 会怎样？

SF 的帧时钟**不是** HWC vsync，而是 `DispSync`（软件模型）：

```cpp
// U_SF + SurfaceFlinger.cpp#646-664（init）
    mEventThreadSource = std::make_unique<DispSyncSource>(&mPrimaryDispSync, vsyncPhaseOffsetNs, true, "app");
    mEventThread   = std::make_unique<impl::EventThread>(mEventThreadSource.get(), ...);
    mSfEventThreadSource = std::make_unique<DispSyncSource>(&mPrimaryDispSync, sfVsyncPhaseOffsetNs, true, "sf");
    mSFEventThread = std::make_unique<impl::EventThread>(mSfEventThreadSource.get(), ...);
    mEventQueue->setEventThread(mSFEventThread.get());
```

- `DispSync` 的周期来自 **active config 的 vsync period**（`SurfaceFlinger.cpp#1287-1290`），HWC vsync 只是用来**修正相位**：

```cpp
// U_SF + SurfaceFlinger.cpp#1325-1352（onVsyncReceived）
    if (!getBE().mHwc->onVsync(displayId, timestamp, &type)) return;
    ...
        if (type == DisplayDevice::DISPLAY_PRIMARY && mPrimaryHWVsyncEnabled) {
            needsHwVsync = mPrimaryDispSync.addResyncSample(timestamp);
        }
    if (needsHwVsync) enableHardwareVsync(); else disableHardwareVsync(false);
```

- present fence 用来算误差（`SurfaceFlinger.cpp#1758-1764` → `DispSync::addPresentFence`，`/tmp/aosp9/sf/DispSync.cpp#430-441`）。

**所以：HAL 完全不报 vsync，present 仍然工作**（DispSync 按 config 周期自由运行），代价是相位漂移、vsync 时间戳不准、Choreographer 抖动【V】。

`EventThread` 里那句日志是"最后兜底"：

```cpp
// U_SF + EventThread.cpp#313-325
                // We don't want to stall if there's a driver bug, so we
                // use a (long) timeout when waiting for h/w vsync, and
                // generate fake events when necessary.
                bool softwareSync = mUseSoftwareVSync;
                auto timeout = softwareSync ? 16ms : 1000ms;
                if (mCondition.wait_for(*lock, timeout) == std::cv_status::timeout) {
                    if (!softwareSync) {
                        ALOGW("Timed out waiting for hw vsync; faking it");
                    }
                    mVSyncEvent[0].header.type = DisplayEventReceiver::DISPLAY_EVENT_VSYNC;
                    ...
```

**【V】** 这句日志的触发条件是"**EventThread 的 VSyncSource（DispSyncSource）1 秒内没送事件**"，而 DispSync 是软件的、按 `mPeriod` 自由跑。所以它出现通常意味着：
- `mPeriod == 0`（active config 的 `VSYNC_PERIOD` 返回 0/无效，或 config 加载失败）→ `DispSyncThread` 里 `if (mPeriod == 0) { mCond.wait(); continue; }`（`/tmp/aosp9/sf/DispSync.cpp#114-121`）永不产生事件；
- 或 DispSync 线程被阻塞。
**不是**"HAL 不发 vsync 回调"的直接后果。反过来说：看到这条日志，先查 `getDisplayAttribute(VSYNC_PERIOD)` 返回值和 `getActiveConfig`，别去查 vsync 回调【I，但有代码支撑】。

### 5.2 如果以后要实现 vsync，最小正确实现

规格原文：

```c
// U_HW + hwcomposer2.h#882-899（vsync 回调）
 * This callback must only be triggered when vsync is enabled for this display
 * (through setVsyncEnabled).
 * This callback should be triggered from a thread of at least
 * HAL_PRIORITY_URGENT_DISPLAY with as little latency as possible, typically
 * less than 0.5 ms. This thread is guaranteed not to call back into the device.
 *   timestamp - the CLOCK_MONOTONIC time at which the vsync event occurred, in
 *       nanoseconds
// U_HW + hwcomposer2.h#852-853（hotplug 之后）
 * Displays which have been connected are assumed to be in HWC2_POWER_MODE_OFF,
 * and the vsync callback should not be called for a display until vsync has
 * been enabled with setVsyncEnabled.
```

最小实现清单：
1. `registerCallback(HWC2_CALLBACK_VSYNC, data, fn)` 保存 `data`/`fn`（`hwcomposer2.h#1003-1025`）。
2. `setVsyncEnabled(ENABLE)` 启动一个 `SCHED_FIFO`/`HAL_PRIORITY_URGENT_DISPLAY` 线程；`DISABLE` 停掉它（并且**停止后再也不发**）。
3. 时间戳 = `CLOCK_MONOTONIC` 纳秒；只在 enable 期间发；主显示 id = 0（`HWC_DISPLAY_PRIMARY`）。
4. 平台侧两种取源方式：
   - 有 vblank/page-flip 事件：等 KMS 的 `drmWaitVBlank` 或 `PAGE_FLIP_EVENT`；
   - 没有：按当前 config 周期合成（drm_hwc 的 `VSyncWorker::SyntheticWaitVBlank()` 就是 60Hz 兜底，`/tmp/drmhwc/vsyncworker.cpp:88-94` 起）。
5. 不要在回调里再调进 HAL（规格保证 SF 不会，但自己别造成锁环）。
6. 注意 `DispSync::addResyncSample` 需要**连续**样本才能锁相；丢样本/抖动大的时间戳会让模型反复 resync（`/tmp/aosp9/sf/DispSync.cpp#443-497`）。

---

## 6. 风险表：naive HAL 行为 → 后果 → 规则

| # | naive 行为 | 会发生什么（证据） | 必须遵守的规则 |
|---|---|---|---|
| 1 | `getFunction` 对"用不到"的函数返回 NULL（如 `CREATE_VIRTUAL_DISPLAY`、`GET_MAX_VIRTUAL_DISPLAY_COUNT`、`DUMP`） | 适配器 `initDispatch()` 返回 false → `android.hardware.graphics.composer@2.1-service` 加载 HAL 失败 → SF 拿不到 composer → 开机卡住/黑屏（`/tmp/aosp9/hidl/HwcHal.h:521-584`） | 全部返回非 NULL（可返回 `UNSUPPORTED`） |
| 2 | `registerCallback` 只存指针、不补发 hotplug | SF `LOG_ALWAYS_FATAL_IF(!isConnected(PRIMARY))` → 启动即 abort（`U_SF + SurfaceFlinger.cpp#682`） | 注册 hotplug 后立刻回调 `(data, 0, HWC2_CONNECTION_CONNECTED)`；drm_hwc 同做法（`/tmp/drmhwc/drmhwctwo.cpp:145-149`） |
| 3 | `getActiveConfig` 返回 `BAD_CONFIG` / 返回未注册的 id | SF `activeConfig->getVsyncPeriod()` 空指针 → SIGSEGV（`SurfaceFlinger.cpp#1287-1290`、`U_SFD + HWComposer.cpp#276-277`） | 永远有 valid active config |
| 4 | `getDisplayConfigs` 返回 0 条，或 `VSYNC_PERIOD` 返回 0/负数 | DispSync 周期 0 → 无软件 vsync → EventThread 每 1s 伪造一次（`EventThread.cpp#313-325`），整机卡顿；负值时计算全错 | ≥1 config，period>0 ns |
| 5 | `getChangedCompositionTypes` / `getDisplayRequests` / `getReleaseFences` 在数组指针为 NULL 时照样写 | composer 服务 SIGSEGV（`/tmp/aosp9/hidl/HwcHal.h:340-345`、`356-368`、`398-410` 都会先传 NULL）→ SF 失去 composer → 黑屏/重启 | 三种接口都必须支持"只回报数量"的调用 |
| 6 | `validateDisplay` 返回 NONE 但 `*outNumTypes>0` | Pie 上能跑（适配器宽容），但违反 `hwcomposer2.h#1900-1931`；且 SF 的 `HasChanges` 分支成了死代码，将来任何"严格实现"的适配器/CTS 会失败 | `NONE ⇔ 0 变更`；`HAS_CHANGES ⇔ n>0`；数量必须与 `getChangedCompositionTypes` 完全一致 |
| 7 | 上层某层失败→仅它自己变 CLIENT，下层仍 DEVICE（naive 写法：逐层独立决定） | target 在最底层，该层被画到下层 DEVICE 之下 → **层次错乱/内容被盖住**（`SurfaceFlinger.cpp#2987-3026` 只画 CLIENT；zpos 不可改） | CLIENT 组必须连续且在最底部（§1.4 算法） |
| 8 | `getReleaseFences` 一律 `-1`（当前 `z1hwc2.cpp` 仍是如此，代码里已标 TODO） | SF 认为上一块立即可写 → producer 覆盖正在扫描的 buffer → **撕裂**（`hwcomposer2.h#1443-1445`；`U_SF + BufferLayerConsumer.cpp#227-230`） | 每个换 buffer 的 DEVICE 层给真实 fence |
| 9 | `presentDisplay` 的 `*retire_fence = -1` | target buffer 无 fence 释放（`U_SFD + FramebufferSurface.cpp#162-176`）→ 撕裂；DispSync 无误差输入（`SurfaceFlinger.cpp#1758-1764`） | 给真实 present fence（OUT_FENCE 或 timeline） |
| 10 | `setClientTarget`/`setLayerBuffer` 的 acquire fence 只 `close()` 不等 | 扫描到未完成渲染的 buffer → 撕裂/半帧（`hwcomposer2.h#1540-1544`、`#1990-1994`） | dup 保留 → KMS `IN_FENCE_FD`，或先 `Fence::waitForever` |
| 11 | 没有 CLIENT 层时仍把上一帧 target 留在平面 0 | 屏上残留上一帧的客户端合成内容（SF 该帧不会重画/重发 target：`U_SF + DisplayDevice.cpp#367-372` + `U_SFD + FramebufferSurface.cpp#93-152`） | 每帧判断"本帧是否有 CLIENT 层"，没有就关闭平面 0 |
| 12 | advertise `SKIP_VALIDATE`（v1 版曾如此；当前版已修） | 命令引擎先 present（`ComposerCommandEngine.h:277-289`）→ SF 走 `validateWasSkipped`（`HWComposer.cpp#444-455`）→ **`getChangedCompositionTypes` 不再被调用** → 类型变更全丢；平面状态可能过期 | 不 advertise |
| 13 | advertise `SKIP_CLIENT_COLOR_TRANSFORM` 但不实现 `setColorTransform` | SF 不再在 GL 里做色彩矩阵（`SurfaceFlinger.cpp#2915-2921`）→ 夜间模式/无障碍色彩失效 | 不 advertise |
| 14 | `setLayerBuffer` 对不支持的格式返回 `BAD_PARAMETER` | SF 只打日志（`U_SF + BufferLayer.cpp#683-688`），层仍 DEVICE → 该层内容缺失 | 接受调用，在 validate 里报 CLIENT |
| 15 | 把 CLIENT 层改成 DEVICE | 非法变更（`U_SFD + HWComposer.cpp#109-126` 会 ALOGE），SF 不画该层且可能不提供 target → 缺内容 | 只允许 DEVICE→CLIENT |
| 16 | 认为 z 是稳定 id / 用 z 做数组下标 | 层被遮挡时从可见列表消失、z 重新编号（`SurfaceFlinger.cpp#1839-1852`）→ 平面错位 | z 只用于本帧排序 |
| 17 | 不处理 `destroyLayer/createLayer` 抖动 | 层不可见时 HWC layer 被销毁（`SurfaceFlinger.cpp#1849-1851`），HAL 若仍引用会崩溃/UAF | 销毁时释放所有资源 |
| 18 | 忽略 `setLayerPlaneAlpha` / `setLayerBlendMode` | 半透明 UI（对话框、Toast、转场）显示成不透明黑块/错误颜色（`Layer.cpp#500-512` 会下发 `Premultiplied/Coverage/None`） | 映射到 KMS `alpha` + `pixel_blend_mode` |
| 19 | 每帧重建 `fb_id`、不缓存 import | 每帧 `drmModeAddFB2`+`RmFB` → 掉帧、内存碎片 | 按 handle 缓存 |
| 20 | `presentDisplay` 在 `setPowerMode(OFF)` 之后仍提交 | SF 不会调（`SurfaceFlinger.cpp#2063-2075`、`#2099-2116`），但 `presentOrValidate` 路径可能兜到；此时提交会点亮屏幕 | OFF 状态下 present 立即返回 NONE、不做原子提交 |

---

## 7. 现状实现对照（重要：该文件在调研期间被**连续改写**，本节只用函数名，不绑定行号）

**快照历史（同一路径 `z1a9_gpt/scripts/z1hwc2/z1hwc2.cpp`）**：

| 时间 | 行数 | md5 |
|---|---|---|
| 2026-11-09 21:13（本报告开始） | 1196 | （未记） |
| 21:36（第一次改写） | 2348 | `514bf0deea6de6bbebc1884dee8d3105` |
| 21:40（第二次改写，本报告定稿前最后核对） | 2302 | `e51422b145d94c69128d48410aca1c12` |

> 行号每隔几分钟就会变，所以下面**只引用函数名**。核对方式：`grep -n "static .* z1_<函数名>" `。

**结论先说：最新版本满足 TL;DR 的 1/3/4/5/6/9/10 条（第 2 条也基本满足），仍缺第 7、8 条（release / present fence）；另有 3 个 P2/P3 小项。**

### 7.1 已符合（不要再"修"）

| 规则 | 函数 | 核对要点 |
|---|---|---|
| 1 生命周期 | `z1_register_callback()` | 注册 `HWC2_CALLBACK_HOTPLUG` 后立刻回调 `(data, 0, HWC2_CONNECTION_CONNECTED)`，注释写明理由 ✔ |
| 1 函数指针 | `z1_get_function()` | 适配器 `initDispatch()` 列表里的描述符都有分支 ✔（改代码后请重新对照 `/tmp/aosp9/hidl/HwcHal.h:521-584` 复核） |
| 2 config | `z1_get_display_configs()` / `z1_get_active_config()` / `z1_get_display_attribute()` | 1 个 config（id 0）、active config 恒成功、`VSYNC_PERIOD` 有 `vrefresh?1e9/vrefresh:16666667` 兜底（>0）✔ |
| 3 校验返回码 | `z1_validate_display()` | 结尾 `return h->changed.empty() ? HWC2_ERROR_NONE : HWC2_ERROR_HAS_CHANGES;`，且 `*out_num_types = changed.size()`，自洽 ✔ |
| 4 count 查询 | `z1_get_changed_composition_types()` / `z1_get_display_requests()` / `z1_get_release_fences()` | 都判了 `!out_layers || !out_fences` 并只回报数量 ✔；changed 版本还检查了调用方容量 ✔ |
| 5 类型变更方向 | `z1_validate_display()` | 只报 `→CLIENT`，**从不**把 CLIENT 改成 DEVICE ✔ |
| 6 z 序 | `z1_validate_display()`（自顶向下 + `client_above` 标志） | 遇到请求 CLIENT 的层就把其下所有层一并降为 CLIENT（代码注释原文："DEVICE layers must form a contiguous top suffix"）✔；并且无条件保留最底平面，注释给出的理由（`setClientTarget` 在 `validateDisplay` 之后才被调用）正确 ✔ |
| 9 acquire fence（target） | `z1_set_client_target()` | `dup()` 保存、旧 fd 先 close，注释引用"client 可随时关闭 fd" ✔ |
| 9 acquire fence（layer） | `z1_present_display()` | 提交前 `wait_fence(fd, 100ms)` 再 close ✔（v1 是"只 close"，已修） |
| 10 capabilities | `z1_get_capabilities()` | 返回 0 个 cap，注释直接写明 `SKIP_VALIDATE` 对本 HAL 不安全 ✔ |

### 7.2 仍然缺（按优先级）

| 优先级 | 规则 | 函数 | 现状 | 后果 |
|---|---|---|---|---|
| **P0** | 7 release fence | `z1_get_release_fences()` | 遍历**所有**层、`out_fences[n] = -1`；代码注释自认 *"real sw_sync fences are the next milestone"* | 撕裂：SF 认为上一块 buffer 已可写（`hwcomposer2.h#1440-1445`、`BufferLayerConsumer.cpp#227-230`） |
| **P0** | 8 present fence | `z1_present_display()` | 开头 `*out_retire_fence = -1;`，全程不再赋值 | ① 客户端 target buffer 无 fence 释放 → 撕裂（`FramebufferSurface.cpp#162-176`）；② DispSync 收不到 present 样本 → 永不锁相、反复 enable 硬件 vsync（`SurfaceFlinger.cpp#1758-1764`） |
| P2 | 8 失败帧语义 | `z1_present_display()` | 提交失败分支 `return HWC2_ERROR_NONE; /* keep SurfaceFlinger alive */` | 折中可接受（否则 SF 每帧报错）；但**接上真 fence 后**，失败帧不能返回 NONE + 旧/无效 fence，否则 SF 会拿它去释放 target |
| P2 | 7 返回范围 | `z1_get_release_fences()` | 返回所有层（含 CLIENT 层） | 规格只要"本帧换 buffer 的 device 层"；现在多返回 `-1` 无害（SF 丢弃无效 fence），但接上真 fence 后给 CLIENT 层发 fence 会不必要地拖住 producer |
| P3 | 2 属性错误码 | `z1_get_display_attribute()` 的 `default:` 分支 | 返回 `HWC2_ERROR_BAD_PARAMETER`；规格只列 `BAD_DISPLAY`/`BAD_CONFIG`，未知属性应 `*out_value = -1` + `HWC2_ERROR_NONE` | SF 目前不查其它属性，仅 CTS 风险 |

### 7.3 一次性解决 P0 的建议（与 §3.1/§3.2 同一方案）

1. 建一个 `sw_sync` timeline（`/sys/kernel/debug/sync/sw_sync` 或 `SW_SYNC_IOC_CREATE_FENCE`，前提是内核开了 `CONFIG_SW_SYNC`）。
2. 每帧 `presentDisplay` 开始处：为**本帧参与扫描的每个 DEVICE 层**各取该 timeline 上的一个 fence（每层 `sync_merge` 自己一份，满足"unique fence"），存进该层的状态。
3. 本帧提交完成（或下一次提交成功——参考 `/tmp/drmhwc/drmdisplaycompositor.cpp:825-854` 的顺序）后 `SW_SYNC_IOC_INC` 递增 timeline → 上一帧的 release fence 全部 signal。
4. 同一 timeline 上的一个 fence 直接作为 `*out_retire_fence` 返回（近似值，比 `-1` 好得多）；若本机 KMS 支持 `OUT_FENCE_PTR`（【未确认】，见 §9.2 第 1 条），优先用真 out-fence。
5. `getReleaseFences` 只返回"本帧换了新 buffer 的 DEVICE 层"，其余层不返回。

## 8. 实现检查清单（交付前逐项打勾）

### 8.1 加载与生命周期
- [ ] `hw_module_t` / `HAL_MODULE_INFO_SYM` 名字为 `HWC_HARDWARE_COMPOSER`，`common.version = HWC_DEVICE_API_VERSION_2_0`；`hwc2_open()` 能返回 `hwc2_device_t*`。
- [ ] `getFunction()` 对适配器 `initDispatch()` 的**全部**描述符返回非 NULL（对照 `/tmp/aosp9/hidl/HwcHal.h:521-584` 列表逐项核对；含 `CREATE_VIRTUAL_DISPLAY`、`DESTROY_VIRTUAL_DISPLAY`、`GET_MAX_VIRTUAL_DISPLAY_COUNT`、`GET_CLIENT_TARGET_SUPPORT`、`GET_DISPLAY_NAME`、`SET_OUTPUT_BUFFER`、`SET_LAYER_COLOR`、`SET_CURSOR_POSITION`、`DUMP`）。
- [ ] `getCapabilities` 返回 0 个（不 advertise 任何 cap）。
- [ ] `registerCallback` 保存 hotplug/refresh/vsync 的 `data`+`fn`；**hotplug 注册后立刻回调 `(data, 0, 1)`**。
- [ ] 主显示 `getDisplayType` = `HWC2_DISPLAY_TYPE_PHYSICAL`；`getMaxVirtualDisplayCount` = 0。
- [ ] `common.close()` 能干净释放（SF 在 composer 重启时会关）。

### 8.2 配置
- [ ] `getDisplayConfigs` 返回 ≥1 个 config（包含 active 的那个）；`configs==NULL` 时只写数量。
- [ ] `getDisplayAttribute` 对 5 个属性均可答：WIDTH/HEIGHT 像素、VSYNC_PERIOD>0 ns、DPI=`dpi*1000` 或 `-1`。
- [ ] `getActiveConfig` 从 hotplug 起就一直成功；`setActiveConfig` 对合法 id 返回 NONE，非法 id 返回 BAD_CONFIG。
- [ ] `setPowerMode(ON/OFF)` 同步完成；OFF 后不起提交；ON 后一切可继续。
- [ ] `getDozeSupport` 返回 0。

### 8.3 每帧协议
- [ ] 收到 `setLayerBuffer` 只记录（CLIENT/SOLID_COLOR/SIDEBAND 层无副作用）；acquire fence **dup 保存**。
- [ ] 收到 `setClientTarget` 记录 handle + acquire fence（dup）；`target==NULL` 时不报错。
- [ ] `validateDisplay`：
  - [ ] 针对**每个**层把 requested 与 validated 分开算；
  - [ ] 只报 `DEVICE/CURSOR/SIDEBAND/SOLID_COLOR → CLIENT`；
  - [ ] **强制 CLIENT 组连续且在最底部**（算法见 §1.4：`cut = 1 + max(索引 of CLIENT)`，`index < cut` 全部降 CLIENT，`index >= cut` 里从上往下最多 `plane_count-1` 个 DEVICE）；
  - [ ] 返回码与 `*outNumTypes` 自洽；`*outNumRequests = 0`；
  - [ ] 幂等：连续两次 validate 结果一致。
- [ ] `getChangedCompositionTypes` / `getDisplayRequests` / `getReleaseFences` 三个都能处理 `(…, NULL, NULL)` 的计数查询；填充时不得超过调用方给的容量。
- [ ] `acceptDisplayChanges` 把 validated 落到 requested（或至少标记"已接受"），返回 NONE。
- [ ] `presentDisplay`：
  - [ ] 一次原子提交包含**所有**平面（含 disable 多余平面），`ALLOW_MODESET` 只在首次/模式切换时加；
  - [ ] 每平面 `IN_FENCE_FD` = 该层 acquire fence（无则 -1）；
  - [ ] `OUT_FENCE_PTR` → `*outPresentFence`（无 out-fence 时用 timeline 方案）；
  - [ ] 本帧有 CLIENT 层才提交平面 0；否则关闭；
  - [ ] 未 validated 时返回 `HWC2_ERROR_NOT_VALIDATED`。
- [ ] `getReleaseFences`：对每个"本帧换了 buffer 的 DEVICE 层"给出有效 fence（unique fd、所有权转移）；无则**不返回**该层（而不是返回 -1 占位）。

### 8.4 平面/导入/合成
- [ ] gralloc handle → `drmPrimeFDToHandle` → `drmModeAddFB2`，按 handle 缓存；失败 → 该层在 validate 报 CLIENT。
- [ ] 只支持无缩放（source crop 尺寸 == display frame 尺寸），否则报 CLIENT。
- [ ] 只支持 `transform == 0`，否则报 CLIENT。
- [ ] `setLayerPlaneAlpha` → KMS plane `alpha`；`setLayerBlendMode`(None/Premultiplied/Coverage) → `pixel_blend_mode`（None / Pre-multiplied / Coverage）按本机驱动枚举映射。
- [ ] `setLayerZOrder` 只用于本帧排序；平面顺序 = zpos（不可改）。
- [ ] `destroyLayer` 释放 fb/gem/plane 占用，避免 UAF。

### 8.5 Vsync（可选，后续再加）
- [ ] `setVsyncEnabled(ENABLE)` 才发；`DISABLE` 立即停；只对主显示。
- [ ] 时间戳 `CLOCK_MONOTONIC` ns；线程优先级 ≥ `HAL_PRIORITY_URGENT_DISPLAY`。
- [ ] 无 vblank 源时按 config 周期合成（参考 `/tmp/drmhwc/vsyncworker.cpp`）。

### 8.6 观测性
- [ ] `dump()` 输出：当前 active config、每个平面的 fb/格式/位置/z、上一帧 present fence 状态、最近一次 validate 的变更列表（便于对照 `dumpsys SurfaceFlinger`）。
- [ ] 关键路径日志限速（每帧 ALOGE 会拖垮 60fps）。

---

## 9. 附录

### 9.1 证据索引（全部已本地核对）

**本地（任务给定的参考实现）**

| 主题 | 位置 |
|---|---|
| validate 只改 SolidColor/Cursor/Sideband → Client；恒返回 NONE | `/tmp/drmhwc/drmhwctwo.cpp:690-710` |
| client target 放在"最高 CLIENT 层的 z" | `/tmp/drmhwc/drmhwctwo.cpp:497-518` |
| present 里 import 失败 → `NoResources`（整帧失败） | `/tmp/drmhwc/drmhwctwo.cpp:519-524` |
| present fence = 上一帧的 fence | `/tmp/drmhwc/drmhwctwo.cpp:573-576` |
| getReleaseFences 遍历所有层 | `/tmp/drmhwc/drmhwctwo.cpp:449-469` |
| setClientTarget dup 保存 acquire fence | `/tmp/drmhwc/drmhwctwo.cpp:619-630`、`drmhwctwo.h:71-73` |
| validate/accept 两套类型状态 | `/tmp/drmhwc/drmhwctwo.h:41-55`、`drmhwctwo.cpp:243-249` |
| hotplug 注册后立刻回调 CONNECTED | `/tmp/drmhwc/drmhwctwo.cpp:135-156` |
| per-frame timeline fence + 下一帧提交后才 signal | `/tmp/drmhwc/drmdisplaycomposition.cpp:222-282`、`drmdisplaycompositor.cpp:825-854` |
| 合成型 vsync 兜底 | `/tmp/drmhwc/vsyncworker.cpp:88-94` |
| gralloc → DRM fb 导入与格式映射 | `/tmp/drmhwc/platformdrmgeneric.cpp:52-131` |
| 无 zpos 属性（Pie 版完全没用到 zpos） | `grep -rn zpos /tmp/drmhwc` → 0 命中 |

**AOSP Pie（tag `android-9.0.0_r1`）**

| 文件 | 行 | 内容 |
|---|---|---|
| `U_HW` | 36-53 | attribute 枚举（DPI*1000） |
| `U_HW` | 106-115 | SKIP_VALIDATE 定义 |
| `U_HW` | 120-131 | CLIENT/DEVICE 定义（device 不得改 CLIENT） |
| `U_HW` | 181-191 / 276-283 | display request / layer request |
| `U_HW` | 206-217 | 错误码（HAS_CHANGES=5） |
| `U_HW` | 837-861 | hotplug 回调（每个 display 至少要报一次；连接后默认 OFF） |
| `U_HW` | 882-899 | vsync 回调（CLOCK_MONOTONIC ns，仅 enable 时） |
| `U_HW` | 1055-1074 | acceptDisplayChanges |
| `U_HW` | 1108-1128 | getActiveConfig（BAD_CONFIG 语义） |
| `U_HW` | 1131-1162 | getChangedCompositionTypes |
| `U_HW` | 1164-1194 | getClientTargetSupport（RGBA_8888 最低要求） |
| `U_HW` | 1253-1274 | getDisplayAttribute（未知 → -1） |
| `U_HW` | 1276-1297 | getDisplayConfigs |
| `U_HW` | 1321-1358 | getDisplayRequests |
| `U_HW` | 1433-1468 | getReleaseFences（"没返回就当作可写"） |
| `U_HW` | 1471-1505 | presentDisplay（present fence 语义） |
| `U_HW` | 1527-1568 | setClientTarget（acquire fence 所有权） |
| `U_HW` | 1729-1752 | setPowerMode（返回即完成） |
| `U_HW` | 1900-1931 | validateDisplay（NONE/HAS_CHANGES） |
| `U_HW` | 1980-2010 | setLayerBuffer（错误码只有 BAD_LAYER/BAD_PARAMETER） |
| `U_HW` | 2185-2200 / 2294-2310 / 2312-2329 | display frame / visible region / z order |
| `U_SF` `SurfaceFlinger.cpp` | 1528-1542 | handleMessageRefresh 顺序 |
| 同上 | 1814-1886 | rebuildLayerStacks（可见层升序 + destroyHwcLayer） |
| 同上 | 1958-2076 | setUpHWComposer（setGeometry→setPerFrameData→prepare） |
| 同上 | 2010 | `layer->setGeometry(displayDevice, i)`（i 就是 z） |
| 同上 | 2063-2075 / 2078-2096 / 2099-2116 | 三处 `isDisplayOn()` 跳过 |
| 同上 | 2114-2149 | presentDisplay + getReleaseFences + 逐层 onLayerDisplayed |
| 同上 | 2868-2887 | doDisplayComposition（先合成后 swapBuffers） |
| 同上 | 2889-3040 | doComposeSurfaces（2940-2947 清透明；2987-3026 按 z 画 CLIENT） |
| 同上 | 1265-1352 | enable/resync/onVsyncReceived |
| 同上 | 1706-1795 | postComposition（present fence → FenceTime/DispSync） |
| 同上 | 3700-3760 | setPowerModeInternal（ON 时 resyncToHardwareVsync） |
| 同上 | 682 | `LOG_ALWAYS_FATAL_IF(!isConnected(PRIMARY))` |
| `U_SFD` `HWComposer.cpp` | 109-126 | validateChange（CLIENT→任何 非法） |
| 同上 | 270-279 | getRefreshTimestamp 空指针风险 |
| 同上 | 300-319 | getActiveConfig（BAD_CONFIG → nullptr） |
| 同上 | 394-403 | setClientTarget |
| 同上 | 406-520 | prepare：skip-validate 路径（437-456）+ 变更应用（483-500）+ acceptChanges（514） |
| 同上 | 501-511 | layer request 处理 |
| 同上 | 556-600 | getLayerReleaseFence / presentAndGetReleaseFences |
| `U_SFD` `HWC2.h` | 136-170（DPI 换算 158-165） | Config::Builder（DPI/1000、-1 → 默认密度） |
| `U_SFD` `HWC2.cpp` | 538-568 | getReleaseFences（未知 layer → BadLayer） |
| 同上 | 571-582 | present（`new Fence(fd)`） |
| 同上 | 596-603 | setClientTarget 先 dup |
| 同上 | 644-657 | Display::validate |
| 同上 | 685-693 | hotplug → loadConfigs |
| `U_SF` `Layer.cpp` | 486-640 | setGeometry（blend/alpha/frame/crop/z） |
| `U_SF` `BufferLayer.cpp` | 256-258 / 618-689 | onLayerDisplayed / setPerFrameData |
| `U_SF` `BufferLayerConsumer.cpp` | 227-243 | setReleaseFence 忽略无效 fence |
| `U_SFD` `FramebufferSurface.cpp` | 93-176 | advanceFrame/nextBuffer/setClientTarget/onFrameCommitted |
| `U_SF` `DisplayDevice.cpp` | 343-380 | prepareFrame/swapBuffers/advanceFrame |
| `U_SF` `EventThread.cpp` | 313-325 | "Timed out waiting for hw vsync; faking it" |
| `U_SF` `DispSync.cpp` | 114-121 / 390-441 / 443-497 | period==0 等待 / 忽略 present fence / addResyncSample |
| `U_SFD` `ComposerHal.cpp` | 599-613 / 1162-1179 / 1233-1245 | validateDisplay 恒 NONE / hasChanges / takePresentFence |
| `U_WIRED` `types.hal` | 20-30 | HIDL Error 无 HAS_CHANGES |
| `U_WIRED` `utils/passthrough/.../HwcHal.h` | 111-131 / 327-377 / 385-425 / 521-584 | 回调注册 / validateDisplay / presentDisplay / initDispatch |
| `U_WIRED` `utils/hal/.../Composer.h` | 56-73 | getCapabilities 只转发 3 个 |
| `U_WIRED` `utils/hal/.../ComposerCommandEngine.h` | 248-268 / 271-310（SKIP_VALIDATE 分支 277-289） | executeValidateDisplay / executePresentOrValidateDisplay |

### 9.2 未能确认的事项（会让哪些结论失效）

1. **Z1 KMS 驱动是否支持 `OUT_FENCE_PTR` 与 plane `IN_FENCE_FD`** ——【未确认】。若不支持：§3.1、§3.2、§3.3 的"交给 KMS"方案要退回 `sw_sync` timeline + 提交后 software signal（`/tmp/drmhwc` 就是这种），且 present fence 只能是近似值（多一帧延迟）。**验证方式**：在板上 `modetest -p` 看 plane 属性里有无 `IN_FENCE_FD`，`modetest -c` / atomic 属性里看 CRTC 有无 `OUT_FENCE_PTR`。
2. **该镜像里 `android.hardware.graphics.composer@2.1-service` 的二进制是否就是 Pie `2.1/default` 的 passthrough 实现** —— 任务上下文给出，本机未反汇编核对【未确认】。若它被换成 2.2/2.3 实现，`getCapabilities` 转发集合与 `presentOrValidate` 细节可能不同。
3. **本地 `/tmp/drmhwc` 的确切 commit** —— 已确认是 Pie 时代 HWC2 版本（有 `drmhwctwo.cpp`、`separate_rects.h`、`HWC2::Callback`），但未核对具体 tag/commit，引用只针对眼前这份代码。
4. **"SF 是否会产出 CLIENT 层夹在 DEVICE 层中间"的实际频率** —— 我确认了 SF **不会**主动避免这种组合（§1.3），但未实测具体场景（如 HDR 视频层在最上方时，下面 UI 是否仍是 DEVICE）。这影响"必须自己实现 z-cut"的成本，不影响结论（必须实现）。
5. **present fence 的真实语义在 command-mode 面板上的偏差** —— `hwcomposer2.h` 写的是"starts to transfer to panel memory"，本机是 MIPI command mode（`mt6572` DSI），实际 signal 点取决于驱动/硬件。这与"必须给真实 fence"的结论无关，只影响 1～2 帧的延迟估算【未确认】。

### 9.3 复现命令（本报告所有 AOSP 引用的取法）

```bash
mkdir -p /tmp/aosp9 && cd /tmp/aosp9
fetch() { curl -sSL "https://android.googlesource.com/platform/$1/+/refs/tags/android-9.0.0_r1/$2?format=TEXT" | base64 -d > "$3"; }
fetch frameworks/native services/surfaceflinger/SurfaceFlinger.cpp sf/SurfaceFlinger.cpp
fetch frameworks/native services/surfaceflinger/DisplayHardware/HWComposer.cpp sf/HWComposer.cpp
fetch frameworks/native services/surfaceflinger/DisplayHardware/HWC2.cpp sf/HWC2.cpp
fetch frameworks/native services/surfaceflinger/DisplayHardware/ComposerHal.cpp sf/ComposerHal.cpp
fetch hardware/libhardware include/hardware/hwcomposer2.h hw/hwcomposer2_pie.h
fetch hardware/interfaces graphics/composer/2.1/utils/passthrough/include/composer-passthrough/2.1/HwcHal.h hidl/HwcHal.h
fetch hardware/interfaces graphics/composer/2.1/utils/hal/include/composer-hal/2.1/Composer.h hidl/Composer.h
fetch hardware/interfaces graphics/composer/2.1/utils/hal/include/composer-hal/2.1/ComposerCommandEngine.h hidl/ComposerCommandEngine.h
# 校验本地头文件与 Pie 完全一致（输出 IDENTICAL）：
diff -u hw/hwcomposer2_pie.h /home/lxj/claude/6572/z1a9_gpt/scripts/z1hwc2/include/hardware/hwcomposer2.h && echo IDENTICAL
```

> 说明：`HWComposer.cpp` / `HWC2.cpp` / `ComposerHal.cpp` / `FramebufferSurface.cpp` 在 Pie 里位于
> `services/surfaceflinger/DisplayHardware/`（本报告用 `U_SFD` 简写）；其余 `SurfaceFlinger.cpp`、`Layer.cpp`、
> `BufferLayer*.cpp`、`DisplayDevice.cpp`、`EventThread.cpp`、`DispSync.cpp` 直接位于 `services/surfaceflinger/`（`U_SF`）。
