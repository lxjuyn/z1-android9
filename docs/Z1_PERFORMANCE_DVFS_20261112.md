# Z1 性能优化：CPU/GPU 动态调频 + 流畅度（2026-11-12 上板实测）

> 本文保留 2026-11-12 性能候选的历史观测，不代表 11 月 13 日恢复候选已经上板。
> 当前恢复顺序和验收边界见 [恢复计划](Z1_RECOVERY_PLAN_20261112.md)。

## 结论摘要

| 项 | 改前 | 改后 | 证据 |
|---|---|---|---|
| CPU 频率 | **1001 MHz 固定** | **schedutil 动态 806–1300 MHz** | `armpll` 1001000000 → 1300000000 |
| CPU 驱动 | 无（`/sys/.../cpufreq` 不存在） | `mediatek-cpufreq` | `avail=598000 806000 1001000 1209000 1300000` |
| GPU 调频 | 无 | `devfreq` 26/416 MHz | `/sys/class/devfreq/13010000.gpu` |
| **滑动卡顿率** | **79/354 = 22.3%**（ondemand + GPU416） | **57/349 = 16.3%**（schedutil+806 地板） | `dumpsys gfxinfo` 同一套 15 次自动滑动 |
| UI 线程慢 | 18 次 | **3 次** | 同上 |
| SystemUI ANR | 频繁 | **0** | `/d/wakeup_sources` 兜底修复 |

## 一、CPU DVFS：为什么必须用 mediatek-cpufreq，不能用 cpufreq-dt

CPU 的时钟源头就是 `armpll` 本身：

```
cpu_mux (infracfg 0x0 bits[2:3]) 父: clk26m / armpll / univpll / mpll_d2
```

`cpufreq-dt` 直接 `clk_set_rate(cpu_mux, …)` → 透传到 `armpll` 的 PLL 寄存器 →
**PLL 重锁期间 CPU 停止取指 → 死机**。上板实测：

```
rcu: INFO: rcu_sched detected stalls on CPUs/tasks:
rcu: 0-...0: (0 ticks this GP)
```

正解是 `mediatek-cpufreq`：它先把 CPU 时钟 reparent 到 `intermediate`
（`CLK_TOP_MPLL_D2`），改完 PLL 再切回 armpll。11-08 的提交早已把 MT6572
表加进该驱动（`{ .compatible = "mediatek,mt6572", .data = &mt6572_platform_data }`），
**唯一缺的是 `CONFIG_ARM_MEDIATEK_CPUFREQ` 没开**。

另一处关键：驱动用 `regulator_get_optional(cpu_dev, "proc")`，即 DTS 必须是
**`proc-supply`**（不是 `cpu-supply`，那是 cpufreq-dt 的 `find_supply_name()` 认的），
否则 `-ENODEV` 直接不 probe。

OPP 电压必须 ≤ `mt6572_platform_data.proc_max_volt = 1250000`（1.25 V）。
早期把 1404/1508 MHz 写成 1.30/1.35 V，ondemand 一上来就：

```
regulator_check_voltage from regulator_set_voltage_unlocked
regulator_set_voltage from mtk_cpufreq_set_target
Code: ... (e7f001f2)          ← BUG()
Kernel panic - not syncing: Fatal exception
```

最终 OPP：598/806/1001/1209/1300 MHz，**全部 1.15 V**（与 11-08 的“低风险”结论一致）。

## 二、调频策略：schedutil + 806 MHz 地板

2 核 Cortex-A7 承受不了 ondemand 的轮询延迟（采样周期几十毫秒），滑动起始阶段
CPU 还在低频。同等工作量 A/B：

| governor | 总帧 | 卡顿帧 | 90th |
|---|---|---|---|
| ondemand（598M 地板） | 354 | **79 (22.3%)** | 19 ms |
| schedutil（806M 地板） | 349 | **57 (16.3%)** | 19 ms |

`schedutil` 跑在调度器路径里（不是轮询），配合抬高地板后接地更快。
仍然保持动态：空闲时降到 806 MHz。

## 三、GPU：当前保留 26/416 MHz 配置，491 MHz 单次测试失败

时钟树里 `mfg_sel` 只能二选一（`upll_d3`=416 MHz / `mfg_mux_sel`），而
`mfg_mux_sel` 的 8 个父时钟里有 `mfg_pre_491m`=491.52 MHz 等更高档。
加了 491.52 MHz OPP 并让 `performance` 顶到该档后实测：

```
Total frames rendered: 5    Janky frames: 5 (100.00%)
50th percentile: 300ms      Number Missed Vsync: 4
```

该次 491.52 MHz 配置的帧耗时显著增加，但这些帧统计本身不能确认 GPU 超时根因，
也不能证明硅片的物理上限。该 OPP 已从 DTS 删除，当前保留既有可用的 26/416 MHz
配置。恢复阶段不扩展 OPP，性能验收仍需在恢复后的相同启动状态和工作负载下重测。

## 四、流畅度的其它两处（已写入开机脚本）

1. **`/d` + `wakeup_sources` 兜底**：`BatteryStatsImpl` 在
   `/proc/wakelocks` 与 `/d/wakeup_sources` 都不存在时调用 `Slog.wtf`，
   userdebug 上**直接杀死进程** → system_server 死 → SystemUI ANR/重启。
   `/d` 是 init 挂载 debugfs 之后才建的，system_server 开机早期会撞上空窗。
   开机脚本提前 `ln -s /sys/kernel/debug /d` 并 `chmod 0644`。
2. **停掉诊断中继**：实测 logcat 中继 **323 行/分钟** 送往串口，外加每周期
   `dumpsys window/activity/ps/meminfo` 扫描。开机脚本里停掉
   `z1_logcat_relay/z1_acm_log/z1_logpersist/z1_heartbeat/z1_sf_dump/z1_klog_relay`。
3. **动画时长 0.5×**（`window/transition/animator_animation_scale`）。

## 五、内存：实测不是瓶颈

```
MemTotal 1010528 kB / MemFree 498504 kB / MemAvailable 852992 kB
zram 262144 kB, Used = 0
```

该采样时刻可用内存约 852 MB，zram 未使用，未见内存容量不足的直接证据。
单个内存快照不能排除 GC、内存带宽或其它时段的压力，也不足以把卡顿归为单一根因。

## 六、产物

| 文件 | sha256（前 32） | 说明 |
|---|---|---|
| `out/boot_final_perf.img` | 见提交时输出 | mediatek-cpufreq + schedutil + GPU devfreq + 兜底修复 |

## 七、踩坑记录（避免重犯）

1. **MTK boot.img 的 kernel 段要求 512 B MTK 头**（`88 16 88 58` + size + `KERNEL`），
   且 Android 头的 `kernel_size` = 载荷 + 512。少这 512 B 时 LK 报
   `KERNEL partition magic error` 并无限重启。
2. **`z1_pack_ok.py` 曾硬编码 ramdisk 路径**，导致改了脚本却打包了旧 ramdisk
   （镜像 sha 与上一版相同）——打包后必须反解确认新内容在里面。
3. **两个 `z1_flash_chunked.sh` 并发跑会交叉写同一个分区**（状态里出现“块=4/2”），
   必须串行。
4. 用 Python 正则删 DTS 块极易漏掉收尾 `};`，删完必须 `make …dtb` 验证。
