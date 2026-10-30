# 黑屏根因确认（2026-10-30，串口实证）

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
