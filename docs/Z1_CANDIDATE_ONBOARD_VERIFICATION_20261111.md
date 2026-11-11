# 候选镜像上板验收结论（2026-11-11）

> 本文记录把 `out/z1_driver_candidates_20261110/system_peripherals_v1.img`
> 真正刷进板子之后的**实测结果**。主机侧声明见
> `Z1_HANDOVER_VERIFIED_20261110.md`（已逐条复核通过）。

---

## 一、主机侧声明：全部复核通过

| 声明 | 复核方式 | 结论 |
|---|---|---|
| 9 套测试全过 | 我本人重跑 | ✅ 187 用例 |
| 改动前源码挂 27/34 | 同一套 harness 跑 `0e6b50f` 版 | ✅ 27 failed |
| 镜像 sha `833a65f2…` | 重算 | ✅ |
| `base_unchanged` | 挂载后全树 hash 对比 | ✅ 2134 文件仅 6 个不同 |
| `e2fsck rc=0` | 重跑 `e2fsck -fn` | ✅ |
| ART 产物 `fb7240ed…` | 从当前树重建 | ✅ 逐字节一致 |

---

## 二、上板实测（本轮新信息）

### 2.1 刷写与文件到位

分块刷写 `android` 分区（0x4340000，900 MiB）19 块全部成功，约 2.5 分钟。
板上 6 个文件 sha256 与 manifest **完全一致**：

```
bc7e0525…  /system/vendor/lib/libwifi-hal.so
43944199…  /system/vendor/lib/libbt-vendor.so
d037d76c…  /system/vendor/lib/hw/audio.primary.mt6572.so
255b08bb…  /system/lib/modules/z1_cst836u.ko
ccbd0e9c…  /system/usr/keylayout/mt6779-keypad.kl
621e1de4…  /system/usr/keylayout/z1_cst836u.kl
```

**刷写未损坏**（另验证 5 个核心文件与镜像逐字节一致）：
`surfaceflinger 6b4be33a` / `libsurfaceflinger c0c47b5e` / `libc aaf22ecf` /
`libbinder 3bc7e7a7` / `libui b74a74c8`。

### 2.2 keylayout 修复在真机生效

三键实测上报：`x=80 → 158 BACK`、`x=120 → 102 HOME`、`x=200 → 139 MENU`
（`.kl` 映射为 APP_SWITCH）。**返回/主页/多任务全部可用**。

### 2.3 顺手修掉的真 bug（已提交 `5d4d266`）

驱动按 X 分区：`x < key_x_split1 → BACK`。三个键的原始 X 实测是 **80 / 120 / 200**，
而旧默认 `key_x_split1 = 80`，`x == 80` 不满足严格小于 → 最左键落到 HOME 区。
改为 **90 / 160**（三个实测中心的中点）后修复。

---

## 三、上板暴露的两个既有问题（阻塞功能验收）

### 3.1 `/data` ext4 损坏 → 只读 → zygote 每 5 秒 SIGABRT

```
EXT4-fs error (device mmcblk0p6): ext4_mb_release_inode_pa:5464: group 1, free 23, pa_free 35
EXT4-fs (mmcblk0p6): Remounting filesystem read-only
```

`fstab.z1` 里 `/data` 本来就带 `check`，开机 `e2fsck` 跑过并修正（`exit(1)`）。
修正后本轮 `EXT4-fs error` 计数为 0，`/data` 保持 rw。

### 3.2 `Log.wtf` 打死 system_server（**关键**）

```
android.util.Log$TerribleFailure: neither /proc/wakelocks nor /d/wakeup_sources exists
    at com.android.internal.os.KernelWakelockReader.readKernelWakelockStats(KernelWakelockReader.java:92)
    at com.android.internal.os.BatteryStatsImpl.updateKernelWakelocksLocked(BatteryStatsImpl.java:11813)
```

内核没有 `/proc/wakelocks`，而 `/d/wakeup_sources`（→ `/sys/kernel/debug/wakeup_sources`）
**存在但权限是 0600 root**，`system_server`（uid 1000）读不到就被当成"不存在"，
`Log.wtf` 在 userdebug 上抛 `TerribleFailure` → system_server 死亡 → 全系统级联崩溃。

**修复**：`chmod 0644 /sys/kernel/debug/wakeup_sources`。修完 `TerribleFailure` 计数归零。

### 3.3 修复的持久化

`/system/bin/z1_touch_load.sh` 由 init 以 **root** 执行（`exec_background ... root root`），
已在它的 `exit 0` 之前插入：

```sh
chmod 0644 /sys/kernel/debug/wakeup_sources
sleep 2; echo 90  > /sys/module/z1_cst836u/parameters/key_x_split1 2>/dev/null
echo 160 > /sys/module/z1_cst836u/parameters/key_x_split2 2>/dev/null
```

⚠️ 这两处是**板上的 system 改动**，下次刷 system 镜像会被覆盖 —— 应该并入镜像构建。

### 3.4 仍未解决：SurfaceFlinger abort

```
Abort message: 'Status.cpp:149] Failed HIDL return status not checked:
                Status(EX_TRANSACTION_FAILED): 'DEAD_OBJECT: ''
#00 abort+62  #01 android::base::DefaultAborter  #02 LogMessage::~LogMessage
```

`vendor.hwcomposer-2-1` 本身 `running` 且无死亡记录，所以不是组合器服务挂了，
而是 SF 依赖的其它 HIDL 端点返回 `DEAD_OBJECT`。系统退化为 `FallbackHome`、
app 进程反复起停。**尚未定位到单一根因**。

---

## 四、结论与下一步

* 6 个文件的**内容正确性**已验收到位（sha、位置、权限、刷写完整性）。
* keylayout 的**功能**已在真机验收（三键 158/102/139）。
* **WiFi / 蓝牙 / 音频的功能仍未验收** —— 系统本身起不稳，做不了有意义的交互测试。
  （值得注意：WiFi 的原始报错 `Failed to open wlan fw path param` 是修复前的旧系统状态，
  换新 `libwifi-hal.so` 后尚未复测。）
* **下一步应做干净 A/B**：刷回 `out/z1_v35_selftest/system_v35.img`（不含这 6 个文件），
  确认基线是否同样不稳定 —— 以此判断 3.4 的不稳定是候选镜像引入的，还是这块板子的
  既有脆弱性。只有基线干净，才能逐项验收 WiFi/BT/音频。

### 已知环境约束（避免重复踩）

* `z1push.sh` 在这块板子上不可用（标记协议收不到回显）；小文件用 `z1sh.sh` + base64 分段。
* console 连续收到大段命令会"只回显不执行"（UART 溢出），需要 Ctrl-C 或重启。
* `pkill -f <名字>` 会杀掉自己（`bash -c` 的 cmdline 含同一字符串）。
* `/tmp` 会被清理，`z1devlibs`、`rootfs_v79.bin` 需要重新生成；串口工具已入库
  （`scripts/z1deploy/z1cap.sh`、`z1sh.sh`）。
