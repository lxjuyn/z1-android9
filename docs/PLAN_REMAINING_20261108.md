# Z1 收尾计划 — 2026-11-08（今晚不刷写，全部离线推进）

> 每条：现象 → 证据 → 根因 → 动作 → 验收判据 → 风险。没有证据的不进"已修"。

---

## 0. 今晚产出的三对镜像（按刷写顺序）

| # | 镜像 | sha256 | 作用 |
|---|---|---|---|
| boot | `out/z1_fix3r_boot/boot.img` | `fd3b4298c8d4b7a38390811e90754ec55fa5d483d381edc5241be43daf91d2d7` | fix3q + **PMIC 中断真正修好** |
| system 基线 | `out/z1_v20_audio_system/system_v20.img` | `3e57dd0c352a7f85a168d71c5f88912405020aaffc481192e65dfa8a5e91ef7f` | v17 + 触摸/音频模块换成树内构建 |
| system GPU | `out/z1_v22_gpu24_gate/system_v22.img` | `388f8a956467cab02becedddd1a6675e361a14f3a288c11acea560775f6219d1` | v20 + Mesa 24.0.9 + **自动回退 gate** |
| 回滚 boot | `out/z1_fix3p_boot/boot.img` | `db98a5e5fd7cf1efed09067c91d3d81d9207b199ffc01731aee7544d85444983` | |

刷写纪律：boot(6MB) 用 `tools/flash_with_retry.sh`；system(943MB) 用
`tools/z1_flash_chunked.sh`（48MB 分块 + 断点续写）。每次只改一个变量。

---

## 1. P0 — 已经闭合并有硬证据的

### 1.1 PMIC 父中断（电源键/耳机/充电中断/RTC 闹钟）—— 根因找到并修好

**现象**：`mt6572-pinctrl: Can not find pin` → `request_irq(37) -EINVAL`。

**根因（这次是完整的，三个环）**：
1. `of_irq_parse_one()` **先解析 `interrupts-extended`**，只有它不存在才回退到
   `interrupt-parent`+`interrupts`。所以 `mt6323.dtsi` 的 `<150 …>` 和我 11-05 加的
   `&pmic { interrupts = <16 …> }` **都是死代码** —— 这正好解释了"改了 150→16 之后
   irq 号仍是 37、报错一字不差"。
2. 板上真正生效的是 z1.dts 里早就存在的 `interrupts-extended = <&pio 7 IRQ_TYPE_LEVEL_HIGH>`，
   而 **7 是 GPIO 号**；pio 的中断域是 MT6572 EINT 控制器（`ap_num=26`，hwirq 是 **EINT 号**），
   `mtk_xt_get_gpio_n(7)` 在引脚表里找不到 `eint_n==7` 的引脚 → `EINT_NA` → `-EINVAL`。
3. 正确值是 **EINT 16**（GPIO7→`PMIC_EINT`，`pinctrl-mtk-mt6572.h: MTK_EINT_FUNCTION(1,16)`），
   中断线开漏低有效 ⇒ `LEVEL_LOW`；引脚改 `bias-pull-up`（原来的 `pull-down` 会把低有效线常拉低 ⇒ 中断风暴）。

**动作**：`interrupts-extended = <&pio 16 IRQ_TYPE_LEVEL_LOW>;` → `out/z1_fix3r_boot/boot.img`。
打包后从 boot.img 反解确认 `interrupts-extended = <0x13 0x10 0x08>`。

**验收**：`mt6397-pmic` 不再打印 `failed to register irq`；`IRQ controller enabled`；
按电源键 `getevent` 有 `EV_KEY code 116`。
若出现 `PMIC IRQ entered 64 times with no status bit set; masking the parent IRQ`
⇒ 极性猜错（改 `LEVEL_HIGH`），有风暴保护不会挂死。

**风险**：低。最坏退化成"中断不工作"（即现状）。

### 1.2 镜像里的模块是旧构建（系统性缺陷）

- **触摸**：`/bin/z1_touch_load.sh` 加载的是 `/system/lib/modules/z1_cst836u.ko`，
  **不是 ramdisk 里那份** —— 11-05 我只改了 ramdisk，等于没修。镜像里仍是 `e9439f4e`（缺 `INPUT_PROP_DIRECT`）。
- **音频**：5 个模块全部是旧构建，`snd-soc-mt6323.ko` 里 MIC 相关字符串 **0 / 树内 6**，
  即镜像音频栈**不含采集路径**，录音不可能工作。
- **动作**：v20 把这 6 个模块全部换成树内构建，已从镜像反解核对 md5。
- **验收**：`dumpsys input` 里触摸 `Sources: TOUCHSCREEN`；录音能出数据。
- **规矩**：说"模块修好了"之前，必须确认**运行时实际加载的是哪个路径下的哪一份**。

## 2. P0 — 仍未闭合

### 2.1 `/data` 运行时被写坏 → 开不了机

**现象**：zygote 每 5 s SIGABRT（258 次/206 条），停在二屏。

**证据链**：`EXT4-fs error (mmcblk0p6): ext4_free_inode:354: bit already cleared for inode 7743`
→ `Aborting journal` → `Remounting filesystem read-only` → `app_process` chown
`/data/dalvik-cache/<abi>` 得 EROFS → `LOG_ALWAYS_FATAL`。

**一个重要的纠正**：我原先以为"加 fsck 标志"能解决，**不行**。取证结果：
- 那次开机用的 fix3p **fstab 里没有 `check`**，e2fsck 照样跑了 —— 因为超级块带 `RECOVER`；
  fs_mgr 只要看到"未干净卸载"就会跑 `e2fsck -f -y`（`fs_mgr.cpp:440-442`）。
- 更关键：fsck 在 t≈15 s 报"干净"，损坏发生在 **t≈1036 s 运行时**。**开机 fsck 防不了运行时损坏。**

**所以真正的选项只有三条**：
1. **事后自愈**：下次开机 fs_mgr 已经会自动 fsck（本次已证），所以"重启一次多半能恢复"。
   缺的是**别让它卡在二屏 30 分钟** —— 建议加一个看门狗：`/data` 变成只读就 `reboot`
   （带次数上限，避免无限重启）。
2. **换掉损坏源**：`/data` 加 `metadata_csum`（需重格式化，数据会丢）——能让写坏在写入时就暴露，
   但同样会 remount-ro，不解决"卡住"。
3. **降级启动**：把 /data 从 fstab 摘掉 → `ro.crypto.state=unsupported` → zygote 能起但 /data 易失
   （`z1a9_gpt/scripts/bootrobust/z1_fsck_guard.sh --degrade-fstab`，已实现未验证）。

**今晚的动作**：保留 fix3q/fix3r 的 `wait,check`（无害，且 guard 没跑成时是兜底）；
把 guard 脚本入库；**看门狗方案留待决策**（它需要改 ramdisk 且要防重启风暴）。

**验收**：开机日志出现 `[z1fsck] ... RESULT=` 一行；/data 只读时能自动重启而不是卡死。

### 2.2 GPU 彩虹条

**现象**：`gp error irq state=4000 status=3`（`LIMA_GP_IRQ_PLB_INVALID_CMD`）20 次。
**已推翻**："416 MHz 无 VGPU 电压"（lima 用 optional regulator，MT6572 无独立 VGPU 轨；
同一内核 7-17 离屏测试画出过正确三角形）。
**假设**：Mesa 19.1.8 用户态喂了非法命令流（其 `lima_drm.h` 缺 `LIMA_BO_FLAG_HEAP`；
仓库 24.0.9 与内核 UAPI 逐字节一致）。
**动作**：v22 = v20 + Mesa 24.0.9 + `libglapi` 同换 + **自动回退 gate**
（tmpfs 盖住 `/vendor/lib/egl` 改变目录列举；`/data/local/tmp/z1_gpu_disable` 可禁用；
watcher 在 SF 反复重启时自动置标记并重启）。已确认 `CONFIG_OVERLAY_FS` 未开、bind-mount 会 ELOOP，
所以用 tmpfs 覆盖是唯一稳妥机制。
**验收**：`gp error irq` 消失 + 屏幕正常；若仍彩虹但 `gp error irq` 消失 ⇒ 问题转到合成路径。
**风险**：mesa 仍然没有 SwiftShader 运行时回退（gate 是**下次开机**生效），首次验证失败 = 一次重刷/重启。

## 3. P1 — 需要决策或更大工程量

| 项 | 现状 | 下一步 |
|---|---|---|
| 开机极慢（10~30 min） | 无 boot image，68 次 dex2oat，1044 s 未完成 | 等 `boot-speed` agent：SIGILL 根因 + `pm.dexopt.first-boot` 语义 |
| 蓝牙 | 三模块/节点/HAL 都在，`BT_open` 命中 0 | 等 `bt-gps`：框架为何不开蓝牙的可验证判据 |
| GPS | mnld 40 ms 退出，卡 `/sys/class/gpsdrv/gps/status` | 等 `bt-gps`：驱动 or patch mnld |
| 关屏/亮度 | HWC `blank` 空函数、simplefb 无 `fb_blank`；light HAL 已进 manifest（未验证） | 等 `power-ux` |
| cpufreq | 缺，thermal 中间档位无效 | 等 `power-ux` |
| WiFi 端到端 | HIDL 栈完整、`wlan0 scanning` 已见 | 未排期 |
| adbd / USB gadget | 无 | 未排期（长期靠串口） |

## 4. NOT VERIFIED
- fix3r 的 PMIC 中断（极性/编号）未上板。
- v20 的触摸/录音、v22 的 GPU 与 gate，全部未上板。
- `/data` 损坏的**根因**（存储层写坏 vs ext4 竞态）未定；`metadata_csum` 方案未验证。
- 蓝牙/GPS/关屏/cpufreq 的任何"已修"结论都还不存在，只有分析。

---

# 5. 四个 agent 的结论并入（2026-11-08 凌晨）

## 5.1 PMIC 中断（agent: pmic-irq）— 与我自己的结论一致，并补了两条

- 修前/修后判读：`hwirq=7 / gpio_n=65535` → `hwirq=16 / gpio_n=7`，"Can not find pin" 消失。
- 修复后 Linux irq 号**预计是 46**（域基址 30+16），不是 37。
- **不能改 `mt6323.dtsi`**：`mt7623n-bananapi-bpi-r2.dts` 也 include 它，那边 `ap_num=169`，
  `<150>` 是合法的。只能在板级 dts 覆盖 —— 我们就是这么做的。
- **同类 bug 还在另一块板**：`mt6572-s6.dts:405`。
- 留了一份调试补丁（两处 `dev_info`），修前/修后各打一行，上板时可用来确认。

## 5.2 `/data` 健壮性（agent: boot-robust）

- 确认 `check` 标志**不是**这次故障的解法（详见 §2.1）。
- 交付 `scripts/bootrobust/z1_fsck_guard.sh`（12 KB，24 项主机测试通过）：
  `--pre` 在 mount_all 前按超级块状态决定是否 `e2fsck -f -y`，`--post` 检测 /data 是否可写，
  结论一行写 kmsg；支持 `--degrade-fstab` 摘掉 /data 让系统以易失模式起来。
- **代价要说清楚**：跑 `--pre` 需要改 `on fs`（手工挂 /system → exec → umount → mount_all），
  属于对启动最早期流程的改动，风险不低。**建议先只上 `--post`**（零风险、只加日志），
  等确认 `/data` 真的会反复坏，再上全量自愈。

## 5.3 GPU gate（agent: gpu-gate）— 机制确认

- 三选一里只有 **tmpfs 覆盖目录**可靠；bind-mount 会被 init 的 shared propagation 自遮蔽（本机复现 ELOOP），
  overlayfs 内核没编。
- 关键前提：`init` 在 `mount_all` 阶段会**重新解析 `/system/etc/init`**，所以 gate 的 rc 放 system 侧
  就能在 surfaceflinger（18.57 s）之前生效，**不用动 boot.img**。
- fail-open：三件套预检不过就不动，宁可不隐藏也不能把目录弄空。

## 5.4 关屏 / 亮度（agent: power-ux）— 有一个"原来如此"

**关屏链路本来就通**：`DisplayPowerController` → `LightsService` → light HAL →
`leds-mt6323.c` 的 `brightness==0 → mt6323_led_hw_off()`（真关 ISINK 时钟）。
**不需要 HWC 参与。**

那为什么用户从没见过关屏？因为 `z1_pm_guard.sh:23-24` 把
`screen_off_timeout` 设成 **7200000（2 小时）**、`stay_on_while_plugged_in=7`（插 USB 常亮）。
**这是策略问题，不是能力问题。**

顺带发现一个真 bug（低亮度黑洞）：驱动 `mt6323_spec.max_brightness = 6`（`leds-mt6323.c:680`），
而 light HAL 用整数缩放 `b*6/255`，于是**亮度 1–42 全部映射成 0** —— 滑到最低约 17% 时
框架认为亮屏、实际背光全灭。修法二选一：把框架最小亮度抬到 43，或给 HAL 打向上取整补丁。
**未修**，需先上板确认观感。

HWC 的 `hwc_blank()` 补丁已用 NDK r26d 编出 `hwcomposer.z1.so`（原版与补丁版都能编过），
但如果 light 链路可用，这个补丁不是必需的。

cpufreq：`mediatek-cpufreq.c` 有 mt6572 表，但驱动用 `regulator_get_optional(cpu,'proc')`，
缺 `proc-supply` 就 `-ENODEV` 不 probe —— 这是"没有 cpufreq"的真正原因。dts 补两行即可（补丁已入库）。

## 5.5 仍未回来的
- `boot-speed`（开机 10–30 分钟根因）、`bt-gps`（蓝牙/GPS）。
