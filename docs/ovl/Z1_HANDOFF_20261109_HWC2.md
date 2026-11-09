# Z1 交接文档 · 第二份（2026-11-09 20:40）— 显示已通，正在做满血 OVL

> 第一份：`out/Z1_HANDOFF_20261109.md`（426 行，显示链路维修全过程 + drm_hwcomposer 调研）
> 本文件：**当前真实状态 + 自己写的 HWC2 的实现细节 + 未完成的排查清单**
> 给下一个接手的 agent：读完本文件就能继续，不需要重读全部历史。

---

## 0. 一句话现状

**内核显示链路 100% 通了**（LineageOS 二屏可见、`sys.boot_completed=1`、SurfaceFlinger 只启动 1 次、0 崩溃、
composer 打开 fb0 成功）。**但合成仍是 Client（GPU 画整帧 → memcpy 到 fb0）**，
满血 OVL（Device composition）正在做：**我们自己写的 HWC2 HAL 已经编译链接成功、能被 SurfaceFlinger 加载，
但 `open()` 里打开 `/dev/dri/card1` 失败（ENODEV），下一版加了诊断日志还没上板验证。**

---

## 1. 板上/仓库里现在是什么

| 项 | 值 |
|---|---|
| 最后**刷入并验证**的 boot | `out/z1_v79/boot.img` sha256 `d332b5e962438b8f07efa22ebb785f895516454fb3cc8548990d11c75bf0fc84`（5,808,128 B） |
| **已打包未刷**的 boot | `out/z1_v82/boot.img` sha256 `f267a1749a92c5ea028701ff2a2c03c88ce6ca8d91537a022835e1c8937b8df9`（5,810,176 B） |
| 可回滚的可用 boot | `out/z1_v78/boot.img`（这一版**显示完全正常**，用的是原厂 composer） |
| system 分区 | `out/z1_v35_selftest/system_v35.img`（900 MB，**没动过**） |
| 内核源树 | `/home/lxj/claude/6572/fork_linux`（未提交改动见 §3） |
| 串口 | CH340 `/dev/ttyUSB0` @921600；抓取脚本 `/tmp/z1cap.sh <log>`（自动重连） |
| 当前日志 | `mainline_recon/z1_boot_20261109_1838.log` |

---

## 2. 已验证 / 未验证

### ✅ 已验证（板上实测）
- 内核显示全链路：`OVL0 → RDMA0 → DSI0`，路由 `0x050=1 / 0x058=1 / 0x04c=0` 真正写入
- `vblank wait timed out` = 0；时钟门 `CG_CON0=0xffdf7e00`、`CG_CON1=0xffffffec` 全开
- DSI 保持 LK 状态不动（before/after 寄存器逐字节相同）
- composer 能打开 fb0（`hwc_open` 失败 = 0），SF 只启动 1 次、0 崩溃，二屏可见，`boot_completed=1`
- **KMS 门禁 PASS**（`z1drmtest`）：
  ```
  ---- /dev/dri/card0 fd=4 driver=lima ----        DRM_CLIENT_CAP_ATOMIC -> -1（render-only，正常）
  ---- /dev/dri/card1 fd=4 driver=mediatek ----    UNIVERSAL_PLANES -> 0, ATOMIC -> 0
  resources: crtcs=1 encoders=1 connectors=1
  crtc[0] id=59 ; connector[0] id=34 type=DSI status=connected modes=1 240x320@60
  planes: count=4   id=35/41/47/53 possible_crtcs=0x1
  formats: XR24 AR24 BX24 BA24 AB24 XB24 RG24 BG24 RG16 UYVY YUYV
  plane[3] type=Cursor zpos=3 rotation=1 alpha=65535 blend=Pre-multiplied
  RESULT: PASS (a KMS node supports atomic)
  ```
- 我们自己的 `hwcomposer.z1.so` **能被加载**：v79 日志
  `E ComposerHal: failed to open hwcomposer device: No such device`
  （= 我们的 `z1_open()` 返回了 `-ENODEV`，说明 `hw_get_module` 找到了 .so、dlopen 成功、`open()` 被调用）

### ❌ 未验证 / 当前失败
- **我们的 HWC `z1_open()` 里 `drm_open()` 失败**（ENODEV）。v82 已加详细 kmsg 诊断 + 30 秒等待节点，**尚未上板**
- 满血 OVL（`dumpsys SurfaceFlinger` 显示 `Device` 合成）完全未验证
- WiFi / 蓝牙 / 音频 / 亮度 / 三大金刚键：之前修过，这一轮**没验收**
- GPU 是不是真 lima、合成类型：没有 shell/adb，无法 `dumpsys`

### ⚠️ 已知问题
1. **开机动画退出后黑屏**（老 composer 的固有限制）：`bootanim` 210 秒正常退出、桌面 `com.android.launcher3` 是前台，
   但屏幕黑，日志反复出现 `E SurfaceFlinger: Failed to remove layer from mapping - could not find matching layer`。
   v73（老 composer）也是这样 —— **不是我们改坏的**，换成真 HWC 才有望解决。
2. 路线 B 下 `atomic_disable` 停 RDMA 但不停 DSI → 息屏后画面冻结，再亮屏不恢复。
3. 时钟/regulator 引用计数泄漏（`mtk_dsi_poweron` 早退不关时钟）。
4. `DISP_RDMA_MEM_START_ADDR` 宏是 MT8183 的 `0x0f00`，MT6572 应为 `0x28`（当前不可达，做 RDMA 抓帧前必须改）。

---

## 3. 内核改动（相对 HEAD，5 个文件，无调试代码）

```
 .config                                        |  2 +-   DRM_FBDEV_OVERALLOC=200
 drivers/gpu/drm/mediatek/mtk_disp_rdma.c       | 11 +-   size_con0=0xfff / size_con1=0xfffff
 drivers/gpu/drm/mediatek/mtk_drm_drv.c         | 20 ++- ① 路径去 COLOR0 ② mode_config.preferred_depth=16
 drivers/gpu/drm/mediatek/mtk_dsi.c             |175 ++-- 路线 B（z1_keep_lk_dsi）+ DSI IRQ RACK 忙等上限
 drivers/gpu/drm/panel/panel-sitronix-st7785m.c | 26 +++ 路线 B（z1_keep_lk_panel）
```

**两个模块参数（保留，不要删）**：
`mtk_dsi.keep_lk_dsi`（默认 true）、`panel.keep_lk_panel`（默认 true）。

**`preferred_depth = 16` 绝对不能删** —— 它让 DRM fbdev 变成 RGB565，原厂 gralloc 才肯打开 fb0；
删了 composer 立刻回到"每 5 秒退出"的状态。

编译：`cd fork_linux && make ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- zImage -j$(nproc)`

---

## 4. 满血 OVL：我们自己写的 HWC2

### 4.1 为什么不用 AOSP 的 drm_hwcomposer

拉了 `android-9.0.0_r1` 的 `external/drm_hwcomposer`（75 KB，23 个 .cpp / 27 个 .h）到 `/tmp/drmhwc/`。
补依赖时卡在硬伤：`ui/GraphicBuffer.h` 需要 `ANativeWindowBuffer`，
而该 tag 的 `system/core/include/system/` **没有** `window.h`，
从 `libs/nativewindow/include` 拿到的却是 **Android 10+ 版本**（`vndk/window.h` 引用未定义的 `ANativeWindowBuffer`，自己也编不过）。
`GraphicBuffer` 公开继承该结构体，手写 ABI 错一个字段就是崩溃 ⇒ **放弃**。

### 4.2 我们自己的实现

**源码：`z1a9_gpt/scripts/z1hwc2/z1hwc2.cpp`（约 1160 行）**

设计：
- 只依赖 `<hardware/hwcomposer2.h>`（已复制到 `z1hwc2/include/hardware/`，另加 `hwcomposer_defs.h`、`hardware.h`）+ libdrm + gralloc 句柄
- 打开 `/dev/dri/card1`（可用属性 `hwc.drm.device` 覆盖；**注意 card0 是 lima，不能用**）
- 4 个 OVL plane：`plane[0]` 留给 client target（最底层），`plane[1..3]` 放 DEVICE 图层
- `validateDisplay`：不能进硬件层的（缩放/旋转/超尺寸/超数量/格式不支持）一律标 `HWC2_COMPOSITION_CLIENT`
- `presentDisplay`：`drmModeAtomicCommit(NONBLOCK)`，首帧带 `ALLOW_MODESET` + connector CRTC_ID + crtc MODE(blob) + ACTIVE=1

构建（`build.sh` 未写，命令如下）：

```bash
NDK=/home/lxj/claude/6572/android-ndk-r26d
CXX=$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/armv7a-linux-androideabi28-clang++
cd /home/lxj/claude/6572/z1a9_gpt/scripts/z1hwc2
$CXX -c -O2 -fPIC -std=gnu++17 -Iinclude \
     -I/home/lxj/claude/6572/z1a9_gpt/scripts/hal_stub \
     -I/home/lxj/claude/6572/mesa_android/libdrm-install/include \
     -I/home/lxj/claude/6572/mesa_android/libdrm-install/include/libdrm \
     -o /tmp/z1hwc2.o z1hwc2.cpp
$CXX -shared -o /tmp/z1hwcout/hwcomposer.z1.so /tmp/z1hwc2.o \
     /home/lxj/claude/6572/mesa_android/libdrm-static-build/libdrm.a \
     -L/tmp/gralloclibs -llog -lcutils -lm -Wl,-soname,hwcomposer.z1.so
# /tmp/gralloclibs 里是从 system 镜像提的 libc++.so / libcutils.so / liblog.so
# 缺了就重新提：
#   debugfs -R "dump /lib/libc++.so /tmp/gralloclibs/libc++.so" out/z1_v35_selftest/system_v35.img
```

验证导出符号：`arm-linux-gnueabihf-nm -D hwcomposer.z1.so | grep HMI` 必须看到 `D HMI`。

### 4.3 部署方式（**每轮只需刷 boot.img，1 分钟**）

1. ramdisk 的 `fstab.z1` 把 `/system` 从 `ro` 改成 **`rw`**
2. ramdisk 根目录放 `hwcomposer.z1.so`（0644）
3. `init.z1.rc` 的 `on post-fs-data` 里：
   ```
   exec u:r:init:s0 root root -- /system/bin/cp /hwcomposer.z1.so /vendor/lib/hw/hwcomposer.z1.so
   exec u:r:init:s0 root root -- /system/bin/chmod 0644 /vendor/lib/hw/hwcomposer.z1.so
   ```
4. **不需要改 build.prop**：内核 cmdline 有 `androidboot.hardware=z1` ⇒ `ro.hardware=z1`
   ⇒ `hw_get_module("hwcomposer")` 直接找 `hwcomposer.z1.so`

打包：

```bash
python3 z1a9_gpt/scripts/z1_ramdisk_edit.py /tmp/rootfs_v79.bin /tmp/rootfs_vNEXT.bin \
    --replace hwcomposer.z1.so=/tmp/z1hwcout/hwcomposer.z1.so
python3 tools/z1_pack_mtk_bootimg.py --template out/z1_v79/boot.img \
    --zimage fork_linux/arch/arm/boot/zImage \
    --dtb fork_linux/arch/arm/boot/dts/mediatek/mt6572-z1.dtb \
    --rootfs /tmp/rootfs_vNEXT.bin --name z1_vNNhwc -o out/z1_vNN/boot.img
```

**回滚**：刷回 `out/z1_v78/boot.img` 即可（内核和 system 都没被改）。

### 4.4 `z1drmtest`（DRM 探测工具，很有用，保留）

`z1a9_gpt/scripts/z1drmtest/z1drmtest.c` + `build.sh`（NDK + 静态 libdrm，动态依赖只有 libc/libdl）。
由 `init.z1.rc` 的 `on post-fs-data` 执行，结果写 `/dev/kmsg`，前缀 `z1drm:`。
它会**轮询等待非 lima 的 DRM 节点**（mediatek-drm 到 ~33 秒才注册）再探测 —— 这个等待逻辑很关键。

---

## 5. 当前正在查的问题（下一个 agent 从这里接手）

### 5.1 现象
v79 上板：我们的 .so 被加载（拷贝/chmod/dlopen 全部成功），但
```
E ComposerHal: failed to open hwcomposer device: No such device
init: Service 'vendor.hwcomposer-2-1' (pid ...) exited with status 1   ← 每 5 秒
```
`No such device` = `-ENODEV`，是我们 `z1_open()` 里 `drm_open()` 失败时的返回值。

### 5.2 v82 已加的诊断（**尚未上板**）
- `klog()` 直接写 `/dev/kmsg`（不依赖 logcat 转发），报每一步：
  `z1hwc2: z1_open(hwc)` / `z1hwc2: opened /dev/dri/card1 fd=N` /
  `z1hwc2: %s has no atomic modesetting` / `z1hwc2: drmModeGetResources failed: ...` /
  `z1hwc2: resources crtcs=.. connectors=..` / `z1hwc2: no connected connector / crtc` /
  `z1hwc2: missing crtc/connector properties (...)` / `z1hwc2: plane N missing properties (...)`
- `open()` 遇 `ENOENT` 每 0.5 s 重试，最多 30 s（composer 24 s 起，card1 33 s 才出现）

### 5.3 已知可能原因（按可能性排序，**未证实**）
1. **时序**：composer 23.99 s 启动时 `card1` 还不存在（v82 的重试应该解决）
2. **权限**：`ueventd.rc` 有 `/dev/dri/* 0666 root graphics`，理论上没问题；但 service 是 `user system`
3. **`drmSetClientCap(ATOMIC)`** 在某个时刻失败
4. **connector 状态**：`drmModeGetConnector` 在非 master 下能否拿到 `connected`
5. **`drmModeGetResources` 失败**（EACCES / ENODEV）

### 5.4 被中断的独立审查 —— **这些问题还没人回答，请接手的人补**
我派了一个 agent 独立审查这份 HWC 实现，中途被叫停，**没有任何结论产出**。它原本要回答：

**A. HWC2 契约**
1. `getCapabilities` 声明 `SKIP_VALIDATE` / `SKIP_CLIENT_COLOR_TRANSFORM` 是否会被 SF 依赖到未实现的语义？
2. `validateDisplay` → `getChangedCompositionTypes` → `acceptDisplayChanges` → `presentDisplay` 的顺序与返回值是否正确？
   `acceptDisplayChanges` 里什么都不做有没有问题？
3. `presentDisplay` 返回 `retire_fence = -1` 是否合法？会不会让 SF 不释放 buffer？
4. `getReleaseFences` 每次返回 `-1` 是否合法？会不会让 SF 的 BufferQueue 卡住？
5. `setClientTarget` 的 acquire_fence 被直接 `close()` 没等 —— 后果？
6. 哪些 `SET_LAYER_*` 我们返回了 `HWC2_ERROR_NONE` 但**实际必须生效**（尤其 z-order）？
7. `HWC2_FUNCTION_GET_LAYER_*` 全部返回 nullptr，SF 会不会调用？调用会怎样？

**B. DRM atomic**
8. `drmModeAtomicAddProperty` 返回值**全都没检查** —— 失败时 commit 会怎样？
9. 首次提交带 `ALLOW_MODESET`、之后不带；关闭显示只跳过提交 —— 对不对？
10. `SRC_*` 是 16.16 定点，取自浮点 crop；crop 为空时（已加回退）还会不会有问题？
11. `program_plane` 对 CRTC_W/H 做了 clamp 但没同比调整 SRC_* → 会不会触发内核 `-ERANGE`（禁止缩放）？
12. plane 的 `zpos` 我们**只读不写**（内核 immutable）；收集顺序 vs zpos 顺序是否一致？（v82 已加按 zpos 排序）

**C. 生命周期**
13. `destroyLayer` / `setLayerBuffer` 换 buffer 时释放 fb —— 有没有重复释放/泄漏？
14. `client_fb` 用 handle 指针做缓存键；SF 若双缓冲轮换 → 每帧重建 fb（v82 已改成按 handle 变化重建）
15. `z1_close` 有没有漏关 fd / 漏 RmFB？

**D. 其它**
16. 没有 vsync 上报（SF 日志 `Timed out waiting for hw vsync; faking it`）会不会影响 present？
17. `set_power_mode(OFF)` 后 SF 仍可能 present，我们直接返回 NONE —— 副作用？
18. `drm_open` 里有最长 30 秒的重试，会阻塞 SF 主线程 —— 要不要改成 fork/异步？

---

## 6. 下一步建议（按顺序）

1. **刷 v82 → 看 `z1hwc2:` 日志**，确认 `drm_open` 到底哪一步失败（这是唯一的阻塞点）
2. 修掉后确认 `z1hwc2: first commit done: 240x320` + **屏幕有画面**
3. 有画面后再谈多层 DEVICE 合成：把 `validateDisplay` 的日志加一行（每个 layer 的 z/尺寸/格式/结论），
   对照 `dumpsys SurfaceFlinger`（需要 shell —— 目前没有，可考虑把 `dumpsys` 结果 dump 到 `/data/local/tmp/` 再用串口读）
4. 补 §5.4 的 18 个审查问题
5. 回归验收：WiFi / 蓝牙 / 音频 / 亮度 / 三大金刚键 / ART 首启

---

## 7. 常用命令

```bash
# 抓串口（自动重连，USB 重新枚举不会丢）
/tmp/z1cap.sh /home/lxj/claude/6572/mainline_recon/z1_boot_$(date +%Y%m%d_%H%M).log

# 刷 boot（先起脚本再插 USB；脚本模板 /tmp/z1flash82single.sh，改 BOOT=）
/tmp/z1flash82single.sh

# 打包 boot.img（模板用当前板上那版，rootfs 用 /tmp/rootfs_v79.bin 起）
python3 tools/z1_pack_mtk_bootimg.py --template out/z1_v79/boot.img \
  --zimage fork_linux/arch/arm/boot/zImage \
  --dtb fork_linux/arch/arm/boot/dts/mediatek/mt6572-z1.dtb \
  --rootfs /tmp/rootfs_vNEXT.bin --name <≤16字符> -o out/z1_vNN/boot.img
```

**刷写铁律**：mtkclient 必须先起再插 USB；5.8 MB 正常 1~2 秒（5~6 MB/s）；
**45~60 秒连进度条都不出 = preloader 僵死，长按电源 10 秒断电重来**；必须 `读回逐字节一致 ✅` 才允许开机。

---

## 8. 关键坐标（省时间）

| 东西 | 位置 |
|---|---|
| HWC2 源码 | `z1a9_gpt/scripts/z1hwc2/z1hwc2.cpp`（+ `include/hardware/`） |
| HWC2 头（Pie 原名） | `z1a9_gpt/scripts/z1hwc2/include/hardware/hwcomposer2.h`（98 KB） |
| DRM 探测工具 | `z1a9_gpt/scripts/z1drmtest/{z1drmtest.c,build.sh}` |
| AOSP drm_hwcomposer 源码（未用） | `/tmp/drmhwc/`（含已抓的 ui/libutils/libhardware/sync 头） |
| gralloc 源码（可直接用，含 lima 分配器） | `z1a9_gpt/graphics/gralloc/` |
| 静态 libdrm（arm32） | `mesa_android/libdrm-static-build/libdrm.a` |
| 显示链路维修全过程 | `out/Z1_HANDOFF_20261109.md` + `out/PLAN_DISP_ALIGN_20261108.md` |
