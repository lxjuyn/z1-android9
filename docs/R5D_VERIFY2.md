# R5D 第二轮独立核验报告

**核验员**：独立核验 agent（只读）
**日期**：2026-10-29
**对象**：`out/z1_r5c_boot/boot.img` + `out/z1_r5_system/system.img.r5d_final`
**回滚基线**：`out/z1_r41_final/boot.img` + `out/z1_r5_system/system.img.r5_final`
**上一轮报告**：`out/R5C_VERIFY.md`（已读，但本轮所有结论均独立重测，未照抄）

---

## 结论

### **可以刷。对"刷入即变砖"的 blocker：无。**

四项声明的 r5d 改动**全部实测核实存在且内容正确**；两个镜像 e2fsck 干净；0 删除；
启动关键件逐字节未变；boot.img 结构与全部地址算术自洽。

### 但有 **1 个必须让交付方知悉的 BLOCKER 级发现（针对"可复现性"而不是"能否刷"）**：

**B1. 交付内核不是由仓库 HEAD 的源码树编出来的，`/vendor/bin/mnld` 与镜像不匹配。
按仓库 defconfig 重编 = 必然起不来（bootloop）。**

证据链（全部实测，见 §B1）：

| 事实 | 证据 |
|---|---|
| 交付内核多出 `z1-charger` MFD cell + `z1,mt6323-charger` DT 节点 | r5c 内核有这两个串；r41 回滚内核**一个都没有** |
| 该 cell 来自 commit `aa2089b54`（在归档构建**之后**） | `mt6397-core.c` 里的 cell 在 `aa2089b54` 加入 |
| 归档 `src_changes/r5_all_changes.patch` 里**完全没有** `z1_charger` / `mt6397-core.c` | 实测 grep 命中 0 |
| 但镜像里 **有** `z1_charger.ko` 且 `load_audio.sh` 加载它 | 镜像实测（`z1_charger.ko` 10148 B，NEEDED/alias 正确） |

即：交付内核在 `/tmp/r5k` 构建时，tree 里已有一份 `z1-charger` 改动，**但它从未被记入归档、
也从未被提交**。仓库里能编出 mt6397-core.c 那一段，是在 `aa2089b54` 之后。

---

## 一、sha256（实测为准）

给定值与实测**四项全部匹配**：

```
96f00d03265add4bdc8db94286c23b08ce459243517cb8f24d5c4b2180535da7  out/z1_r5c_boot/boot.img            ✔
c258c196f6286f6b5551b04c93ed5e6611b291214e827e383db9e79b32b30e0b  out/z1_r5_system/system.img.r5d_final ✔
61e698649dbed155277dad750fd1000f0e8b532897d443b102afa91cbc8e0f49  out/z1_r41_final/boot.img           ✔
706eee2f3f623bf082c5473b731e62ee453b4d1f5e1e3b78e8b5c049dc966257  out/z1_r5_system/system.img.r5_final  ✔
```

尺寸：boot 6,209,536 B（两件一致）；system 943,718,400 B（两件一致，= p4 分区整尺寸）。

---

## 二、Blocker 清单

### B1（BLOCKER，针对可复现性）— 交付内核的 charger MFD cell 未入库，且 mnld 与镜像不匹配

三条独立的事实，任何一条都能单独误导重编者：

**(a) 内核与归档对不上。** 交付内核（`#3`，2026-10-28 13:07:08）**含** charger MFD cell；
归档 `products_r5_kernel_20261028_125210/zImage_r5`（`#2`，12:51:12）**不含**。
归档 SHA256SUMS 与树内 `vmlinux` 也不一致：

```
归档 zImage_r5      : dac89c128236e6bed6e7a1f51daa788df925f769cd0c79cbf5c79044654ac130
归档 payload sha256 : 54fc7919e494fe0218e9cbaba90a9da93a909930d61040be351d254f3a27b922
交付 boot kernel seg: f7cc818723f09f9cd1bc06141fab0b2e7011338268e9bc37e922b258dad575e3
交付 payload sha256 : 5d2158c0a4f983d74cd63cd0bb4577abd8d148ea85fd3a7c657d94c22249c485
树内 vmlinux sha    : (与交付 zImage 同一轮，13:0x)
```

**(b) 归档 patch 不完整。** `products_r5_kernel_20261028_125210/src_changes/r5_all_changes.patch`：

```
grep -c 'z1_charger'            -> 0   （归档里根本没有 charger 源码）
grep -c 'drivers/mfd/mt6397-core.c' -> 0   （没有 MFD cell 那一段）
```
而 `drivers/mfd/mt6397-core.c` 的 charger cell 由 `aa2089b54` 引入，**在 12:51 构建之后**。

**(c) 仓库 defconfig 重编 = bootloop。** 用仓库 `arch/arm/configs/z1_defconfig` 生成配置，
`CONFIG_POWER_SUPPLY` 变成 `# ... is not set`（交付内核是 `=y`），
于是 `CHARGER_Z1_MT6323`（`depends on MFD_MT6397`，`tristate`）**被静默丢弃**。
而交付内核的 mt6397-core.c 无条件注册 `z1-charger` cell → 内核里有没有 charger 驱动**无关**，
它每次 boot 都会 `devm_mfd_add_devices()` 创建设备。
后果链：`z1-charger` 设备存在但无驱动 → **`load_audio.sh` 必然失败**。

更糟的是失败方式：`load_audio.sh` 是 `#!/system/bin/sh`，**无 `set -e`**，
但 `load_audio.sh` 里的 `load()` 后紧跟 `if [ -d /sys/class/power_supply ]`，
失败不致命；**真正致命的是 MFD cell 与 DT 节点一起消失**：
`z1_charger.ko` 有 `alias of:N*T*Cz1,mt6323-chargerC*`，
若 DT 节点也缺（重编不带该 dts 改动）→ 无 platform device → insmod `-ENODEV`。
三处（内核 cell / DT 节点 / defconfig `POWER_SUPPLY`）在仓库里是**同一次缺失**，
必须一起补，补任何一个都不够。

> **判定**：本条不影响"刷 `r5d_final` 这件产物"（产物本身内部自洽），
> 但它使"从仓库重编一个等价内核"目前**不可能**。若交付方后续要重编内核（例如
> `Z1_CPUFREQ_GAP.md` 里承诺的 cpufreq 修复），必须先补这三处，否则重编即 bootloop。

### B2（重要，非刷机 blocker）— `/vendor/bin/mnld` 与两个 DT/脚本事实不匹配

1. **`z1_gps_prep.sh` 种的配置键名 mnld 不认。**
   脚本写 `MTK_GPS_CHIP=MT6620`；但 `strings mnld` 里**没有** `MTK_GPS_CHIP` 字面量
   （`grep -c` = 0），也无任何 `=`/`%s=%s` 形式的 key 解析。mnld 只用**固定路径**
   `/data/misc/GPS_CHIP.cfg`、`%d` 格式与 `0x6620/0x6572/...` 十六进制表。
   `libmnlp_mt6572` 与 `gps.default.so` 也都不含 `MTK_GPS_CHIP`。
   → 该行的**值部分**很可能是装饰性的；实际芯片判定走 `persist.mtk.wcn.combo.chipid`
   （镜像里全树 grep **无**该属性，所以是空 → 可能回落 `%s: chip is unknown`）。
   **未确定**：缺的是反编译（mnld stripped，需对 `.rodata` @0x49c9 的引用做完整反汇编）。
   我不猜结论：**脚本的 `MT6620` 与镜像里唯一存在的 `libmnlp_mt6572` 存在语义冲突，须上板取证。**

2. **镜像里只有 1 个 libmnlp，脚本却把它指向 `MT6620`。**
   `xbin/libmnlp_mt6572` 是**目录**（不是符号链接），`/system/xbin` 存在
   （`bin -> /system/bin`，故 `/system/xbin` 就是镜像 `xbin/`）。路径解析正确 ✔。
   但 mnld 的 `0x6620 → libmnlp_mt6620`、`0x6572 → libmnlp_mt6572` 两张表，
   若 chip 被解析成 6620 会去 dlopen **不存在的** `libmnlp_mt6620` → GPS 用户态起不来。
   两种取值下都可能有错，取决于 mnld 实际读到的 chip id。

**注意**：`z1_gps_prep.sh` 是**唯一**为 mnld 准备 `/data/misc` 的脚本，而它由
`on property:sys.z1.radio.modules_ready=1` 触发 —— 即**依赖整条 radio 链先跑通**。
`z1_radio_finish.sh` 只在 `/dev/stpwmt`+`/dev/stpbt` 都出现时才 setprop。
若 radio 链断，`sys.z1.radio.modules_ready` 永不置 1 →
`z1_gps_prep.sh` 不执行 → `/dev/stpgps` 不存在 → `z1_mnld` 永不 start。
**GPS 全链是串行依赖，任一环断则全断**，且失败点只能从 dmesg 的 `<6>[z1gps]` 反推。

### B3（记录，非本件引入）— `CONFIG_CPUFREQ_DT` / `CPU_THERMAL` 是**全域**分歧

`out/Z1_CPUFREQ_GAP.md` 只列了这两项，说"其它已核对一致"。实测**不是**这两项。
`make z1_defconfig` 生成结果与交付 `.config_r5` 的**语义**差异（含隐式携带）：

| 类别 | 数量 |
|---|---|
| defconfig 生成后启用(=y/m/字符串)、交付未启用 | **578** |
| 交付启用、defconfig 生成后未启用 | **157** |
| **合计不一致** | **735** |

其中**启动/功能关键**项（逐条实测，非推断）：

```
CONFIG_MMC_BLOCK            gen: not set    delivered: y     ← eMMC 块设备
CONFIG_WATCHDOG             gen: not set    delivered: y     ← 喂狗（CLAUDE.md 记录过的复现坑）
CONFIG_MEDIATEK_WATCHDOG    gen: 不存在     delivered: y
CONFIG_SECURITY             gen: not set    delivered: y     ← SELinux 整块
CONFIG_SECURITY_SELINUX     gen: 不存在     delivered: y
CONFIG_NETFILTER            gen: not set    delivered: y
CONFIG_USB_CONFIGFS         gen: not set    delivered: y     ← ACM gadget
CONFIG_USB_MUSB_GADGET      gen: not set    delivered: y
CONFIG_USB_MUSB_DUAL_ROLE   gen: y          delivered: not set
CONFIG_TOUCHSCREEN_GOODIX   gen: not set    delivered: y     ← 触摸
CONFIG_ZRAM                 gen: not set    delivered: y     ← fstab.z1 swapon_all
CONFIG_DEBUG_LL             gen: not set    delivered: y
CONFIG_KERNEL_XZ            gen: not set    delivered: y     ← zImage 压缩（大小预算！）
CONFIG_MTK_MT6572_CONNSYS   gen: not set    delivered: y     ← 本项目自加符号，见下 ★
CONFIG_PM_WAKELOCKS         gen: not set    delivered: y     ← z1_heartbeat 等
CONFIG_ANDROID_BINDER_IPC   gen: not set    delivered: y     ← Android 必需
CONFIG_I2C_MT65XX           gen: not set    delivered: y
CONFIG_LOCALVERSION         gen: ""         delivered: "-z1"   ← ★ vermagic！
CONFIG_LOCALVERSION_AUTO    gen: y          delivered: not set ← ★ vermagic！
CONFIG_CPUFREQ_DT           gen: y          delivered: not set
CONFIG_CPU_THERMAL          gen: y          delivered: not set
```

**★ 决定性证据**：`CONFIG_MTK_MT6572_CONNSYS` 定义在 `drivers/soc/mediatek/Kconfig:77`，
**是本仓库自己加的符号**（upstream 不存在）。仓库 defconfig 生成后它是 `not set`
⇒ defconfig 绝不可能与"编出 CONNSYS 内核"的那份配置同源。**"已被核对一致"的断言是错的。**

**★ vermagic 双杀**：defconfig 生成 `CONFIG_LOCALVERSION=""` + `LOCALVERSION_AUTO=y`
⇒ `make z1_defconfig && make` 产出的内核 vermagic 是 `7.0.0-rc7-<git-hash>`，
而镜像里 60 个 .ko **全部**是 `7.0.0-rc7-z1+`。按 CLAUDE.md §5.7 的纪律，
这会让**全部 60 个 .ko 失配**。

**推论（强）**：`arch/arm/configs/z1_defconfig` 是**2026-10-06 之前的遗留物**，
自 10-06 起实测有效的配置只存在于工作树 `.config`；`291233f8a`（10-28 15:32）
往一份"陈旧快照"上追加了 18 行——所以追加的项生效、更早的分歧全部残留。
`291233f8a` 提交说明里的"逐项与 .config_r5 对齐"只对新增行成立。

**补充事实（推翻文件自身的叙述）**：树 `.config` 里**有** `CONFIG_CHARGER_Z1_MT6323=m`
（L2632），而交付 `.config_r5` 里**没有**（grep 命中 0），只有 `CONFIG_POWER_SUPPLY=y`。
即 `b6e1d7897` 的提交说明声称已对齐 `CHARGER_Z1_MT6323=m`，**实际未对齐**。
同一类错误（提交说明未回读复核）在 `3ed1cf36b` 已发生过一次，`b6e1d7897` 自述就是为了修它 ——
**但只修了一部分**。

---

## 三、非阻塞风险

### N1. 交付内核是"孤儿构建" —— 无法从任何提交重编
交付内核 `#3` @ 13:07:08 晚于归档 `#2` @ 12:51:12，但早于 `291233f8a`(15:32)、
`5ce6b2069`/`f2800e694`(15:36/15:41)、`aa2089b54`(mt6397-core cell)、
`3ed1cf36b`/`b6e1d7897`(20:42/20:52)。该时间窗**没有任何提交**。
即交付内核对应的树状态**只存在于 `/tmp/r5k`**（可能已销毁）。仓库 HEAD 重编 ≠ 交付内核。

### N2. `xbin/libmnlp_mt6572` 只有破折号版，键名不匹配的旁证
`libmnlp` 的 dlopen 名由 mnld 的 `0x%x` 十六进制表决定，而仓库里的两个变体
（`mnld.pre_xlog` / `libmnlp_mt6572.pre_xlog`，均无 libxlog NEEDED）与 patched 版的
**文件名都带破折号**。这不构成 bug，但说明命名约定是"破折号"，与 mnld 表里的
`0x%x` 拼接逻辑**无关联**——两套命名并存，容易误读。

### N3. `z1_charger.ko` 有两个不同二进制
```
out/z1_charger_stage/z1_charger.ko                          10,100 B  08:35:25  a80355567f2b8372
镜像 /lib/modules/z1_charger.ko                             10,148 B  13:08:20  99cfda1af7587b407
```
两者 vermagic 相同、`nm -u` 未定义符号**逐项相同**；镜像里那个与交付内核同轮（13:08）。
**入镜像的是对的**；`z1_charger_stage` 那个是 08:35 的旧件。**不是 blocker**，
但归档目录里留着旧件容易误用。建议刷前只认镜像内那个。

### N4. `mt6779-keypad.kl` 与 `mt6779_keypad.kl` 并存（无害）
两者**逐字节相同**（828 B）。内核 input 设备名带连字符，精确名匹配优先，
`mt6779-keypad.kl` 会命中。重复文件不产生行为差异，仅多 828 B。

### N5. `Z1_CPUFREQ_GAP.md` 的结论方向对、范围错
该文档判定"无 cpufreq 不影响开机"——**这条对**（板载只是读文件失败的 log）。
但"defconfig 与实际交付内核不一致"被描述成"只有 2 项"，
实际是 735 项（§B3）。文档据此给出的"修法：同时把 defconfig 与 .config 对齐"
**不够**：按现在的 defconfig 对齐，内核会丢掉 `MMC_BLOCK`/`WATCHDOG`/`SELINUX`/
`BINDER_IPC`/`KERNEL_XZ`/`LOCALVERSION=-z1` 等，直接起不来。

### N6. 我推翻的上一轮结论（R5C_VERIFY.md）

| R5C_VERIFY.md 的说法 | 实测 |
|---|---|
| §N2「**全镜像扫描没有任何 ELF 引用 `libxlog`**，纯占 10 KB，可能是漏了某步」 | **错**。`r5d_final` 里 `mnld` / `libmnlp_mt6572` / `gps.default.so` **三者都有** `NEEDED libxlog.so` 且 dynsym 里都有 `U __xlog_buf_printf`。上一轮扫的是 **r5c_final**，那时确实没有 patched 版本 —— 该观察在**当时是对的**，但它被写成了"全镜像"的普适结论，而 r5d 恰好是来解这个问题的。**r5d 正是 N2 的修复**。 |
| §N3「`media_codecs_performance.xml` 含 6 处 `OMX.qcom.*`，是源文件没 strip」 | **在 r5c 上成立，在 r5d 上已修**。r5d 的该文件 **858 B**（r5c 2,691 B），`grep -c 'OMX.qcom'` = **0**，且 `vendor/etc` 全目录 `OMX.qcom` 命中 **0**。 |
| §一「ramdisk cpio 全部 mode：**41/41 记录类型正确**」 | **正确，但记录总数是 75**（含 TRAILER 和我另计的 1 条），且**只有 41 条是 regular file**。用 `cpio -itv` 独立复核：41 个 `-` + 14 个 `d` + 19 个 `l` = 74（cpio 隐藏 TRAILER）。两法一致 ⇒ **0 条缺 `S_IFREG`**，缺陷确实修好了。 |

**另需指出上一轮的盲点**：R5C_VERIFY 的结论表里没有关于**内核可复现性**（交付内核
是否等于仓库某提交）的任何检查项。这正是 B1/B3 的藏身处。

---

## 四、逐项证据

### A1. `z1_pmic_pwrkey_poll` fallback 驱动

**(a) 未注册进 Kconfig / Makefile —— 确认 ✔**
```
grep -n 'PWRKEY_POLL\|z1_pwrkey\|Z1_PMIC' drivers/input/keyboard/Kconfig drivers/input/keyboard/Makefile
-> 无命中（exit 1）
grep -rn 'z1_pwrkey_poll' --include=Kconfig --include=Makefile --include='*.dts*' .
-> 只命中该目录自己的 Makefile 注释（自述"deliberately NOT referenced"）
```
**结论：确认是故意的死目录，不会误编进内核。** ✔（与主会话说法一致）

**(b) 未定义符号在交付内核里全部存在 ✔**
`.ko` 24 个未定义符号，逐个对 `products_r5_kernel_20261028_125210/Module.symvers`
（12,490 条导出）：
```
__aeabi_unwind_cpp_pr0 cancel_delayed_work_sync delayed_work_timer_fn _dev_err
dev_get_regmap _dev_info devm_input_allocate_device devm_kmalloc input_event
input_register_device input_set_capability memset __msecs_to_jiffies
of_find_device_by_node of_node_put __of_parse_phandle_with_args
of_property_read_variable_u32_array __platform_driver_register
platform_driver_unregister queue_delayed_work_on regmap_read __stack_chk_fail
system_percpu_wq timer_init_key
-> 24/24 EXPORTED，0 缺失
```
`vermagic = 7.0.0-rc7-z1+ SMP mod_unload ARMv7 p2v8` ✔
`name = z1_pmic_pwrkey_poll`，DT alias `of:N*T*Cz1,pmic-pwrkey-pollC*` ✔

**(c) DT 里没有该节点 —— 确认，故为备用死代码（正确）✔**
```
grep -c 'pmic-pwrkey-poll' r5c/z1.dts  -> 0
grep -c 'pwrkey-poll'      r5c/z1.dts  -> 0
grep -c 'pwrkey-poll'      r41/z1.dts  -> 0
```
驱动引用的 `mediatek,pwrap` phandle 目标存在（`pwrap@1000f000`，dtb 行 411）——
**即"驱动能编、符号齐全、但永不 probe"**，与"备用"定位一致。**判定：符合预期** ✔

> 唯一瑕疵：源码注释自述 `linux,keycode` 用 `KEY_POWER` = 116，
> 且 `MT6323_CHRSTATUS` 位 1 为按下。这两条我没有硬件无法验证，标 **未确定**。

### A2. `mt6397-irq.c` 交付内核用的是**非致命版** —— 确认 ✔

在交付内核 zImage 解出的 payload（15,377,208 B）里逐串 grep：

| 串 | r5c（交付） | r41（回滚） | 归档 zImage_r5 |
|---|---|---|---|
| `unsupported chip: 0x%x` | **@12357620** | @12357452 | @12357452 |
| `could not create irq domain` | **@12357644** | @12357476 | @12357476 |
| `failed to mask INT_CON0` | **不存在** | 不存在 | 不存在 |
| `failed to mask INT_CON1` | **不存在** | 不存在 | 不存在 |
| `failed to mask INT_CON2` | **不存在** | 不存在 | 不存在 |
| `INT_CON0 readback failed` | **不存在** | 不存在 | 不存在 |
| `PMIC write path: INT_CON0` | **不存在** | 不存在 | 不存在 |
| `failed to register irq=%d` | @12357688 | @12357520 | @12357520 |
| `mt6397-irq` | @12357724 | @12357556 | @12357556 |

**(a) 判定：非致命版，来源基准（`be6f4059…`，即 `regmap_write` 返回值被丢弃）✔**
- 致命版（`5ce6b2069`，`2b993d4c…`）的串 `failed to mask INT_CON0: %d\n` **不在**内核里；
- `f2800e694` 的非致命版串是 `...(continuing)\n`，但**基础串前缀**同样不在内核里。
  ⇒ 交付内核既不是致命版，**也不是** `f2800e694` 的版本，而是**第三个形态**：
  baseline 的"忽略返回值"。
- 旁证：`(continuing)` 在 r5c 里命中 3 次，但全部来自无关代码
  （`panic: target CPU %d is offline, continuing on CPU %d` 等），
  与 r41 的 3 次**完全相同** ⇒ 与 PMIC 无关 ✔

**(b) 对电源键的实际影响：无 ✔**（重要）
这三条路都不致命：
```
baseline（交付）: regmap_write 返回值被丢弃 → 继续 probe  → 电源键正常
f2800e694 非致命: dev_err(...,"(continuing)")  → 继续 probe → 电源键正常
5ce6b2069 致命  : dev_err + return ret        → MFD probe 失败 → 电源键死
```
**交付内核在"不会自伤开机路径"这一点上与 `f2800e694` 效果等价**，
且比 `f2800e694` 更保守（连 INT_CON0 回读都没有）。
**代价**：失去 `f2800e694` 想要的诊断价值（无法一行日志区分 PMIC 总线坏 vs 中断/DT 错），
且**没有报告出来**——主会话以为用的是 `f2800e694`。

**(c) 若原样刷入，dmesg 不会出现** `PMIC write path: INT_CON0(...) readback 0x....`
—— 因为交付内核里没有这行。**取证前须知**，否则会误判"诊断段没跑"。

### A3. defconfig 可复现性 —— **大规模不一致（见 §B3）**，方法论如下

用 `KCONFIG_CONFIG=` 重定向，**未写树内 `.config`**：
```bash
# 树 .config 前后 sha256 完全一致（7afc2c6471bbbfc2e9e165c0f3af71a9e8541819f2907030b976e299b2e010fe）
# git status --porcelain 全程为空
make ARCH=arm KCONFIG_CONFIG=/tmp/v2/defcfg_kconfig z1_defconfig
# 唯一警告：SERIAL_8250_CONSOLE / SERIAL_OF_PLATFORM / USB_GADGET 重复赋值（既存）
```
语义对比（`CONFIG_X=y|m|"str"` = 启用；`# ... is not set` = 关闭）：

```
TOTAL keys: defcfg=5983  r5=5459  union=6356
启用集: defcfg=2253  r5=1832
仅 defconfig 启用 : 578
仅 r5 启用        : 157
合计不一致        : 735
```
以 `savedefconfig` 形式直接对比（499 行 vs 仓库 defconfig 1379 行，差异 38 KB）。

**无关噪声**（已排除，避免误报）：defconfig 生成后会补出大量 `# ... is not set`
与新增不适用符号（`CONFIG_AHCI_DM816=y`、`CONFIG_SOC_SAMA5D2=y`、`CONFIG_ARM_V7_PMU=y`
等 ARM 多平台样板），这些**不代表行为差异**。上面 735 项只统计**有效启用集**的差异。

**关键 21 项**（§B3 表格，逐条实测 `grep -E "^(# )?CONFIG_X( | is|=)"` 两文件对比）。

### A4. `z1_ramdisk_edit.py` 的修复确实作用于交付 ramdisk —— 确认 ✔

交付 boot.img 的 ramdisk = MTK `ROOTFS` blob（gzip 在 offset 512），
解出 cpio body 3,673,116 B。**两种独立方法**逐条列 mode：

**方法一（自写解析器）**：75 条记录（含 `TRAILER!!!`）：
```
records=75 consumed=3673028 leftover=88   ← 尾部 88 字节 \x00 填充
type=040000 (dir)     : 14
type=0100000 (regular): 42  （含 TRAILER 的 0x8000 = S_IFREG|0）
type=0120000 (symlink): 19
bad(no type bits)     : 0
```

**方法二（独立工具 `cpio -itv`）**：74 条（cpio 隐藏 TRAILER）
```
41 个 '-' (regular)  +  14 个 'd'  +  19 个 'l'
awk 'length($1)!=10' -> 空（无任何缺类型位）
```

**判定：0 条缺 `S_IFREG` ✔**，上一轮的 41/41 与我的 42 之差就是 TRAILER，
两者在"缺陷已修"上**一致**，且我比上一轮多了一条独立工具交叉验证。

**该修复的意义（实测确认，且上一轮判断正确）**：
对比回滚 boot（r41）的 ramdisk：
```
r41 records with NO type bits: 6    ← 缺陷态
r5c records with NO type bits: 0    ← 已修
```
缺陷态的 6 条正是被 `z1_ramdisk_edit.py` 重写过的：
```
TRAILER!!!          r41 mode=0000000  ->  r5c mode=0100000
z1_audio_probe.sh   0755  ->  0100755    ★ 会致 bootloop
z1_gps_prep.sh      0755  ->  0100755
z1_wifi_connect.sh  0755  ->  0100755
z1_wifi_pre.sh      0755  ->  0100755
z1_wpa_start.sh     0755  ->  0100755
```
`_fix_mode()` 的判据 `elif cur & 0o170000: return hdr`（已合规即不改）使**其余 69 条
逐字节保留** —— 与脚本注释一致。
> 其余 4 个（`z1_touch_load.sh` 等）在 r41 里本来就有 `S_IFREG`，
> **回滚 boot 并非必然 bootloop**，取决于具体 init.rc 是否 exec 到那 5 个脚本。
> 准确表述：**回滚 boot 带一个会致 bootloop 的潜在缺陷**（我未实测 r41 是否真挂）。

**`z1_gps_prep.sh` 含 `insmod` + `mknod` —— 确认 ✔**
```sh
MODDIR=/system/lib/modules/z1-radio
GPS_KO=$MODDIR/mtk_stp_gps_soc.ko
if [ -d /sys/module/mtk_stp_gps_soc ]; then ... elif [ ! -f "$GPS_KO" ]; then ... else insmod "$GPS_KO" ...
/system/bin/mknod /dev/stpgps c 191 0
```
（这是 r5c 相对 r41 的**唯一** ramdisk 差异，其余 74 条逐字节相同，见 C10。）

### A5. ramdisk 死文件扫描 —— **无死文件** ✔

ramdisk 内 13 个 `*.sh`，对 `init.z1.rc` / `init.z1.network.rc` / `init.rc` /
`ueventd*.rc` 及**全部其它文件全文**交叉引用：

```
z1_audio_probe.sh        <- init.z1.network.rc
z1_display_takeover.sh   <- init.z1.rc
z1_fbcon_takeover.sh     <- z1_display_takeover.sh   ★ 间接（见下）
z1_gps_prep.sh           <- init.z1.network.rc
z1_heartbeat.sh          <- init.z1.rc
z1_logpersist.sh         <- init.z1.rc
z1_pm_guard.sh           <- init.z1.rc
z1_radio_finish.sh       <- init.z1.rc, z1_wifi_pre.sh
z1_touch_load.sh         <- init.z1.rc
z1_wifi_connect.sh       <- z1_wifi_connect.sh(自身), z1_wpa_start.sh  ★ 间接
z1_wifi_pre.sh           <- init.z1.network.rc
z1_wifi_probe.sh         <- init.z1.rc
z1_wpa_start.sh          <- init.z1.network.rc, z1_wpa_start.sh
```
两个"无直接 rc 引用"的已定位到调用点：
```
z1_display_takeover.sh:14:  /system/bin/sh /z1_fbcon_takeover.sh
z1_wpa_start.sh:20:  # drive it — wpa_cli / z1_wifi_connect.sh will.
```
**其余**非 `.sh` 顶层文件（`z1_cst836u.ko`、`z1_gpio_hunt.ko`、
`wifi-nvram-WIFI.bin`、`regulatory.db`、`z1_bootdiag`、selinux contexts、`sepolicy`…）
亦逐一查到消费者：`regulatory.db`/`wifi-nvram-WIFI.bin` 由 `z1_radio_finish.sh` 落地到
`/system/etc/firmware`；`z1_gpio_hunt.ko`/`z1_cst836u.ko` 由 `z1_touch_load.sh` insmod。
**结论：0 个死文件** ✔（上一轮未做此项）

### B6. `load_audio.sh` charger 段

**(a) `load()` 第二参数语义 vs 模块名 —— 匹配 ✔**
```sh
load() {
    ko="$1"
    mod="${2:-$(echo "${1%.ko}" | tr '-' '_')}"    # 第二参数是 /sys/module/<name>
    if [ -d "/sys/module/$mod" ]; then ... fi
```
调用 `load z1_charger.ko z1_charger` ⇒ `mod=z1_charger`。
`modinfo -F name` = **`z1_charger`** ⇒ **完全匹配** ✔
（注：若省略第二参数，默认 `tr '-' '_'` 对 `z1_charger.ko` 也得 `z1_charger`，
所以显式传参是冗余但正确的。对 `mt6323-accdet.ko` 那种带连字符的，显式传参是必需的。）

**(b) `z1_charger.ko` 未定义符号全满足 ✔**
28 个未定义符号逐个对交付 `Module.symvers`（12,490 导出）：
```
__aeabi_unwind_cpp_pr0 cancel_delayed_work_sync delayed_work_timer_fn _dev_err
dev_err_probe dev_get_regmap _dev_info __devm_add_action devm_iio_channel_get
devm_kmalloc devm_power_supply_register devm_request_threaded_irq _dev_warn
iio_read_channel_processed mutex_init_generic mutex_lock mutex_unlock of_irq_get
of_property_read_u32_index __platform_driver_register platform_driver_unregister
power_supply_changed power_supply_get_drvdata queue_delayed_work_on regmap_read
__stack_chk_fail system_percpu_wq timer_init_key
-> 28/28 EXPORTED，0 缺失
```
`.ko alias = of:N*T*Cz1,mt6323-chargerC*` ✔（与 DT 节点 compatible 一致）

**(c) MFD cell 在交付内核里 —— 在 ✔（但这正是 B1 的雷）**
```
grep -a 'z1-charger'      r5c/vmlinux_raw -> 1 命中
grep -a 'z1,mt6323-charger' r5c/vmlinux_raw -> 1 命中
grep -a 'z1-charger'      r41/vmlinux_raw -> 0 命中   ← 回滚内核没有
grep -a 'z1-charger'      arch_r5/vmlinux_raw -> 0 命中 ← 归档 zImage_r5 也没有
```
源码侧：`drivers/mfd/mt6397-core.c` 的 `.name="z1-charger"` cell **无条件**
（前后 `#if/#ifdef` 实测为空），来自 `aa2089b54`。

**(d) DT 节点在交付 dtb 里 —— 在 ✔**（反编译 boot.img 尾部 dtb，20,679 B）
```
usb-charger {
    compatible = "z1,mt6323-charger";
    io-channels = <0x1b 0x04>;        // &mt6323_adc 通道 4 = VCDT
    io-channel-names = "vcdt";
    interrupt-parent = <0x1c>;         // &pmic
    interrupts = <0x0a 0x04>;          // IRQ 10 = CHRDET, LEVEL_HIGH
    status = "okay";
};
```
r41 的 dtb **无**此节点；`&pmic` 下另有 `power-off-time-sec = <0x05>`（两版都有）。

**(e) 镜像侧一致 ✔**
```
lib/modules/z1_charger.ko  10,148 B  vermagic=7.0.0-rc7-z1+  name=z1_charger
load_audio.sh  r5_final: 无 'load z1_charger.ko' 行
load_audio.sh  r5d_final: 有 'load z1_charger.ko z1_charger' + power_supply 取证段
```
⇒ B1 的雷是"**内核 cell 与 DT 节点未入库**"，**不是**"镜像里没接"。

### B7. GPS 全链

```
z1_gps_prep.sh → insmod /system/lib/modules/z1-radio/mtk_stp_gps_soc.ko
              → mknod /dev/stpgps c 191 0
              → start z1_mnld
```

**(a) `/system/bin/mknod` 在镜像里 —— 在 ✔（是 toybox 符号链接）**
```
bin/mknod -> toybox          （镜像 /bin/mknod，readlink = "toybox"）
bin/toybox  348,548 B 存在
readlink bin = "/system/bin" ⇒ /system/bin/mknod == 镜像 bin/mknod ✔
```
注：`/system/bin/z1_gps_prep.sh` 里写 `/system/bin/mknod`，而 `bin -> /system/bin`，
所以 `/system/bin/mknod` 解析为**镜像自身的 `bin/mknod`** ✔（`xbin/mknod` 不存在，
但脚本没用那个路径，无影响）。

**(b) `z1_mnld` service 定义 —— 在 ✔**（`init.z1.network.rc`）
```
service z1_mnld /vendor/bin/mnld
    class main
    user system
    group system inet media net_bt
    socket mnld stream 0660 system system
    disabled
    seclabel u:r:init:s0
on property:sys.z1.radio.modules_ready=1
    exec_background ... -- /system/bin/sh /z1_gps_prep.sh
```
`/vendor/bin/mnld` 在镜像里存在（34,685 B，0755）✔
**全镜像（system.img）grep `z1_mnld` 命中 0** ⇒ 无同名重复 service ✔

**(c) `mnld` NEEDED 全解析 ✔**
```
NEEDED: libxlog.so  libcutils.so  libc.so  libm.so  libnvram.so  libstdc++.so
解析检查（镜像内）:
  libxlog.so    -> /lib/libxlog.so (5,164 B) ✔ 与 /vendor/lib/libxlog.so (5,164 B) ✔ 两处都在
  libcutils.so  -> lib/ ✔
  libc.so       -> lib/ ✔
  libm.so       -> lib/ ✔
  libnvram.so   -> /vendor/lib/libnvram.so (46,200 B) ✔ 两版都在
  libstdc++.so  -> lib/ ✔
libxlog.so: ELF 32-bit ARM, SONAME=libxlog.so, 导出 __xlog_buf_printf ✔
```
**且 libxlog 确有消费者**（推翻 R5C_VERIFY §N2）：
```
mnld               dynsym U __xlog_buf_printf  且 DT_NEEDED libxlog.so
libmnlp_mt6572     dynsym U __xlog_buf_printf  且 DT_NEEDED libxlog.so
gps.default.so     dynsym U __xlog_buf_printf  且 DT_NEEDED libxlog.so
（对照 r5_final 的三件：NEEDED 里 libxlog 计数均为 0）
```

**(d) 硬编码路径 vs 镜像落点 ✔（路径对，但芯片选择有疑，见 B2）**
```
mnld 硬编码: /system/xbin/libmnlp_mt6572
镜像实际  : xbin/libmnlp_mt6572 存在（39,637 B，0755）
            xbin 是**真目录**（drwxr-xr-x），bin -> /system/bin
            ⇒ /system/xbin/libmnlp_mt6572 解析正确 ✔
mnld 另硬编码 /dev/stpgps ✔（与 mknod 目标一致）
             /data/misc/GPS_CHIP.cfg ✔（z1_gps_prep.sh 会建）
```
`mtk_stp_gps_soc.ko`：vermagic `7.0.0-rc7-z1+` ✔，`depends=mtk_stp_wmt`；
9 个 `mtk_wcn_*` 未定义符号**不由内核导出**，但由同目录
`mtk_stp_wmt_soc.ko` 的 `__ksymtab_*` 导出（逐个实测命中）✔
（`mtk_stp_wmt_soc.ko` 由 `z1_linux7_radio_init.sh` 在 `post-fs` 更早阶段 insmod，
先于 `sys.z1.radio.modules_ready=1` 触发的 `z1_gps_prep.sh`）✔

**(e) 镜像里 60 个 .ko 的 vermagic 全部 `7.0.0-rc7-z1+ SMP mod_unload ARMv7 p2v8` ✔**
（逐一 `modinfo -F vermagic`，含 7 个 z1-radio + 12 个 z1-network + 12 个音频 + charger）

### B8. codec → 库闭环 —— 实测：**无悬空声明** ✔（含 video，上一轮只核过 audio）

6 个 media_codecs*.xml 的全部 `<Include href=>` 与 `<MediaCodec name=>`：

**Include 闭环**（4 个 href 目标全部存在）✔
```
media_codecs.xml -> media_codecs_google_audio.xml          OK
                 -> media_codecs_google_telephony.xml      OK
                 -> media_codecs_google_video_le.xml       OK
media_codecs_performance.xml -> media_codecs_google_performance.xml  OK
```

**21 个 OMX.google.* 组件 vs 编译进 `libstagefright_omx.so` 的 SoftOMX 表**：
从 `.rodata` 直接读出组件↔库名对照表（AOSP 在表里存库名后缀，实际 dlopen
`libstagefright_soft_<后缀>.so`）：
```
OMX.google.aac.decoder        -> aacdec      ✔ 存在
OMX.google.xaac.decoder       -> xaacdec     ✗ 库不存在  ← 但 xml 未声明该组件，无害
OMX.google.aac.encoder        -> aacenc      ✔
OMX.google.amrnb.decoder      -> amrdec      ✔
OMX.google.amrnb.encoder      -> amrnbenc    ✔
OMX.google.amrwb.decoder      -> (amrdec)    ✔   ← 表里复用 amrdec
OMX.google.amrwb.encoder      -> amrwbenc    ✔
OMX.google.h264.decoder       -> avcdec      ✔
OMX.google.h264.encoder       -> avcenc      ✔
OMX.google.hevc.decoder       -> hevcdec     ✔
OMX.google.g711.alaw.decoder  -> g711dec     ✔
OMX.google.g711.mlaw.decoder  -> g711dec     ✔
OMX.google.mpeg2.decoder      -> mpeg2dec    ✔
OMX.google.h263.decoder       -> mpeg4dec    ✔   ★ 见下
OMX.google.h263.encoder       -> mpeg4enc    ✔   ★
OMX.google.mpeg4.decoder      -> mpeg4dec    ✔
OMX.google.mpeg4.encoder      -> mpeg4enc    ✔
OMX.google.mp3.decoder        -> mp3dec      ✔
OMX.google.vorbis.decoder     -> vorbisdec   ✔
OMX.google.opus.decoder       -> opusdec     ✔
OMX.google.vp8.decoder        -> vpxdec      ✔
OMX.google.vp9.decoder        -> vpxdec      ✔
OMX.google.vp8.encoder        -> vpxenc      ✔
OMX.google.vp9.encoder        -> vpxenc      ✔
OMX.google.raw.decoder        -> rawdec      ✔
OMX.google.flac.decoder       -> flacdec     ✔
OMX.google.flac.encoder       -> flacenc     ✔
OMX.google.gsm.decoder        -> gsmdec      ✔
```
**★ video 专项（本轮重点）**：`media_codecs_google_video_le.xml` 声明 6 decoder
（mpeg4/h263/h264/hevc/vp8/vp9）+ 4 encoder（h263/h264/mpeg4/vp8）。
- 镜像里**没有** `libstagefright_soft_h263dec.so` / `h263enc.so`
  ⇒ 一开始看像悬空。
- **但反汇编证明 h263 走 mpeg4 库**：表里 `OMX.google.h263.decoder` → `mpeg4dec`，
  `h263.encoder` → `mpeg4enc`，与 AOSP 上游第三方库行为一致（h263 与 mpeg4 part 2
  共用一个实现）。两个目标库**都在镜像里** ✔
- **判定：无悬空声明，h263 不是缺陷** ✔

`media_codecs_performance.xml`（858 B，r5d 版）：**`OMX.qcom` 命中 0** ✔
且 `vendor/etc/` 全目录 `OMX.qcom` 命中 **0** ✔（r5c 的 2,691 B 版有 6 处，已修）

### C9. e2fsck —— 两件都干净 ✔

```
e2fsck -fn out/z1_r5_system/system.img.r5d_final
  第1..5 遍，0 错误，Exit=0
  system: 2660/57600 文件（0.5% 不连续），196059/230400 块
  文件系统状态 clean，UUID 57204fc9-25b6-4b46-83e2-486ea4c74e51

e2fsck -fn out/z1_r5_system/system.img.r5_final   （回滚件，复检）
  第1..5 遍，0 错误
  system: 2650/57600 文件（0.5% 不连续），196038/230400 块
```
**挂载纪律**：挂载前 `/proc/mounts` 无相关条目；用完**全部卸载**，
`losetup -a` 已回到空；`grep -c '/home/lxj/claude/6572/out' /proc/mounts` = 0 ✔
（两次均 `-o loop,ro` 只读。）

### C10. 与 `r5_final` 递归 diff —— **0 删除**，启动关键件**逐字节未变** ✔

用 `debugfs -R "rdump /"` 把两个镜像各自解出（**不用 mount**，因此**不解析符号链接**，
无"跟到宿主绝对路径"的假阴性）：

```
LC_ALL=C diff -rq img_r5 img_r5d
```
**删除（Only in r5_final）：0** ✔
**修改：6 件**（4 件是声明的 r5d 改动 + 2 件未列入清单）
**新增：10 件**

| # | 路径 | r5_final | r5d_final | 类别 |
|---|---|---|---|---|
| 1 | `/vendor/bin/mnld` | 30,196 | **34,685** | 声明①，patched（+NEEDED libxlog）✔ |
| 2 | `/xbin/libmnlp_mt6572` | 34,572 | **39,637** | 声明②，patched ✔ |
| 3 | `/vendor/lib/hw/gps.default.so` | 50,584 | **65,453** | 声明③，patched ✔ |
| 4 | `/vendor/etc/media_codecs_performance.xml` | 2,691 | **858** | 声明④，`OMX.qcom` 0 处 ✔ |
| 5 | `/lib/modules/load_audio.sh` | — | — | 加 charger 加载段（未列入清单） |
| 6 | `/etc/permissions/z1-mainline-features.xml` | 511 | **651** | **未列入清单！删掉 4 个 `<unavailable-feature>`** |
| 7 | `/lib/libxlog.so` | 不存在 | 5,164 | 新增 |
| 8 | `/vendor/lib/libxlog.so` | 不存在 | 5,164 | 新增 |
| 9 | `/lib/modules/z1-radio/mtk_stp_gps_soc.ko` | 不存在 | 11,936 | 新增 |
| 10 | `/usr/keylayout/mt6779-keypad.kl` | 不存在 | 828 | 新增 |
| 11 | `/vendor/etc/media_codecs.xml` | 不存在 | 2,348 | 新增 |
| 12 | `/vendor/etc/media_codecs_google_audio.xml` | 不存在 | 4,830 | 新增 |
| 13 | `/vendor/etc/media_codecs_google_performance.xml` | 不存在 | 3,900 | 新增 |
| 14 | `/vendor/etc/media_codecs_google_telephony.xml` | 不存在 | 973 | 新增 |
| 15 | `/vendor/etc/media_codecs_google_video_le.xml` | 不存在 | 5,693 | 新增 |

**声明④需要修正**："`media_codecs_performance.xml` 从 2,691 清到 858"隐含"该文件已存在"，
但按基线 `r5_final`，**整个 media_codecs 家族 6 个文件都是 r5d 新增的**。
2,691→858 是**r5c→r5d 的差分**（r5c_final 里该文件是 2,691），
**不是 r5_final→r5d_final 的差分**。数字本身对（858 B ✔、`OMX.qcom` 0 处 ✔），
参照系标错了。清单以 `r5_final` 为回滚基线，这里应对齐。

**第 6 件（未列入清单）的内容**：
```diff
-<!-- Z1 Linux7 currently has no supported SIP, Wi-Fi, or Bluetooth stack. -->
+<!-- Z1 (mainline Linux 7 + LOS16): WiFi/BT HIDL stack is shipped ... -->
     <unavailable-feature name="android.software.sip" />
     <unavailable-feature name="android.software.sip.voip" />
-    <unavailable-feature name="android.hardware.wifi" />
-    <unavailable-feature name="android.hardware.wifi.direct" />
-    <unavailable-feature name="android.hardware.bluetooth" />
-    <unavailable-feature name="android.hardware.bluetooth_le" />
```
**这是一项有框架可见后果的行为改动**（把 WiFi/BT 从"不可用"改成"可用"），
与 R5C_VERIFY §N1 讨论的是同一件，但**改动清单里没有它**。
应当补进清单 —— 否则它既无变更记录，也无独立风险评估。

**启动关键件逐字节对比**（跨 r41 → r5c，即回滚 boot → 交付 boot）：
```
init.rc             e7b97d1a10b30c3f   SAME
fstab.z1            13b3ac6a22ecd5b3   SAME
default.prop        19b55b90e0db65b9   SAME
ueventd.z1.rc       fab742fb7c182da4   SAME
init.z1.rc          35169d3a784738e1   SAME
init.z1.network.rc  ee598ea5f742c3c3   SAME
init.zygote32.rc    2c49f23834f32566   SAME
init.environ.rc     475d3a9df35bb935   SAME
ueventd.rc          d40b80e5c1063191   SAME
```
`build.prop` / `vendor/build.prop` / `product/build.prop` 在 r5_final↔r5d_final **未变**
（需 sudo 读，已读）：
```
vendor/build.prop    d0b4541d126ee3179e816eba  SAME
product/build.prop   3cc9aae9660236e0f00f579d  SAME
```
**整个 ramdisk 只有 1 处变化**：`z1_gps_prep.sh`（加 insmod+mknod），其余 74 条逐字节相同 ✔

### C11. boot.img 结构 —— 全部算术自洽 ✔

```
magic            "ANDROID!"                                    ✔
page_size        2,048                                         ✔
kernel_addr      0x10008000   ramdisk_addr 0x11000000           ✔
tags_addr        0x10000100   second_addr  0x10f00000           ✔
kernel_size      4,228,295
  hdr.size(MTK KERNEL 头 @ko+4) = 4,227,783 = kernel_size-512    ✔
  kernel_size-512 = 4,227,783 = zImage 文件大小（含 appended dtb）✔
  MTK 头: magic 0x58881688, name "KERNEL"                        ✔
ramdisk_size     1,977,962
  hdr.size(MTK ROOTFS 头) = 1,977,450 = ramdisk_size-512         ✔
  MTK 头: name "ROOTFS"                                          ✔
dt_size          0  （dtb 走 appended 方式，非独立 dt 区 —— 与本项目历史结论一致）✔
文件总长          6,209,536 = 2048 + 4,229,120 + 1,978,368     （余量 0，"delta 0"）✔
≤ 6 MiB 分区     6,209,536 ≤ 6,291,456 = 5.9219 MiB             ✔（余量 81,920 B）
cmdline          与 r41 **逐字节一致**（无新增参数）             ✔
id[0..7]         全 0                                          ✔
```

**appended dtb 在 zImage 尾部 ✔**
```
zImage 大小 4,227,783；dtb magic 'd00dfeed' @ 4,207,104，尾部 20,679 B
```
**dtb `totalsize` 自洽 ✔**
```
totalsize = 20,679 = 实际尾部长度        ✔
ver=17 lastcomp=16 bootcpu=0 off_struct=56 off_strings=18728 off_rsv=40 szstr=1951 szrsv=18672 ✔
dtc 反编译: 0 error，3 warning（与 r41 的回滚 dtb **完全相同**）✔
```

**`initrd-end == 0x84100000 + (ramdisk_size - 512)` ✔**
```
0x84100000 + (1,977,962 - 512) = 0x84100000 + 0x1E2C6A = 0x842E2C6A
dtb chosen 的 linux,initrd-end = 0x842e2c6a                    ✔ 精确匹配
dtb chosen 的 linux,initrd-start = 0x84100000                  ✔
```

**reserved-memory 无非法重叠 ✔**（与上一轮结论一致，我独立重算）
```
connsys@be000000     [0xbe000000,0xbe100000)   1 MB
ramoops@be200000     [0xbe200000,0xbe300000)   1 MB   record 0x20000 + console 0x40000 + pmsg 0x20000 = 0x80000 ≤ 0x100000 ✔
framebuffer@bf400000 [0xbf400000,0xc0000000)  12 MB
simple-framebuffer   [0xbfd00000,0xbfd28000)  240x320@r5g6b5 stride 0x200 ✔ 落在上面那块内，**正是设计意图**
default-pool         alloc-ranges [0x90000000,0xa0000000)  shared-dma-pool，reusable ✔
initrd               [0x84100000,0x842e2e6a)
两两互斥区（connsys/ramoops/framebuffer）之间**无重叠** ✔
（与 memory@80000000 的"重叠"是 no-map 保留区的正常语义，非缺陷）
```

---

## 五、刷前建议

1. **`tools/flash_with_retry.sh` 必须带 `Z1_VERIFY=0`**（CLAUDE.md §4a / MEMORY 已记录
   verify 误报会回滚）。本条我**无法独立复测**（未读该脚本的行为、未碰设备），
   按已知纪律重申。
2. **不要期望 dmesg 出现 `PMIC write path: INT_CON0(...) readback`** ——
   交付内核是非致命但**无诊断**的版本（§A2(c)）。电源键异常时靠
   `/proc/interrupts` + `evtest` 取证，别等那行日志。
3. **charger 相关的三条链在交付产物里是齐的**（内核 cell ✔ / DT 节点 ✔ / .ko+脚本 ✔）——
   刷后若 `power_supply` 仍不出现，问题在 pwrap 写路径或 `RGS_CHRDET` 位，
   不在"没接上"。可用 `cat /sys/class/power_supply/*/online` 判。
4. **GPS 是串行长链**，任一环断则 `/dev/stpgps` 不出。刷后看 dmesg 的
   `<6>[z1gps]` / `<6>[z1radio]` 前缀定位。`/data/misc/GPS_CHIP.cfg` 的键名问题
   （§B2）只有上板才能定论。
5. **本次刷机只验 boot+system**。B1（重编即 bootloop）不影响这次刷，
   但**下一次要重编内核前必须先修**：仓库 `z1_defconfig` 需从 `.config_r5`
   重新 `savedefconfig` 生成（并补 `CONFIG_MTK_MT6572_CONNSYS=y`、
   `MMC_BLOCK`、`WATCHDOG`、`SECURITY_SELINUX`、`USB_CONFIGFS`、`KERNEL_XZ`、
   `LOCALVERSION="-z1"`/`LOCALVERSION_AUTO=n`、`POWER_SUPPLY=y`…），
   并把 `aa2089b54` 的 charger cell 与 dts 节点纳入可复现的构建入口。

---

## 六、未确定项（缺证据，不猜）

| # | 事项 | 缺什么证据 |
|---|---|---|
| U1 | `mt6323-pmic-keys` 是否真的报出 KEY_POWER | 需上板 `evtest` / `/proc/interrupts` |
| U2 | mnld 能否从 `/data/misc/GPS_CHIP.cfg` 得到 chip id，取到的值 | 需对 mnld（stripped）`.rodata` @0x49c9 引用做完整反汇编；本轮只证明"字面量 `MTK_GPS_CHIP` 不在二进制里" |
| U3 | `load_audio.sh` 是 init 同波次执行、还是竞态执行 | 需上板看 kmsg 里 `<6>[Z1Audio]` 是否出现 |
| U4 | 回滚 boot（r41）的 6 条无类型位是否真的导致过 bootloop | 需上板实测；本轮只证明"缺陷存在" |
| U5 | `CONFIG_MTK_PMIC_WRAP_Z1_WRITE_DENY` 是否导致 PMIC 实际写入被门控 | 需上板读 `INT_CON0` 回读值（而交付内核没有这行诊断） |
| U6 | 交付内核是否有 `apply_r5b_*` 脚本之外的其它未入库改动 | 本轮只覆盖了"能通过串/符号差集发现"的类型（charger 就是靠这个抓到的）。`.text` 级的静默差异无法穷举 |

**U6 值得强调**：我是通过"r5c 比 r41 多出 `z1-charger` 串"才发现 B1 的。
如果某处未入库改动**没有引入新字符串**（纯逻辑/常量修改），
现有方法**看不见**。因此"交付内核 == 仓库 HEAD 编译结果"这一命题，
本轮**未能证真**——证据方向全部指向"不相等"。

---

**本轮未做任何修改。** 所有操作只读：解包、反编译、grep、`nm`/`readelf`/`modinfo`、
`debugfs rdump`、`e2fsck -fn`、只读 `mount -o loop,ro`（已全部卸载）。
唯一写入是 `out/R5D_VERIFY2.md`（本报告）。

`fork_linux` 工作树 **git status 全程为空**，`.config` sha256 前后一致
（`7afc2c6471bbbfc2e9e165c0f3af71a9e8541819f2907030b976e299b2e010fe`）。
`make savedefconfig` 一度在树根生成 `defconfig`（已被 `.gitignore:174` 忽略、未入库），
**已 `rm` 删除**，树已复原为干净态。
