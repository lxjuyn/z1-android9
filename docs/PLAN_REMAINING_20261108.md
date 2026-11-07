# Z1 收尾计划 — 2026-11-08（今晚不刷写，全部离线推进）

> 原则：每条都先写"现象 → 证据 → 根因假设 → 动作 → 验收判据 → 风险"。
> 没有证据支撑的条目一律标 NOT VERIFIED，不进"已修"。

---

## A. 当前基线（上板实测过的）

| 项 | 状态 | 证据 |
|---|---|---|
| 显示链路（LK→simplefb→面板） | ✅ 正常 | 用户肉眼确认 LOS 二屏 |
| boot 分区刷写 + 读回校验 | ✅ | `第 2 次写入及严格读回校验均成功` |
| system 分块写入（943MB） | ✅ | 19/19 块，断点续写工具已沉淀 |
| 电池遥测 | ✅ 修好 | `[z1hb] ... voltage=3880000` |
| GPU 路径归因 | ✅ 单变量确认 | 有 mesa→彩虹，无 mesa→正常 |

## B. 阻塞项（按优先级）

### P0-1 `/data` 被写坏导致开不了机
- **现象**：zygote 每 5 s SIGABRT，屏幕停在二屏；258 次 zygote 启动 / 206 条 abort。
- **证据**：`EXT4-fs error (mmcblk0p6): ext4_free_inode:354: bit already cleared for inode 7743`
  → `Aborting journal` → `Remounting filesystem read-only`；随后
  `Abort message: 'Error changing dalvik-cache ownership : Read-only file system'`。
- **动作**：`out/z1_fix3q_boot/boot.img`（fstab /data 加 `check`，开机前 e2fsck）。
  *待验证*：fs_mgr 是否真的会用 `-y` 跑、修不好时的降级路径。
- **验收**：开机日志出现 `Running /system/bin/e2fsck on /dev/block/mmcblk0p6` 且随后
  `/data` 挂载为 `r/w`；zygote 不再在 5 秒内重启。
- **风险**：e2fsck 需要交互时卡住 → 必须有超时/降级。

### P0-2 镜像里的模块是旧构建（系统性）
- **已发现并修**：`z1_cst836u.ko`（触摸，缺 `INPUT_PROP_DIRECT`）、5 个音频模块（不含 MIC 路径）。
- **关键教训**：`/bin/z1_touch_load.sh` 加载的是 `/system/lib/modules/z1_cst836u.ko`，
  **不是 ramdisk 里那份** —— 11-05 我只改了 ramdisk，等于没修。
- **动作**：`out/z1_v20_audio_system/system_v20.img`（sha `3e57dd0c…`）。
- **验收**：`dumpsys input` 里触摸设备带 TOUCHSCREEN；录音能出数据。

### P1-1 PMIC 父中断（电源键/耳机/充电中断/RTC 闹钟）
- **现象**：`mt6572-pinctrl: Can not find pin` → `request_irq(37) -EINVAL`。
- **已证伪**：把 dts 的 `<150>` 改成 `<16>`（GPIO7 的 eint_n 确为 16）后行为**完全没变**，
  irq 号仍 37。说明换算不在我以为的那条路上。
- **动作**：见 agent `pmic-irq` 的结论（待并入）。

### P1-2 GPU 彩虹条
- **现象**：`gp error irq state=4000 status=3`（PLB_INVALID_CMD），20 次。
- **已推翻**："416 MHz 无 VGPU 电压"——lima 用 optional regulator，MT6572 无独立 VGPU 轨。
- **假设**：Mesa 19.1.8 用户态喂了非法命令流（其 `lima_drm.h` 缺 `LIMA_BO_FLAG_HEAP`）。
- **动作**：`out/z1_v21_gpu24_system/system_v21.img`（= v20 + Mesa 24.0.9 + libglapi 同换，
  sha `64b40c31…`）。**未上板**。
- **验收**：`gp error irq` 消失 + 屏幕正常；若仍在则问题在 lima 内核侧。

### P1-3 开机极慢（10~30 分钟）
- 无 boot image（`arm.noimage`），首启 68 次 dex2oat，1044 s 未完成。
- 待 `boot-speed` agent 给根因与路线。

### P2 功能缺失
- 蓝牙：三模块+节点+ HAL 都在，但 `BT_open` 从未被调（内核侧从未上电）。
- GPS：mnld 40 ms 退出，卡在 `/sys/class/gpsdrv/gps/status` 不存在。
- WiFi：HIDL 栈完整、`wlan0 scanning` 已见；端到端未验证。
- 关屏：HWC `blank` 空函数、simplefb 无 `fb_blank`；light HAL 已进 manifest（未验证）。
- cpufreq 缺失 → thermal 中间档位无效。
- 无 adbd / USB gadget。

---

## C. 今晚（离线）已完成
1. 磁盘：把 9 个旧 system 镜像 + 99 个 10 月的 products 归档移到机械硬盘（符号链接保留路径），
   根分区 4.5 G → 27 G。
2. CLAUDE.md §4.00 更新到真实状态（原内容已过期，会误导后续 agent）。
3. 发现并修复"模块陈旧"这一类问题：v20（音频 5 个 + 触摸）。
4. 更正 11-05 "触摸已修"的错误结论（实际加载的是 system 里那份旧模块）。
5. 产出 v21（v20 + Mesa 24），为 GPU 单变量验证准备好一对镜像。

## D. 下次上板顺序（不今晚）
1. 刷 `fix3q boot` → 让 e2fsck 修 /data → 确认能进系统（这是所有后续的前提）。
2. 刷 `v20` → 验证触摸（TOUCHSCREEN）与录音。
3. 只换 system 刷 `v21` → GPU 单变量验证（判据见 P1-2）。
4. 每次只改一个变量。

## E. NOT VERIFIED（不要当成已做）
- fix3q 的 fsck 是否真生效、修不好 /data 会怎样。
- v20 的触摸/录音、v21 的 GPU。
- PMIC 中断能否修通（等 agent）。
- 蓝牙/GPS/WiFi/关屏 的任何"已修"结论都不存在，只有分析。
