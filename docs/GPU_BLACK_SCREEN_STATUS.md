# GPU 加速 / 黑屏 排查状态（2026-10-30 中午）

## 已确认的事实（全部实测）

### 时间线：mesa 硬渲染**曾经成功过**
| 时间 | 渲染器 | SF 崩溃 | 结果 |
|---|---|---|---|
| **10-19 / 10-20** | **Mali400**（Mesa 19.1.8） | **0** | ✅ 成功（log 有 renderer:Mali400） |
| 10-23 ~ 10-25 | SwiftShader | 0 | ✅ 能显示（但很卡） |
| **10-30（r5i/r5k）** | mesa 尝试 | **163~421 次** | ❌ 黑屏 |

### 已排除（都不是根因）
1. **EGL/GLES 栈**：r5k 现在与 **p12_r2（10-20 Mali400 跑通的那套）逐字节相同**
   （`libGLES_mesa.so` `824516a4` / `gallium_dri.so` `a4c99e60` / `libglapi.so` `7639e55e` /
   `/lib/libEGL.so` `2edd585b` 全等）。
2. **`/lib/libglapi.so`（Mesa 24.0.9 那份）**：已删；且 p12 里本来就没有它 → 删对了，但**不够**。
3. **`libEGL_mesa.so` 缺失**：错判 —— A9 loader 先找 `libGLES_<tag>.so`，
   它本身导出 44 个 `egl*` 入口，`libEGL_mesa.so` 全程不参与
   （全项目 log：`libGLES_mesa.so` 加载 81 次 / 全等 18 log；`libEGL_mesa.so` **0 次**）。
4. **`/dev/dri/renderD128` 不存在**：不是根因 —— 10-20 成功那次同样是
   `Failed to open DRM device /dev/dri/renderD128`（98 次），照样出 Mali400。
5. **lima 驱动**：正常 probe（`[drm] Initialized lima 1.1.0 for 13010000.gpu on minor 0`，0.36s）。
6. **GPU 时钟 25390**：不是根因 —— 10-20 成功那次**同样是 25390**（独立问题，已另有修复）。
7. **graphics HAL**：`mapper@2.0-impl` / `allocator@2.0-impl` / `composer@2.1-impl` 两镜像都在且相同。
8. **`gralloc.z1.so` / `hwcomposer.z1.so`**：确实不同（+12B / +84B），
   但反汇编差异 8804 行全是 **PLT/GOT 重定位偏移**（同源码不同链接时刻），**逻辑等价**。

### ★ 新发现：boot 差异很大
| | p12_r2 boot（10-20 Mali400 成功时用的） | r5c boot（当前） |
|---|---|---|
| size | 5,658,624 B | 6,209,536 B |
| ksz | **4,370,772** | 4,228,295 |
| rsz | **1,282,978** | 1,977,962 |
| cmdline | **含 `console=tty0`** | **缺 `console=tty0`** |

→ **10-20 的成功是在 p12_r2 的 boot + system 组合下取得的，不是当前 r5c boot。**
→ 所以"r5k 图形栈 == p12 图形栈 却黑屏"不矛盾：**boot 侧不同**。

## 为什么还没定案
串口日志**只有内核/init 层**（`z1logcat` 0 行），**拿不到 SF 的 abort message**——
那在 Android logcat 里。SF 崩在用户态，内核只看到 `received signal 6`。
**必须有 logcat 才能知道它为什么 abort。**

## 下一步：对照实验（最快定案）
既然 p12_r2 是被实证过的"能出 Mali400"组合，做**整包对照**：
- 若刷 p12_r2（boot+system）→ **能显示** ⇒ 我的 system 里有东西坏了，二分定位
- 若刷 p12_r2 → **仍黑屏** ⇒ 不是镜像问题，是更底层的（boot 内核/dtb、或硬件态变化）

然后按结果二分：先只换 boot（p12 boot + r5k system），再只换 system。

## 诚实说明
- **根因尚未确定**。已排除 8 项，剩 boot 侧与"需要 logcat 才能看到的原因"两个方向。
- 本轮我犯过一个错：曾据"日志无 lima"下结论，后发现那份日志从 `[275s]` 才开始、
  丢了早期启动 —— 结论已撤回。**后来用完整日志（从 `[0s]`）证实 lima 正常 probe。**
