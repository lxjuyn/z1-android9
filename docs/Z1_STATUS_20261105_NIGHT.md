# Z1 状态与明早待办 — 2026-11-05 夜

设备当前**离线**（无 USB、无串口节点），电池在 19:55 测得 3034 mV，低于刷写门限 3600 mV。
本轮全部工作都是**离线可做的静态修复**，每一处都从镜像/源码自行取证，不采信上一轮结论。

---

## 一、本轮真正修掉的东西（都已在仓库，未上板）

| # | 问题 | 根因（硬证据） | 产物 |
|---|---|---|---|
| 1 | 触摸在 Android 侧不被当触摸屏 | fix3j/fix3k 的 ramdisk 里 `z1_cst836u.ko` 是旧构建，反汇编缺 `orr r3,r3,#2 / str [r2,#20]`（`input_dev.propbit` 置 `INPUT_PROP_DIRECT`）那三条；源码 11-04 已修但没重打 ramdisk | fix3m → 已被 fix3p 取代 |
| 2 | 充电从未真正使能 | 板上 10 条 `charging ENABLED` 的 `CHR_CON0` 全是 **0x006b**（bit4 CHR_EN=0），CSDAC=0；驱动只打印不校验。补厂商漏写的 `CHR_CON15.FLAG_WR` 清 WDT 锁存、`CON23.VCDT_MODE=0`、`CON7.BATON_HT_EN=0`；使能后**校验 bit4**，不置位就返回 `-EIO` 并每 2 s 重试，失败时 dump CON0..CON29 | v17 system |
| 3 | **电源键/耳机/充电中断/RTC 闹钟全死** | `mt6323.dtsi` 的 `interrupts = <150>` 是从 MT6397/MT8173 继承的 GPIO 号；MT6572 的 pin150 是 `NO_EINT_SUPPORT` → `mtk_xt_get_gpio_n()` 返回 EINT_NA → `-EINVAL`（板上 `failed to register irq=37; err: -22`）。真实映射是 GPIO7→`PMIC_EINT`，EINT 号 **16** | fix3n/fix3p boot |
| 4 | FM 永远加载不了 | `z1_fm.rc` 跑 `/lib/modules/load_fm.sh`，但运行时**没有 `/lib`**（`init.rc` 只 symlink `/bin /etc /d /vendor`），且 `load_fm.sh` 里 `KO=/lib/modules/...` 同样错 | v17 system |
| 5 | 电池遥测全空 | `z1_heartbeat.sh` 读 `/sys/class/power_supply/adc-battery/`，实际 psy 名是 `battery` | fix3p ramdisk |
| 6 | 一批 SELinux 标签错/缺 | `light@2.0-service` 是 `vendor_file`（fc 明确要求 `hal_light_default_exec`）；4 个 rc 用了策略里**不存在**的 `vendor_init_rc`；15 个文件无 xattr（含 Trebuchet.apk、两个 .ko） | v17 system |
| 7 | GPU 路线认知错误 | 见下节 | 文档 |

### 关于 GPU 路线（重要纠正）

`ro.hardware.egl` 这台机器上**完全不起作用**：`ld.config.txt` 与 AOSP r61 的
`ld.config.legacy.txt` 逐字节相同、没有 `sphal` 命名空间，`android_getDriverNamespace()`
恒返回 NULL，`load_updated_driver()` 永不执行。真正决定用谁的，是
`load_system_driver()` 对 `/vendor/lib/egl/libGLES_*` 的**通配匹配**：
v14 有 `libGLES_mesa.so` 就用 mesa，v16 没有就回退 `libEGL_swiftshader.so` 三件套。
所以 **fix3j 与 fix3k 功能完全等价**（ramdisk 只差这一行），切 GPU 只需换 system 分区。
详见 `out/GPU_PATH_VERIFIED_20261105.md` §5。

---

## 二、可直接刷的产物

| 角色 | 文件 | sha256 |
|---|---|---|
| **boot（推荐）** | `out/z1_fix3p_boot/boot.img` | `db98a5e5fd7cf1efed09067c91d3d81d9207b199ffc01731aee7544d85444983` |
| **system（推荐）** | `out/z1_v17_system/system_v17.img` | `cfb415d4956efc43ca6c408c222859e3345ebef31bc6d621fba388a69118dbbd` |
| **GPU 验证用 system** | `out/z1_v18_gpu_system/system_v18.img`（= v17 + Mesa；`6be0c5d5…`） | 见 `out/GPU_PATH_VERIFIED_20261105.md` §6 |
| 回滚 boot | `out/z1_fix3k_boot/boot.img`（= fix3j，可启动） | — |
| 回滚 system | `out/z1_v16_system/system_v16.img` | — |
| 兜底 | `1/boot.img`（原厂） | — |

**v17 追加一项（22:18）**：`/vendor/etc/vintf/manifest.xml` 里原本**没有 `android.hardware.light`**，
而 light HAL 二进制和 rc 从 11-04 就在镜像里。VINTF 里没声明的接口，hwservicemanager 可以拒绝注册，
表现就是 `LightsService: Unable to get ILight interface`（背光调不了、也关不掉）。
已补上 `@2.0::ILight/default`（源文件 `z1a9_gpt/rootdir/manifest_v17.xml`），标签
`vendor_configs_file`（plat_file_contexts:20）。上板后用
`logcat -d | grep -i "Light HAL service is Ready"` / `service list | grep -i light` 验证。

整幅镜像的 sha256 只标识"这个文件"，不是可复现的构建指纹（debugfs 每次都会更新 ext4
超级块的写时间）。**判定内容是否正确请用这三个 content hash**（v17 实测就是这个值）：
```
/lib/modules/z1_charger.ko  8967b0ba5cb3ac592dcd047a96b8fb48e4ae19ddc8d75a4f7fe852d0aa6cf2fe
/etc/init/z1_fm.rc          e0be383b927811bb6f3e47a1426d422927266048625cba7f12b900b85178aacd
/lib/modules/load_fm.sh     fad51b3789fbbc77a7a177d1fcab28e4249c16e4ed5506b8baaf63a247ca8ada
```
用 `tools/z1_build_v17_system.sh` 可以从 v16 完整重放 v17（已实测三个 hash 与 xattr 一致）。

fix3p 相比 fix3n：修回被我一度覆盖成旧版的 `z1_cst836u.ko`（md5 必须 = `3aa31907d7eb7b31b5b7cfa63d742db0`），
加 `z1_heartbeat.sh` 电池路径修复。**打包后从 boot.img 反解校验过**：kernel_size=4236583=payload+512、
ramdisk_size=1982044=gz+512、dtb `initrd-end=0x842e3c5c`、cpio 里 .ko md5 正确。

刷写前置：`tools/z1_vbat_guard.sh` 必须返回 0（当前 3034 mV，会拒绝）。
刷写命令：
```
tools/flash_with_retry.sh bootimg out/z1_fix3p_boot/boot.img out/z1_fix3k_boot/boot.img 8
tools/flash_with_retry.sh android  out/z1_v17_system/system_v17.img out/z1_v16_system/system_v16.img 8
```

---

## 三、上板后第一件事：验证这几条新日志

1. **PMIC 中断**：期望 `mt6397-pmic` 不再打印 `failed to register irq=... err: -22`，
   而是 `IRQ controller enabled (irq NN), exposing 10 MFD cells`；
   按电源键应看到 `getevent` 有 EV_KEY code 116。
   若出现 `PMIC IRQ entered 64 times with no status bit set; masking the parent IRQ`
   → 说明极性猜错（改 `IRQ_TYPE_LEVEL_HIGH`），但**不会挂死**（风暴保护）。
2. **充电**：logcat/kmsg 里 `charging ENABLED` 后面 `CHR_CON0=` 必须含 bit4（即 ≥0x7b）。
   若出现 `charge enable FAILED: CHR_EN never set`，紧接着的 `CHR_CON0..CON29` 全量 dump
   就是判别依据：CON2/CON23 等于驱动写入值 ⇒ pwrap 写是通的、PMIC 拒收；
   保持 LK 前的值 ⇒ 写被静默丢弃、要查 pwrap 写路径。
   同时用外部仪表量 VBUS 电流（CC 设的是 450 mA）。
3. **FM**：`dmesg | grep z1fm` 应出现 `insmod OK` → `/dev/fm ready`；绝不能是 `no /lib/modules/...`。
4. **触摸**：`dumpsys input` 里该设备应是 `Sources: TOUCHSCREEN`，不应出现鼠标指针。
5. **电池**：`[z1hb]` 心跳里 `voltage=` 不再是空。
6. **标签**：只能在刷写前后用 `debugfs -R "ea_list <path>"` 核对（本轮已核对通过）。
   **不要**用 `dmesg | grep "invalid context"` 去验 —— 复核证明这条路根本不会打印：
   `security_context_to_sid_default()` 带 force=1，遇到未定义类型时把原始串存成
   uninterpreted context 并返回 0，既不回退 `def_sid` 也不打日志
   (`security/selinux/ss/services.c:1601-1606`、`:1671-1675`)。原来 4 个 rc 的
   `vendor_init_rc` 在 enforcing 下照样会全部被拒，只是**没有任何日志证据**。

---

## 四、审计发现但**本轮没动**的（按价值排序）

1. **屏根本关不掉**：`hwcomposer.cpp:148-153` 的 `hwc_blank()` 是空函数，`simplefb` 没有
   `fb_blank`，没有 DRM/DISP 驱动。所以框架"关屏"后面板仍显示最后一帧、背光不变。
   配合"电源键曾是死键"，这就是用户看到的"黑屏/关不掉"。
   修法：在 HWC 里接 `/sys/class/leds/lcd-backlight/brightness`（背光关=黑屏），
   并确认 light HAL 是否注册成功（`/vendor/etc/vintf/manifest.xml` **缺 light 条目**，
   建议补上，否则 `LightsService` 拿不到 ILight）。
2. **没有 cpufreq**：thermal 的 57/67/87 ℃ trip 拿不到 cooling device，
   只剩 107 ℃ 关机兜底；`KernelCpuSpeedReader` 报 `/sys/.../cpufreq/stats/time_in_state` 不存在。
3. **suspend 是单程票**：没有可用唤醒源（PMIC 父 IRQ 死 → 已修，需验证），
   一旦真 suspend 就回不来。`z1_pm_guard.sh` 的永久 wake_lock 挡住了，但它依赖 permissive
   （策略里没有 `allow init sysfs_wake_lock:file write`）。另外持有 wake_lock 时
   `cat /sys/power/wakeup_count` 会**永久挂死**（不是超时），任何取证脚本都要小心。
4. **GPS（mnld）**：40 ms 内退出，根因是 `/sys/class/gpsdrv/gps/status` 不存在
   （MTK gpsdrv sysfs class 主线没有实现），mnld 把它当致命错误。
   修法二选一：写一个小驱动造这些属性，或 patch mnld 容忍缺失。
5. **蓝牙**：链路全通（HIDL HAL、库、/dev/stpbt 节点、WMT 模块都对），
   唯一缺的是**没人去打开蓝牙** —— 21 次开机里 `BT_open` 命中 0。
   上板试 `adb shell cmd bluetooth_manager enable`（或设置里点开关），再看 `[MTK-BT] BT_open`。
6. **`z1_speedup.rc` 的 `z1_bootimg_build`**：开机 10 分钟后 `mount -o remount,rw /system`
   并跑 dex2oat 重建 boot image。因为镜像是 `arm.noimage`（重命名成 `arm` 会 SIGILL，
   这是已知的），护栏永远不成立 → 每次开机都干这活，把 /system 改成不可复现状态。
   要不要留，取决于它在板上到底有没有成功产出过 `boot.art`。
7. **半写（86.8%）**：`flash_with_retry.sh` 先擦后写，中途失败就留下 inode 12 的 extent 块全零
   （日志里 220 条 `ext4_find_extent ... invalid magic`）。镜像没有 `metadata_csum`，
   所以挂载期完全看不出。`/system` 开机从不 fsck。低电是主要诱因（已加 vbat_guard）。
8. 一堆 rc 死语句（`zygote_secondary`、`audio-hal-2-0`、`healthd`、`update_verifier_nonencrypted`），
   噪声而已，`netd.rc:8` 一条每次 netd 重启刷一行（273 次/采集）。
9. **没有 `service adbd`**，ADB 结构性不可用（USB gadget 也没解决，暂时靠串口）。

---

## 五、方法论上新增的两条硬规矩

1. **改了会进 ramdisk 的东西就必须重打 ramdisk**，不能 `--rootfs` 沿用旧包；
   打完必须从 boot.img 反解回来核对关键文件 md5。本轮我自己就踩了一次
   （fix3p 第一次打包把修好的 `z1_cst836u.ko` 又换回旧的，靠反解才发现）。
2. **`mainline_recon/z1_boot_mainline_*.log` 是 data 文件**，`grep` 必须带 `-a`，
   否则静默无输出，会被误当成"日志里没有这行"。
