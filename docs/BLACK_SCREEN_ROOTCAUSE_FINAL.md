# 黑屏根因（已定案，2026-10-30）—— 不是电压问题

## 一句话

**SurfaceFlinger 在 `HWC2::Composer::execute()` → `MessageQueue::initMemory()` 处
`FmqInternal.cpp:25] Check failed: exp` 断言失败 → SIGABRT → SF 崩 → zygote 重启 → 黑屏。
与电压/供电无关，与 EGL/mesa 无关。**

## 决定性证据（本轮新增的 logcat 通道拿到）

本轮改了 ramdisk，把 Android logcat 经 `/dev/kmsg` 送到 CH340 串口
（本机 ACM gadget 从未枚举成功，串口是唯一可用通道）。于是拿到完整 tombstone：

```
F libc    : Fatal signal 6 (SIGABRT), code -6 (SI_TKILL) in tid 219 (surfaceflinger)
F DEBUG   : pid: 219, name: surfaceflinger  >>> /system/bin/surfaceflinger <<<
F DEBUG   : Abort message: 'FmqInternal.cpp:25] Check failed: exp '
F DEBUG   :     #04 libsurfaceflinger.so (android::hardware::MessageQueue<unsigned int,
                     MQFlavor::SYNCHRONIZED>::initMemory(bool)+52)
F DEBUG   :     #05 libsurfaceflinger.so (android::Hwc2::impl::Composer::execute()+714)
F DEBUG   :     #06 libsurfaceflinger.so (android::Hwc2::impl::Composer::presentOrValidateDisplay(...)+136)
F DEBUG   :     #07 libsurfaceflinger.so (HWC2::Display::presentOrValidate(...)+60)
F DEBUG   :     #08 libsurfaceflinger.so (android::HWComposer::prepare(android::DisplayDevice&)+242)
F DEBUG   :     #09 libsurfaceflinger.so (android::DisplayDevice::prepareFrame(android::HWComposer&)+10)
F DEBUG   :     #10 libsurfaceflinger.so (android::SurfaceFlinger::handleMessageRefresh()+1302)
E gralloc : invalid gralloc handle (at 0x0)
I ServiceManager: service 'SurfaceFlinger' died
D BootAnimation: SurfaceFlinger died, exiting...
```

**FMQ**（Fast Message Queue）= A8/A9 里 SurfaceFlinger ↔ HWComposer HAL 之间传显示参数的
无锁共享内存队列。`initMemory` 建不起来 → 断言失败 → SF abort。

## 回答"是不是电压不够"

**不是。** 证据：
1. **心跳一直正常**：`[z1hb] beat=31/32/33... uptime=495/511/526s free=778644kB tasks=5/191`
   —— 系统连续跑了几百秒没掉电。若是欠压，会看到 brown-out / PMIC 复位 / 随机挂死，
   而不是"精确地在 SF 建 FMQ 这一步崩"。
2. **崩溃点是确定性的**：每次都在同一函数、同一断言、同一 PC 偏移 —— 这是**软件 bug**的特征，
   不是电源问题的特征（电源问题表现为随机性）。
3. 那些 `[pmic6323_init] Preloader Start` / `Jump to BL` 是**用户手动断电重启**时的
   preloader banner（每次冷启动都有），不是设备自己复位。
4. "无限重启"的性质是 **init 层的 SF→zygote 重启循环**（`restart zygote ... succeeded` 反复出现），
   **不是硬复位**（没有 `Kernel panic` / `Oops` / WDT 咬死记录）。

## 为什么之前一直在 EGL 里打转（我的方向错误，已纠正）

崩溃发生在 **HWC2 路径**，而正常（10-20 Mali400 成功那次）的 log 里
**`Fmq`/`MessageQueue` 命中数 = 0**，且它成功打印了完整 `EGL information: vendor lima /
renderer Mali400`。当前黑屏 log 里 **EGL 也加载成功了**
（`libEGL: loaded /vendor/lib/egl/libGLES_mesa.so`）—— 所以 EGL 是好的，不是根因。
我把"图形栈相同却结果不同"误当成 EGL 问题，实际差异在 **HWC2 的 FMQ 初始化**。

## 待定的精确原因（两个候选，需进一步验证）

`Check failed: exp` 里的 `exp` 是 libbase 的 `CHECK()` 在 `MessageQueueBase::initMemory()`
里对**期望值**的断言。在 A9 的 libfmq 里，该断言通常校验：
1. **`/dev/ashmem` 或 memfd 的分配/映射是否成功** —— 主会话实测 `libfmq.so` 只调 `mmap`，
   而 mainline 内核**已移除 ashmem**（`CONFIG_ASHMEM` 在树里不存在）。
   若 FMQ 走的路径需要 ashmem 而内核没有 → 分配失败 → 断言炸。
   **候选修法**：确认 A9 libfmq 用 `memfd_create` 还是 ashmem；前者需要内核有 memfd（一般有）。
2. **HWC2 HAL 侧的 FMQ 描述符有效性** —— `E gralloc : invalid gralloc handle (at 0x0)`
   出现在同一时刻，暗示 HAL 侧给了个空描述符。

## 下一步（明确）

1. **反汇编 `libfmq.so` 的 `initMemory`**，看 `CHECK(exp)` 到底断言什么（离线可做）。
2. 反汇编 `libsurfaceflinger.so` 的 `Composer::execute()+714`，看它拿到的描述符从哪来。
3. 对照 10-20 正常那次的 HWC 路径（它没走 FMQ，说明有配置/属性能让 SF 不用 FMQ）。
   查 A9 的 `debug.hwc.force_gpu` / `ro.surface_flinger.*` 相关属性。

## 本轮产出的调试能力（可复用）

新增 2 个 ramdisk 脚本（已刷入 `out/z1_dbg_boot/boot.img`）：
- `z1_klog_relay.sh`：把 logcat 经 `/dev/kmsg` 送串口（过滤 FATAL/abort/EGL/SF/tombstone）
- `z1_sf_dump.sh`：同样内容落盘 `/data/local/z1diag/sf.txt`，带回读验证

**从此 Android 层日志可从 CH340 串口直接看到** —— 这是本轮最有价值的产出，
后续所有用户态问题都能这样定位。
