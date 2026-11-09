# Z1 HWC2 HAL 构建 / 验证工具链（可复现）

作用域：**只**构建并验证自定义 HWC2 硬件合成器 HAL（MT6572 / armv7 / Android 9 / LineageOS 16）。
不改源码、不刷机、不需要网络；源码树只读，产物全部落在 outdir。

- HAL 源码：`/home/lxj/claude/6572/z1a9_gpt/scripts/z1hwc2/z1hwc2.cpp`（1196 行 / 34003 B）
- 构建脚本：`/home/lxj/claude/6572/z1a9_gpt/scripts/z1hwc2/build.sh`
- 设备库提取：`/home/lxj/claude/6572/z1a9_gpt/scripts/z1_extract_devlibs.sh`
- 本文档：`/home/lxj/claude/6572/out/Z1_HWC2_BUILD_HARNESS.md`

---

## 0. 一分钟复现

```bash
cd /home/lxj/claude/6572/z1a9_gpt/scripts/z1hwc2
./build.sh /tmp/z1hwc2_build                       # 编译 + 链接 + 5 项检查 + manifest
sha256sum /tmp/z1hwc2_build/hwcomposer.z1.so       # 期望 e51ce5368da0b48e...
./build.sh --verify-only /tmp/z1hwc2_build/hwcomposer.z1.so   # 期望 exit 0
```

若 `/tmp/z1devlibs` 丢了，先跑（这一步是唯一需要 sudo 的地方，密码 `1`）：

```bash
/home/lxj/claude/6572/z1a9_gpt/scripts/z1_extract_devlibs.sh /tmp/z1devlibs
```

---

## 1. 交付物

| 文件 | 作用 |
|---|---|
| `z1a9_gpt/scripts/z1hwc2/build.sh` | 构建 + 5 项后置检查 + `manifest.txt`；`--verify-only <file>` 只做检查；可 `source` 复用 `verify_so` |
| `z1a9_gpt/scripts/z1_extract_devlibs.sh` | 从系统镜像（`loop,ro`）重建 device-lib 目录，逐个 `file` + size + sha256 校验，幂等 |
| `out/Z1_HWC2_BUILD_HARNESS.md` | 本文档 |

---

## 2. 依赖（全部实测存在）

| 组件 | 路径 | 说明 |
|---|---|---|
| NDK | `/home/lxj/claude/6572/android-ndk-r26d` | clang **17.0.2**，sysroot API 28 |
| 编译器 | `<NDK>/toolchains/llvm/prebuilt/linux-x86_64/bin/armv7a-linux-androideabi28-clang++` | `Target: armv7a-unknown-linux-android28` |
| HAL 头文件 | `z1hwc2/include/hardware/{hardware,hwcomposer2,hwcomposer_defs}.h` | 随源码树提供（AOSP A9 头） |
| 设备专用头桩 | `z1a9_gpt/scripts/hal_stub/{hardware,cutils,log,system}` | 只提供声明，不参与链接 |
| 静态 libdrm | `mesa_android/libdrm-static-build/libdrm.a`（98640 B） | sha256 `f729b067…c36eb` |
| libdrm 头 | `mesa_android/libdrm-install/include` + `include/libdrm` | `xf86drm.h` / `drm_fourcc.h` |
| 设备运行库 | `/tmp/z1devlibs`（11 个 `.so`） | 从系统镜像 `/lib` 提取，见 §3.1 |
| 系统镜像 | `out/z1_v35_selftest/system_v35.img`（943718400 B，ext4） | 只读挂载到 `/mnt/z1sys` |
| 宿主工具 | `file`、`readelf`+`nm`（或 NDK `llvm-readelf`/`llvm-nm`）、`sha256sum`、`stat`、`findmnt`/`mountpoint` | `--verify-only` 不需要 NDK（已实测） |

必须存在的 device libs（链接期强制）：`libc++.so` `liblog.so` `libcutils.so`；
`z1_extract_devlibs.sh` 复制完整 11 个：`libc++ libcutils liblog libutils libsync libhardware libui libgui libEGL libGLESv2 libbinder`。

---

## 3. 精确命令

### 3.1 准备 device libs（二选一）

```bash
# A) 系统镜像已经挂在 /mnt/z1sys（当前状态），直接复制，不需要 sudo
/home/lxj/claude/6572/z1a9_gpt/scripts/z1_extract_devlibs.sh /tmp/z1devlibs

# B) 冷启动：脚本自己 loop,ro 挂载、复制、退出时卸载（不碰已有的 /mnt/z1sys）
IMG=/home/lxj/claude/6572/out/z1_v35_selftest/system_v35.img \
MNT=/tmp/z1mnt \
/home/lxj/claude/6572/z1a9_gpt/scripts/z1_extract_devlibs.sh /tmp/z1devlibs
```

`build.sh` 永不调用 sudo：`DEVICE_LIBS` 缺库时它只会从**已挂载**的 `/mnt/z1sys/lib` 复制，
否则打印带完整命令的错误并退出 1。

### 3.2 构建

```bash
cd /home/lxj/claude/6572/z1a9_gpt/scripts/z1hwc2
./build.sh                  # outdir = <脚本目录>/out（源码树内，见 §7-8）
./build.sh /tmp/z1hwc2_build   # 推荐：不往源码树写东西
```

脚本从自身路径推导仓库根（跟随符号链接、任意 cwd 均可），无任何硬编码 `~`。
编译参数带 `-Wall -Wextra`，**不带** `-Werror`；编译/链接命令行都会打印。

### 3.3 只验证（不构建）

```bash
./build.sh --verify-only /tmp/z1hwc2_build/hwcomposer.z1.so     # 期望 exit 0
./build.sh --verify-only /usr/lib/x86_64-linux-gnu/libz.so.1.3  # 期望 exit 1（x86-64）
./build.sh --verify-only /usr/lib/x86_64-linux-gnu/libz.so      # 期望 exit 1（symlink）
./build.sh --verify-only /tmp/z1devlibs/liblog.so               # 期望 exit 1（arm32 但不是本 HAL）
```

失败时**逐项**打印 PASS/FAIL 与实测依赖集合（含 `missing` / `unexpected` 明细），退出码非 0。
也可以 `source build.sh` 后直接调用 `verify_so <file>`（注意会把 `set -eu` 带进当前 shell，
应写成 `if verify_so …; then`）。

### 3.4 环境变量覆盖

| 变量 | 默认值 |
|---|---|
| `NDK` | `<repo>/android-ndk-r26d` |
| `DEVICE_LIBS` | `/tmp/z1devlibs` |
| `LIBDRM_A` | `<repo>/mesa_android/libdrm-static-build/libdrm.a` |
| `LIBDRM_INC` | `<repo>/mesa_android/libdrm-install/include` |
| `SRCS` | `z1hwc2.cpp`（空格分隔多个；相对路径优先按脚本目录解析） |
| `CXX` | `<NDK>/…/armv7a-linux-androideabi28-clang++` |
| `HAL_STUB` | `<repo>/z1a9_gpt/scripts/hal_stub` |
| `API_LEVEL` | `28`（不要动，见 §7-3） |
| `HOST_TAG` | `linux-x86_64` |
| `OUT_NAME` | `hwcomposer.z1.so`（同时决定 `-Wl,-soname`） |
| `Z1_SYS_IMAGE` / `Z1_SYS_MNT` | `out/z1_v35_selftest/system_v35.img` / `/mnt/z1sys` |
| `READELF` / `NM` | 自动：系统 `readelf` / NDK `llvm-nm` |

`z1_extract_devlibs.sh` 另有：`IMG`、`MNT`、`SUDO_PASS`（默认 `1`）。

### 3.5 实际展开的两条命令（来自 manifest）

```
<NDK>/bin/armv7a-linux-androideabi28-clang++ -c -O2 -fPIC -std=gnu++17 -Wall -Wextra \
  -I <script>/include -I <repo>/z1a9_gpt/scripts/hal_stub \
  -I <repo>/mesa_android/libdrm-install/include \
  -I <repo>/mesa_android/libdrm-install/include/libdrm \
  -o <outdir>/obj/01_z1hwc2.o <script>/z1hwc2.cpp

<NDK>/bin/armv7a-linux-androideabi28-clang++ -shared -o <outdir>/hwcomposer.z1.so \
  <outdir>/obj/01_z1hwc2.o <repo>/mesa_android/libdrm-static-build/libdrm.a \
  -L<DEVICE_LIBS> -llog -lcutils -lm -Wl,-soname,hwcomposer.z1.so
```

> 注意：`libdrm.a` 必须放在 `.o` 之后、`-l*` 之前（静态库单遍解析）。
> 外部记录的旧配方写的是 `-I z1hwc2/include`，那是相对 `scripts/` 的路径；
> 从 `scripts/z1hwc2/` 里编译时正确值是 `-I include`（脚本用绝对路径，不受 cwd 影响）。

---

## 4. 产物与实测证据（2026-11-09，本次运行）

| 项 | 值 |
|---|---|
| 输出 | `/tmp/z1hwc2_build/hwcomposer.z1.so` |
| 大小 | **112084** 字节 |
| sha256 | `e51ce5368da0b48e7b0a8889e6a8e28cc013d29ba1c7a2d8cbd697ae1b075ba4` |
| `file` | `ELF 32-bit LSB shared object, ARM, EABI5 version 1 (SYSV), dynamically linked, not stripped` |
| SONAME | `hwcomposer.z1.so`（`FLAGS: BIND_NOW`，NDK 默认 `-z now`） |
| NEEDED | `liblog.so libcutils.so libm.so libc++.so libdl.so libc.so`（恰好 6 个） |
| 导出符号 | `00018538 D HMI`（`llvm-nm -D \| grep ' D HMI'`） |
| 编译告警 | 1 条：`z1hwc2.cpp:487:66: warning: unused parameter 'display' [-Wunused-parameter]` |
| 确定性 | 7 个不同 outdir 产出 sha256 **完全相同**（`_build`/`_auto`/默认 `out`/`_fromtest`/`_partial`/`_final`/`relout`） |

`manifest.txt`（与 `.so` 同目录）字段：

| 段 | 内容 |
|---|---|
| `[output]` | 路径、size、sha256、`file` 描述、soname、needed、HMI 符号行 |
| `[toolchain]` | NDK 路径、编译器全路径、版本串、target、API level |
| `[link]` | libdrm.a 路径 + sha256、libdrm 头目录、device libs 目录、`-llog -lcutils -lm`、完整链接命令 |
| `[sources]` | 每个源文件 path / **sha256** / bytes |
| `[headers]` | HAL 三个头 + libdrm 三个头 path / sha256，`hal_stub` 目录树 sha256 |
| `[device_libs_sha256]` | liblog / libcutils / libc++ 的 sha256 + 字节数 |
| `[compile]` | 每条完整编译命令 |
| `[verification]` | 5 项检查 `pass` + `result=pass`（仅在全部通过后写出） |

`z1_extract_devlibs.sh` 实测（`/tmp/z1devlibs_test`，第二次运行）：

```
 libc++.so                636332 f39a96786eb89e3b copied / unchanged
 libcutils.so              67476 14628776feaf7556 ...
 liblog.so                 88992 1057e3d261074fb6 ...
 libutils.so               96924 f7591bd4f3d03835 ...
 libsync.so                15948 4b8813c8009c4371 ...
 libhardware.so            15928 e9807f95fbdd805c ...
 libui.so                 122676 b74a74c8f5137481 ...
 libgui.so                478068 e274cd32c7bd66a9 ...
 libEGL.so                132192 5c78674deeeb014c ...
 libGLESv2.so              78476 e6248971750d2fdb ...
 libbinder.so             421956 3bc7e7a7b6fccc6d ...
 result  : 0 copied, 11 unchanged, 0 failed (of 11)      ← 幂等
```

冷启动路径实测：`MNT=/tmp/z1mnt_test` 时脚本自己 `mount -o loop,ro`、复制 11 个、退出时
`unmount`；`/mnt/z1sys` 全程未被卸载（`findmnt` 复核）。

---

## 5. 五项检查分别证明什么

| # | 检查 | 证明什么 / 失败意味着 |
|---|---|---|
| a | 文件非空且 `file` 为 ELF **32-bit ARM** | 产物真的是设备架构；防止把它机库、空文件、symlink 当作 HAL（`libz.so` 是 symlink，会被这条挡下） |
| b | `readelf -d` 的 NEEDED **恰好** 等于 6 个设备库 | 运行期只依赖设备上确实存在的库；多一个就是误链 host 库，少一个说明链接被裁掉了 |
| c | `HMI` 是**已定义的数据符号**（`D/G/B`，本例 `D`） | `hw_get_module()` 靠 `dlsym` 找 `HAL_MODULE_INFO_SYM`（宏展开即 `HMI`）；符号缺失或变成 `U/T` 则 SurfaceFlinger 加载 hwcomposer 模块失败 |
| d | NEEDED 不含 `libc++_shared.so` / `libc++abi.so` | A9 设备上**没有**这两个库（只有 `libc++.so`，且它已内置 libc++abi）；链进它们 → `dlopen` 直接失败 |
| e | 打印 sha256 + size 并写入 manifest | 产物可追溯；同输入同工具链时字节可复现（本次 7 次构建 sha256 全部相同） |

额外 info（不参与判定）：产物因 `BIND_NOW` 会在 **加载时**解析全部符号，实测有 5 个未定义的
C++ ABI 符号 —— `__cxa_allocate_exception`、`__cxa_end_cleanup`、`__cxa_free_exception`、
`__cxa_throw`、`__gxx_personality_v0` —— 逐个确认由设备 `libc++.so` 以 `T` 导出
（另有两个带版本号的 `__cxa_atexit@LIBC` / `__cxa_finalize@LIBC`，由 libc 提供）。
所以“不要 libc++abi 依赖”不是形式主义：缺了这些设备符号，HAL 一加载就挂。

---

## 6. 头文件实际解析到哪（实测 `-E -H`）

| `#include` | 解析到 |
|---|---|
| `hardware/hwcomposer2.h`、`hardware/hardware.h`、`hardware/hwcomposer_defs.h` | `z1hwc2/include/`（AOSP A9 头） |
| `log/log.h` | `z1a9_gpt/scripts/hal_stub/log/log.h`（桩） |
| `sys/system_properties.h` | NDK r26d sysroot（API 28 自带，**不**来自桩目录） |
| `xf86drm.h`、`drm_fourcc.h` | `mesa_android/libdrm-install/include{,/libdrm}` |

---

## 7. 已知坑

1. **libc++ ABI（最致命）**：必须让链接器拿到**设备**的 `libc++.so`，即 `-L$DEVICE_LIBS` 里要有它。
   否则 NDK 会退回到自带 `libc++_shared.so` —— 设备上没有这个文件，`dlopen` 必失败。
   `build.sh` 把 `libc++.so` 列为链接前置条件，并对 NEEDED 做精确断言，就是为了堵这条。
   **不要**把 `libc++_shared.so` 放进 `DEVICE_LIBS`。
2. **HMI 导出**：`HAL_MODULE_INFO_SYM` 在头里被 `#define` 成 `HMI`，必须是默认可见性的 **data** 符号。
   加 `-fvisibility=hidden`、把它改成 `static`、或改名，都会让 `hw_get_module` 找不到模块，
   现象是 SF 加载 hwcomposer 失败（而不是编译报错）。检查命令：
   `llvm-nm -D hwcomposer.z1.so | grep ' D HMI'`。
3. **API level 必须是 28**：设备 bionic 就是 A9（API 28）。用 27 及以下可能缺 28 才有的符号/属性，
   用 29+ 可能引用设备上没有的新符号；链接 ABI 必须与设备同代。这里的 `sys/system_properties.h`
   也来自 API 28 sysroot。
4. **不要加 `-Werror`**：源码现有 1 条 `-Wunused-parameter`（`z1hwc2.cpp:487` 的 `display`）。
   该参数是 HWC2 回调签名的一部分，警告无害；加 `-Werror` 会直接把构建打挂。
   （要清掉它得改源码 —— 不属于本任务范围。）
5. **`llvm-nm` / `llvm-readelf` 不认 `--`**：GNU 工具认，LLVM 的会报 `unknown argument '--'`。
   脚本已对这两个工具去掉 `--`，并 `export LC_ALL=C` 保证 `readelf` 输出可解析（否则中文 locale
   下 `NEEDED` 行是“共享库：[…]”）。手敲命令时同样要注意。
6. **确定性**：同路径、同输入、同工具链下产物字节完全一致（已实测 7 次、7 个不同 outdir 的 sha256 全部相同）。
   同一 outdir 重跑时，`manifest.txt` 只有 `generated_utc` 会变（换 outdir 时路径字段当然也变）；
   比对产物请用 `.so` 的 sha256。
7. **`/tmp/z1devlibs` 是易失目录**：`/tmp` 被清理后重跑 `z1_extract_devlibs.sh` 即可；
   或把 `DEVICE_LIBS` 指到持久目录。`build.sh` 缺库时只会从已挂载镜像复制，**不会** sudo。
8. **默认 outdir 在源码树内**（`z1a9_gpt/scripts/z1hwc2/out/`）。想保持源码树干净就显式传 outdir；
   本工具链除此之外不写源码树。
9. **`OUT_NAME` 与 SONAME 强耦合**：改名必须同时改 `OUT_NAME`，脚本用同一个值传
   `-Wl,-soname`，否则部署时会与预期文件名不一致。
10. **不要用 `-static-libstdc++`/`-nostdlib++` 之类的“省事”参数**：设备 HAL 需要与 SF 共享
    同一个 `libc++.so` 实例，静态链接会把 ABI 语义搞乱。
11. **部署不在本工具范围**：本脚本只产出 `hwcomposer.z1.so` 并验证其链接契约，没有上板/刷机，
    也没有验证 SELinux 标签、`/vendor/lib/hw` 放置与权限 —— 这些要另做。

---

## 8. 边界（明确没做的事）

- 未修改 `z1hwc2.cpp`、其头文件、`hal_stub/`、任何既有源码或配置。
- 未执行任何 `git` 命令、未刷写、未触碰 `/dev/ttyUSB0`、未使用网络。
- 生成物只在 outdir（本次演示用 `/tmp`）与本文件；源码树仅临时创建并已移走的默认 outdir。
- 只验证“**能构建 + 链接契约正确**”，不验证 HAL 在设备上的功能表现（合成是否正确、是否黑屏等）。
