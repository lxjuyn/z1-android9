# 蓝牙 / GPS 落地方案 — 2026-11-08（离线，未上板）

## 一、蓝牙：闸门只有一个 —— `Settings.Global.bluetooth_on == 0`

从**镜像里真实的 `services.jar`** 反汇编得到的决策链（不是按 AOSP 记忆）：

```
BMS 构造:  mEnableExternal = isBluetoothPersistedStateOn()   // bluetooth_on 默认 -1 -> 视为 ON
phase 500: publishBinderService("bluetooth_manager", BMS)
phase 550: handleOnBootPhase():
             if (isBluetoothDisallowed()) return;            // 只查 no_bluetooth 用户限制
             if (mEnableExternal && isBluetoothPersistedStateOnBluetooth())  // 默认 1
                 -> "Auto-enabling Bluetooth." -> sendEnableMsg -> IBluetooth.enable()
             else if (!isNameAndAddressSet())
                 -> "Getting adapter name and address"        // 只 bind app 取名字/地址，**不 enable**
```

资源侧已用 aapt 确认：`config_supportBluetoothPersistedState = true`（`0x011200b4`），
`def_bluetooth_on = true`（`com.android.providers.settings`），且 `/vendor/overlay`、`/system/overlay`、
`/product/overlay` 都没有覆盖 framework-res 的 RRO。

⇒ **只要 `/data` 里的 `bluetooth_on` 是 0，`handleOnBootPhase()` 就必然走"只取名字/地址"分支**，
栈不初始化、不调 `IBluetoothHci::initialize()`、不 open `/dev/stpbt` —— 这与观察到的
"21 次开机 `BT_open` 命中 0"完全吻合，**不需要"没人按开关"这个解释**。

### 二分用串口字符串（默认 relay 只转 W 及以上，D 级看不到）
- 不出现 `Bluetooth boot completed` ⇒ `handleOnBootPhase()` 没跑到
- 出现 `Bluetooth persisted state: 0` ⇒ **根因确认：`bluetooth_on=0`**
- 出现 `Auto-enabling Bluetooth.` ⇒ 框架在开，问题在更下游

工具：`scripts/btgps/bt/z1_bt_trace.sh`（把 D 级按 tag 实时转 kmsg）、`z1_bt_diag.sh`（事后拉 logcat）、
`z1_bt_enable.sh` + `z1_bt_enable.rc`（不依赖 UI 的强制使能，待上板验证权限）。

### 解决
1. 若确认是 `bluetooth_on=0`：清掉该设置或写 1，**下次开机**框架会自己开（运行时改不生效，
   BMS 只在构造和 phase 550 读）。`settings put global bluetooth_on 1` 需要先有 shell。
2. 若 `Auto-enabling` 出现但栈仍不起：问题在 `IBluetoothHci::initialize()` 之后，用 trace 脚本继续二分。

## 二、GPS：mnld 死于 `/sys/class/gpsdrv/gps/status` 不存在，两条路都已实现

| 路线 | 做法 | 产物 |
|---|---|---|
| **A（推荐）** | 加内核模块造出 `gpsdrv` sysfs class，mnld 不用改 | `scripts/btgps/gps/z1_gpsdrv/`（`z1_gpsdrv.c` 4.9KB + 已编 `z1_gpsdrv.ko`，vermagic `7.0.0-rc7-z1+` ✓） |
| B | 不改内核，patch `mnld` 让它容忍 open 失败 | `scripts/btgps/gps/routeB/mnld_patched` + `patch_mnld.py` |

模块实现的属性（按 mnld 实际读写行为反推，不是猜）：
`status`（写 48 字节 "(%d/%d/%d %d:%d:%d) - %d/%d"）、`pwrctl`（'0'..'5'）、
`suspend`/`pwrsave`（'0'/'1'）、`state`（'0'..'5'）。

安装脚本 `install_gps.sh [A|B]`，配套 `init.z1.network.rc.patch` 与 `z1_gpsdrv_load.sh`。

## NOT VERIFIED
- 蓝牙：`bluetooth_on` 的实际值（在 `/data`，读不到）；trace/enable 脚本未上板。
- GPS：模块能否 insmod、mnld 起来后能否真的定位（还需要 WMT/GPS 核上电，那是另一层）。
