# 主会话独立核验笔记（2026-10-29）

> 这些是**主会话自己实测**的结论，用于交叉核对 agent 报告。不替代 agent 报告。

## 1. `/vendor/etc/init/hw/` 缺失问题 —— 已判定为**非问题**（实证）

agent 提出疑虑：ramdisk `init.rc` 有
```
import /vendor/etc/init/hw/init.${ro.hardware}.rc
```
但镜像里 `/vendor/etc/init/hw/` 目录**不存在**。若这导致 `/vendor/etc/init/*.rc`
不被扫描，则 15 个 HIDL service 全部不启动 —— 那会是灾难级问题。

**主会话实证判定：该目录确实被扫描，此非问题。**
证据（板载 log 反查）：
- `vendor.audio-hal-2-0` 在板载 log 里出现 **89 次**（含 `received signal 9`、`process group` 等
  init 管理的痕迹）。而该服务名**只**定义在 `/vendor/etc/init/android.hardware.audio@2.0-service.rc`。
- `vndservicemanager` 出现 23 次、`gnss` 147 次，同样只定义在 `/vendor/etc/init/`。
- 结论：A9 init 对 `/vendor/etc/init/` 的扫描不依赖那个 `hw/` 路径；
  `hw/` 那条 import 失败是**非致命**的（import 不存在的文件只记日志）。

`/vendor/etc/init/*.rc` 里定义的全部 15 个服务（实测 grep）：
`health-hal-z1` / `hostapd` / `vendor.audio-hal-2-0` / `vendor.bluetooth-1-0` /
`vendor.cas-hal-1-0` / `vendor.configstore-hal` / `vendor.gnss_service` /
`vendor.gralloc-2-0` / `vendor.hwcomposer-2-1` / `vendor.keymaster-3-0` /
`vendor.media.omx` / `vendor.sensors-hal-1-0` / `vendor.wifi_hal_legacy` /
`vndservicemanager` / `wpa_supplicant`

## 2. 音频失败根因 —— 板载 log 实证（两条对照）

```
E AudioFlinger: loadHwModule() error -22 loading module primary      <- 失败
I AudioFlinger: loadHwModule() Loaded r_submix audio interface, ...  <- 成功
```
`-22` = `-EINVAL`。**primary 失败但 r_submix 成功**，说明：
- AudioFlinger 本身、HIDL 链、`/dev/snd` 相关机制**不阻塞**（否则 r_submix 也起不来）
- 唯 primary 的 HAL 加载失败 → 与"`libaudio.primary.default.so` 是 null 空壳"完全吻合
- 该现象在 2026-10-06 与 10-25 两次 boot 里**可复现**（非偶发）

## 3. ramdisk cpio 类型位 bug —— 与 agent 结论一致（我已独立复现）
`out/z1_r5c_boot/boot.img` 的 ramdisk 解包后 41 条记录全部有 `S_IFREG`；
而修复前的 R5 ramdisk 有 6 条畸形（5 个脚本 + TRAILER）。根因在
`z1_ramdisk_edit.py` 只写权限位。**已在 git 提交 `012ce81` 修复并推送。**

## 4. 关于 `libxlog.so` "无消费者" 的更正（重要）
某个 agent 报告说"全镜像无任何 ELF 引用 libxlog"。**这部分不准确。**
实测三个 GPS 二进制的 `nm -D -u` **都有** `__xlog_buf_printf` 未定义符号：
`mnld` / `libmnlp_mt6572` / `gps.default.so` 各 1 处。
但**补丁前**版本的 `DT_NEEDED` 里**没有** `libxlog.so`（只有裸未定义符号），
**补丁后**版本（patched）才有。这解释了为何"grep NEEDED 找不到消费者"：
- 旧版：`UND __xlog_buf_printf` 有，`NEEDED libxlog` 无 → 靠运行时解析
- 新版：两者都有
R5d 已把三件换成**新版**（`NEEDED libxlog` 已确认在位）。所以 `libxlog.so` **确有消费者**，
agent 的"可能是漏了某步"顾虑**不成立**。

## 5. PMIC 模式核验 —— 交付内核**不是**诊断模式（实证）
旧板载 log（`z1_android9_acm_20261025_*`）里有：
```
mt6397 1000f000.pwrap:mt6323: MT6323 regulator-only diagnostic mode; IRQ controller disabled
```
这是**危险模式**：`MFD_MT6323_NO_IRQ_DIAGNOSTIC=y` 时 MFD 只暴露 regulator+auxadc 两个 cell，
**电源键 / PMIC 键盘 / RTC / LED 全部不建**。

**主会话对当前交付内核实测**（解 `out/z1_r5c_boot/boot.img` 的 zImage → xz 解 → 查字符串）：
```
regulator-only diagnostic mode   0 命中
IRQ controller enabled           1 命中
```
→ **交付内核走的是 IRQ-controller 正常路径**，电源键 cell 会建。旧 log 那条是更早的内核。

同理 `# CONFIG_MFD_MT6323_NO_IRQ_DIAGNOSTIC is not set` 在树 `.config`、
交付 `.config_r5`、defconfig 三处一致（已逐项核对）。

## 6. SELinux `module_load` denied 是**非致命**（permissive）
板载 log 有：
```
avc: denied { module_load } for pid=1 comm="init"
  path="/system/lib/modules/z1-network/fuse.ko" ... permissive=1
avc: denied { module_load } for ... ip6table_filter.ko ... permissive=1
```
关键在末尾 **`permissive=1`** —— 这是**仅记录、不拦截**。所以模块照样能 insmod。
板载 avc denied 统计（该 log）：`search` 31 / `write` 23 / `watch` 17 / `getattr` 17 /
`open` 16 / `read` 12 / `module_request` 9 / `sys_module` 7 / `module_load` 2 …
全部 permissive。→ 这也解释了为何"转 enforcing 前必须补 xattr"是**未来的**事，
当前功能不受阻。

## 7. 音频 HAL 启动时机（风险，待 agent 结论）
板载 log 时间线（两次 boot 均可复现）：
```
t=26.008  AudioFlinger: Using default 3000 mSec as standby time.
t=26.779  E AudioFlinger: loadHwModule() error -22 loading module primary
t=27.015  I AudioFlinger: loadHwModule() Loaded r_submix audio interface, handle 10
t=27.063  AudioFlinger's thread ready to run
```
即 AudioFlinger 在 t≈26s 就尝试加载 primary HAL。而 `load_audio.sh`（装 11 个音频 .ko、
声卡才出现）挂在 `init.z1.network.rc` 的 **post-fs** 阶段，**是否早于 t=26s 未证**。
若 HAL 在 `/dev/snd/pcmC0D0p` 出现前被 open，`pcm_open` 必失败。
**这条时序风险已转给 audiodep agent 专项判定**（要求：要么证明安全，要么给修法）。

## 8. ★ UI 卡顿是**当前最大的用户可见问题**（主会话量化，此前无人报）

从板载 log 统计 `OpenGLRenderer: Davey! duration=` 帧耗时：
```
样本数: 32
最小:  1,303 ms
中位:  5,112 ms
最大: 31,534 ms
平均:  7,872 ms
>1s 的帧:  32 / 32  (100%)
>5s 的帧:  17 / 32
```
**正常应为 <16 ms（60fps）。中位 5.1 秒 = 屏幕每 5 秒才更新一帧。**

附带后果（同一 log）：
```
E ActivityManager: ANR in com.android.systemui        (3 次)
W Watchdog: *** WATCHDOG KILLING SYSTEM PROCESS: Blocked in handler on
             foreground thread (android.fg)
```
即 **SystemUI 反复 ANR，最后 Watchdog 把 system_server 杀了** → 这是"机器看起来能用但一直卡/重启"的根因。

### 根因（两层，都已实证）
**① 框架在用纯软件渲染，没用 GPU**
```
build.prop: ro.hardware.egl=swiftshader
板载 log:   libEGL: loaded /vendor/lib/egl/libEGL_swiftshader.so   (以及 GLESv1/v2_swiftshader)
```
而**硬件 GPU 栈其实已经在镜像里**（都就位）：
```
/vendor/lib/egl/libEGL_mesa.so        259,992 B
/vendor/lib/dri/lima_dri.so        13,533,780 B
/vendor/lib/libglapi.so               173,184 B
```
`libEGL_mesa.so` 的 10 个 NEEDED 依赖**全部在镜像里解析得到**（已逐项核）。
即：**只差把 `ro.hardware.egl` 从 `swiftshader` 改成 `mesa`** 就能走硬件路径
—— 但前置条件是 `/dev/dri/renderD128` 必须存在。

**② `/dev/dri/renderD128` 可能根本没创建**
- 内核侧**应该**建：`lima_drv.c:266` 有 `DRIVER_RENDER | DRIVER_GEM | DRIVER_SYNCOBJ`，
  板载 log 也确认 `[drm] Initialized lima 1.1.0 for 13010000.gpu on minor 0`。
- 但板载 log 里 `renderD128` **0 命中**、`/dev/dri` **0 命中**（`lima` 本身 6 命中）。
- ramdisk `ueventd.z1.rc` 只有一条 `/dev/dri/renderD128 0660 system graphics`
  （ueventd 规则**不能创建**节点，只能在节点出现后改权限；节点要由内核 devtmpfs 建）。
- **待证**：是节点没建，还是"建的时机晚于框架探测"（框架在 t≈23s 就定 gralloc 了）。
  若是后者，可在 init 早期显式 `mkdir /dev/dri` + 等节点。

### 另一条独立问题：GPU 时钟被限在 25.39 MHz
```
lima 13010000.gpu: bus rate = 25390
lima 13010000.gpu: mod rate = 25390
```
25.39 MHz —— Mali-400 MP1 在 MT6572 上可用到 **416 MHz**，即**低了约 16 倍**。
说明 clk mux / `assigned-clock-rates` 没切到高频档。即使切到 mesa 硬件路径，
这个频率下性能仍会差一个量级。**需查 dts 的 gpu 节点 clocks/assigned-clock-rates。**

### 建议（按序）
1. 先确认 `/dev/dri/renderD128` 是否真不存在（上板 `ls -l /dev/dri/`）。
2. 若不存在：查 dts gpu 节点 + 是否需要 `CONFIG_DRM_FBDEV_EMULATION` 之类；
   或在 init 里确保 devtmpfs 已挂载后节点可见。
3. 若存在：把 `ro.hardware.egl` 改 `mesa`（build.prop），单变量验证帧耗时是否从 5s 降到 <100ms。
4. 独立修 GPU 时钟（dts `assigned-clock-rates`）。
5. 兜底缓解（不依赖 GPU）：上 `out/z1_perf_stage/` 的 `debug.sf.nobootanimation=1`
   + 关后台 dexopt，能直接减掉一大块卡顿源。

## 9. ★ WiFi 是**回归**：R5 装的是 A 版（已失效），B 版（板上跑通过）被回退

EMI 裁决 agent（`out/WMT_EMI_RULING.md`）的结论，**主会话独立复核并确认**：

### 主会话实证
```
# R5 交付镜像里的 wmt 模块
debugfs dump /lib/modules/z1-radio/mtk_stp_wmt_soc.ko  -> sha 786e9ab4d9ba…
out/z1_wifi_stage/modules/mtk_stp_wmt_soc.ko           -> sha 786e9ab4d9ba…（cmp 零差异）

# EMI 基址（反汇编看 movt r0,#imm）
R5 交付件        : movt r0,#0x8008  (2 处)  -> CONSYS_EMI_AP_PHY_BASE = 0x80080000  ← A 版
out/alpine_rootfs: movt r0,#0xbe08  (2 处)  -> 0xbe080000                            ← B 版
```
即：**R5 交付的 WiFi 模块与"报 EMI mapping fail 的那一枚"是同一个文件**（sha 全等）。
而 `out/alpine_rootfs/lib/modules/7.0.0-rc7-z1+/mtk_stp_wmt_soc.ko`（2026-08-16）
用的是 **0xbe080000**，vermagic 也是 `7.0.0-rc7-z1+`。

### 机理（agent 从本机源码给出，我认可其自洽性）
`fork_linux/arch/arm/mm/ioremap.c:314-320`：
```c
if (WARN_ON(memblock_is_map_memory(PFN_PHYS(pfn)) && mtype != MT_MEMORY_RW))
        return NULL;
```
`0x80080000` 落在内核管理的 RAM 里（非 no-map），`ioremap` 属性不匹配 → WARN + 返回 NULL
→ 驱动打印 `EMI mapping fail`。板载 log 的 WARN 行号（`ioremap.c:318`）与
`.ko` 内硬编码行号（663）完全对上。

### 一个操作性要点（agent 实测，重要）
**成功时不会打印 `EMI mapping OK`**（debug 级被编译掉，5 枚 ko 里 0 命中）。
所以**上板判据是"没有 `EMI mapping fail` 行"，不是"出现 OK 行"** —— 别等 OK。
另有 2026-08-14 的完整成功日志（`mainline_recon/z1_boot_mainline_20260814_202853.log`）：
`Open FW image done, 160464 bytes` → `WMT turn on WIFI success!` → `wlan0` →
`iw scan` 扫到 12 个真实 AP，MAC `20:72:0d:39:0d:1f`。

### 我的符号满足性复核（更正了我自己的一次误判）
先用粗糙方法判 agent 重编的 B 版，误报"10 个 `mtk_wcn_btif_*` 无提供者"。
**用正确的 `nm --defined-only`（不限类型码）+ R5 归档 `Module.symvers`（12,490 导出）
+ 镜像内全部 ko（1,644 定义）重判：无法满足 = 0。**
那 10 个 btif 符号由 `mtk_btif_drv.ko` 导出（它在镜像里，37 个 btif 导出）。
**板上跑通的那枚也需要同样的 10 个符号** → 说明这是正常的模块间依赖，不是缺陷。
教训：判符号满足性必须用 `Module.symvers` 或全量 `nm`，不能拿"内核 Image 里的字符串"当符号表。
