# Z1 cpufreq / CPU_THERMAL 状态与缺口（2026-10-28）

## 现象（板载实证）
`/sys/devices/system/cpu/cpu0/cpufreq/stats/time_in_state` 不存在，
板载 log 里 `Failed to read cpu-freq: ... (No such file or directory)` 出现 **73 次**。

## 根因（实测）
1. **交付内核没编 cpufreq**：R5 构建配置 `.config_r5` 里
   - `# CONFIG_CPUFREQ_DT is not set`
   - `# CONFIG_ARM_MEDIATEK_CPUFREQ is not set`
   - `# CONFIG_CPU_THERMAL is not set`
   所以内核 Image 里 `cpu-thermal` 串 0 命中（`cpufreq-dt` 有 2 处是被平台代码引用，
   驱动本体没编进去）。

2. **defconfig 与实际交付内核不一致（可复现性 bug）**：
   `arch/arm/configs/z1_defconfig` 里写的是
   - `CONFIG_CPUFREQ_DT=y` (L117)
   - `CONFIG_CPU_THERMAL=y` (L542)
   用 `make z1_defconfig` 生成，这两项确实是 `=y`；
   但交付的内核是**用另一份 .config 编的**，两项为 `n`。
   → 即"照仓库 defconfig 重编"与"实际刷进机器的内核"**行为不同**：
   重编版会有 cpufreq、交付版没有。

3. dtb 侧有 `opp-table` + `operating-points`（Image 里 3 命中），
   说明 DT 数据在，只是**没有驱动消费它**。

## 影响
- 无 cpufreq：CPU 恒定频率，省电与 burst 性能都做不了。
- 无 CPU_THERMAL：`thermal-zones/cpu-thermal` 无人消费，无温控降频。
- 不影响开机（板载 73 次只是一条读文件失败的 log，非致命）。

## 修法（需重编内核，故未在本轮动手）
选一条并测：
- **A（推荐先试）**：开 `CONFIG_CPUFREQ_DT=y` + `CONFIG_CPU_THERMAL=y`，
  并确认 dtb 的 `cpu@0` 有 `operating-points-v2`/`clocks`/`clock-names`
  与 `#cooling-cells`，否则 `cpufreq-dt` probe 会失败（-EPROBE_DEFER 循环）。
- **B**：用 `CONFIG_ARM_MEDIATEK_CPUFREQ=y`（MTK 专用，若 mainline 已有 mt6572 支持）。
- 必须**同时**把 defconfig 与交付用的 .config 对齐（本文件记录的分歧就是教训），
  否则重编与交付再次不一致。

## 纪律
重编内核前先 `df -h /`（本项目多次因盘满导致编译静默失败）；
编完必须重打 boot.img 并验证 ramdisk/appended-dtb/initrd-end 三项自洽。
