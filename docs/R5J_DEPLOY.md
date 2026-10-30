# R5J 部署报告

日期：2026-10-30 · 执行：r5j 部署工程师（subagent）
基线：`out/z1_r5_system/system.img.r5i_final`，sha256 `35616bfd23320b32d195083442670c019309e09ff6d4cc9d7eae0faba44fd1af`，943,718,400 B

---

## 一、结论（先说最重要的一句）

**r5j 与 r5i 逐字节相同 —— sha256 完全一致，0 新增 / 0 修改 / 0 删除。**

```
35616bfd23320b32d195083442670c019309e09ff6d4cc9d7eae0faba44fd1af  out/z1_r5_system/system.img.r5j_final
35616bfd23320b32d195083442670c019309e09ff6d4cc9d7eae0faba44fd1af  out/z1_r5_system/system.img.r5i_final
```

原因：**任务书里的三件事，前两件已经完整在 r5i 里了，第三件的前提陈述有误（不需要做）。**
我没有为了"产出差异"而硬塞改动 —— 三件都逐项实测验证通过，r5j 就是 r5i 的**经验证副本**。

| 任务项 | 实测结论 |
|---|---|
| 1. WiFi 框架 HAL | **已完整部署在 r5i**（17 文件 sha 全对 + xattr 全对 + NEEDED 全解析 + manifest 已合并），无需动作 |
| 2. 充电器 .ko | **镜像内的才对，stage 那个是旧件**（任务书的方向反了）；A6 加载段已在，**R5/R6 内核都带 MFD cell**，无需换件 |
| 3. GPS 缺库 | **前提有误 —— 8 个 libloc* 是 Qualcomm HAL 的，本机 MTK HAL 一个都不需要**；"自引用"是把 SONAME 误读成 NEEDED。无需动作 |

---

## 二、任务项 1：WiFi 框架 HAL —— 已部署，逐项复核通过

### 2.1 17 个交付件：sha256 与素材 `SHA256SUMS` 逐个比对，**全部一致**

镜像内（`/tmp/mnt_r5i` 只读挂载，`objdump -p` / `sha256sum`）对 `out/z1_wifi_hal_stage/SHA256SUMS`：

| 镜像内路径 | 字节 | sha256（前 16） | 对 stage | xattr |
|---|---|---|---|---|
| `/vendor/bin/hw/android.hardware.wifi@1.0-service` | 272,272 | `759c5d0d3bac9609` | OK | `hal_wifi_default_exec` |
| `/vendor/bin/hw/wpa_supplicant` | 1,485,140 | `fd24bb69310cd450` | OK | `hal_wifi_supplicant_default_exec` |
| `/vendor/bin/hw/hostapd` | 506,476 | `dbc597fb8045f13d` | OK | `hal_wifi_hostapd_default_exec` |
| `/vendor/lib/libwifi-hal.so` | 95,116 | `0a9ba41186680851` | OK | `vendor_file` |
| `/vendor/lib/libwpa_client.so` | 24,332 | `779da252d0584688` | OK | `vendor_file` |
| `/vendor/lib/libkeystore-wifi-hidl.so` | 16,132 | `06f79611c22c8b3e` | OK | `vendor_file` |
| `/vendor/lib/libkeystore-engine-wifi-hidl.so` | 20,796 | `a9a454cd47674781` | OK | `vendor_file` |
| `/lib/android.hardware.wifi@1.0.so` | 971,596 | `19366d6787e0264f` | OK | `system_file` |
| `/lib/android.hardware.wifi@1.1.so` | 184,188 | `f150293983b921d4` | OK | `system_file` |
| `/lib/android.hardware.wifi@1.2.so` | 518,992 | `f00f3423696e49fa` | OK | `system_file` |
| `/lib/android.hardware.wifi.supplicant@1.0.so` | 1,307,324 | `ae2898095661d16b` | OK | `system_file` |
| `/lib/android.hardware.wifi.supplicant@1.1.so` | 489,088 | `55eb906f3441895f` | OK | `system_file` |
| `/lib/android.hardware.wifi.hostapd@1.0.so` | 84,508 | `685de40502cfaf03` | OK | `system_file` |
| `/vendor/etc/init/android.hardware.wifi@1.0-service.rc` | 173 | `51e16c30b99057e2` | OK | `vendor_init_rc` |
| `/vendor/etc/init/hostapd.android.rc` | 599 | `55768d655d907205` | OK | `vendor_init_rc` |
| `/vendor/etc/init/wpa_supplicant.rc` | 1,508 | `7f70dad82e37241b` | OK | `vendor_init_rc` |
| `/vendor/etc/wifi/wpa_supplicant_overlay.conf` | 23 | `621564704ba0c124` | OK | `vendor_configs_file` |

模式全部正确（3 个 service 0755，其余 0644）。**17/17 件、17/17 sha、17/17 xattr 全中。**

### 2.2 SELinux 标签

全部 17 件都有 `security.selinux` xattr。注意一处与 `README_DEPLOY.md` 的差异（**是改进，不是问题**）：
overlay.conf 实际标签是 `u:object_r:vendor_configs_file:s0`，而 stage 脚本 `CTX[]` 写的是 `wifi_vendor_data_file`。镜像里用了 AOSP 标准的 `vendor_configs_file`，对 `/vendor/etc` 下的配置**更正确**，不改。

顺带纠正一个数字：任务书说"75 文件 SELinux xattr"，实测**全镜像 2,628 个路径带 `security.selinux`**（`getfattr -R`）。前轮 75 指的应是某一次增补批次，不是镜像总量。这不影响结论，只是别按 75 去核对。

### 2.3 NEEDED 全解析（我自己跑的，不依赖前轮结论）

用 `objdump -p` 提取 NEEDED，再对 `/lib` `/vendor/lib` `/vendor/lib/egl|hw|dri` 逐个查存在性：

- `android.hardware.wifi@1.0-service` — 17 个 NEEDED **全 OK**
- `wpa_supplicant` — 18 个 **全 OK**
- `hostapd` — 15 个 **全 OK**
- `libwifi-hal.so` / `libwpa_client.so` / `libkeystore-wifi-hidl.so` / `libkeystore-engine-wifi-hidl.so` — **全 OK**

**0 个 MISS**。`libwifi-hal.so` 无芯片私有依赖（与 README §二 的"核心 nl80211"一致），`libnl.so`/`libcrypto.so`/`android.system.wifi.keystore@1.0.so` 都在位。

### 2.4 manifest.xml 与 FCM

- `/vendor/etc/vintf/manifest.xml`（5,825 B）**已含** wifi 三条：`@1.0::IWifi/default`、`@1.1::ISupplicant/default`、`@1.0::IHostapd/default`，且 `python3 xml.dom.minidom.parse` **解析通过**。
- FCM 侧：`/etc/vintf/compatibility_matrix.{1,2,3,legacy}.xml` 里 android.hardware.wifi / wifi.supplicant / wifi.hostapd / wifi.offload **11 条全部 `optional="true"`**（逐个 `getAttribute('optional')` 读过，非 grep 猜）。**确认"失败 = 服务起不来，无 bootloop 风险"。**

### 2.5 该不该再跑一次 `deploy_debugfs.sh`？**不该**

我把脚本读完了：它纪律良好（源只读、输出另开文件、写后断言 `Type: regular`、强制 e2fsck）。但它做的是**无条件 `debugfs write` 覆盖 + 再合并 manifest**。对当前 r5i 跑它属于**纯覆盖、零收益**，还要额外吃 900MB 磁盘（现仅 1.8G 可用的 99% 满盘）。**决定：不跑。**所有它要做的，r5i 已达成且经本文独立验证。

---

## 三、任务项 2：充电器 .ko —— **方向判定为"保持不动"，且理由与任务书预期相反**

### 3.1 判定：**保持镜像内现有件，不要用 stage 的**

任务书假设"`out/z1_charger_stage/` 是 R6 顺产的新版"。**实测相反**：

| | 镜像内 `/lib/modules/z1_charger.ko` | `out/z1_charger_stage/z1_charger.ko` |
|---|---|---|
| sha256 | `99cfda1af7587b407f58fb53b00293af9699baf4619dc5ba38c89871aa8f571b` | `a80355567f2b8372bcfc05b564a01e600a965baa795de386c3f3d18d4b3ee13f` |
| 大小 | 10,148 B | 10,100 B |
| mtime | 2026-10-28 13:08 | 2026-10-28 **08:35（更旧）** |
| vermagic | `7.0.0-rc7-z1+` | `7.0.0-rc7-z1+` |

**决定性证据（我 `cmp` 逐字节复核过）：镜像内那件与 R5、R6 两个内核归档件逐字节相同**：

```
99cfda1af7587b407f58fb53…  bridge_backups/products/products_r5_kernel_20261028_124942/ko/z1_charger.ko
99cfda1af7587b407f58fb53…  bridge_backups/products/products_r6_kernel_20261030_010537/z1_charger.ko
99cfda1af7587b407f58fb53…  /tmp/mnt_r5i/lib/modules/z1_charger.ko      ← cmp IDENTICAL
```

而 stage 那件是 08:35 的**更早**产物，比内核归档件旧 48 字节的 modinfo（缺 `intree=Y`，因为它走 `M=` 外编路径，modpost `-e` 标成 external）。**若按任务书"换新的"去刷 stage 件，是降级**（并多一条 `loading out-of-tree module taints kernel`；功能等价，但没必要）。

**判定依据一句话：镜像内件 = R5/R6 内核归档顺产件；stage 件 = 更旧的外编件。保持不动。**

### 3.2 `load_audio.sh` 的 A6 加载段：**已存在**，位于 `/lib/modules/load_audio.sh`（不是 `/vendor/bin`）

sha `ce96b3041b25b1a1…`（3,657 B）。第 74-86 行原文：

```sh
# --- Z1 charger / battery (MT6323 RGS_CHRDET) ---
# z1_charger.ko sits in this same directory and its MFD cell + DT node are in
# the delivered kernel, but nothing loaded it, so the charger never probed and
# the health HAL reported status UNKNOWN with no capacity. Load it here (same
# hook, same idempotent helper) so the power_supply shows up for the framework.
load z1_charger.ko z1_charger

if [ -d /sys/class/power_supply ]; then
    for psy in /sys/class/power_supply/*; do
        [ -e "$psy/type" ] || continue
        log "psy $(basename "$psy"): type=$(cat "$psy/type" 2>/dev/null) online=$(cat "$psy/online" 2>/dev/null) status=$(cat "$psy/status" 2>/dev/null)"
    done
fi
```

加载名 `z1_charger` 与实际文件名 `z1_charger.ko` **一致**。调用方在 ramdisk：`init.z1.network.rc` → `on post-fs / exec_background … /system/lib/modules/load_audio.sh`。

### 3.3 **r5j 配哪个内核都对 —— 任务书这个担心不成立**

任务书说"R6 内核才带 charger MFD cell 的正确形态；若 r5j 配 R5 内核，charger 可能仍不 probe"。**实测两边都有**。我把两个 zImage 的 XZ 载荷解出来后直接查字符串：

| 内核 | 载荷大小 | `z1-charger` | `z1,mt6323-charger` |
|---|---|---|---|
| R5 `products_r5_kernel_20261028_124942/zImage` | 15,377,208 B | **1** | **1** |
| R6 `products_r6_kernel_20261030_010537/zImage` | 15,377,336 B | **1** | **1** |

DT 侧同样两边都在。我解了当前队列里**两个** boot.img 的 dtb：

```
out/z1_r5c_boot/boot.img  → dtb sha 1a2c5689770da10b…  charger 节点 "z1,mt6323-charger" ×1
out/z1_r6_boot/boot.img   → dtb sha 1a2c5689770da10b…  charger 节点 "z1,mt6323-charger" ×1
```

（两个 boot.img 的 dtb 是**同一个** `1a2c5689`，与 `STATUS_20261030.md` 记的"R6 boot 用 r5c 的新版 dtb"一致。）

**结论：R5 和 R6 内核都带 MFD cell + DT 节点，r5j 配任一个充电器都能 probe。**换 R6 的理由是别的（`mt6323-thermal`、消灭 cpu-freq 报错），**不是** charger。

---

## 四、任务项 3：GPS "缺库" —— **前提经实测不成立，不需要补任何库**

### 4.1 "`gps.default.so` NEEDED 含它自己" —— **是把 SONAME 误读成了 NEEDED**

任务书来源 `out/Z1_FEATURE_GAP_FINAL.md:111` 写"`readelf -dW` 显示 NEEDED 含 `gps.default.so`（自己）"。我用**两种独立方法**验证，**都否定这个说法**：

```
$ readelf -dW vendor/lib/hw/gps.default.so | grep -iE 'NEEDED|SONAME'
  NEEDED  libxlog.so / liblog.so / libcutils.so / libhardware.so / libc.so / libstdc++.so / libm.so
  SONAME  gps.default.so          ← 是 SONAME，不是 NEEDED

$ objdump -p vendor/lib/hw/gps.default.so | grep -iE 'NEEDED|SONAME'
  （同样 7 个 NEEDED + SONAME gps.default.so）
```

完整 dynamic 段（27 项）显示 `libxlog.so` 是**第 1 个** NEEDED，`gps.default.so` 出现在段**最末第 25 项**且类型是 `0x0000000e (SONAME)`。一个只有 7 个 NEEDED 的 65,453 B 老库，段里不可能塞下 8 个 loc 库。

### 4.2 那 8 个 `libloc*` 属于 **Qualcomm** HAL，本机 MTK HAL **一个都不用**

`libgps.utils / libloc_core / libloc_api_v02 / libgeofence / libizat_core / libsam / libloc_eng / libloc_ds_api` 这 8 件确实在 `out/z1_hal_a9/vendor/lib/`（jfltexx 素材）备着，但它们的消费者是 **`gps.msm8960.so`**（同为素材件，NEEDED 正是这 8 个 + libc++/libdl），**那是 msm8960 的 HAL，不是 MTK 的**。镜像里只有 MTK 的 `gps.default.so`（MT3326 系，导出 `mt3326_init/start/stop`、`send_cmd_to_agpsd`），它不引用任何一个 loc 库。把 QCOM 栈拷进去是**错误方向**。

### 4.3 决定性方法：**全镜像 missing-NEEDED 扫描**

不靠人工挑文件，我对镜像里**每个 ELF**（`.so` + 所有可执行）提取 NEEDED，逐个在 `/lib` `/vendor/lib` `{egl,hw,dri}` 下查存在性：

```
MISSING libfile_op.so      <- /vendor/lib/libhwm.so
MISSING libextsys.so       <- /lib/libbtsession.so
MISSING libbtcust.so       <- /bin/mtkbt
MISSING libbtcust.so       <- /bin/btconfig
MISSING libbtcusttable.so  <- /bin/btconfig
MISSING libstlport.so      <- /vendor/bin/mtk_agpsd
MISSING libssladp.so       <- /vendor/bin/mtk_agpsd
```

**`gps.default.so`、`mnld`、`libmnlp_mt6572` 一个 MISS 都没有。**`mnld` 的 closure 完整：`libxlog` / `libcutils` / `libc` / `libm` / `libnvram`（在 `/vendor/lib`，找得到）/ `libstdc++` **全 OK**。

**结论：GPS 路径没有任何缺库需要补。**未定义符号我也单独查了（不是只看 NEEDED）：`mnld`、`gps.default.so` 需要的符号在镜像内均有定义者。

### 4.4 顺手发现：扫描出的 7 个 MISS 里，**没有一个在 GPS 路径上**

| MISS | 谁需要 | 是否活路径 | 处置 |
|---|---|---|---|
| `libfile_op.so` | `libhwm.so` ← **`sensors.mt6572.so`** | 是（传感器 HAL，但见下） | 素材在 `los14/system/lib/libfile_op.so`（46,196 B，导出 `FileOp_BackupToBinRegion_All` 正是 libhwm 的未定义符号） |
| `libextsys.so` | `libbtsession.so` ← `/bin/mtkbt` | 是（BT，但无 rc 拉起 mtkbt） | **全机找不到素材**（`/`、`/mnt`、`/media`、los14 全树 0 命中） |
| `libbtcust.so` / `libbtcusttable.so` | `mtkbt` / `btconfig` | 否 — 两者都无 rc/service 拉起 | 素材在 `out/z1_bt_stage/`（但 `bluetooth.default.so` 时隐时现于构建，且 BLUETOOTH 本轮不在 scope） |
| `libstlport.so` / `libssladp.so` | **`mtk_agpsd`** | **否 — `mtk_agpsd` 在镜像和 ramdisk 里都没有任何调用方** | 死重量，不用管 |

**关于 `libfile_op` 这一条我没有动，理由要讲清楚（避免下一次误判为"漏做"）：**
- 我先查了 `sensors.mt6572.so` 的未定义符号：**它一个 `FileOp_*` 符号都不引用**。缺库出在 `libhwm.so` 内部，所以 `sensors.mt6572.so` 是否 `dlopen` 到 `libhwm`、以及是否真走到那条路径，**我没能证明**。
- 即便要修，**同一次加载里还有一道更早的墙**：`sensors.mt6572.so` 是 26,100 B，而原厂件更大 —— 它很可能是 **R9 触摸驱动轮（`z1_cst836u`）的占位/stub**，要等那一轮才定稿。现在补 `libfile_op` 可能白补。
- 补库会**动一个 r5i 已验证的交付件**，与任务书"不破坏既有交付"冲突。**故列为未决项，不在 r5j 动手。**
- 另外 `sensors@1.0-service.rc` 的 exec 路径写的是 `…@1.0-service.jf`，而实际文件是 `…@1.0-service`（**无 `.jf`**）→ init exec 必 ENOENT。这条**前轮文档 `out/Z1_REMAINING_GAPS_20261028.md:270` 已记录**，非本次新发现，一并列出但不改（属 R9 范畴）。
- 同批 rc 路径审计里，**只有 sensors 这一条 MISSS**，其余 14 条 service exec 路径**全部 OK**（含 WiFi 三条）。

### 4.5 `MTK_GPS_CHIP=MT6620` 未定论项：**证据指向 `MT6620` 是错的**

顺带查了（任务书要求"能给证据就给"）：

1. **`MTK_GPS_CHIP` 这个字面量在镜像里 0 命中** —— 确认任务书的观察。它只在 ramdisk 的 `/z1_gps_prep.sh`（源 `z1a9_gpt/scripts/ramdisk_v9/z1_gps_prep.sh:11`）里，写成 `printf 'MTK_GPS_CHIP=MT6620\n' > /data/misc/GPS_CHIP.cfg`。
2. **`mnld` 期望的格式不是这个**：它的 strings 里有 `/data/misc/GPS_CHIP.cfg`、`%d`，以及一张 **`0x` 十六进制**芯片表（`0x6620 0x6628 0x6630 0x6572 0x6571 0x8127 0x6582 0x6592`）和 `we get MT6620` / `we get MT6572` 等分支。也就是说它按 `%d` 解析一个 **hex 数字**，而 `MTK_GPS_CHIP=MT6620\n` **既不是数字、也不匹配它认的键名形式**。
3. **手机自己的原厂 ramdisk 定的是 `0x6572`，不是 `0x6620`**：`out/forensic/live/rd/meta_init.rc:286` 与 `init.charging.rc:610` 均有
   `setprop persist.mtk.wcn.combo.chipid 0x6572`（带注释 `# set comob chipid 6572`）。
4. 旁证：镜像里 **只有 `libmnlp_mt6572`**，没有任何 `libmnlp_mt6620`。

**判定：`MT6620` 高置信度为误值，正确的是 `0x6572`（且格式应是 hex 数字，非 `MTK_GPS_CHIP=…`）。**
**仍未确定的部分（不猜）**：`mnld` 的 `chip_detector` 到底优先读 `/data/misc/GPS_CHIP.cfg` 还是 `persist.mtk.wcn.combo.chipid` —— `mnld` 已 strip，我没做完整反汇编；且当前 LOS16 的 `build.prop` 里该 property **未设置**（ramdisk 也没设）。**证据缺口：一次上板 `getprop persist.mtk.wcn.combo.chipid` + `cat /data/misc/GPS_CHIP.cfg`。** 修法归 GPS 轮，本轮不改。

---

## 五、与 r5i 的递归 diff

```
r5j_final vs r5i_final：sha256 相同 → 文件树、内容、inode 布局、xattr 全部一致
新增 0 · 修改 0 · 删除 0 · 意外删除 0
```

因二者是同一份字节（`cp -n` 且 sha 复核），无需再做条目级 diff；这本身即"0 意外删除"的最强证明。

---

## 六、保留项复核（mesa / 音频 / perf 未被破坏）

| 项 | r5i 基线 | 复核实测 | 判定 |
|---|---|---|---|
| `vendor/lib/egl/libGLES_mesa.so` | 94,488 B | 94,488 B，sha `aab57fd4a5bc0f02` | 原样 |
| `vendor/lib/dri/gallium_dri.so` | 4,662,060 B | 4,662,060 B，sha `f7497e4a2090b173` | 原样 |
| 3 个 dri symlink | `lima/swrast/kms_swrast` → `gallium_dri.so`（14 B） | 3/3 指向正确 | 原样 |
| `vendor/lib/egl/libGLESv2_mesa.so` | symlink → `libGLES_mesa.so`（15 B） | 正确 | 原样 |
| `vendor/lib/hw/audio.primary.mt6572.so` | 8,196 B | 8,196 B，sha `73d22b253acce390` | 原样 |
| `build.prop` | 3,621 B | 3,621 B，sha `3e1e0d9fc0b725b0` | 原样 |
| `ro.hardware.egl=mesa` | 存在 | 存在，**未被覆盖** | 原样 |
| perf 3 键 | r5h 加入 | 存在（`r5h perf append` 段） | 原样 |
| `lib/modules/z1_charger.ko` | 10,148 B | sha `99cfda1af7587b40` | 原样（且为正确件） |
| `lib/modules/load_audio.sh` | 3,657 B | sha `ce96b3041b25b1a1` | 原样（A6 段在） |

**逐项回读确认：mesa + 音频 + perf + charger 均未被破坏。**

---

## 七、上板验证步骤（刷完 grep 什么、期望什么、失败分叉）

刷写对象（**不要覆盖 r5i_final**）：
```
out/z1_r5_system/system.img.r5j_final   35616bfd…（与 r5i 同件）
回滚件：out/z1_r5_system/system.img.r5h_final（f009bbee…）
刷写命令须带 Z1_VERIFY=0（见 z1a9_gpt/docs/STATUS_20261030.md:8）
```
boot 段按单变量原则先用 `out/z1_r5c_boot/boot.img`（charger 已可 probe，见 §3.3）。

### 7.1 WiFi HAL（本轮最关心）

```sh
# 1) 服务是否注册（需先开一次 Settings 里的 WiFi 开关以触发 hwservicemanager 按需拉起）
lshal | grep -iE 'wifi|supplicant'
#   期望：android.hardware.wifi@1.0::IWifi/default
#         android.hardware.wifi.supplicant@1.1::ISupplicant/default
#   （hostapd 未启动属正常 —— 只有开热点才拉起）

ps -A | grep -E 'wpa_supplicant|android.hardware.wifi'
#   期望：开开关后出现 /vendor/bin/hw/wpa_supplicant

# 2) 服务侧日志
logcat -d | grep -iE 'WifiVendorHal|wpa_supplicant|ISupplicant|Failed to start vendor HAL'
#   期望（正常）：WifiVendorHal: Failed to start vendor HAL   ← 预期内，非故障
#     原因：libwifi_hal 的 DriverTool::LoadDriver 硬编码 /vendor/lib/modules/wlan.ko，
#     本机脚本加载的是 wlan_mt6620.ko → 必败 → 框架降级为 wificond+supplicant 模式
#     （README_DEPLOY.md §四 已定案；扫描/连接/信号不受影响，丢的是 llstats/APF/连接期 MAC 随机化）

# 3) 功能面
dumpsys wifi | grep -iE 'mWifiState|hal'
#   期望：WiFi 能开、能扫描出 AP
```

**失败分叉：**
- `lshal` 里**没有** ISupplicant → 查 `logcat | grep -i hwservicemanager` 是否 `Cannot find entry android.hardware.wifi.supplicant@1.1::ISupplicant/default`。若报这条，则 manifest 没被读到（本轮已验 manifest 含该 fqname 且 XML 合法，概率低）。
- supplicant 反复重启 → 查 `/data/vendor/wifi/` 目录是否建成（rc 的 `on post-fs-data` 三行）；查 `/vendor/etc/wifi/wpa_supplicant.conf` 的 `ctrl_interface`。
- **WiFi 开关能开但连不上** → 走驱动/固件链（`z1_wifi_bt_load.sh` insmod 顺序、`/etc/firmware/WIFI_RAM_CODE_SOC`、`/etc/firmware/nvram/WIFI`），与本轮 HAL 部署无关。

### 7.2 充电器

```sh
cat /lib/modules/../load_audio.sh >/dev/null   # 存在性
dmesg | grep -iE 'z1.charger|z1-charger|CHRDET'
#   期望：z1_charger probe 成功
#   可接受：'no CHRDET irq in DT, polling only'（2s 轮询回退）
#   失败分叉：'failed to get PMIC regmap' / 'PMIC charger regs unreadable' → 停手报告
ls /sys/class/power_supply/
cat /sys/class/power_supply/z1_charger/{online,type,voltage_now}
dumpsys battery       # 期望 status 不再是 UNKNOWN、有 capacity
```

### 7.3 GPS

```sh
lshal | grep -i gnss                 # 期望 @1.0::IGnss/default
ls -l /dev/stpgps                    # 传输节点
logcat -d | grep -iE 'mnld|gnss|gps' # 期望 mnld 起、HAL dlopen gps.default.so 成功
/usr/bin/gps.default.so 依赖已全解析（本轮已验），若仍崩，查 namespace 而非查缺库
```

### 7.4 回归体检（确认没弄坏老功能）

```sh
getprop ro.hardware.egl              # 期望 mesa
logcat -d | grep -iE 'libGLES_mesa|gallium'   # mesa 19.1.8 生效
tinypcminfo -D 0 -d 0                # 音频路径
ls /vendor/lib/egl/libGLES_mesa.so /vendor/lib/dri/gallium_dri.so   # 均在
```

### 7.5 一个**必须当场注意的上板行为**

ramdisk 的 `init.z1.network.rc` 里有一条：
```
on property:sys.wlan0.up=1
    start z1_wpa_supplicant          ← 启动 /z1_wpa_start.sh，即 LOS14 2.6 legacy supplicant
```
`sys.wlan0.up` 由 `z1_wifi_probe.sh` 在 wlan0 可扫描后发布。届时会**同时存在** legacy supplicant（`/system/bin/wpa_supplicant`，走 `/data/misc/wifi/sockets`）与 HIDL supplicant（`/vendor/bin/hw/wpa_supplicant`，走 `/data/vendor/wifi/wpa/sockets`）。两者抢 `wlan0`，**上游事件在触发才发生**（并非必然）。README §六.3 已预警此并存冲突。

**处置建议（本轮不改，因为 ramdisk 不在 r5j 的可动范围）：** 上板若观察到框架 WiFi 时好时坏，**先停掉 `z1_wpa_supplicant`** 再复测，以区分"HAL 没起"和"两个 supplicant 抢接口"。要彻底解则需在 ramdisk 里摘掉那条 `on property` 或把 `z1_wifi_probe.sh` 的发布条件改掉，**属 boot 段单变量轮**。

---

## 八、未决风险

1. **`sys.wlan0.up=1` → legacy supplicant 抢接口**（§7.5）。已预警，未解；解法在 ramdisk 不在 system。
2. **vendor HAL 降级运行是设计内行为**：`IWifi.start` 必失败（`LoadDriver` 找 `/vendor/lib/modules/wlan.ko`）。功能面可接受，但"WiFi 开关"在框架里走的是 wificond+supplicant 路径；**GOOGLE_OUI 特性层（GSCAN/llstats/国家码）在 MTK 上必然逐项失败**（README §六.2）。**未上板实测过——这是本轮最大的未知。**
3. **`libfile_op.so` 缺失（sensors→libhwm 路径）未修**，理由见 §4.4（`libhwm` 是否真加载未证；`sensors.mt6572.so` 可能是 R9 占位件；补库会动已验证件）。素材可用：`los14/system/lib/libfile_op.so`（46,196 B，导出符号对得上）。
4. **`libextsys.so` 全机无素材**（BT `mtkbt` 路径）。若将来要做 BT 需另找来源。
5. **`sensors@1.0-service.rc` exec 路径带 `.jf` 而文件无 `.jf`** → init ENOENT。前轮已记录（`out/Z1_REMAINING_GAPS_20261028.md:270`），本轮未改（R9 范畴）。
6. **`MTK_GPS_CHIP=MT6620` 的 cfg-vs-property 优先级未定论**（§4.5）。
7. **R5/R6 内核选择未定**：charger 两者皆可（§3.3），但 R6 另有 `mt6323-thermal` / 消 cpu-freq 报错的收益，**建议按 `STATUS_20261030.md` 的"单变量"原则先 r5c 验 r5j，再单独换 R6 boot**。
8. **磁盘**：全程 1.7–1.8G 可用（99% 满）。本轮**只建了 1 份 900MB 副本**（r5j），未建第三份。800MB 级临时文件全部即用即删。

---

## 九、本次改了/建了哪些文件

| 动作 | 路径 | 说明 |
|---|---|---|
| **建** | `out/z1_r5_system/system.img.r5j_final` | 943,718,400 B，sha `35616bfd…`（= r5i 逐字节） |
| **建** | `out/R5J_DEPLOY.md` | 本报告 |
| **建** | `bridge_backups/products/products_r5j_20261030_093845/SHA256SUMS_IMAGES.txt` | r5j/r5i 镜像 sha 清单（**镜像原件留 `out/`，归档只留清单**，磁盘紧） |
| **建** | `bridge_backups/products/products_r5j_20261030_093845/SHA256SUMS_STAGE.txt` | 素材件 sha 清单 |
| **未动** | `out/z1_r5_system/system.img.r5i_final` | **只读，sha 未变**（`35616bfd…`） |
| **未动** | `out/z1_wifi_hal_stage/*` | 只读 |
| **未动** | `out/z1_charger_stage/*` | 只读（判定为不采用） |
| **未动** | 任何 boot.img / ramdisk | 未碰 |

**全程：未刷写、未碰设备。**只用了 `debugfs` 只读查询、`/tmp/mnt_r5i` 既有只读挂载、`/tmp` 临时解包（已全部删除）。

**自我纠正一处**：`cp --reflink=auto` 在本机 ext4（无 reflink 支持）下等于普通复制，第一份拷贝确实吃了 900MB 实盘，之后我改用一次性复制 + 复用既有只读挂载，未再产生多余副本。

---

## 十、诚实声明：与任务书前提不符之处（3 条）

1. **任务项 1 已完成** —— 任务书以为 WiFi HAL"已备好但未部署"，实测**早在 r5d 就进了镜像**（我逐版本查过：r5d/r5f/r5h/r5i 都有 `wpa_supplicant`；mesa 从 r5h 起）。
2. **任务项 2 的方向反了** —— 不是"stage 是 R6 新版、要换进去"，而是"**镜像内的才是 R5/R6 归档顺产件，stage 是更旧的外编件**"；且 R5/R6 内核**都**带 MFD cell，"r5j 配 R5 内核 charger 可能不 probe"的担心不成立。
3. **任务项 3 不成立** —— "`gps.default.so` NEEDED 含自己"是 **SONAME 被误读成 NEEDED**（两种独立方法否定）；8 个 `libloc*` 是 **Qualcomm HAL** 的依赖，本机 MTK HAL 一个都不用。**全镜像扫描证实 GPS 路径 0 缺库。**

我另花力气做了**任务书没要求的全镜像 missing-NEEDED 扫描**，因此把真正存在的 7 处缺库（sensors/BT/agpsd 路径）核清并给出素材位置与"为何本轮不动"的理由（§4.4），避免下一轮重复摸索或误当成 GPS 问题。
