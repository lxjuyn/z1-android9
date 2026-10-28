# WiFi 框架 HAL 落地报告 — out/z1_r5_system/system.img.wifi_hal

日期：2026-10-28 · 执行：系统镜像工程师（agent）
任务：把 WiFi 框架层 HAL 落地到新 system.img 副本，并做完整回读验证。

| 项 | 值 |
|---|---|
| **产物** | `out/z1_r5_system/system.img.wifi_hal` |
| **sha256** | `1c14eaba5172e29a68d01bd54171a6645dda4a9b31051ec10c979ff4776c1c73` |
| **大小** | 943,718,400 B（与源逐字节同尺寸） |
| **源镜像（只读未动）** | `out/z1_r3f_system/system.img` sha256 `ebb58810c3b9bdac7336873133e907f377c9b099ecba7229982acfc666987665`，mtime 保持 `2026-10-27 22:26:27` 未变 |
| **文件验证** | **17/17 全部通过**（Type=regular + Mode + Size + sha256 回读一致） |
| **依赖库缺口** | **0**（13 个 ELF 的 NEEDED 全部在镜像内解析） |
| **e2fsck** | 5 遍全过，`2646/57600 文件, 196373/230400 块` |

---

## 〇、必须先说的两件事（与任务书前提不符，已实测）

### 1. 另一 agent 在同一路径产出，我复核的是最终字节
本次运行中 `out/z1_r5_system/` 出现并发写入：我执行 deploy 后拿到初版 `4b8ae96c…`，
之后该文件被同会话另一 agent 覆盖为重做版（含 `ea_set` 写 SELinux 标签），并已写出
`WIFI_HAL_LANDING.md` / `system.img.audio_tools` / `system.img.r5_final` 等文件。

**处置**：未再复制镜像（根盘已 99% 满，`/dev/sda3` 仅余 ~1.2G），改为**对当前最终字节就地全量复核**。
下方所有数字均来自当前文件的实测输出，非沿用旧报告。当前文件 sha256 `1c14eaba…` 稳定（连读两次一致）。

### 2. **`wifi_load_driver()` 是纯属性桩，不是 insmod —— 这推翻 README 的降级预测**
README/旧报告称"`wifi_load_driver()` 硬编码 `/vendor/lib/modules/wlan.ko` + `/proc/modules` 标签 `wlan ` → LoadDriver 必败 → `IWifi.start` 失败"。
**实测该结论不成立**。`vendor/lib/libwifi-hal.so` 反汇编证据：

```
0000def0 <wifi_load_driver@@Base>:          # 全函数仅 18 字节
    def0: b580       push {r7, lr}
    def2: 4804       ldr  r0, [pc, #16]      # -> "wlan.driver.status"
    def4: 4904       ldr  r1, [pc, #16]      # -> "ok"
    def6/f8:         add  r0/r1, pc
    defa: blx 5208   property_set@plt        # property_set("wlan.driver.status","ok")
    defe: 2000       movs r0, #0             # return 0 == SUCCESS
    df00: bd80       pop  {r7, pc}
```

* 字节码：`80b5 0448 0449 7844 7944 f7f7 86e9 0020 80bd`，与上面反汇编逐条吻合。
* `is_wifi_driver_loaded()`（0xde98）：只 `property_get("wlan.driver.status")` 再 `strcmp(...,"ok")`。
* `wifi_unload_driver()`（0xdf0c）：只 `property_set("wlan.driver.status","unloaded")`。
* **全文件搜不到 `insmod` / `modprobe` / `.ko` / `/proc/modules` / `/vendor/lib/modules`**；
  UND 符号表里也没有 `finit_module` / `init_module` / `delete_module`（grep 计数 = 0）。

**推论（重要，方向反转）**：
1. `DriverTool::LoadDriver()`（0x5514，仅 `blx wifi_load_driver` + 返回值翻转）**在 Z1 上会成功**，
   因为它的唯一失败条件就是 `wifi_load_driver()` 返回非 0。
2. 于是它**不会**自己 insmod 驱动，也**不会**因 `/vendor/lib/modules/wlan.ko` 缺失而失败——
   它只是把 `wlan.driver.status` 置成 `ok`。驱动的加载仍完全由既有 bring-up 链负责
   （`init.z1.rc` → `z1_linux7_radio_init.sh`/`z1_radio_finish.sh` → `z1_6620` launcher → `z1_wifi_probe.sh`）。
3. `wifi_wait_for_driver_ready()`（0x6310）只 `fopen("/sys/class/net/wlan0","r")` 轮询 100 次×200ms，
   **超时返回 -6**。所以：`LoadDriver` 会过，但若 `wlan0` 没起来，`wifi_initialize` 仍会因
   "Timed out waiting on Driver ready" 失败 → `IWifi.start` 报错。
   **"vendor HAL 降级"的真实触发点是 wlan0 不存在，不是模块路径不匹配。**
4. 附带好处：**不需要**把 `wlan_gen2.ko` 改名成 `/vendor/lib/modules/wlan.ko`；README「可选升级」
   的整条建议可作废（它假设存在真实 insmod 路径，实测不存在）。

---

## 一、逐文件验证表（17/17 通过）

验证方法：`debugfs -R 'stat /<path>'`（Type / Mode / Size / Uid:Gid）+ `debugfs -R 'dump /<path> …'`
回读后 `sha256sum` 与 `out/z1_wifi_hal_stage/deploy/` 源件比对。

| # | 镜像内路径 | 权限 | Type | Size | sha256（回读） | 结果 |
|---|---|---|---|---|---|---|
| 1 | `/vendor/bin/hw/android.hardware.wifi@1.0-service` | 0100755 | regular | 272,272 | `759c5d0d3bac9609…` | ✅ |
| 2 | `/vendor/bin/hw/wpa_supplicant` | 0100755 | regular | 1,485,140 | `fd24bb69310cd450…` | ✅ |
| 3 | `/vendor/bin/hw/hostapd` | 0100755 | regular | 506,476 | `dbc597fb8045f13d…` | ✅ |
| 4 | `/vendor/lib/libwifi-hal.so` | 0100644 | regular | 95,116 | `0a9ba41186680851…` | ✅ |
| 5 | `/vendor/lib/libwpa_client.so` | 0100644 | regular | 24,332 | `779da252d0584688…` | ✅ |
| 6 | `/vendor/lib/libkeystore-wifi-hidl.so` | 0100644 | regular | 16,132 | `06f79611c22c8b3e…` | ✅ |
| 7 | `/vendor/lib/libkeystore-engine-wifi-hidl.so` | 0100644 | regular | 20,796 | `a9a454cd47674781…` | ✅ |
| 8 | `/lib/android.hardware.wifi@1.0.so` | 0100644 | regular | 971,596 | `19366d6787e0264f…` | ✅ |
| 9 | `/lib/android.hardware.wifi@1.1.so` | 0100644 | regular | 184,188 | `f150293983b921d4…` | ✅ |
| 10 | `/lib/android.hardware.wifi@1.2.so` | 0100644 | regular | 518,992 | `f00f3423696e49fa…` | ✅ |
| 11 | `/lib/android.hardware.wifi.supplicant@1.0.so` | 0100644 | regular | 1,307,324 | `ae2898095661d16b…` | ✅ |
| 12 | `/lib/android.hardware.wifi.supplicant@1.1.so` | 0100644 | regular | 489,088 | `55eb906f3441895f…` | ✅ |
| 13 | `/lib/android.hardware.wifi.hostapd@1.0.so` | 0100644 | regular | 84,508 | `685de40502cfaf03…` | ✅ |
| 14 | `/vendor/etc/init/android.hardware.wifi@1.0-service.rc` | 0100644 | regular | 173 | `51e16c30b99057e1…` | ✅ |
| 15 | `/vendor/etc/init/hostapd.android.rc` | 0100644 | regular | 599 | `55768d655d907205…` | ✅ |
| 16 | `/vendor/etc/init/wpa_supplicant.rc` | 0100644 | regular | 1,508 | `7f70dad82e37241b…` | ✅ |
| 17 | `/vendor/etc/wifi/wpa_supplicant_overlay.conf` | 0100644 | regular | 23 | `621564704ba0c124…` | ✅ |

* UID:GID 全部 `0:0`（root:root）✅
* 全部 17 个 sha256 与 stage 源件**逐字节一致**（stage 树 `sha256sum -c SHA256SUMS` 19/19 通过，含 2 个非镜像文件）

**说明：为什么是 17 不是 18**
stage 树 `SHA256SUMS` 共 **19** 条，其中 2 条**不是镜像文件**：
`deploy/vintf/wifi_manifest_fragment.xml`（合并进 `/vendor/etc/vintf/manifest.xml`，不单独落盘）、
`deploy/selinux/file_contexts_fragment.txt`（enforcing 用的片段，暂不入镜像）。
`19 − 2 = 17` 个镜像内文件，与脚本 `FILES` 数组长度一致。

### 目标目录 `ls -l` 确认（17 个文件全部在列）
```
/vendor/bin/hw    : + android.hardware.wifi@1.0-service(272272) wpa_supplicant(1485140) hostapd(506476)
/vendor/etc/init  : + android.hardware.wifi@1.0-service.rc(173) hostapd.android.rc(599) wpa_supplicant.rc(1508)
/vendor/etc/wifi  : + wpa_supplicant_overlay.conf(23)      （原有 wpa_supplicant.conf 672 保留）
/vendor/lib       : + libwifi-hal.so libwpa_client.so libkeystore-wifi-hidl.so libkeystore-engine-wifi-hidl.so
/lib              : + android.hardware.wifi@{1.0,1.1,1.2}.so + supplicant@{1.0,1.1}.so + hostapd@1.0.so
```

### 镜像体积变化
| | 块数(已用/总) | 空闲块 | 文件数 |
|---|---|---|---|
| 源 | 194,904 / 230,400 | 35,496 | 2,629 |
| 产物 | 196,373 / 230,400 | 34,027 | 2,646 |

净增 **1,469 块 ≈ 5.7 MiB**、**17 个文件**，与 deploy 树实际字节数（5,932 KB）吻合，
无暴涨、无空闲块异常。**镜像文件尺寸不变**（943,718,400 B，分区镜像不扩）。

### e2fsck 实测输出
```
$ e2fsck -f -n out/z1_r5_system/system.img.wifi_hal
第 1 遍：检查 inode、块，和大小
第 2 遍：检查目录结构
第 3 遍：检查目录连接性
第 4 遍：检查引用计数
第 5 遍：检查组概要信息
system: 2646/57600 文件（0.5% 不连续），196373/230400 块
```
（源镜像基线同样是干净的：`2629/57600 文件, 194904/230400 块`，无 pre-existing 错误。）

---

## 二、依赖库核查表

方法：对 13 个 ELF（3 个可执行 + 10 个 .so）做 `readelf -d` 取 NEEDED，逐个比对镜像
`/lib` 与 `/vendor/lib` 的 `ls -l` 目录列表（**不用 stat 退出码**）。

| 依赖库 | 需求方 | 位置 | 状态 |
|---|---|---|---|
| libnl.so | libwifi-hal / wpa_supplicant / hostapd / wifi-service | `/lib` 101,796 B | ✅ |
| libhidlbase.so | 全部 HIDL 件 | `/lib` 113,140 B | ✅ |
| libhidltransport.so | 全部 HIDL 件 | `/lib` 321,972 B | ✅ |
| libhwbinder.so | 全部 HIDL 件 | `/lib` 131,172 B | ✅ |
| libbase.so | HAL / supplicant / hostapd | `/lib` 62,976 B | ✅ |
| libutils.so | 全部 | `/lib` 96,924 B | ✅ |
| libc++.so | 全部 | `/lib` 636,332 B | ✅ |
| libwifi-system-iface.so | wifi@1.0-service | `/lib` 20,192 B | ✅ |
| libcutils.so / liblog.so | 全部 | `/lib` | ✅ |
| libcrypto.so / libssl.so | wpa_supplicant / hostapd | `/lib` | ✅ |
| **android.system.wifi.keystore@1.0.so** | libkeystore-wifi-hidl / -engine | `/lib` 84,552 B | ✅ |
| `/system/bin/linker`（解释器） | 3 个可执行 | 镜像 `/bin/linker` | ✅ |

**结论：13/13 ELF 的 NEEDED 全部 `ALL-RESOLVED`，依赖缺口 0。**
（`android.hardware.wifi.offload@1.0.so`、`libwifi-system.so`、`libwifikeystorehal.so`、
`android.hardware.bluetooth@1.0-impl.so` 等也已在镜像内，属既有件。）

补充：三件套架构均为 `ELF 32-bit LSB ARM EABI5, interpreter /system/bin/linker, stripped`，
与 Z1 armv7 一致。`/etc/passwd`、`/vendor/etc/passwd` 在镜像内均**不存在**，但这不是问题——
libc 的 `android_ids[]` 编译期表（从 `/tmp/libc.so` 解析共 87 项）已含
`wifi=1010 / gps=1021 / keystore=1017 / inet=3003 / net_raw=3004 / net_admin=3005 / bluetooth=1002`，
init 解析 `user wifi` / `group wifi gps` 等在**无 passwd 文件时也走该表**，不会失败。

---

## 三、SELinux xattr 结论

### 现状（实测）
当前产物里 **17 个文件全部已带 `security.selinux`**（`ea_list` 实测）：
```
/vendor/bin/hw/android.hardware.wifi@1.0-service  -> u:object_r:hal_wifi_default_exec:s0
/vendor/bin/hw/wpa_supplicant                     -> security.selinux (46)   [值见下方 getfattr]
/vendor/lib/libwifi-hal.so                        -> u:object_r:vendor_file:s0
/vendor/etc/init/wpa_supplicant.rc                -> u:object_r:vendor_init_rc:s0
```
> 注：`debugfs ea_list` 对部分文件**只打长度不打值**（如 `(46)`），这是 debugfs 1.47.0 的显示缺陷，
> 不代表未写入。权威复核方式是 loop 挂载后 `getfattr -n security.selinux --only-values`。
> 我的独立验证用了 `dump` 回读 sha256（内容）+ `ea_list`（标签存在性），两者均通过。

### 这些标签在策略里真实存在吗（关键，已逐项核对）
从 `out/boot_r3f_final.img` ramdisk 解出的 `sepolicy` 二进制直接搜字符串：
| 标签 | 在 sepolicy 中 |
|---|---|
| hal_wifi_default_exec | ✅ 存在 |
| hal_wifi_supplicant_default_exec | ✅ 存在 |
| hal_wifi_hostapd_default_exec | ✅ 存在 |
| vendor_file | ✅ 存在 |
| **vendor_init_rc** | ❌ **不存在** |
| **wifi_vendor_data_file** | ❌ **不存在**（deploy 的 selinux 片段用了它，是错的） |

`vendor_file_contexts`（ramdisk 内）确有 wifi 专属规则：
```
/vendor/bin/hw/hostapd                       -> hal_wifi_hostapd_default_exec
/vendor/bin/hw/wpa_supplicant                -> hal_wifi_supplicant_default_exec
/vendor/bin/hw/android.hardware.wifi@1.0-service -> hal_wifi_default_exec
/data/vendor/wifi/wpa(/.*)?                  -> wpa_data_file
/data/vendor/wifi/hostapd(/.*)?              -> hostapd_data_file
```
但**没有任何** `/vendor/etc/init/*.rc` 的 vendor 专属规则；rc 文件靠 `plat_file_contexts` 的
`/(vendor|system/vendor)/etc(/.*)?  -> vendor_configs_file` 兜底。

### 结论与建议
* **`androidboot.selinux=permissive`（当前 boot 实际 cmdline，已从 `out/boot_r3f_final.img` 解析确认）**：
  无标签/标签不对都**不阻塞**，WiFi 可以跑。**当前状态可上板。**
* **转 enforcing 前必须做 3 件事**：
  1. **修正标签片段**：把 `deploy/selinux/file_contexts_fragment.txt` 里的
     `vendor_init_rc` → **`vendor_init_rc` 不存在，改用 `vendor_configs_file`**；
     `wifi_vendor_data_file` → **改用 `wpa_data_file` / `hostapd_data_file`**（这两个 sepolicy 里真实存在）。
  2. 片段并进 `/system/etc/selinux/plat_file_contexts`（或 vendor 侧），否则重打镜像会丢标签。
  3. 重打镜像后跑一次 `restorecon -R /vendor`（或挂载后 `setfiles`）。
* `vendor_hwservice_contexts` 已有 wifi 全套（`IWifi` / `ISupplicant` / `IHostapd` / `IOffload` /
  `IKeystore`），说明**框架原本就预期这几个 HAL 存在**，注册 hwservice 不会有策略缺口。

---

## 四、rc 文件接线结论（决定 WiFi 开关能否工作）

### 4.1 会被 init 解析吗？—— 会
* Z1 的 `init` 二进制（ramdisk 内 `init`，1,637,512 B）内含三个扫描路径字符串：
  `/vendor/etc/init`、`/system/etc/init`、`/odm/etc/init` → **`/vendor/etc/init/*.rc` 会自动 import**。
* ramdisk `init.rc` 里 `symlink /system/vendor /vendor`；`fstab.z1` 把 p4 挂 `/system`，
  system-as-root 镜像里 `vendor/` 就位于 `/system/vendor` → **`/vendor` 路径可达**，rc 能被找到。
* init 的 service 选项关键字表实测含 `class` / `disabled` / `oneshot` / `interface` / `capabilities` /
  `socket` / `user` / `group` / `onrestart` / `critical` / `namespace` 等 —— **`interface` 关键字存在**，
  说明这是带 lazy-HAL 支持的 P 版 init，`wpa_supplicant.rc` 的 `interface` 行会被正确解析。
* `init.rc` 里 `class_start hal` 存在 → `class hal` 的 wifi HAL 服务会被启动。

### 4.2 wpa_supplicant 是 `disabled oneshot` —— 按需拉起，不自启
```ini
service wpa_supplicant /vendor/bin/hw/wpa_supplicant \
    -O/data/vendor/wifi/wpa/sockets -puse_p2p_group_interface=1 -g@android:wpa_wlan0
    interface android.hardware.wifi.supplicant@1.0::ISupplicant default
    interface android.hardware.wifi.supplicant@1.1::ISupplicant default
    class main
    socket wpa_wlan0 dgram 660 wifi wifi
    disabled
    oneshot
```
* **`disabled`** = 不随 class main 自动启动；**`oneshot`** = 退出后不重启。
* 拉起机制 = `interface ...::ISupplicant default` 两行 → hwservicemanager 收到 `getService()` 未命中时
  写 `ctl.interface_start` 属性。**已实测 `hwservicemanager` 二进制含 `ctl.interface_start` 字符串** ✅
  → P 版 lazy HAL 机制在位，**框架首次要 supplicant 时会自动拉起，无需手写 `start`**。
* ⇒ **这正是 WiFi 开关能工作的形态**：Settings 拨开关 → `WifiService` → `SupplicantStaIfaceHal` 取
  `ISupplicant` → hwservicemanager 拉起服务。旧报告"决定 WiFi 开关能否工作"的答案是：
  **机制正确，具备可工作条件**。

### 4.3 其它两个 rc
| rc | 关键行 | 形态 | 影响 |
|---|---|---|---|
| `android.hardware.wifi@1.0-service.rc` | `class hal`，无 disabled | **自启**（`class_start hal`） | vendor wifi HAL 常驻；`IWifi.start` 成败见 §〇.2 |
| `hostapd.android.rc` | `class main` + `disabled` + `oneshot` + `interface ...::IHostapd` | 按需拉起 | 只在开热点时启动；不开则零影响 |
| `wpa_supplicant.rc` | `disabled` + `oneshot` + 2×`interface` | 按需拉起 | 见上 |

### 4.4 rc 里 `on post-fs-data` 的目录创建
```
mkdir /data/vendor/wifi 0771 wifi wifi
mkdir /data/vendor/wifi/wpa 0771 wifi wifi
mkdir /data/vendor/wifi/wpa/sockets 0770 wifi wifi
```
ramdisk `init.rc` 已先建 `/data/vendor 0771 root root` 与 `/data/vendor/tombstones/wifi`，
**没有** `wpa`/`wpa/sockets` → 本 rc 的 mkdir 是必要补充，无冲突。✅

### 4.5 ⚠ 一个潜在冲突（需上板确认，本轮未解决）
* `/bin/wpa_supplicant`（LOS14 2.6 legacy 版，1,252,528 B）与新增 `/vendor/bin/hw/wpa_supplicant`
  （v2.7-devel-9 HIDL 版，1,485,140 B）**共存**。
* 实测 ramdisk 内所有 rc（`init.rc` / `init.z1.rc` / `init.z1.network.rc` / `ueventd*.rc`）
  **没有任何 service 启动 `/bin/wpa_supplicant`** —— 现存的 `/bin/z1_wpa_start.sh` 也**未被任何 rc 引用**，
  只能手工跑。⇒ **当前不会自动双开**，框架模式默认独占 wlan0，安全。
* 但若上板后手工跑过 `z1_wpa_start.sh`，再拨框架开关，两者会抢 `wlan0`。
  **纪律：框架 WiFi 与手工脚本二选一。** 后续建议在 `/bin/z1_wpa_start.sh` 头部加互斥保护。

### 4.6 vintf manifest 合并已生效（实测）
`/vendor/etc/vintf/manifest.xml`：1,226 inode，Mode 0644，Size 4543 → **5825**（+1282 B），
XML 解析通过、`hal` 节点 12 → **16**，新增 `android.hardware.wifi` / `.supplicant` / `.hostapd` 三条 ✅。
`/compatibility_matrix.xml` 中三者均 `optional="true"`（wifi 1.0-2 / hostapd 1.0 / supplicant 1.0-1），
⇒ **声明是"启用开关"而非"强制项"，装了即走 HIDL 路径，不装也不失败。**

---

## 五、落地后仍缺什么，才能让设置里的 WiFi 开关真正可用

按"阻塞度"排序，全部基于本轮实测：

| # | 缺口 | 阻塞度 | 现状与说明 |
|---|---|---|---|
| 1 | **`wlan0` 必须真的出现** | 🔴 硬阻塞 | `wifi_wait_for_driver_ready()` 只轮询 `/sys/class/net/wlan0` 100×200ms，超时 -6 → `wifi_initialize` 失败。驱动链是既有 `z1_6620`(6620_launcher) + `z1_wifi_probe.sh`(`echo 1 > /dev/wmtWifi`)。**system 侧无法代劳，取决于 CONNSYS 上电/固件下载是否成功。** |
| 2 | 模块名/路径 | ✅ 已不成问题 | 已证 `wifi_load_driver()` 是属性桩（§〇.2），**不需要** `/vendor/lib/modules/wlan.ko`，**不需要**给 `wlan_gen2.ko` 改名。镜像里 `/lib/modules/z1-radio/wlan_gen2.ko` 就位（vermagic `7.0.0-rc7-z1+` 匹配）。 |
| 3 | `wlan.driver.status` 属性 | 🟡 低 | 由 `wifi_load_driver()` 自动置 `ok`；既有 `z1_wifi_probe.sh` 明确"从不设置该属性"。上板应见 `getprop wlan.driver.status` = `ok`（vendor HAL 起来后）。 |
| 4 | **bcmdhd GOOGLE_OUI 特性层** | 🟡 降级 | `libwifi-hal.so` 是 bcmdhd 构建，`/sys/module/dhd/parameters/firmware_path`、`dhd` 等 bcmdhd 痕迹在；GSCAN/llstats/APF/国家码 vendor 命令 MTK wlan_gen2 不应答 → 框架**逐项降级**，不影响扫描/连接。日志会有对应 error，属预期。 |
| 5 | wificond 侧 | ✅ 已有 | `/bin/wificond` 在镜像内 + `/etc/init/wificond.rc`（`class main`，`user wifi`，`NET_RAW/NET_ADMIN`）。不需补。 |
| 6 | framework 侧 HIDL stub | ✅ 已有 | `wifi-service.jar`（761,841 B）classes.dex 实测含 **581 处** `Landroid/hardware/wifi/…`，`IWifi` + `ISupplicant V1_0/V1_1` + `ISupplicantStaIface/Network` 全套 stub。**不需补 jar。** |
| 7 | SELinux | 🟢 permissive 下无碍 | 见 §三。转 enforcing 时要先修片段里两个不存在的类型名。 |
| 8 | 手工/LOS14 supplicant 互斥 | 🟡 纪律项 | 见 §4.5，当前无自动双开，但需人工避免。 |
| 9 | hostapd / 热点 | ⚪ 未验 | HIDL hostapd + wlan_gen2 `start_ap` 未上板验。不开热点则服务永不启动，零影响。 |
| 10 | `wpa_supplicant_overlay.conf` 落点 | 🟡 待确认 | 新件落 `/vendor/etc/wifi/wpa_supplicant_overlay.conf`（`disable_scan_offload=1`），镜像**原有** `/etc/wifi/wpa_supplicant_overlay.conf`（`p2p_no_group_iface=1` + `driver_param=…`）仍存在。supplicant 同时读两处（AOSP 惯例），内容互补不冲突；但若发现 p2p 行为异常，优先看这里。 |

### 上板验证清单（刷入后照抄执行）
```sh
# ① 文件与标签
ls -l /vendor/bin/hw/android.hardware.wifi@1.0-service /vendor/bin/hw/wpa_supplicant /vendor/lib/libwifi-hal.so
getfattr -n security.selinux --only-values /vendor/bin/hw/wpa_supplicant   # 期望 hal_wifi_supplicant_default_exec

# ② 动态库齐不齐（最直接的"能否跑起来"判据）
/vendor/bin/hw/wpa_supplicant -h            # 能打 usage 即 NEEDED 全解析
/vendor/bin/hw/android.hardware.wifi@1.0-service --help 2>&1 | head

# ③ 驱动是否就位（真正的卡点）
ls -d /sys/class/net/wlan0
getprop wlan.driver.status                  # 期望 ok（vendor HAL 起后）

# ④ HAL 注册
lshal | grep -i wifi                        # 期望 android.hardware.wifi@1.0::IWifi/default
                                            #      + wifi.supplicant@1.1::ISupplicant/default
ps -A | grep -E 'wificond|wpa_supplicant|wifi@1.0'
dumpsys wifi | head -40

# ⑤ 拨设置里 WiFi 开关后
logcat -c; logcat | grep -iE 'wifi|wificond|supplicant|IWifi|WifiVendorHal'
#   sain 标志: ISupplicant 注册 + wificond 扫描出结果
#   预期内 error: "Timed out waiting on Driver ready" 或 WifiVendorHal 相关（若 wlan0 未起）
#   危险信号: avc: denied（SELinux）、cannot locate symbol、缺 .so
```

---

## 六、纪律与回滚

* **源镜像全程只读**：`out/z1_r3f_system/system.img` sha256 与 mtime 均未变
  （`ebb58810…` / `2026-10-27 22:26:27`），本轮所有 `debugfs` 调用**未加 `-w`**（读操作后 mtime 实测未变）。
* **未刷写、未碰设备**（本轮零设备操作）。
* 所有"存在/缺失/已写入"判断都基于 `ls -l` 目录列表或 `dump` 回读，**未使用 stat 退出码**（本项目已知坑14）。
* **回滚件**：`out/z1_r3f_system/system.img`（`ebb58810…`，原 r3f）。
* **刷写**（待批）：`tools/flash_with_retry.sh android out/z1_r5_system/system.img.wifi_hal <回滚件>`；
  先起 mtkclient 再按开机键；GPT 名 `android`。
* **并发写入警告**：`out/z1_r5_system/` 本轮有多个 agent 产出
  （`system.img.audio_tools`、`system.img.r5_final`、`WIFI_HAL_LANDING.md` 各属不同任务）。
  **整合时请以本报告的 sha256 为准核对 `system.img.wifi_hal`**；若后续有人再写同名文件，须重跑本报告验证。
* 根盘 `/dev/sda3` 已 99%（余约 1.2 G），**再做镜像副本前先清空间**。
