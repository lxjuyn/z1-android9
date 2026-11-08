# v31：把 MT6323 充电驱动从模块改成内建 —— 治"插充电器就无限重启"

日期：2026-11-08

## 现象（已上板实测）

插着充电器开机：每轮 9–16 秒无提示复位（无 panic / 无 oops），LK 每轮都打印
`[PLFM] USB/charger boot!` + `[kernel_charging_boot] Kernel Power Off Charging with Charger/Usb`，
`boot_reason=1`。拔掉充电器：`boot_reason=0`，循环立即消失。

对照日志：`mainline_recon/z1_boot_mainline_20261108_135205.log`
（16 轮 banner，33 次 kernel_charging_boot；拔线后 boot_reason=0 并一路开到
`sys.boot_completed=1`@239s）。

## 根因

1. LK 在"充电启动"路径里会给 MT6323 的**充电看门狗 CHRWDT 上膛，超时 4 秒**
   （驱动注释 `CHR_CON13 TD[3:0]=0 → 4 s`，vendor `charging_hw_init()` 同样如此）。
2. 我们的 `z1_charger` 之前是**模块**，由 `/system/lib/modules` 在第 **22 秒**才 insmod：
   ```
   [21.91] z1-charger ... CHRDET irq 74 armed
   [22.05] [Z1Audio] insmod OK z1_charger.ko
   ```
3. 9–16 秒的复位点**永远早于 22 秒**，所以从来没人在窗口内清 CHRWDT 标志、喂狗
   → PMIC 复位 → 又走一遍 LK 充电启动 → 无限循环。循环里充电一次都没真正开起来。

## 修法

```
CONFIG_CHARGER_Z1_MT6323=m  →  y
```

内建后 probe 在 ~1 秒（pwrap 0.35 s 之后），hw_init 立刻清 CHRWDT 标志，
并且驱动自带的 2 秒 poll work 持续喂狗 —— 从根上消掉这个窗口。
顺带充电从第 1 秒开始，而不是第 22 秒。

**同时踩到并避开的一个坑**：树里的 `.config` 还留着 11-08 早上试 cpufreq 的
`CONFIG_ARM_MEDIATEK_CPUFREQ=y`（fix3u/kernel #116 那版就是它导致 4–12 秒复位）。
因为最早那版 v31 就是拿这份 .config 编的，所以**必须回滚 cpufreq**：

```
cp bridge_backups/dot.config_pre_cpufreq_20261108_095518.bak fork_linux/.config
# 再把 CONFIG_CHARGER_Z1_MT6323 改成 y
```

最终 v31b 内核核对：
```
$ arm-linux-gnueabihf-nm vmlinux | grep -c mediatek_cpufreq   →  0   （cpufreq 已关）
$ arm-linux-gnueabihf-nm vmlinux | grep -c z1_charger_probe   →  1   （内建已进）
```

## 产物

`out/z1_v31b_chargerbuiltin/boot.img`（6,223,872 B，sha256 `8effd339…`）
- ramdisk 与 fix3r 逐字节相同（解压 md5 `f1afc148d6bb0510b840aa9323b1c96e`）
- `kernel_size == MTK size + 512` ✅
- `linux,initrd-end = 0x842e4330` ≥ 0x842e4248 ✅

刷写与校验：
```
mtk.py wo 0x2980000 0x5EF800 out/z1_v31b_chargerbuiltin/boot.img   → Wrote ... ✅
mtk.py ro 0x2980000 0x5EF800 /tmp/boot_v31b_rb.bin                 → cmp 逐字节一致 ✅
```
（刷写必须 root：`HANDOFF_20261023.md:182`，本机无 MTK udev 规则；
且 mtkclient 必须在**设备未连接时启动**、再插线，否则报
`DaHandler - Please disconnect, start mtkclient and reconnect.`）

## 待验证（下一步）

插着充电器开机，预期：
- 第 1 秒左右出现 `z1-charger ... CHRDET irq armed` / `charging ENABLED`
- 一路开过 22 秒不再复位，直到 `sys.boot_completed=1`
