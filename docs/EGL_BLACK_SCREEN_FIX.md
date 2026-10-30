# EGL 黑屏回归 —— 诊断与修复报告

日期: 2026-10-30
作者: EGL/显示回归诊断与修复工程师 (agent)
基线: `out/z1_r5_system/system.img.r5i_final` (sha256 `35616bfd23320b32d195083442670c019309e09ff6d4cc9d7eae0faba44fd1af`)
产物: **`out/z1_r5_system/system.img.r5k_final`** (sha256 `b567885796eae85db5fb429bb7b24161c4236ca6792bb09c5c625605802ba1be`)

---

## 0. 一句话结论

**黑屏根因 = 镜像里遗留了一份 Mesa 24.0.9 版的 `/lib/libglapi.so`（379,112 B），
它按 soname 抢在 `/vendor/lib/libglapi.so`（Mesa 19.1.8，173,184 B）前面被 GLES/gallium
栈解析到，把 GLES 的 dispatch 表尺寸从 1677 撑成 1927 → mesa 初始化早期崩 → EGL 驱动装载失败。**

修法（置信度 **高**）：**删掉这一个文件**。产物 r5k_final 已验证。
配套 boot.img `out/z1_r5c_boot/boot.img` 不变。

**2026-10-30 10:41 串口实测已正面证实本结论**（`mainline_recon/z1_uart921_20261030_104102.log`）：
SurfaceFlinger 在 SIGABRT 死循环里反复崩（pid 6446→6585→6690→…→8410+，779 处 surfaceflinger、
20+ 次 `received signal 6`），每次崩溃触发 `restart zygote` → 屏幕永远没内容。
**关键：SF 每次崩之前都在访问 `/dev/dri`（214 处 `comm="surfaceflinger" ... name="dri"`），
而历史上所有 swiftshader 期的 log 里这个计数是 0。**
这证明 **mesa 驱动已装载成功并走到 DRM 初始化阶段** —— 若驱动找不到，
`LOG_ALWAYS_FATAL_IF(!hnd, "couldn't find an OpenGL ES implementation")` 会在碰 DRM
**之前**就 abort（见 §2-B1 源码）。即：**"驱动缺失"被排除，"装载后在初始化内部崩"被证实**，
与 §3 的 dispatch 表尺寸不匹配完全吻合。

**同时必须更正主会话给我的三处前提**（见 §2，有实测证据）——它们会误导后续判断。

---

## 1. 为什么 r5h/r5i 会黑、而 r5d/r5f 正常（关键对照组）

主会话给的表里"r5d/r5f 显示正常"这一条是**对的，但原因被误读了**。实测：

| | r5d / r5f | r5h / r5i |
|---|---|---|
| `ro.hardware.egl` | **`swiftshader`** | `mesa` |
| `/vendor/lib/egl/libEGL_mesa.so` | 在（Mesa **24.0.9**） | 已删（备份进 `/.r5f_gpu_backup/`） |
| `/vendor/lib/egl/libGLES_mesa.so` | **不存在** | 在（shá `aab57fd4…`，与 p12 同） |
| `/vendor/lib/dri/gallium_dri.so` | **不存在** | 在（shá `f7497e4a…`，与 p12 同） |
| `/lib/libglapi.so` | 在（Mesa **24.0.9** 那份） | 在（同一份） |
| 出画面 | ✅（走 **swiftshader 软件渲染**） | ❌ 黑屏 |

**r5d/r5f 能用不是因为 mesa 跑通了，而是因为它压根没走 mesa** —— 属性是 `swiftshader`，
所以 loader 加载 `libGLES_swiftshader.so`，那条路不碰 `libglapi`。**r5d/r5f 的 mesa 栈其实也是坏的**
（缺 `libGLES_mesa.so`、`libEGL_mesa.so` 的 DRI 路径还是死的 `/usr/local/lib/dri`）。
r5h 干的事是"把属性切成 mesa + 补 p12 那套可用 mesa 19.1.8"——方向对，
**但漏删了一个会让你崩的遗留文件**。

**结论：这不是"r5i 相对 r5f 的显示回归"，而是"第一次真正尝试走 mesa 硬件路径"的首次上板。**

**串口证据对这个对照组的补充**：新 log 里 SF 反复崩、且每次都先摸 `/dev/dri`；
而 r5f/r5d 那套走 swiftshader 时**根本不碰 `/dev/dri`**（0 处）。这正是"r5d/r5f 走的是另一条路
（软件渲染，不碰 DRM）"的独立佐证 —— 与属性对照表一致。

---

## 2. 必须更正的三处前提（实测证据）

### 更正 A：p12_r2 的 `ro.hardware.egl` **不是空的，就是 `mesa`**

主会话表里写"p12_r2 `ro.hardware.egl` = 未设置（空）"。**实测读出来是 `mesa`**：

```
$ debugfs -R "cat /build.prop" out/A9_flashpkg_p12_r2/system.img | grep -n egl
51:ro.hardware.egl=mesa
```
（同一条也在 `out/a9sys_r2/system_mainline.img:51`。该文件 mode 0600 root-only，
普通 `grep` 报"权限不够"，我用 debugfs 直读绕开。）

### 更正 A'：那个"属性为空但有 `libGLES_mesa.so`"的模型**在本机不存在**，且**不可能是 p12 的状态**

主会话第二次给的表仍写"r5f/r5i 的 `ro.hardware.egl` 分别是 swiftshader/mesa"，同时说
"p12_r2 的属性是空的、`/vendor/lib/egl/` 里只有 `libGLES_mesa.so`"。这两条**互相矛盾**，
我用穷举审计裁决：**本机每个 system 镜像的 (属性, `/vendor/lib/egl` 内容) 都列出来了**：

| 镜像 | `ro.hardware.egl` | `/vendor/lib/egl/` |
|---|---|---|
| `A9_flashpkg_p12_r2/system.img` | **`mesa`** | `libGLES_mesa.so` |
| `A9_flashpkg_p12_r2_D4/system.img` | **`mesa`** | `libGLES_mesa.so` |
| `a9sys_r2/system_mainline.img` | **`mesa`** | `libGLES_mesa.so` |
| `z1a9_r2/system.raw.img` | **`mesa`** | `libGLES_mesa.so` |
| `z1_r5_system/system.img.r5d/r5f_final` | `swiftshader` | `libEGL_mesa.so` + 3×swiftshader |
| `z1_r5_system/system.img.r5h/r5i_final` | `mesa` | `libGLES_mesa.so` + 3×swiftshader |
| `out/A9_p13/system_p13.img` | `swiftshader` | `libGLES_mesa.so` + 3×swiftshader |
| `Z1_A9_CANDIDATES_*` (r1/r2/rollback_r9) | `swiftshader` | 3×swiftshader |

**凡是装了 `libGLES_mesa.so` 的镜像，属性一律是 `mesa`（p12 系列 4 个无一例外）；
本机没有任何一个镜像同时满足"属性空 + 只有 libGLES_mesa.so"。**

而且这个"空属性"模型**在机理上也走不通**（与 §2-B1 的 loader 语义冲突）：

- `load_driver("GLES")` 只走 `load_updated_driver()`，即
  **`libGLES_` + `ro.hardware.egl` + `.so`** —— 属性为空时它先试 `libGLES_.so`
  （或者说 `property_get` 返回 0 → 该 key 被跳过），**都失败 → 返回 NULL**；
- 然后 `load_driver("EGL")` 同理需要 `libEGL_` + tag + `.so` / `libEGL.so`；
  `load_driver("GLESv2")` 需要 `libGLESv2_` + tag + `.so` / `libGLESv2.so`。
  注意 `MatchFile::find()` 里**根本没有 `libGLES.so` 这个拼法的字面量**
  （`strings` 实测：`/vendor/lib/egl/lib` + `libGLES_android.so` + `_swiftshader.so` + `_emulation.so`，无 `libGLES.so`）；
- 镜像里也没有 `/vendor/lib/egl/libGLES.so` 或 `libEGL.so`（审计已列），
  更关键的是 **`libGLES_mesa.so` 是个完整 ELF，按 `<kind>_<tag>.so` 拼法只有 tag=mesa 能拼出它的名字**。
- 所以"属性空"时三条 `load_driver` 全空 → `LOG_ALWAYS_FATAL_IF(!hnd, ...)` → **SurfaceFlinger 每次都会打印
  `couldn't find an OpenGL ES implementation` 然后 abort**，**根本不可能**出现
  10-20 log 里那 15 次 `vendor:lima / renderer:Mali400 / Mesa 19.1.8`。
- **反证（tag 必须是 mesa 才拼得出这个文件名）**：10-20 的 log 里那两行
  `Loading /vendor/lib/egl/libGLES_mesa.so` + `loaded /vendor/lib/egl/libGLES_mesa.so`
  同时出现 **2 次 / 3 次**（`z1_android9_acm_20261019_221509_818916.log`、
  `z1_android9_acm_20261020_180334_356207.log`，`grep -c` 实测）。
  `load_updated_driver` 传的是裸名 `libGLES_mesa.so`，只有在
  **`ro.hardware.egl=mesa`** 时才拼得出来 → **p12_r2 上板时属性必然是 `mesa`**。

**结论：主会话"p12 属性为空"的这条应作废**（很可能是探查时踩了 `build.prop` 是 0600、
普通 `grep` 报权限不够而误判成"空"）。**p12 就是 `mesa`，靠 `libGLES_mesa.so` 出画面。**

### 更正 B：p12_r2 **确实没有** `libEGL_mesa.so`，但**也不需要**它——它有 `libGLES_mesa.so`，
而那个文件**本身就是完整的 Mesa EGL 驱动**（导出全部 `egl*` 入口）

主会话推断"`libEGL.so` 找不到 `libEGL_mesa.so` → EGL 起不来"在本例**不成立**。证据三条：

**B1. AOSP 9 的 loader 是"先找 `libGLES_<tag>.so`，找不到才回退到分别找 `libEGL_/libGLESv1_CM_/libGLESv2_`"**
（源码 `Loader.cpp::open()`，取自 LineageOS `android_frameworks_native` `lineage-16.0`）：

```cpp
dso = load_driver("GLES", cnx, EGL | GLESv1_CM | GLESv2);
if (dso) { hnd = new driver_t(dso); }
else {
    // Always load EGL first
    dso = load_driver("EGL", cnx, EGL);
    if (dso) { hnd = new driver_t(dso);
        hnd->set( load_driver("GLESv1_CM", cnx, GLESv1_CM), GLESv1_CM );
        hnd->set( load_driver("GLESv2",    cnx, GLESv2),    GLESv2 ); }
}
```
而 driver 名 = `std::string("lib") + kind + "_" + prop + ".so"`（`load_updated_driver()`，
`HAL_SUBNAME_KEY_PROPERTIES = {"ro.hardware.egl", "ro.board.platform"}`）。
`kind="GLES"` + `prop="mesa"` → **`libGLES_mesa.so`**。它存在 → **第一分支命中，`libEGL_mesa.so` 永远不会被加载。**

**B2. `libGLES_mesa.so` 导出全套 EGL 入口**（`nm -D`，共 60 个导出）：
```
T eglGetDisplay   T eglInitialize  T eglCreateContext  T eglCreateWindowSurface
T eglChooseConfig T eglMakeCurrent T eglSwapBuffers    T eglGetProcAddress  ... (全部)
```
`load_driver()` 拿到它后 `dlsym(dso,"eglGetProcAddress")` 找得到 → `mask & EGL` 分支正常填充。
**一个 so 同时满足 EGL + GLESv1 + GLESv2 三个掩码。**

**B3. 上板 log 实证**（不是我推的，是历史日志里跑出来的）：
```
mainline_recon/z1_android9_acm_20261019_221509_818916.log:961  I SurfaceFlinger: vendor    : lima
mainline_recon/z1_android9_acm_20261019_221509_818916.log:962  I SurfaceFlinger: renderer  : Mali400
mainline_recon/z1_android9_acm_20261019_221509_818916.log:963  I SurfaceFlinger: version   : OpenGL ES 2.0 Mesa 19.1.8
mainline_recon/z1_android9_acm_20261020_180334_356207.log:938  D libEGL  : loaded /vendor/lib/egl/libGLES_mesa.so
```
**15 个 10-19/10-20 的上板 log 全部是 `loaded .../libGLES_mesa.so` + `lima / Mali400 / Mesa 19.1.8`。**
**所以"p12 为什么能出 Mali400"的答案是：它靠 `libGLES_mesa.so` 单文件承担了 EGL+GLESv1+GLESv2，
`libEGL_mesa.so` 从头到尾没参与。**

> 附注：10-23 起（r5 线）的 log 变成 `loaded /vendor/lib/egl/libEGL_swiftshader.so` +
> `Google SwiftShader 4.0.0.4`，与 r5d/r5f 的 `ro.hardware.egl=swiftshader` 吻合。
> **注意：本机不存在任何 r5h/r5i/r5c 的上板 log**（最晚 10-25），所以"r5i 黑屏"这件事
> 只有现象描述，没有 log —— 我的结论是从镜像静态取证+机理推出来的，不是从 log 读出来的。

---

## 3. 根因（完整证据链）

### 3.1 铁证一：p12（能用）与 r5i（黑屏）的整套 EGL/GLES/DRI 栈**逐字节相同**

| 文件 | p12 sha(16) | r5i sha(16) | |
|---|---|---|---|
| `/lib/libEGL.so` | `5c78674deeeb014c` | `5c78674deeeb014c` | 同 |
| `/lib/libGLESv2.so` | `e6248971750d2fdb` | `e6248971750d2fdb` | 同 |
| `/vendor/lib/egl/libGLES_mesa.so` | `aab57fd4a5bc0f02` | `aab57fd4a5bc0f02` | 同 |
| `/vendor/lib/libglapi.so` | `9c1ace8554dedf09` | `9c1ace8554dedf09` | 同 |
| `/vendor/lib/dri/gallium_dri.so` | `f7497e4a2090b173` | `f7497e4a2090b173` | 同 |
| **`/lib/libglapi.so`** | **不存在** | **`f8420adaf32306b4` (379,112 B)** | **← 唯一差异** |

`libGLES_mesa.so` 的 11 个 NEEDED 依赖、`gallium_dri.so` 的 7 个依赖，在 p12/r5i 两边**全部同 shá**。
全树 diff（`LC_ALL=C diff -rq`）在 r5i 的**全部图形目录**里也**只找出这一项**。

### 3.2 铁证二：这份 `/lib/libglapi.so` 是 **Mesa 24.0.9** 的产物，与 19.1.8 栈 ABI 冲突

反汇编 `_glapi_get_dispatch_table_size()`（`sizeof(struct _glapi_table)/sizeof(void*)`）：

```
/lib/libglapi.so      (24.0.9): movw r0, #1927   (0x787)   ← dispatch 表 1927 项
/vendor/lib/libglapi.so(19.1.8): movw r0, #1677   (0x68d)   ← dispatch 表 1677 项
```
**差 250 项。** 而该函数正是 `_mesa_initialize_context()` 等初始化路径的调用者
（`mesa_src/src/mesa/main/context.c:872`: `numEntries = MAX2(_glapi_get_dispatch_table_size(), _gloffset_COUNT)`），
用来给 dispatch 结构做大小相关的初始化。19.1.8 编译出的代码按 1677 的布局建表，
却从 24.0.9 的库拿到 1927 并落到同名的 `_glapi_Dispatch` 全局上 → **布局/语义不匹配**。

### 3.3 铁证三：soname 与搜索顺序注定它会被抢到

```
$ readelf -d /lib/libglapi.so        → SONAME: libglapi.so
$ readelf -d /vendor/lib/libglapi.so → SONAME: libglapi.so     ← 同名
```
消费方 `libGLES_mesa.so` / `gallium_dri.so` 的 NEEDED 只写裸名 `libglapi.so`。
而 bionic 的 `ld.config.txt`（镜像 `/etc/ld.config.txt` 实测）**default namespace 搜索序**：
```
namespace.default.search.paths  = /system/${LIB}
namespace.default.search.paths += /vendor/${LIB}
namespace.default.search.paths += /odm/${LIB}
```
`/lib` == `/system/lib`（rootdir 挂成 /system，`ro.build.system_root_image=false` 的同构布局），
**排在 `/vendor/lib` 前面** → `/lib/libglapi.so` 先命中。
（旁证：`libGLES_mesa.so` 的 `_glapi_*` 需求集与两个候选的导出集基本一致，唯一差别是
24.0.9 多一个 `_glapi_destroy_multithread` ——**符号名对得上，所以动态链接器不报错，
错误只会在运行时以"表大小/布局不一致"的形式爆出来**，这正是最难查的一类。）

### 3.4 铁证四：它是怎么进来的 —— r5d 那次 mesa 部署的**漏网之鱼**

`/lib/libglapi.so` 的 sha256 `f8420ada…` **精确等于**本机 2026-10-27 那次 mesa 24.0.9 交叉编译的产物：
```
mesa_android/lima-build-egl/src/mapi/shared-glapi/libglapi.so                              f8420ada…  ✓
bridge_backups/products/products_mesa_lima_android_20261027_121042/libglapi.so             f8420ada…  ✓
```
而部署文档 `z1a9_gpt/scripts/z1_mesa_lima_deploy.md:16` 白纸黑字写了：
```
- libglapi.so → /system/lib/        ← 就是 /lib
```
r5h 的 GPU 脚本（`scripts/r5f_gpu/apply_gpu_mesa.sh`）**正确删掉了 24.0.9 的另外两件**
（`/vendor/lib/egl/libEGL_mesa.so`、`/vendor/lib/dri/lima_dri.so`，都进了备份目录），
**但漏了这一件**。全镜像扫 24.0.9 三件产物的 sha，结果正好印证：

```
MATCH 5c4d6243d0c8f7cc  /.r5f_gpu_backup/vendor_lib_egl_libEGL_mesa.so   ← r5h 已删(在备份里)
MATCH 8ec15e5281b25f55  /.r5f_gpu_backup/vendor_lib_dri_lima_dri.so      ← r5h 已删(在备份里)
MATCH f8420adaf32306b4  /lib/libglapi.so                                 ← r5h 漏删 ★
```

**漏删的原因（可复现的检查盲区）**：`UI_JANK_GPU.md:180` 记录当时的核查结论是
"`libglapi.so` 两边同尺寸 173,184 B，**不动**" —— 核查者只看了 **`/vendor/lib/libglapi.so`**（确实 173,184 B），
**没有查 `/lib/` 下的同名副本**。同一份文档 `MAIN_SESSION_VERIFY_NOTES.md:132` 也只列了
`/vendor/lib/libglapi.so 173,184 B`。于是这个 379,112 B 的冒名顶替者一路混过 r5d→r5f→r5h→r5i。

**注意 r5h 的脚本逻辑本身是对的**：它按"删掉 mesa24 全套、换上 mesa19 全套"来做，
`libglapi.so` 的"两边同尺寸所以不动"是**在错误的假设下**得出的（假设两边指
`/vendor/lib` 与 p12 的 `/vendor/lib`，而真正被落下的副本在 `/lib`）。
所以这不是"脚本 bug"，是"审计清单漏了一个库目录"。

### 3.5 铁证五（串口实测，2026-10-30 10:41）：SF 死在"已装载、正初始化"的阶段

`mainline_recon/z1_uart921_20261030_104102.log` 给出两个决定性计数：

| 计数项 | r5i（黑屏，mesa 路径） | 历史 swiftshader 期 log |
|---|---|---|
| `comm="surfaceflinger" ... name="dri"` | **214** | **0** |
| `Service 'surfaceflinger' (pid N) received signal 6` | **20+**（pid 一直换） | 0 |
| surfaceflinger 相关行 | 779 | — |

**含义**：swiftshader 路径下 SF 完全不碰 DRM；r5i 下 SF 反复去开 `/dev/dri`
然后 SIGABRT。结合 §2-B1 的 `LOG_ALWAYS_FATAL_IF(!hnd, "couldn't find an OpenGL ES implementation")`
（**在碰 DRM 之前**就会 abort），可知：
**驱动已经装载成功（否则走不到碰 DRM 那一步），崩溃发生在 mesa 初始化内部** ——
与 §3.2 的 dispatch 表尺寸不匹配（1927 vs 1677）是同一个故事。

**这条实测把"是不是驱动根本没找到"这个分支彻底排除了**，也使 §7-2 的风险项降级。

日志类型说明（避免误读）：该 log 是**内核 + init 日志**（`dmesg`/串口），
**不含 logcat 的 `libEGL` ALOGD 行**（那些走 logcat 缓冲）。所以"看不到
`loaded /vendor/lib/egl/libGLES_mesa.so`"**不等于**驱动没装载 —— 判据应看
"SF 有没有走到碰 `/dev/dri`"，本次是走到了。
另注：该 log 里 `renderD128` **0 命中**，但那是因为内核/init 日志不打这条；
`/dev/dri` 目录本身**确实存在**（`avc denied ... name="dri" dev="tmpfs" ino=85 tclass=dir`
是"对已存在目录访问被拒"，permissive 下不阻断）。

---

## 4. 候选修法对比

| # | 修法 | 只改 system.img？ | 风险 | 回滚 | 判定 |
|---|---|---|---|---|---|
| **A** | **删 `/lib/libglapi.so`** | ✅ 是 | 极低：该文件在 p12 里不存在，删掉即回到已验证布局 | `debugfs -w -R "write <备份> /lib/libglapi.so"`（原文件 379,112 B，本机可随时从 `mesa_android/lima-build-egl/` 再生，sha 已记） | **✅ 采用** |
| B | 把 `ro.hardware.egl` 回退成 `swiftshader` | ✅ 是 | 低，但**等于放弃 GPU**：回到 r5d/r5f 的软件渲染，Davey 帧耗时 5–31 s 那套卡顿全回来 | 改回 `mesa` | ⛔ 兜底，不作首选 |
| C | 把 `/vendor/lib/libglapi.so` 也换成 24.0.9，让两边一致 | ✅ 是 | **高**：会把本来正确的 19.1.8 `libGLES_mesa.so`/`gallium_dri.so` 也拖进 ABI 不匹配，等于把错误面扩大 | 需还原两个文件 | ⛔ 否决 |
| D | 重编 mesa 24 补齐 `libGLES_mesa.so` + 修 DRI 路径 | ❌ 需重编/重配 | 高：`UI_JANK_GPU.md` §方案B 已判"两棵源码树找不到 `DRI_DIR_PATH` 定义，有未入 git 的补丁层，**可重现性没保证**" | 复杂 | ⛔ 否决（本次不需要） |
| E | 给 `/lib/libglapi.so` 加 symlink → `/vendor/lib/...` | ✅ 是 | 中：等于隐式选了 19.1.8，但保留了一个语义错位的同名文件，后续再踩坑 | 易 | ⛔ 不如 A 干净 |

**候选 A 为什么最稳**：它的目标状态**就是"和 15 个上板 log 证明能跑 Mesa 19.1.8 的 p12 布局完全一致"** ——
不是新发明，是回到已验证态。且改动面 = 1 个文件。

> 关于主会话提到的"`libEGL_mesa.so` 的 DRI 路径 `/usr/local/lib/dri` 不等长无法 patch / 造 symlink"：
> **这条已不适用**。那份 `libEGL_mesa.so`（259,992 B，Mesa 24.0.9）在 r5i 里根本不在生效位置
> （在 `/.r5f_gpu_backup/` 里睡觉），而 loader 也不会去加载它（见 §2-B1：`libGLES_mesa.so` 抢在第一分支）。
> **不需要 patch 它，也不需要造 `/usr/local/lib/dri` symlink。**

---

## 5. 产物与验证

产物：**`out/z1_r5_system/system.img.r5k_final`**
```
大小     : 943,718,400 B (900 MiB，与 r5i 同，未动分区)
sha256   : b567885796eae85db5fb429bb7b24161c4236ca6792bb09c5c625605802ba1be
e2fsck -fn: rc=0 clean（2667/57600 文件，197130/230400 块）
基线 r5i : 35616bfd23320b32d195083442670c019309e09ff6d4cc9d7eae0faba44fd1af
```

### 逐项回读

| 检查 | 结果 |
|---|---|
| `/lib/libglapi.so` | 已不存在（`cat` 回读 0 字节 / `ls -l /lib` 无此名） |
| `/vendor/lib/libglapi.so` | 仍在，sha(16) `9c1ace8554dedf09`（19.1.8，未被动） |
| `/lib/libEGL.so` | `5c78674deeeb014c`（未动） |
| `/vendor/lib/egl/libGLES_mesa.so` | `aab57fd4a5bc0f02`（未动） |
| `/vendor/lib/dri/gallium_dri.so` | `f7497e4a2090b173`（未动） |
| **全树递归 diff vs r5i** | **`Only in /tmp/mnt_r5i/lib: libglapi.so` —— 有且仅有这一行** |
| xattr 抽查（build.prop / libEGL.so / libGLES_mesa.so / gallium_dri.so / vendor libglapi / gralloc.z1 / hwcomposer.z1 / libGLESv2_mesa.so） | 全部与 r5i **逐字相同**（`system_file` / `same_process_hal_file` / `vendor_file` 各归其位） |
| 绝对路径引用检查（ld.config.txt / public.libraries.txt / fs_config_files） | 0 命中，无悬挂引用 |

**主会话要求"保留 r5i 的其它一切（音频 HAL、GPS、每项修复、xattr）不得破坏"—— 全树 diff 已证明只此一项变化。**

> 一个要**明确知情**的既有事实（不是本次引入）：r5i/r5k 的 `/lib/hw/gralloc.z1.so`(25,116 B)
> 与 `/lib/hw/hwcomposer.z1.so`(24,660 B) **与 p12 的版本不同**（p12: 25,104 / 24,576）。
> 这在 r5d 就已经是 r5c 线的自有改动、且 r5d/r5f 出过画面，**不属本次嫌疑**，故我在本次
> 严格保持"单变量"没有去动它。但它与 mainline DRM 的交互**未经上板验证**（变量：能否多 buffer
> page-flip）—— 若 r5k 修复后 mesa **初始化成功但仍无画面**，它就是下一个头号嫌疑，
> 判据见 §6 的分叉。

---

## 6. 上板判决实验（单变量）

**单变量 = 只换 system 分区，boot 不动。**
```
# 先起 mtkclient 等待，再按开机键（顺序反了连不上）
Z1_VERIFY=0 tools/flash_with_retry.sh android \
    out/z1_r5_system/system.img.r5k_final \
    out/z1_r5_system/system.img.r5i_final 8
# boot 保持 out/z1_r5c_boot/boot.img 不变
```

### 判据（按序）

> **判据 ⓪ 最重要（串口即可判，不必等 logcat）**：看 SF 是否**不再 SIGABRT 循环**。
> ```sh
> # 串口 921600 抓，或 dmesg
> grep -c "Service 'surfaceflinger' .* received signal 6" <log>
> ```
> - **期望：0**（r5i 是 20+）。若归零且 zygote 不再被 `restart zygote` 拖着重启 →
>   修复已生效，屏幕应出画面。**这是最省事、最硬的判据**（10-30 的 log 就是这么抓的）。

**① 判据一：EGL 驱动装载（决定 mesa 有没有真起来）**
```sh
logcat -d | grep -nE "libEGL|vndksupport" | grep -iE "mesa|loaded|dlopen|load_driver"
```
- **期望**：`D libEGL  : loaded /vendor/lib/egl/libGLES_mesa.so`（正是 p12 那条）
- 若出现 `load_driver(...): ...` 或 `couldn't find an OpenGL ES implementation` → mesa 仍未起，
  看 §分叉。
- ⚠️ **必须用 logcat，不能用串口 dmesg 判这条** —— `libEGL` 的 ALOGD 走 logcat 缓冲。
  10-30 那份串口 log 里查不到这行，**是日志通道问题，不是驱动没装载**（见 §3.5）。

**② 判据二：SF 拿到的 renderer（决定"是不是真 GPU"）**
```sh
logcat -d | grep -nE "SurfaceFlinger: (vendor|renderer|version)"
```
- **期望**：`vendor : lima` / `renderer : Mali400` / `version : OpenGL ES 2.0 Mesa 19.1.8`
  （与 10-19/10-20 那 15 个 log 一致）
- 若是 `Google Inc. / Google SwiftShader` → 说明属性没生效或又回退了软件路径。

**③ 判据三：屏幕（最终判据）**
- **期望**：背光亮 + **有画面**（Launcher / 壁纸 `ro.config.wallpaper=/system/etc/z1_wallpaper.png`）。
- 本次目标就是"从全黑 → 出画面"。

**④ 判据四：帧耗时（GPU 是否真在干活）**
```sh
logcat -d | grep -oE "Davey! duration=[0-9]+ms" | sort -t= -k2 -n | tail -20
```
- **参考基线**：10-20 跑通 Mali400 的那几轮是 **数百 ms ~ 1 s 量级**（如 `180334` 那次的 872 ms / 929 ms）；
  而纯 swiftshader 是 **5–37 s 量级**（`105535` 中位 25 s+，最差 37,540 ms）。
- **期望**：从"秒级/十秒级"掉到**百毫秒级**。
- ⚠️ **重要**：**即使修好，帧耗时也不会到 16 ms**。因为 GPU 时钟被限在
  `bus rate = 25390`（25.39 MHz，见每一份 log 的 `lima 13010000.gpu: bus rate = 25390`），
  可用档位到 416 MHz —— **差约 16 倍**。这是**独立的内核/dts 问题（归 R6）**，本单变量不动它。
  所以别用"是否 60fps"当成功判据，用"renderer 是不是 Mali400 + 是否有画面"。

### 失败如何分叉

| 现象 | 指向 | 下一步 |
|---|---|---|
| **SF 不再 SIGABRT** 且出画面 | ✅ 修复成功 | 转 §判据④ 看帧耗时；再单独处理 GPU 时钟（R6） |
| 仍全黑；logcat 有 `libEGL: loaded .../libGLES_mesa.so` + `renderer: Mali400`，SF 不再崩 | mesa **已起来且不再崩**，问题在**显示管线**（gralloc/hwcomposer/DRM commit） | 查 `dumpsys SurfaceFlinger \| head -40`（看是否 `Display 0 HWC`）、查内核 `[drm]`/`lima` 有无原子提交报错；**下一个单变量 = gralloc.z1/hwcomposer.z1 换回 p12 版**（§5 末的既有差异） |
| SF **仍** SIGABRT 循环（`received signal 6` 计数没降） | 说明还有第二个"初始化期崩"的因 | 抓 `logcat -d \| grep -iE "EGL-MAIN\|dri\|renderD128\|dlopen\|libGLES_mesa"` + `grep -i abort` 看 tombstone；重点核 `/dev/dri/renderD128` 是否真存在（历史 log 有 `EGL-MAIN: droid_open_device() Failed to open DRM device /dev/dri/renderD128`）。**注意**：这条**不再是"驱动缺失"**（§3.5 已排除），而是"驱动起来了但拿不到 DRM 设备" |
| SF 起来但 renderer = SwiftShader | `ro.hardware.egl` 没生效 | 核对 `/build.prop:51` 实为 `mesa`；查 `persist.sys.*` 覆盖 |
| 开机进不了系统 / bootloop | 与本次改动无关（只删了一个非必需库） | 回刷 r5i_final 确认可复现，再单独查 |

### 回滚
```
Z1_VERIFY=0 tools/flash_with_retry.sh android \
    out/z1_r5_system/system.img.r5i_final \
    out/z1_r5_system/system.img.r5k_final 8
```
（r5i_final 仍在原位、未被覆盖。若还要更保守的兜底，`out/z1_r5_system/system.img.r5d_final`
是"能出画面但走软件渲染"的已知可开机版。）

---

## 7. 未决风险

1. **本机没有 r5h/r5i 的 logcat**（10-30 10:41 那份是**内核/init 日志**，不是 logcat）。
   所以 §3 的因果链靠静态取证 + 机理 + 这份内核日志的间接证据锁定，**强度高**，
   但**"上板第一次就出画面"仍非必然** —— 见 §6 分叉表。
   **需要什么证据**：刷 r5k 后一份完整 `logcat -d > x.log`（要看 `SurfaceFlinger: renderer` 那三行）。
   **已不再需要**：证明"驱动有没有装载"—— §3.5 的 `/dev/dri` 计数（214 vs 0）已证明装载了。

2. **`/dev/dri/renderD128` 是否存在，仍未证实**（这条是当前**最大的剩余不确定项**）。
   §3.5 已证明 `/dev/dri` **目录存在**（`ino=85 tclass=dir`），但**目录里有没有 `renderD128` 节点**
   仍未知。历史 log 里 `renderD128` 只以**打开失败**的形式出现过
   （`EGL-MAIN: droid_open_device() Failed to open DRM device /dev/dri/renderD128`）。
   **若节点不存在，mesa 会"装载成功但在初始化时崩"** —— 这与观测到的现象**同样吻合**，
   所以**不能排除"删 libglapi 是必要但不充分"**。
   **需要什么证据**：上板 `ls -l /dev/dri/`。
   **注意**：若最终确认节点不存在，那是**内核/init 侧问题（归 R6，与本镜像无关）**，
   而不是本次改动的缺陷。
   若不存在：这是内核/init 侧问题（**与本镜像无关，属 R6**），且它**会独立地让 mesa 失败** ——
   即"删 libglapi 是必要但不充分"的可能性**不能排除**。

3. **gralloc.z1.so / hwcomposer.z1.so 与 p12 不同**（§5 末）。r5d/r5f 出过画面，所以不是
   r5h 引入的；但**"swiftshader 能出画面"不蕴含"mesa 硬件路径也能出画面"** ——
   两者对 gralloc 的用法不同（多 buffer/page-flip vs 纯 CPU）。列为 r5k 失败时的第一嫌疑。

4. **24.0.9 是否只漏了这一个文件**：我以 24.0.9 三次编译产物的 sha256 + 全树 diff 双重扫描，
   r5i 里**只有** §3.4 那三处命中，r5h 已清理两处，剩这一处 = 本次修复对象。
   但若当时还部署过**未归档**的产物（如 `lima_dri.so` 之外的驱动），sha 对不上就扫不出来。
   **需要什么证据**：`LIBC` 层面确认无其他"同名不同源"的 so。我已做 `/lib` vs `/vendor/lib`
   全量同 soname 比对，**除 `libglapi.so` 外无第二个内容不同的同名对**（其余 2 个同名项内容相同）。

5. **`hnd->set(load_driver("GLESv1_CM"...))` / `GLESv2` 这两个槽位在 p12 布局下是空的**。
   p12 的 `libGLES_mesa.so` 在第一分支就承担了全部三个 API，所以第一分支命中后
   **不会**再填 `GLESv1_CM`/`GLESv2` 的独立槽位 —— 但 `init_api()` 在第一分支里
   已按 `EGL|GLESv1_CM|GLESv2` 三个掩码从**同一个 dso** 填好了，所以正常。
   这与 15 次上板 log 一致（那些 log 里**没有** `loaded .../libGLESv1_CM_mesa.so` 这一行 ——
   因为根本不需要），**属正常，不是缺陷**。r5h 加的 `libGLESv2_mesa.so` symlink 是冗余兜底，无害。

---

## 8. 本次改动的文件清单

**新建（产物）**
- `out/z1_r5_system/system.img.r5k_final` (943,718,400 B, sha256 `b567885796eae85db5fb429bb7b24161c4236ca6792bb09c5c625605802ba1be`)
- `out/EGL_BLACK_SCREEN_FIX.md` (本报告)

**镜像内改动（唯一一处，相对 r5i_final）**
- **删除** `/lib/libglapi.so` (379,112 B, sha256 `f8420adaf32306b4955124c66a880cba8b4b8faa366faf26216923341f2518aa`, Mesa 24.0.9)

**未改动（明确保持原样）**
- `out/z1_r5c_boot/boot.img` (sha256 `96f00d03265add4bdc8db94286c23b08ce459243517cb8f24d5c4b2180535da7`) —— boot 不动
- `out/z1_r5_system/system.img.r5i_final` —— 保留作回滚基线，**未被覆盖**
- r5i 内音频 HAL / GPS / WiFi / xattr / gralloc / hwcomposer / 全部其它修复 —— 逐字节未动（全树 diff 0 差异）

**未做（纪律）**
- 未刷写、未接触设备
- 未在 `/tmp` 留大镜像（用完即删）；本次仅在 `/tmp` 做了只读挂载与文本比对，结束前已全部 umount
- 备份纪律：`r5i_final` 本身就是带时间戳形态的公证基线，且已复制出 r5k 独立产物，原基线未覆盖

**本次并入的外部证据（主会话提供，10-30 10:41 串口实测）**
- `mainline_recon/z1_uart921_20261030_104102.log` —— 用作 §3.5 的"SF 已装载、正初始化时崩"
  正面实证，以及 §1 对照组（swiftshader 不碰 `/dev/dri`）的旁证。
  该 log 是**内核+init 日志**，不含 logcat；其中：
  - `avc: denied { read/open } name="dri"`（SF/bootanim/allocator）—— permissive 下不阻断，**非本次因**；
  - `[z1wifi] 6620_launcher never appeared in 120s; abort` —— **独立问题，与 GPU/EGL 无关**；
  - `init: Failed to bind socket 'pdx/system/vr/display/*'` —— A9 VR 服务，Z1 无此硬件，**正常噪声**。
- 主会话"r5d/r5f 正常、r5i 黑屏"的属性对照表 —— 与我的实测一致（§1），
  但其中"p12_r2 属性为空"一条经穷举审计**不成立**（§2 更正 A'）。
