# LiveProbe 联网检索对比清单 (2026-11-01 深夜, 设备离线支援任务)

检索目标:支援 config 考古员与裁决员——SwiftShader/mesa 首渲染冻结的同类案例。
四组检索全部经 WebSearch 执行,另有本地 config 单点交叉验证(见 §5)。

## 1. SwiftShader ARM32 hang / freeze at first EGL call
- **同类案例**:Google Issue Tracker "Crashes in Subzero in SwANGLE GLES31 32-bit tests"(SwiftShader 32 位 Subzero JIT 崩溃类);SwiftShader 官方文档 docs/Subzero.md 确认 **32 位 ARM 的 Reactor 后端 = Subzero JIT**,JIT 首编译失败/挂起常表现为首个 EGL/GL 调用期 hang。
- **症状相似度:高**——本设备冻结点正是三件 .so 加载完成后的首次 GL 调用。
- **解法/绕过**:`SWIFTSHADER_DISABLE_SUBZERO=1`(强制 LLVM 后端)或 `SWIFTSHADER_DISABLE_JIT=1`(禁 JIT),可作为**明早板上变量隔离实验**。
- **差异点(关键)**:纯 SwiftShader 根因**解释不了 mesa@416MHz 同点冻结**(本案渲染器无关),故 Subzero 假说最多是"半个真相"。

## 2. Android 9 zygote stuck after libEGL load / system_server not forked
- **同类模式**:AOSP 文档(source.android.com/docs/core/runtime/zygote)指出 zygote 启动卡住多半由 preload 阶段的资源/native 库(含 libEGL)变更引发;常见根因三类:GPU/GR HAL init 阻塞、preload 期 binder 死锁、native 库内部死锁。
- **诊断手段(明早可直接用)**:watchdog 日志、/data/tombstones/、`debuggerd -b <zygote_pid>`(native 栈)、核对 `ro.hardware.egl`。
- **症状相似度:中**——本例 SF 先冻、SF dump 循环仍活着,与"zygote preload 卡死"时序不完全吻合。
- **差异点**:本例卡点在 SF 进程内(native,无 ART),zygote 只是**还没轮到** fork system_server(或等 SF 无关)——明早 ps/wchan 可定序。

## 3. ART patchoat hang / boot image mismatch
- **同类模式**:经典失败 = 脏刷/半适配后 `/system/framework/boot.art`+`boot.oat` 与 runtime OAT 头版本不匹配,patchoat 反复触发或挂死;**AOSP 5.0+ 有 "boot without functioning patchoat" 回退(commit fb77759/84d7605)走解释器**,class_linker.cc 注释明确"patchoat failed 不应致命"。解法:清 /data/dalvik-cache、刷齐匹配 system。
- **症状相似度:低**。
- **差异点(关键)**:surfaceflinger 是独立 native 进程不走 ART;patchoat 假说解释不了"SF 首次 GL 调用冻结"。仅当"SF 卡住"与"zygote 卡住"实为**各自独立卡在同一种内核原语**时才相关。基本可降权。

## 4. Cortex-A7 / 低核数 SwiftShader pthread 死锁 (+mesa 补充检索)
- **marl 调度器**:SwiftShader 用 **marl** 做工作线程池,低核数设备(双核 A7 即此类)hang 有先例,栈特征 `marl::ConditionVariable`;Godot #123060 是同型"主线程持锁等编译、编译线程等锁"死锁样板。
- **mesa 侧补充**(为"渲染器无关"找公共依赖):llvmpipe 首绘制 hang 的已知模式 = **光栅化线程 barrier 死锁**(Haiku 论坛 2025-12 同类),workaround `LP_NUM_THREADS=0`;MIPS 上 llvmpipe 渲染线程崩坏先例(Bugzilla 868745);futex `FUTEX_WAIT` 唤醒永不抵达型 hang 的同型先例(FreeBSD Linuxulator bug 299220)。
- **共同点(本案核心线索)**:SwiftShader(marl/futex)与 mesa(llvmpipe 线程 barrier)的**公共内核依赖 = 多线程 + futex + barrier/membarrier + JIT 期 mprotect**。"渲染器无关"强烈暗示冻结在**公共内核原语**层,而非渲染器自身。
- **症状相似度:中高**(结构上),但均非 MT6572/32 位 Android 实证案例。

## 5. 本地交叉验证(单点,给 config 考古员;git 只读未 checkout)
对照 `fork_linux` 当前树(e194c9615 = ash 内核 main HEAD,`git log --oneline -1` 实证)与 v7d 构建点 `03d3f33ba`(2026-10-27 "dts: initrd-end 0x842e11c0 for v7d",按日期定位):
- **无差异项**:`grep` 实证两代 .config 均 `CONFIG_FUTEX=y`、`CONFIG_FUTEX_PI=y`、`CONFIG_EPOLL=y`、`CONFIG_SYSVIPC=y`、`CONFIG_SHMEM=y`、`CONFIG_AIO=y`、`CONFIG_MMU=y`、`CONFIG_SMP=y`、`CONFIG_SECCOMP=y`、`CONFIG_PREEMPT_VOLUNTARY=y`(ash 侧 `# CONFIG_PREEMPT is not set` 语义同 v7d 的 VOLUNTARY);另 MEMBARRIER/SCHED_AUTOGROUP/CHECKPOINT_RESTORE/TMPFS/POSIX_MQUEUE/RLIMIT/VM_SYNC/MADV_FREE/USERFAULTFD/COMPAT_BRK 关键词对比全部无差异。
- **唯一差异**:XFRM(ash=y、v7d 无)= IPsec 变换框架,与渲染无关,方向上为 ash 多开而非少开。
- **结论(供考古员参考)**:"futex/抢占/线程基元被 500 行重建 defconfig 关掉"的假说在这组基元上**已被排除**;1697 项差异里的嫌疑应转向其它维度(如 mmap/W^X、binder、LSM/seccomp 细项、或非 config 因素)。
- **局限**:这是关键词级单点抽查,非全量 1697 项 diff(那是考古员主战场);且 .config 对比以 git 历史提交为据,"树内 .config = 交付内核"由 commit 3ed1cf36b/b6e1d7897 的说明背书,未逐字节核对交付 boot.img。

## 6. 来源
- [SwiftShader Subzero 文档](https://github.com/google/swiftshader/blob/master/docs/Subzero.md) / [swiftshader 仓库](https://github.com/google/swiftshader)
- [Khronos SwiftShader 参考实现页](https://www.khronos.org/developers/linkto/swiftshader-reference-implementation-and-fallback)
- [Alpine armv7 chromium-swiftshader 包](https://pkgs.alpinelinux.org/package/v3.22/community/armv7/chromium-swiftshader)(证明 SwiftShader 4.0.0.4 一代在 ARMv7 有发行)
- [AOSP zygote 文档](https://source.android.com/docs/core/runtime/zygote)
- [AOSP 5.0 changelog(含 patchoat 回退 commit fb77759)](http://alberto97.altervista.org/aosp/5.0/aosp-KITKAT-LRX21L.html)
- [class_linker.cc(patchoat failed 回退注释)](https://android.googlesource.com/platform/art/+/android-5.0.1_r1/runtime/class_linker.cc)
- [patchoat 实际调用样例 gist](https://gist.github.com/zoggn/99a11c458d894124851ec4feb4c97e27)
- [Godot #123060 编译线程死锁](https://github.com/godotengine/godot/issues/123060)
- [Haiku 论坛:llvmpipe 渲染 hang(pthread barrier)](https://discuss.haiku-os.org/t/llvmpipe-hangs-when-rendering/18509)
- [FreeBSD bug 299220:futex FUTEX_WAIT 唤醒丢失](https://lists.freebsd.org/archives/freebsd-bugs/2026-October/036714.html)
- [Mozilla Bug 868745:llvmpipe on MIPS 渲染线程崩坏](https://bugzilla.mozilla.org)
- [Mesa 官方 GPU hang 调试文档](https://docs.mesa3d.org)
