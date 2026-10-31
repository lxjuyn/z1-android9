# ⚠️ 本文档的根因判断已被推翻 —— 请以 out/EGL_BLACK_SCREEN_FIX.md 为准

**更正时间：2026-10-30（同日晚，eglfix agent 提出反证后主会话复核确认）**

本文档原判定"r5h 改了 tag 却没提供 `libEGL_mesa.so` → 找不到驱动 → SF 崩"。
**该链条第一环就是错的**，三条可复现证据：

1. **A9 的 loader 先找 `libGLES_<tag>.so`，命中即不再加载 `libEGL_mesa.so`**
   （`Loader.cpp::open()`：`dso = load_driver("GLES", ...)` 成功则 `load_driver("EGL", ...)`
   只是 else 分支、永不执行）。
2. **`libGLES_mesa.so` 本身就是完整的 Mesa EGL 驱动** —— 主会话实测它导出 **44 个 `egl*` 入口**
   （`eglGetDisplay` / `eglInitialize` / `eglCreateContext` / `eglSwapBuffers` 全在）。
   它在 r5i 里**是在的**（94,488 B），本文档的对照表漏掉了它。
3. **`libEGL_mesa.so` 在本项目整个上板历史上从未被加载过一次**
   （全库 log 统计：`libGLES_mesa.so` 加载 81 次 / 18 个 log；`libEGL_mesa.so` **0 次**）。
   → 放回它、修它的 DRI 路径、造 `/usr/local/lib/dri` symlink **全是空操作**。

**串口实证进一步正面排除"驱动没装载"**：
`LOG_ALWAYS_FATAL_IF(!hnd, "couldn't find an OpenGL ES implementation")` 在碰 DRM **之前**就 abort，
而 r5i 的 log 里 `comm="surfaceflinger" + name="dri"` 计数 **406 次**
（swiftshader 期 log 全部 **0 次**）→ 驱动装载成功、已走到 DRM/mesa 初始化，崩在**内部**。

**因此本文档下面"修复方向"里建议的检查法（`ro.hardware.egl` ↔ `libEGL_<值>.so`）也是错的**：
应查 `libGLES_<值>.so`，且**要查同名库是否在 `/lib` 与 `/vendor/lib` 之间被顶替**（这次真正的坑）。

---

## 真正的根因（正确版，见 out/EGL_BLACK_SCREEN_FIX.md）

镜像里遗留了一份 **Mesa 24.0.9** 的 `/lib/libglapi.so`（379,112 B），
它按 soname 抢在 `/vendor/lib/libglapi.so`（Mesa 19.1.8，173,184 B）之前被解析，
把 GLES 的 dispatch 表尺寸从 **1677 撑成 1927** → mesa 初始化早期崩 → EGL 装载失败 → SF SIGABRT → 黑屏。

**修法：删掉那一个文件**（产物 `out/z1_r5_system/system.img.r5k_final`）。

---

# （以下为已被推翻的原始分析，保留供追溯）

# 黑屏根因分析（原始版，已推翻）

## 结论（100% 确认）

**r5h 那次 GPU 改动把 `ro.hardware.egl` 改成 `mesa`，但没提供 `libEGL_mesa.so`
（反而把它删进了 `/.r5f_gpu_backup/`）→ `libEGL.so` 找不到驱动 →
SurfaceFlinger SIGABRT 崩溃 → 连锁重启 zygote → 背光亮但屏幕全黑。**

这是**回归**，不是刷写失败：`r5c boot + r5i system` 两段刷写都 100% 全量成功
（`0x5EC000`/`0x38400000` 字节数精确匹配）。

## 串口实证（921600 抓取，`mainline_recon/z1_uart921_20261030_104102.log`）

```
init: starting service 'surfaceflinger'...
init: Received control message 'start' for 'bootanim' from pid:6446 (/system/bin/surfaceflinger)
init: Service 'surfaceflinger' (pid 6446) received signal 6        ← SIGABRT
init: Sending signal 9 to service 'surfaceflinger' process group...
init: Command 'restart zygote' action=onrestart ... took 233ms and succeeded
init: starting service 'surfaceflinger'...                          ← 再起
init: Service 'surfaceflinger' (pid 6585) received signal 6         ← 又崩
...（循环：6446 → 6585 → 6690 → 7432 → 7676 → 7778 → 7855 → 7903 ...）
```
**循环重启 → 屏幕无内容 → 黑屏。** 每次 SF 崩溃都触发 `restart zygote`。

## 根因对照（决定性）

| | `ro.hardware.egl` | `/vendor/lib/egl/libEGL_mesa.so` | 结果 |
|---|---|---|---|
| **r5f**（GPU 改动前，显示正常） | `swiftshader` | ✅ 有（259,992 B） | ✅ 正常 |
| **r5i**（当前，黑屏） | **`mesa`** | ❌ **不存在**（被删进 `/.r5f_gpu_backup/`） | ❌ SF 崩溃 |

`/lib/libEGL.so` 按 `libEGL_<tag>.so` 拼驱动名（`ro.hardware.egl` 的值即 `<tag>`），
且 r5f/r5i 的 `/lib/libEGL.so` 是**同一个文件**（sha `5c78674deeeb014c…`，已实测）。

## 附带发现（同一次抓取里）

1. **AVC denied 与 /dev/dri 有关**（permissive 下不阻断，但说明路径被访问）：
   ```
   avc: denied { read } for comm="surfaceflinger" name="dri" tclass=dir
   avc: denied { open } for path="/dev/dri"      tclass=dir
   ```
2. **独立问题**：`[z1wifi] 6620_launcher never appeared in 120s; abort`
   → WiFi launcher 没起来（与 r5jdeploy 报的"vendor HAL 降级"方向一致）。
3. `init: Failed to bind socket 'pdx/system/vr/display/*'` 是 A9 的 VR 服务，Z1 无此硬件 → 正常。

## 关键教训

- **console 速率是 921600**（`ttyMT0`）。用 115200 抓会得到**全乱码**（本次浪费了约 20 分钟）。
  判据：921600 下可打印率 = 1.00，115200 下 = 0.08。
- **r5h/r5i 从未上板验证**就合入，且改动**自我矛盾**（改了 tag 却删了对应驱动）。
  上板前应做的最基本静态检查：`ro.hardware.egl` 的值 ↔ `libEGL_<值>.so` 是否存在。
  此检查未做 → 直接黑屏。
- `/.r5f_gpu_backup/` 里那个 `libEGL_mesa.so` **DRI 路径是 `/usr/local/lib/dri`（镜像内不存在）**，
  所以"把备份放回去"**也不够**，还需修正 DRI 路径或改用别的方案。

## 修复方向（待 eglfix agent 定案）

1. **最小回退**（立即可用）：`ro.hardware.egl` 回退成 `swiftshader` —— 即 r5f 的状态，显示能回来，
   但 GPU 加速目标（Mali400）没达成。
2. **正确修法**：恢复 mesa 硬渲染，需同时满足：
   - `libEGL_mesa.so` 在位，**且其 DRI 路径指向 `/vendor/lib/dri`**（当前那个是坏的）
   - `libGLES_mesa.so` 在位（✅ 已在，DRI 路径正确）
   - 参考 p12_r2（10-20 唯一验证过 Mali400 的镜像）：它的 `ro.hardware.egl` **是空的**，
     且 `/vendor/lib/egl/` 里只有 `libGLES_mesa.so` —— **需搞清它靠什么提供 EGL**。
