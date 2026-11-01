# 首渲染冻结 根因判决书（夜间裁决员，2026-11-01 深夜）

综合 config 考古员 + 活体探针员两路证据，叠加本 ask 新做的 8 组交叉验证。所有行号可复核。

## 一、本夜新钉死的事实（全部本机亲跑）

1. **冻结点比"SF GL info"更靠后，是"首个 gralloc 缓冲分配"**：
   - #113 冻结轮：SF 完成 init（`Using HWComposer service: 'default'` log:175.4→内核175.8）、屏幕点亮（`Finished set power mode=2` log:106.6）、bootanim 启动（init log:1537 内核30.95s）、bootanim 加载 SwiftShader 三件（log:107.7-110.9，pid 371）、**gralloc/allocator 服务懒启动并打开 fbdev（log:111-114，logcat 36.581，pid 210）→ 全场 logcat 永久静默**（`mainline_recon/z1_boot_mainline_20261101_003004.log`，tail 定格 GL_MAX_VIEWPORT_DIMS）。
   - 第二轮同型（`/tmp/ash/p12.txt`，内核 1193-1198s≈20 分钟，avc 全 permissive=1）。
   - mesa 轮同型（`z1_boot_mainline_20261031_230505.log` tail 定格 ConfigStore 29.93；`/tmp/ash/bp_media.txt` 证 `ro.hardware.egl=mesa`）→ 渲染器无关再次成立。
   - 冻结态 ps（log:1816-1818）：zygote(201)、surfaceflinger(219) 存活；z1_sf_dump 循环与 dumpsys 正常（sf.txt 153KB→459KB 递增，/tmp/ash/p1/p2.txt）→ binder 主链活着。
2. **任务前提"v7d 无 ashmem 也能跑 SwiftShader 到桌面"被证伪**：
   - 10-20 桌面轮（sys.boot_completed=OK×7，`z1_android9_acm_20261020_221937_756497.log:477…`）的内核 `7.0.0-rc7-z1+ #110`（同 log 内核版本行），其 dmesg 有 **`ashmem: initialized`（同 log:151）**——桌面世代内核本来就有 ashmem。
   - 归档 v7d 包**从未刷写**（`out/z1_v7d/manifest.json`: `"flashing_performed": false`，status=HOST_PACKAGE_PASS_NOT_DEVICE_VERIFIED，base=v7c 仅改 ramdisk wifi）；10-27/28"到桌面"轮实刷的是 v7e boot + r3f system（`out/Z1_NEXT_FLASH_V7D.md`）。v7d.config 无 binder/ashmem 只描述 git 提交点，不描述任何上过板的内核。
   - 10-20 轮的 app_process SIGABRT 风暴根因是 payload prop（`Abort message: 'app_process: Unable to determine ABI list from property ro.product.cpu.abilist32.'`，同 log:987/1039/1183…），与内核无关。
3. **ashmem.c 三代同源**：`git show 616164412:drivers/android/ashmem.c`（9-30, origin/wip/android9）≡ `001d40084`（wip/a9-mainline）≡ `e194c9615`（main 回移版），sha256 全等 `74d3c0da48abe80a…`。**ashmem 驱动实现从未变过，"回移版有 bug"与"桌面版是另一份实现"双双出局。**
4. **桌面世代 .config 已提取并对拍**（`out/night_freeze/desktop110.config` ← 616164412:.config，9-30 提交，其后至 #110 构建 10-03 14:53 期间 .config 无变更记录）：desktop110 vs ash_113 **真值翻转 0 项**；desktop 独有仅 `CONFIG_MFD_MT6323_NO_IRQ_DIAGNOSTIC=y`（f2800e694 故意移除）；ash113 新开 **118 项**（cgroup 全家 CPUACCT/DEVICE/FREEZER/SCHED/CPUSETS(_V1)、PSI、FAIR_GROUP_SCHED、DMABUF_HEAPS(+system/tee)、EXT4_FS_SECURITY、thermal 全家、SND/netfilter/ipv6/xfrm 大多 =m、FUSE、KEYBOARD_GPIO_POLLED 等，全表 /tmp/ash113_only_set.txt 已核对）。ASHMEM/BINDER("binder,hwbinder,vndbinder")/SELINUX/HIGHMEM/VMSPLIT_3G 桌面世代就全有。
5. **两构建点间无 mm/futex/sched 代码改动**：`git diff --stat 03d3f33ba..e194c9615` 全量只有 drivers/android(ashmem 回移)、clk UNIVPLL PCW(5f4a601ba, GPU 25390→416MHz)、mfd/充电、audio ASoC、pwrkey、dts。现役内核 `#113`（10-30 22:45 构建，003004 log 内核版本行亲验）。
6. **r112 差分**：r112 轮（ashmem 缺席，同 log 0 处 ashmem 字样）同 payload 下 SF 走完 init→`Enter boot animation`（log:1740-1752）→ **SIGABRT `FmqInternal.cpp:25] Check failed: exp` 循环**（z1_r112_20261030_185451.log:1754/1786/2345/2511）。#113 把这处**早死变成了同段的静默挂起**：过了 FMQ（power mode=2 已走 composer 命令队列），死在下一个 ashmem 大用户=首个 gralloc 分配。

## 二、根因假设排序（最可能 → 最不可能）

**R1｜首个 GraphicBuffer 分配事务挂起（SF 与 bootanim 都卡在这里；微观挂点三选一未定）**
位置：SF 首帧合成目标缓冲 / bootanim 首缓冲，经 `GraphicBufferAllocator→IAllocator@2.0(hwbinder, vendor.gralloc-2-0)→gralloc "simple"→ashmem`。三个子嫌疑按序：
(a) **#113 新开 config 项与该内核路径的交互**（cgroup/memcg 类页计费、PSI、dmabuf-heaps 与 ashmem mmap/fault 路径交叉；composer FMQ 只用了 1 页成功，大缓冲/多页/写回路径未验证）；
(b) **hwbinder 传输**在 allocator 事务上不回包（binder 主链活着——dumpsys 正常——但 hwbinder 大事务/回调路径未证）；
(c) **现行 gralloc "simple" 世代自身**（10-20 好轮是 60.00Hz 旧 rev（acm log:748），现行是 58.43Hz 软件节奏新 rev，alloc 路径可能变了）。
验证：debuggerd 三栈 + `/proc/*/task/*/wchan` + `/proc/binder/proc/<pid>` + ctl.stop 旁路实验（见 night_fixes/night2_morning_plan_20261101.txt 实验 0/2）。**关键佐证：SF 的 fb 目标缓冲也走同一 allocator——allocator 服务 36.581s 才懒启动，说明 SF 自己也没分到过任何缓冲。**

**R2｜zygote 卡死/静默退出（独立或与 R1 同源，未定）**
#113 上 zygote 起于内核 16.1s（log:1410），20 分钟零日志（无 "Preloading classes"）、system_server 永不 fork；p1.txt 433s 处更见 `Service 'zygote' (pid 3803) exited with status 0` 静默退出重启。r112 轮上 zygote 被 SF 死亡连坐（onrestart，log:1544）无法观察。startVm/ImageSpace 段同属 mmap/ashmem/futex 密集区。验证：debuggerd -b zygote + `/data/tombstones` + 早期 `/proc/1` 对 zygote 的报告。

**R3｜#110→#113 之间 118 项新开 config 的组合效应**
0 值翻转但新开项在好轮不存在；单项目前没有一个有"挂起"语义，组合无法排除。验证：刷 `out/boot_r2_p13.img`（#110 桌面世代内核，sha256 63d2c7d2…已核对在位）+ 现行 system 分区——到桌面则 R3/R1 内核侧整体降级、R4 升级；同点冻则 bisect 这 118 项（先关 CGROUP_*/PSI/DMABUF_HEAPS_*）。

**R4｜payload 世代回归（gralloc rev/system 演化/ramdisk init 演化）**
10-20 好轮之后 payload 至少历经 r3f(10-27)→r5k/r112(10-30)→现(10-31) 三代；gralloc rev 变了。r112-vs-#113 隔离了内核 ashmem 因素但两轮都是失败态。若实验 3 出"老内核+新 system 到桌面"，本条升为头号，修复=payload 侧（回退 gralloc/对拍 r3f system）。

**R5｜SwiftShader Subzero/marl 或 llvmpipe 线程原语（用户态）**
降权：卡点在 JIT 尚未开始前的缓冲分配段（bootanim 还没画第一帧）；zygote 不经渲染器；R4 检索的 SWIFTSHADER_DISABLE_SUBZERO=1/LP_NUM_THREADS=0 备而不发（注入需动 init.rc，见 night_fixes 文件实验 4）。

**R6（否证）｜"重建 500 行 defconfig 关闭了 SwiftShader 依赖"**：三重否证——值翻转 0 项（config 考古员 expanded_u0.diff + 本 ask 对拍 desktop110）、mm/futex/sched 代码 0 改动（diffstat）、桌面世代 config 对拍 0 关闭。

**R7（否证）｜ashmem.c 回移实现差异**：三代 sha256 同源。

**R8（排除）｜thermal 钳频**（1100d000.thermal deferred probe pending，log:1104）、**SELinux**（permissive=1，p12.txt）、**eMMC/内核健康**（watchdogd 正常、无 hung task、z1hb 心跳 free≈710MB）。

## 三、边界（未确定项，如实声明）
- R1 微观挂点（ashmem 交互 vs hwbinder vs gralloc 自身）未定——需板上 debuggerd/两步法栈，今晚设备断电无法执行（`out/night_freeze/LIVEPROBE_DEVICE_OFFLINE_20261101.md`）。
- #110 实际构建（10-03）与 616164412(.config 9-30) 之间若有未提交的临时 config 改动则对拍效力打折（git 内无变更记录，按树自洽采信）。
- "已实证"的 FmqInternal SIGABRT 链出处：r112 板上日志（本 ask 亲验），非推断。
- 冻结轮 r112 与 #113 的 payload 差异仅 egl prop（GL_MAX 4096/8192、bp_*.txt 等长同指纹），按同 payload 采信。
