# Z1 媒体/GPS 素材阶段报告 — 2026-10-31

> 本文档由素材工作流各子线共写。本章为 **GPS 库 NEEDED 逐项对账**（GpsPlan）。
> 所有结论来自 2026-10-31 本机实测，命令与输出摘要均随附。

## 0. GPS 结论速览

| # | 结论 | 证据 |
|---|---|---|
| 1 | **HANDOFF_20261029 表 8 对 r5k 已过时**：r5k 镜像内 `gps.default.so` 是 MTK shim，NEEDED 既不含它自己、也不引用 8 个 qcom loc 库 | debugfs dump + `readelf -d`（§3.1）；strings 含 `init.svc.mnld`/`/dev/gps`/"The MTK GPS Source Project"（§3.3） |
| 2 | "NEEDED 含它自己"的真实来源 = jfltexx 的 **`gps.msm8960.so`**（qcom HAL），其 NEEDED 第 11 项恰为 `[gps.msm8960.so]` | `readelf -d out/z1_hal_a9/vendor/lib/hw/gps.msm8960.so`（§2 末行） |
| 3 | **r5k 镜像 GPS 依赖链已全部满足，本轮无需部署任何 so** | §3 四环节逐一 NEEDED 核对，全部在场 |
| 4 | 8 库 + qmi 五件套**不部署**：qcom msm8960 专用栈，MTK 板无 qmuxd/无 /dev/gss，装了无人加载且硬件必失败 | §5 论证；素材归档 `out/z1_media_stage/gps_libs/` 备查 |
| 5 | `jfltexx_extract/` 是**空目录（0 文件）**；8 库真身在 `out/z1_hal_a9/vendor/lib/`（血统 = jfltexx LOS16 A9 ROM，见其 EXTRACT_REPORT.md） | `find -type f \| wc -l`=0；stat 逐一列出（§2） |

## 1. 输入对账

- 任务给的 HANDOFF 路径 `out/HANDOFF_20261030.md` **不存在**；实件在 `HANDOFF_20261030.md`（工作区根，md5 `237f05ec63e7dc358dfe329a5e68c633`，与 `z1a9_gpt/docs/` 副本逐字节同）。其 GPS 内容仅 §5 一行"P1 r5j 部署轮重跑：WiFi HAL+充电器+GPS"。
- **GPS 缺口段真身 = `out/HANDOFF_20261029.md` §3 表第 7/8 条**（行 252-253）：表 8 记"`gps.default.so` 的 NEEDED **含它自己**，且 `/vendor/lib` 下 `libgps.utils/libloc_core/libloc_api_v02/libgeofence/libizat_core/libsam/libloc_eng/libloc_ds_api` **全缺**（jfltexx 原装齐备）"。
- HANDOFF_20261030 §6.5 已自警"交接前提被推翻……对前任文档的'待判定'项应重取证"——本章即重取证结果。

## 2. 八库逐项对账（血统：jfltexx = Galaxy S4, lineage-16.0-20210309-UNOFFICIAL-jfltexx-userdebug.zip, 405,782,606B）

**素材位置**：任务指的 `jfltexx_extract/` 为空目录（法1 `find -type f | wc -l` = 0；法2 `ls -la` 仅 `.` `..`，两法一致）。8 库真身在 `out/z1_hal_a9/vendor/lib/`，其 `EXTRACT_REPORT.md` 声明源包与逐件大小，与本表 stat 值逐一吻合（两法一致）。

| 库 | 大小 B | sha256（前16） | 架构 | 自身 NEEDED（递归一层，排除 8 库互引后外部依赖见加粗） |
|---|---|---|---|---|
| libgps.utils.so | 34,408 | 1252b4bfbdd61346 | ELF32 ARM | libutils, libcutils, liblog, libc++, libc, libm, libdl |
| libloc_core.so | 43,484 | 3a453b3787d4be7d | ELF32 ARM | libutils, liblog, libcutils, libgps.utils*, libc++, libc, libm, libdl |
| libloc_api_v02.so | 98,164 | a95ad6788237a201 | ELF32 ARM | libutils, libcutils, **libqmi_cci**, **libqmi_csi**, **libqmi_common_so**, libloc_core*, libgps.utils*, libloc_ds_api*, libc++, libdl, libc, libm |
| libgeofence.so | 34,080 | 01217e49c254b4b0 | ELF32 ARM | libutils, libcutils, libgps.utils*, liblog, libloc_core*, libizat_core*, libc, libstdc++, libm |
| libizat_core.so | 38,136 | 0202f3202a39371f | ELF32 ARM | libutils, libcutils, libloc_core*, libgps.utils*, libloc_api_v02*, libc, libstdc++, libm |
| libsam.so | 46,400 | 1a2645eeb85ecddb | ELF32 ARM | libc, liblog, libm, libstdc++ |
| libloc_eng.so | 119,812 | 72f5f15e12b946b2 | ELF32 ARM | libutils, libcutils, liblog, libloc_core*, libgps.utils*, libc++, libc, libm, libdl |
| libloc_ds_api.so | 26,292 | b368423358c5f88f | ELF32 ARM | libutils, libcutils, **libqmi_cci**, **libqmi_csi**, **libqmi_common_so**, **libdsi_netctrl**, **libqmiservices**, libgps.utils*, libc++, libdl, libc, libm |

（`*` = 8 库互引；架构两法验证：`readelf -h` 类别 ELF32 + `file` "ELF 32-bit LSB … ARM, EABI5"。）

**递归一层的缺口结算**：8 库在 r5k 之外还连带 **qmi 五件套**（libqmi_cci 30,372B / libqmi_csi 26,268B / libqmi_common_so 5,552B / libdsi_netctrl 128,976B / libqmiservices 63,448B，均 ARM32，sha 见 SHA256SUMS）。qmi 五件 z1_hal_a9 里**没有**（当初只提 35 文件），已从 jfltexx 全量镜像 `downloads_gsi/jflt_ota_v2/system.img`（EXTRACT_REPORT 认定的唯一可信件，1,181,114,368B）`/vendor/lib` debugfs 只读 dump 取得。其余依赖（libutils/libcutils/liblog/libc++/libstdc++/libc/libm/libdl）r5k `/lib` 全在场。

## 3. r5k 镜像 GPS 链现状（全部只读取证：`debugfs -R` 于 system.img.r5k_final，943,718,400B，ext4）

镜像为 system-as-root 布局：系统库在根 `/lib`（不是 `/system/lib`——注意前任 grep 若用错路径会得到假的"全缺"）。

### 3.1 `/vendor/lib/hw/gps.default.so`（65,453B，10-28 patched，sha256 `515f239b2dab4344…`）
NEEDED 实测（readelf -d）：`libxlog.so, liblog.so, libcutils.so, libhardware.so, libc.so, libstdc++.so, libm.so`。
→ **无 qcom loc 库、无自引用**。libxlog.so 在 `/vendor/lib`（5,164B，sha `6406b636…`），其余全在 `/lib`。**HANDOFF 表 8 与此不符，判定表 8 过时**。

### 3.2 gnss HIDL 链（10-27 已部署，jfltexx 同款）
- `/vendor/bin/hw/android.hardware.gnss@1.0-service`（16,024B）：NEEDED 含 `android.hardware.gnss@1.0.so` + libhidlbase/libhidltransport/libhwbinder/libbinder/libutils/libbase/libc++ 等；
- `/vendor/lib/hw/android.hardware.gnss@1.0-impl.so`（104,360B）：同上；
- `android.hardware.gnss@1.0.so` 与 `@1.1.so` **在 `/lib`**（法1 ls grep 命中；法2 与 z1_hal_a9 报告清单同源），HIDL 基础库 libhidlbase/libhidltransport/libhwbinder 等亦全在 `/lib`（法1 ls；法2 gnss service 同款标准库列表成立）。
→ **满足**。

### 3.3 mnld（MTK GPS 守护，34,685B，10-28 patched）
NEEDED 实测：`libxlog.so, libcutils.so, libc.so, libm.so, libnvram.so, libstdc++.so`。
- libnvram.so 在 `/vendor/lib`（连同 libnvram_sec/libnvram_platform，ls grep 命中）；
- mnld strings 按路径找 `/system/xbin/libmnlp_mt6572`，镜像 `/xbin/libmnlp_mt6572` 在场（39,637B，10-28 patched）。
→ **满足**。

### 3.4 gps.default.so 血统（两法）
- 法1 strings：`init.svc.mnld`、`mnld`、`/dev/gps`、"The MTK GPS Source Project"、`socket_local_client_connect` → MTK shim，经 local socket 连 mnld；
- 法2 readelf：NEEDED 零 qcom 库（§3.1）。
→ 该文件与 8 个 qcom 库**无任何依赖关系**。

### 3.5 "8 库全缺"复核（两法一致）
- 法1 `debugfs ls /vendor/lib` 全列 35 行，grep `gps|loc|izat|geof|sam` 仅命中 libxlog.so；
- 法2 逐库 `debugfs stat /vendor/lib/<lib>` 全部无输出（不存在）。

## 4. HANDOFF 表 8 的考证（张冠李戴实锤）

jfltexx 的 `vendor/lib/hw/gps.msm8960.so`（33,044B，sha `7ff01d1a98ec4326…`）NEEDED 实测末三项：`libloc_eng.so, libloc_core.so, libgps.utils.so`，**且第 11 项 `NEEDED: [gps.msm8960.so]`——"NEEDED 含它自己"与表 8 文字逐点吻合**。判定：表 8 测的是 qcom msm8960 HAL，误记到 MTK 的 gps.default.so 头上（los14 的 `system/lib/hw/gps.default.so` 50,584B sha `0884bb74…` NEEDED 亦仅标准库，同样佐证"gps.default.so≠qcom 栈"）。

## 5. 部署方案（基于 r5k 谱系）

**主方案：零部署。** r5k 现状 GPS 依赖链（gnss HIDL service → impl → gps.default.so(MTK shim) → mnld → libmnlp_mt6572 + /dev/gps(主254, mtk_stp_gps_soc.ko, A5 已备) + libxlog/libnvram）**全部满足**。r5j 轮 GPS 项无需新增任何 so。

**不部署 8 库 + qmi 五件的理由**：
1. r5k 镜像内没有任何二进制 NEEDED 它们（qcom HAL gps.msm8960.so 根本未装）；
2. 即便装入并加载，qcom 栈需要 qmuxd/rmt_storage 等用户态守护与 /dev/gss 设备，MTK6572 板上均不存在，硬件协议不匹配（GNSS 芯片是 MT6620/6627，走 mnld）；
3. 空耗 ~625KB + 5 个 qmi 件 ~254KB 的分区空间与 SELinux 标签负担。

**若未来实验性部署**（仅备查，不推荐）：路径对齐 `/vendor/lib/`（与在场 libxlog.so 同目录同标签 `u:object_r:vendor_file:s0` 类邻件），共 13 件 ~879KB，全部已归档于 `out/z1_media_stage/gps_libs/`（SHA256SUMS 16 件回读校验全"成功"）。

## 6. 上板验证点与未确定项

- 未确定（只能上板判）：gps.default.so shim 与 mnld 的 socket 握手是否在 A9 上工作；`/dev/gps` mknod（c 191 0）后 mnld 是否拿到数据；gnss@1.0-service 是否被 vintf/framework 正确拉起。HANDOFF 表 7 "MTK_GPS_CHIP 值与 libmnlp 冲突"仍未定论（mnld strip，维持"未确定"）。
- 本机可判的均已在 §3 判完：**依赖侧零缺口**。

## 7. 素材归档

`out/z1_media_stage/gps_libs/`：8 库 + gps.msm8960.so + qmi 五件 + 2 个 r5k 对照件（gps.default.so/mnld，文件名 `.FROM_r5k.img` 后缀），14 so 共 ~830KB（<100MB 红线），SHA256SUMS 含来源与结论注释，回读校验 16/16 成功。磁盘 `df -h /` = 4.7G 可用。四镜像只读未动（全部 debugfs 只读 dump/ls）。

---

# FM 收音盘点与最小可行路线（FmPlan）

> 详细版：`out/z1_media_stage/fm/FM_PLAN_20261031.md`；素材清单 `out/z1_media_stage/fm/FM_MATERIALS.sha256`（31 件自校验通过）。本节为速览。

## 0. FM 结论速览

| # | 结论 | 证据 |
|---|---|---|
| 1 | **维持"大工程"判定，修正上轮前提：FM 内核驱动并非"无源码可移植"**——MTK ALPS 系 GPL dump 有现成源码（GitHub `elephone-dev/P9000-Kernel` `drivers/misc/mediatek/fmradio/` 含 `MT6627_FM` Makefile 分支） | 本轮网络检索；上轮 MEDIA_DEPLOY.md §4 称"无源码" |
| 2 | **内核 FM 整层缺失（三法复验全零）**：`find -iname '*fm*'` 空；`System.map`（fork 主树 + a9_wt）fm_drv/stp_fm/wmt_fm 0 命中；源码 grep `do_fm_drv_init/MT6627fm_low_ops_register/mt_fm_probe` 0 源文件；`modules.order` 6 模块无 fm | §2.1；对照组：原厂 `kernel.bin` 同 grep 命中 5 类符号 ⇒ 方法有效 |
| 3 | **CONNSYS 链 FM 走向 = WMT 仲裁 + STP 传输 + `/dev/fm` c 193:0 入口**；WMT 半边已在 mainline（`wmt_func.h:34` `CFG_FUNC_FM_SUPPORT 1`、`wmt_func.c:463-478` `wmt_func_fm_on/off`→`WMTDRV_TYPE_FM`），缺的是 chardev（`stp_chrdev_fm.c` 不存在，pub/ 只有 bt/gps/wifi）+ 芯片驱动 + patch 加载者 | 源码行号逐条复核；原厂 rc `init.rc:669` `mknod /dev/fm c 193 0` |
| 4 | **userspace 六小件已收齐且 sha 落账**（`com.android.fmradio` FMRadio.apk 387,768B + libfmjni 38,688B + libfmcust 13,856B + mt6627 v1 patch/coeff 2,120B + cust.cfg 345B ≈ 442KB）；NEEDED 全闭合（libc++ 等 r5k 实测在位；los14 族**不需要** libxlog，readelf UND 实测 0 xlog 符号） | `fm/los14_7.1/`；`readelf -d/--dyn-syms`；r5k `debugfs stat /lib/libc++.so`=636,332B |
| 5 | **A9 三枚候选镜像与 r5k 均无 FMRadio app / 无 mt6627 固件 / 无 /etc/fmr / 无 fm rc**；r5k 仅多了死件 `radio.fm.mt6572.so`（42,628B, sha=`9a7487db…`=los14 同源）、`libxlog.so`、WMT 工具（6620_launcher/wmt_loader=los14 同源；6620_wmt_concurrency/lpbk=工厂逐字节同） | 四镜像 debugfs 只读盘点；两源 sha 互证 |
| 6 | **关键澄清：mt6627 v2–v5 固件 0/2 字节是 ROM 原状**（原厂 `1/system.img` 与 los14 源树两源同尺寸同 sha），半成品不是提取失败；真固件只有 v1 | `debugfs ls -l` + `ls -la los14/.../mt6627/` |
| 7 | **音频是 FM"能听见"的硬前置**：`mt6323.c` 无 FM/LINE 输入 DAPM 路径（grep 3 处全是 FORMAT 误命中）；dts `sound` 节点现为 `status="okay"`（比上轮记录前进一步），但出声仍卡音频线 | §2.3；`mt6572-z1.dts:314-351` |

## 最小可行路线（按依赖序，估"周"级）

1. **音频出声**（P0-A，全项目音频线，本轮 scope 外）；
2. **拉 GPL 源码比对**：ALPS `fmradio/`（fm_drv + mt6627_fm_{link,native}）vs 3.4.67 基线（原厂 `kernel.bin` 符号）——优先找 mt6572/6582 同代 dump；
3. **内核移植**：`/dev/fm` chardev（major 193，**务必 device_create**）+ fm_drv + mt6627 chip ops + FM_IOCTL ABI + 接 `wmt_func_fm_on` + 固件路径 `/etc/firmware/mt6627/`；
4. **部署六小件**（priv-app + 白名单 + SELinux，素材已齐）；
5. **插耳机上板**（耳机=天线，accdet 已在）→ 搜台 → **实听**。

**不立项则不可行（直说）**：`/dev/fm` 无内核代码创建，libfmjni 首个 ioctl 即 ENXIO，任何 app 层补救无效；网络电台需先通网络，非离线方案，不作目标。

**未确定**（不猜）：① `WIFI_RAM_CODE_SOC` 是否含 FM patch handler（需上板 `wmt_dbg`）；② P9000 树与 3.4.67 的 ioctl ABI 差异度（需拉源码）；③ 本机 FM DSP 实际身份（`drv_wlan/mt6620` 树 vs `mt6627` 固件串并存，需上板读 chipid）。

**素材归档**：`out/z1_media_stage/fm/`（factory_4.4 16 件与 `1/system.img` 逐件 sha 比对一致沿用；los14_7.1 14 件与 los14 源树一致沿用）+ `FM_MATERIALS.sha256` + `FM_PLAN_20261031.md`。本轮零镜像改动、零大文件复制，`df -h /` 终值 3.9G 可用。

---

# 播放器线（PlayerPlan）— 2026-10-31

> 接手 2026-10-30 被停的素材工作流**续做**，不推倒重来（上轮报告 `out/MEDIA_DEPLOY.md` 仍有效，本章只补缺口并钉死 r5k 谱系）。未刷写、未碰设备；四件只读基线（r5k_final/r5i_final/r5c_boot/z1_ash_boot）零改动（r5k_final mtime/sha 复测未变，见 §6.2）。

## 0. 播放器线结论速览

| # | 结论 | 证据 |
|---|---|---|
| 1 | **播放器出货选择维持 LOS16 的 Eleven**（`src_los16/priv-app/Eleven/Eleven.apk`，8,679,786 B，sha `6baeab02…`）；los14 件取证补齐后判定**不用、留档** | §3/§4 |
| 2 | **los14 Eleven = `com.cyanogenmod.eleven` v3.0，minSdk/targetSdk=24/24，编译平台 API 25**（manifest 双法 + strings + build.prop 四重互证）；APK 内零 native 库，其依赖的平台 RS 库（los14 `/system/lib/librsjni.so` 等）实测 ELF32/ARM 双法 | §3.1–3.2 |
| 3 | los14 件**零 CM 平台私有类、零 MTK 类引用**；3 个"废弃未移除"引用（RemoteControlClient 等）在 API28 存活 → **技术上大概率可跑**；但包名不在 r5k privapp 白名单（`WRITE_MEDIA_STORAGE` 拿不到）+ LOS16 件已有 22,775 条引用 100% 解析 0 缺失 → **维持 LOS16 件** | §3.3/§4 |
| 4 | **部署定案：镜像内预装（preinstall），否决上板 pm install**——signature-privileged 权限只授予 /system/priv-app 定位的包；PicoTts 落 /app | §5 |
| 5 | **Recorder 已找到且已在部署层内**：`src_los16/priv-app/Recorder/Recorder.apk` 2,599,105 B sha `daef2594…`，r5k 白名单 1:1 | §5 |
| 6 | **deploy_media.sh 已改基 r5k**（DEF_IN→r5k_final、BASELINE 补四件+r5h+r41），r5k_final 上 4 轮实跑：全新部署/重打/幂等快路径 36 命中/基线拒绝 全过，e2fsck clean；r5k_final 本体零改动（mtime/sha/inode 三证） | §6 |

## 1. 半成品接手盘点（做了什么、缺什么）

| 半成品 | 状态 | 本轮处置 |
|---|---|---|
| `out/z1_media_stage/eleven_extract/` | 已是 los14 Eleven 的完整解包 | **血统闭环**：从 los14 APK 现场重抽 manifest，sha `534bc606…` 与半成品**逐字节同一** → 直接续用，未重解包 |
| `out/z1_media_stage/parse_axml.py` | 可用（其类型常量拿 `0x01000003` 比 1 字节 dataType `0x03`，字符串值只打索引） | 续用做法 A；另写独立暴力扫描器做法 B 交叉（§3.1） |
| `out/z1_media_stage/SHA256SUMS` | 22 条，覆盖全部部署素材 | 未改（素材未变）；los14 件为源档不进此清单 |
| `out/z1_media_stage/deploy_media.sh` | 完整；但默认输入 **r5i_final**，与"基于 r5k"任务要求冲突 | **改**：DEF_IN→r5k_final、BASELINE 补四件（§6.1） |
| `out/z1_media_stage/src_los14/` | **空目录**（上轮被停点） | **补齐**：收档 los14 Eleven.apk（§2） |
| `src_los16/`、`src_factory/`、`stage/`、`fm/` | 完整（LOS16 三件 + 原厂对照件 + 部署树 + FM 两族） | 未动 |

上轮已定的选型结论全部沿用（LOS16 Eleven 胜出、弃原厂 Music.apk 因 21 处 `com/mediatek/*` 引用 11 个未定义、Eleven 必须带 RS 支撑库否则启动即 FC 等），不重复论证。

## 2. los14 树定位与收档

```
$ find /home/lxj/claude/6572/los14 -iname "*eleven*"
/home/lxj/claude/6572/los14/system/priv-app/Eleven
/home/lxj/claude/6572/los14/system/priv-app/Eleven/Eleven.apk
```

- 大小 **3,066,895 B**，sha256 `bbc064d0f27e2838dc5b85fface1c3b526d917fc098fa87f389bf655b3de2c37`
- 收档：`out/z1_media_stage/src_los14/priv-app/Eleven/Eleven.apk`（`cp -p` 后回读 sha **一致**；播放器线产物合计 51MB < 100MB 红线）
- los14 树本身是**解包系统**（boot.img/system/META-INF），非源码树 → LOCAL_SDK_VERSION 源码证据从上游 GitHub 取（§4 法二）

## 3. los14 Eleven 取证

### 3.1 manifest 双法解析（minSdk/targetSdk）

**法 A（parse_axml.py，结构游标走 chunk）**：`uses-sdk: minSdkVersion=…data=24; targetSdkVersion=…data=24`；`manifest: platformBuildVersionCode=…data=25`，versionCode=3。

**法 B（独立暴力扫描器：自写字符串池解析 + 全文件 20B 属性记录暴扫，不走 chunk 游标）**：
```
minSdkVersion → 候选: [(5560, 24)]            （唯一命中）
targetSdkVersion → 候选: [(5580, 24)]          （唯一命中）
platformBuildVersionCode → 候选: [(5484, 25)]  （唯一命中）
```

**交叉佐证（法 C/D）**：`strings -e l AndroidManifest.xml` 见 4 个属性名 + 版本名 `7.1.2`；`los14/system/build.prop` = `ro.build.version.sdk=25` / `ro.build.version.release=7.1.2` / `ro.build.flavor=lineage_Z1-userdebug`，与 platformBuildVersionCode=25 自洽。

**定案：minSdk=24 / targetSdk=24 / 编译平台=API 25（Android 7.1.2），package=`com.cyanogenmod.eleven`（字符串池 idx29，versionName "3.0"）。**

### 3.2 native libs 是否 arm32

- **APK 内零 native 库**（两法）：`unzip -l` 全清单 137 files 无任何 `lib/`、`.so` 条目；解包后 `find . -name '*.so'` 空。其 RenderScript 依赖在**平台侧**：`los14/system/lib/librsjni.so`（51,712 B）、`libRSSupport.so`（911,224 B）、`libblasV8.so`（1,046,072 B）。
- **平台库 arm32 双法判定**：法一 `arm-linux-gnueabihf-readelf -h` → `类别: ELF32`、`系统架构: ARM`；法二 `od -j 18 -N 2 --endian=little` → e_machine 低字节 `0x28`=40=`EM_ARM`，`EI_CLASS=1`（32 位）。

### 3.3 dex 平台引用定性（A9 兼容性的字节级证据）

`strings classes.dex`（dex 字符串池 MUTF-8 可读；注：对 dex 直接 `grep -c` 全 0 是坑，须走 strings）：

| 引用 | 计数 | API28 判定 |
|---|---|---|
| `RemoteControlClient` | 9 | **API28 仍在**（API21 起废弃，未移除）→ 不致命 |
| `Environment.getExternalStorageDirectory` | 1 | API28 仍在（API29 废弃）→ 不致命 |
| `Notification.setPriority` | 1 | API28 仍在（API26 废弃）→ 不致命 |
| `NotificationChannel` | 0 | 与 targetSdk=24 自洽 |
| `MediaSession` | 83 | 现代 API，API28 在 |
| `renderscript` | 112 | 走 `android/support/v8/renderscript/*`（与 LOS16 件同依赖模型） |
| **`Lcyanogenmod/`（CM 平台私有类真名）** | **0** | **零 CM 平台私有引用** |
| `com/cyanogenmod` 非 eleven 包 | **0** | 527 处 `Lcom/cyanogenmod/eleven/*` 全是 app 自有类（CM 时代包名），其余命中为 proguard 开关表字符串 |
| `com/mediatek` | **0** | 无 MTK 私有引用（与原厂 Music.apk 的 21 处形成对照） |

### 3.4 权限清单

12 条 uses-permission（字符串池 idx35–46）：`WRITE_EXTERNAL_STORAGE / ACCESS_NETWORK_STATE / INTERNET / WAKE_LOCK / BROADCAST_STICKY / READ_PHONE_STATE / WRITE_SETTINGS / INSTALL_SHORTCUT / READ_EXTERNAL_STORAGE / **WRITE_MEDIA_STORAGE** / RECORD_AUDIO / MODIFY_AUDIO_SETTINGS`。
**`WRITE_MEDIA_STORAGE` 是 signature-privileged 权限，r5k 的 `privapp-permissions-lineage.xml` 只授给 `org.lineageos.eleven`**——los14 件包名是 `com.cyanogenmod.eleven`，对不上号。

## 4. A9（API28）兼容判定 —— 两法交叉

**法一：manifest 解析**（§3.1，多重互证）→ minSdk 24 ≤ 28 **可安装**（无安装阻断）；targetSdk 24 在 A9 以兼容模式运行（A9 无 targetSdk 下限阻断；阻断线在 API34 的 minSdk<23）。platformBuildVersionCode=25 = 按平台 25 编译。

**法二：上游源码 Android.mk（本机无 LOS14 源码树，经 GitHub 取证）**
`LineageOS/android_packages_apps_Eleven` 分支 `cm-14.1`（lineage-14.1 分支名 404，经 api.github.com 查实分支清单后改用 cm-14.1）的 `Android.mk`（WebFetch 逐字回读）：

```make
LOCAL_STATIC_JAVA_LIBRARIES := android-support-v8-renderscript …
LOCAL_PACKAGE_NAME := Eleven
LOCAL_PRIVILEGED_MODULE := true
LOCAL_JNI_SHARED_LIBRARIES := librsjni
（无 LOCAL_SDK_VERSION 行）
```

- **无 LOCAL_SDK_VERSION** = 平台构建（对完整 framework 编译，即 API 25 平台）→ 与 manifest `platformBuildVersionCode=25` **互证**；
- `android-support-v8-renderscript` → 与 dex 112 处 support-v8 RS 引用**互证**；
- `LOCAL_JNI_SHARED_LIBRARIES := librsjni` → 解释 APK 无内置 lib、依赖平台 `/system/lib/librsjni.so`（los14 树实测在位）；
- `LOCAL_PRIVILEGED_MODULE := true` → 解释 priv-app 定位与 WRITE_MEDIA_STORAGE 需求。

**判定：**
- **技术上大概率可跑**：公共 API 干净（零 CM 平台类、零 MTK 类；3 个"废弃未移除"引用全在 API28 存活）；RS 依赖模型与 LOS16 件相同（r5k 缺 librsjni/libRSSupport，须随层补装——部署层本就含）。
- **但不采用**，两点硬扣分：① 包名 `com.cyanogenmod.eleven` 不在 r5k privapp 白名单，`WRITE_MEDIA_STORAGE` 拿不到（特权路径降级/失败）；② 上轮对 LOS16 件做过 **22,775 条平台引用 100% 解析、缺失 0** 的字节码级验证，且其包名/白名单/app-local 软链先例与 r5k 全对齐。**结论：LOS14 件留档（src_los14/），出货用 LOS16 件。**

## 5. 部署定案：镜像内预装（r5k 谱系），否决上板 pm install

**选 preinstall（deploy_media.sh 落镜像），理由按权重：**

1. **权限模型是硬约束**：`WRITE_MEDIA_STORAGE`（Eleven）、`MEDIA_CONTENT_CONTROL`/`MODIFY_AUDIO_ROUTING`（AudioFX）、`CAPTURE_*_VIDEO_OUTPUT`（Recorder）都是 signature-privileged 权限，**只授予位于 /system/priv-app 的包**；`pm install` 装进 /data 的包拿不到。走 pm install 等于让三个 app 残废。PicoTts 无 privapp 权限需求，按 LOS14 邻位落 `/app`。
2. **签名已闭环**：三个 APK 用平台密钥签名（上轮核：与镜像内 Settings.apk 同指纹 `C8:A2:E9:…`），预装后 signature 级授予成立；r5k 白名单三段 1:1（本轮 debugfs 现场原文）：
   ```
   org.lineageos.eleven   → WRITE_MEDIA_STORAGE
   org.lineageos.recorder → CAPTURE_SECURE_VIDEO_OUTPUT / CAPTURE_VIDEO_OUTPUT
   org.lineageos.audiofx  → CHANGE_COMPONENT_ENABLED_STATE / MEDIA_CONTENT_CONTROL / MODIFY_AUDIO_ROUTING
   ```
3. **不依赖板子可达性**：设备当前只有串口触点，`pm install` 需要可用的 adbd/package 服务链（未验证）；镜像侧预装与刷写闭环独立成事，且可先在主机侧全量回读断言（本轮已做）。
4. **可重放/可回滚/可审计**：非原地、带时间戳备份、幂等、基线拒绝、写后 ro 回读 + e2fsck（§6.2 四轮实跑）。
5. **wipe /data 不丢**。

`pm install` 降级为"上板后的临时试验件通道"，不做主路线。

**Recorder（录音机）定位**：选 `src_los16/priv-app/Recorder/Recorder.apk`（2,599,105 B，sha `daef2594…`；部署层已含，本轮 r5k 实跑回读 OK；minSdk24/targetSdk28、包 `org.lineageos.recorder` 系上轮 §2 记录，本轮未重解该包 manifest，如实注明）。备选未用：原厂 `src_factory/app/SoundRecorder.apk`（+odex，MTK 件）。

### 5.1 r5k_final 前提复核（全部通过）

| 检查 | 命令/证据 | 结果 |
|---|---|---|
| system-as-root | `debugfs -R 'stat /system'` → 无；`stat /etc/init/audioserver.rc` → Inode 2485 regular 0644 | ✅ 镜像根==/system（与上轮 r5i 结论同构） |
| 空间 | `dumpe2fs -h`：Free blocks **33,270** × 4096 = **136.2 MB** | ✅ > 净增 19.6 MB |
| 目标路径零冲突 | `stat` /lib/librsjni.so、libRSSupport.so、libblasV8.so、/priv-app/{Eleven,Recorder,AudioFX}、/app/PicoTts、/tts、/vendor/etc/media_profiles_V1_0.xml → 全不存在；/lib/librs_jni.so（平台件，下划线位不同）→ Inode 371 在 | ✅ |
| 白名单 1:1 | `debugfs -R 'cat /etc/permissions/privapp-permissions-lineage.xml'` 三段原文（§5） | ✅ |
| pre-O 式链接配置 | `/etc/public.libraries.txt` sha `fd836f0a…`、`/etc/ld.config.txt` sha `f26fd0c9…`——与上轮 r5i 记录**逐字节相同**；`namespace.default.isolated = false` | ✅ |
| 邻文件 SELinux 标签 | sudo ro 挂载 getfattr：/lib/libRS.so、/app/Terminal、/priv-app/Settings/Settings.apk = `system_file`；/vendor/etc/audio_policy_configuration.xml、media_codecs.xml = `vendor_configs_file` | ✅ 与脚本标签表一致 |

## 6. deploy_media.sh 修正 + r5k 实跑证据

### 6.1 脚本修改（out/z1_media_stage/deploy_media.sh）

1. `DEF_IN`：`system.img.r5i_final` → **`system.img.r5k_final`**（头注注明任务书钉死 r5k，r5i 仍可显式传参）。
2. `BASELINE` 只读基线补齐任务书四件 + 历史件：**r5k_final、r5i_final、r5h_final、r5c_boot/boot.img、z1_ash_boot/boot.img（=out/z1_ash_boot/boot.img）、r41 boot.img**。
3. 上轮报告 §0 提到的"权限断言循环排在 umount 后"瑕疵，**当前文件已是修复后状态**（chkperm 循环在 `umount` 之前，第 339 行注释与本次实跑行为一致）。

### 6.2 四轮实跑（全部对 r5k_final）

| 轮 | 内容 | 结果 |
|---|---|---|
| 1 | 全新部署 → /tmp/z1_media_r5k_test.img | ✅ 15 文件落位、**22 条 xattr**、12 项 sha 回读全 OK、软链 OK、e2fsck clean；输出 sha `674246af2c4a25375242234ffa9c285d0dd478cf2843023ed7b4a74530ea19f3`（943,718,400 B） |
| 2 | 同输入重打 | ✅ 同样全断言过；镜像 sha `bf4ffb8a…`≠轮 1——**预期**：脚本每轮从输入重 cp 输出，mount rw 必变 fs 元数据（superblock/日志），**镜像字节不承诺复现，文件层由回读断言保证** |
| 3 | 幂等快路径（拿轮 1 产物当输入再部署） | ✅ 36 条"内容已一致，跳过/已是 …"（理论满额 37：15 put + 22 标签，差 1 未逐条追因，零 FATAL、e2fsck clean、无功能影响） |
| 4 | 只读基线拒绝（OUT=r5i_final） | ✅ `拒绝: 输出是只读基线 …/system.img.r5i_final`，零写入 |

**输入完整性**：r5k_final mtime 仍 `2026-10-30 10:26:46`，sha `b567885796eae85db5fb429bb7b24161c4236ca6792bb09c5c625605802ba1be`，与改前硬链备份 `bridge_backups/system.img.r5k_final.pre_media_20261031_013254.bak` 同 inode（2908）→ 未被改动。
临时镜像已删（释放 ~1.9 GB），播放器线结束时 `df -h /` = 3.8G 可用。

### 6.3 刷写（合批决策后执行，本轮未刷）

```bash
cd /home/lxj/claude/6572
sudo bash out/z1_media_stage/deploy_media.sh            # r5k_final → system.img.r5k_final.media.img
Z1_VERIFY=0 tools/flash_with_retry.sh android <产物> <上一块已验证 system 回滚件> 8
```
（Z1_VERIFY=0 纪律：mtkclient verify 误报会回滚掉成功镜像。回滚件选择由主会话定，脚本尾注沿用 r5h_final 仅为示例。）

## 7. 未决风险（诚实清单）

1. **上板行为零实测**：继承 `MEDIA_DEPLOY.md` §5 的 1–5 条（RS 库 dlopen 运行时因素、Eleven FC 为静态推断、libblasV8 是否真被用、240×320 UI、**音频本身能否出声是最大前置**——播放器/录音/TTS 全依赖它，顺序不可颠倒）。
2. **r5k 谱系新增未知**：本轮验证的是"前提与落盘正确"；r5k 与 r5i 的运行时差异（WiFi HAL 引入后）未上板回归；刷后按 `MEDIA_DEPLOY.md` §6 验收 + getprop 核对谱系。
3. **镜像字节不重现**（§6.2 轮 2）：合批流程若依赖"两次生成 sha 相同"不成立；以文件层断言为准。
4. **幂等差 1**（§6.2 轮 3）：36/37，未追因，无功能影响。
5. **los14 件留档未实测**："大概率能跑"是静态判定，未做引用级全量解析（那是上轮给 LOS16 件做的 22,775 条全覆盖）；未来要用它须先补同级别解析。
6. **FM 不在本轮 scope**：FM 线已另章盘点（见上文 FmPlan）；播放器线素材 `fm/` 目录与 FmPlan 的 `FM_MATERIALS.sha256` 共用，勿与 factory_4.4 族混装。
7. **任务书素材源 `jfltexx_extract/` 为空目录**——GPS 线本章已定性（其 §2），8 库真身在 `out/z1_hal_a9/vendor/lib/`；与播放器线无关，特此互认避免后续误引。

## 8. 播放器线文件改动

| 路径 | 动作 |
|---|---|
| `out/z1_media_stage/deploy_media.sh` | 修改（DEF_IN→r5k、BASELINE 补齐、头注） |
| `out/z1_media_stage/src_los14/priv-app/Eleven/Eleven.apk` | 新增收档（3,066,895 B，sha `bbc064d0…`） |
| `out/MEDIA_STAGE_20261031.md` 播放器线章（本章） | 新增 |
| `z1a9_gpt/scripts/media_deploy/{deploy_media.sh,SHA256SUMS}` + 本章副本 | git 提交（仅脚本/校验和/文档，无二进制） |
| 四件只读基线 | **零改动**（§6.2 证据） |

# TTS 线（TtsPlan）— 2026-10-31

任务：los14 PicoTts 全套逐件盘点 → A9 兼容双验证 → r5k 谱系部署方案（含无网备选）。
接手半成品 `out/z1_media_stage/{stage,SHA256SUMS,deploy_media.sh}`（不推倒重来）；fm/、jfltexx GPS 归 GPS/FM 线，本章未动。

## T0. TTS 结论速览

| # | 结论 | 证据 |
|---|---|---|
| 1 | PicoTts 全套 16 件（1 apk + 2 so + 12 语库）盘点完成，stage 血统与 los14 源树逐字节一致，全套 ≈6.67 MB | §T1 两法比对 |
| 2 | **A9 兼容成立（三面闭合）**：机制层/DEX 层/native 层；**不必须 priv-app，/app 即可** | §T2 |
| 3 | system_server（7.1 与 A9 皆然）不承担 TTS 引擎发现/绑定——引擎扫描是 app 进程内 Intent 扫描 | §T2.1 双镜像 services.jar 零命中对照 |
| 4 | PicoTts dex 自带 `com.android.tts.compat.{CompatTtsService,SynthProxy}` 类定义 → A9 上无 framework hidden 依赖 | §T2.2 dex class_defs 解析 |
| 5 | r5k 依赖零缺失：两 so 的 NEEDED 去重 11 个平台库全部在位 | §T2.3 debugfs 逐一 stat |
| 6 | feature 声明缺口补齐：新增 `/etc/permissions/z1-media-features.xml`（audio.output + software.tts）并已随本轮实跑入镜像 | §T3.2 |
| 7 | 部署实跑落地：r5k_final → `system.img.r5k_final.media.img`，sha `c0a9cf17…`，e2fsck clean + 双路回读一致 | §T3.2 |
| 8 | 中文 TTS 仍不可能（维持上轮三重证据），英文离线 TTS 可交付 | §T1/上轮报告 |

## T1. los14 PicoTts 全套逐件盘点

源树 `/home/lxj/claude/6572/los14/`（LOS14 = Android 7.1）。16 件 sha256 全部本机实算：

**引擎三件**（los14/system/ 下）：

| 路径 | 大小 B | sha256（前16） |
|---|---|---|
| app/PicoTts/PicoTts.apk | 23,379 | `292b42b38cfb5ad7` |
| lib/libttspico.so | 240,572 | `a60081a3d4166e49` |
| lib/libttscompat.so | 22,324 | `3c4fef35fffeab87` |

**语音数据 tts/lang_pico/（12 .bin，合计 6,383,884 B）**：de-DE_gl0_sg `39147b81…` / de-DE_ta `df29282…` / en-GB_kh0_sg `8f9f5d13…` / en-GB_ta `57998f64…` / en-US_lh0_sg `d6946526…` / en-US_ta `3a2a4d23…` / es-ES_ta `b18c60ba…` / es-ES_zl0_sg `82e724d3…` / fr-FR_nk0_sg `85e2d63c…` / fr-FR_ta `d9da5eb3…` / it-IT_cm0_sg `c48be4dc…` / it-IT_ta `9b243c34…`（六语系，无中文——与上轮报告三重证据一致）。

**血统两法**：① 三主件 sha 与 stage 逐项相同；② 12 .bin 目录级 sha256（`(cd 源 && sha256sum *.bin) | sha256sum`）los14 与 stage 同为 `12aa46c2df6219ad…`。

## T2. A9 兼容双验证

### T2.1 TtsEngines 机制在 A9 未变（两法）

- **法1（AOSP 源码知识）**：TTS 引擎发现/绑定不经过 system_server——framework 的 `TextToSpeech`（app 进程）用 `Intent("android.intent.action.TTS_SERVICE")` 枚举 + bindService 直连引擎；`initTts()` 顺序：显式引擎 → `Settings.Secure.TTS_DEFAULT_SYNTH`（键名 `tts_default_synth`）→ 第一引擎兜底。
- **法2（双镜像实证）**：r5k A9 `boot-framework.vdex`（18,980,788 B）grep：`android/speech/tts/TextToSpeech`×28、`TextToSpeechService`×19、`TTS_SERVICE`×1、`tts_default_synth`×1——**framework 侧 API 与设置键全在**。system_server 侧：r5k `services.jar` classes.dex（9,037,564 B）`com/android/server/tts/`、`TtsEngines`、`TextToSpeechManagerService` **零命中**（grep 有效性以 PowerManagerService 命中对照）；**los14 7.1 的 services.jar 同样零命中**——两代一致，"未变"不靠记忆。
- **native 路径**：`strings libttspico.so` 硬编码 `/system/tts/lang_pico/`——部署落位即命中，与 AOSP Pico 约定一致。

### T2.2 hidden 类依赖（本轮排掉的最大风险）——APK 自带

PicoTts dex 引用 `Lcom/android/tts/compat/{CompatTtsService,SynthProxy};`。三重证据定案自带：
1. dex `class_defs` 解析（python 实跑，13 类）：两类**在类定义表**，与 `com.svox.pico.*` 9 类全自带；
2. los14 `framework.jar` dex（10,169,524 B）grep `Lcom/android/tts/compat/` = 0——7.1 framework 不提供，能跑的唯一来源是自带；
3. r5k `boot-framework.vdex` grep 同串 = 0——A9 与 7.1 行为一致。

### T2.3 native readelf + 依赖在位

`readelf -h`：两 so 均 **ELF32 LSB ARM EABI5**，interpreter `/system/bin/linker`，stripped。`readelf -d` NEEDED：libttspico.so = libcutils/libexpat/libutils/liblog/libc++/libdl/libc/libm；libttscompat.so = libandroid_runtime/libnativehelper/libmedia/libutils/libcutils/liblog/libdl/libc++/libc/libm。**r5k 11 个库名逐一 `debugfs -R stat /lib/<so>` 全部 regular 0644 在位**（libc++ inode 255、libexpat 50、libutils 70、libcutils 149、liblog 473、libmedia 107、libandroid_runtime 464、libnativehelper 76、libdl 19、libc 158、libm 260）——上轮担心的 `libc++.so` 问题在 r5k 闭环。

APK：`parse_axml.py` 解 manifest = **minSdk/targetSdk 25**（≤28 向前兼容）、package `com.svox.pico`、service `.PicoService`（TTS_SERVICE + tts_lang categories eng/GBR/USA/deu/fra/ita/spa）。

### T2.4 priv-app 必要性 —— 不必须，/app 即可

1. 机制（AOSP 知识）：TTS 引擎只需 `FLAG_SYSTEM`（/app 与 /priv-app 均置位）；"默认引擎必须系统引擎"校验位于 system_server 的 TtsEngines——r5k 实测无此类（T2.1），该层校验在目标镜像上不存在。
2. 权限面（manifest 实测）：uses-permission 全集仅 `DELETE_PACKAGES`（signature）+ `WRITE_EXTERNAL_STORAGE`（dangerous），**无 privileged 级** → 不触发白名单强制；r5k 两份 privapp-permissions*.xml grep `svox` 均 0 条（实测）。
3. 先例：los14 出厂在 `/system/app/PicoTts`；Z1 原厂 4.4 在 `/app/PicoTts.apk`。

**未确定**：PicoTts.apk 证书与 r5k platform 同钥与否未验；若异钥，`DELETE_PACKAGES`（signature）被拒——仅影响语音包安装/卸载辅助（Z1 无市场场景），**不影响合成主链**。

### T2.5 离线性

dex 无任何网络字符串（`http|INTERNET|socket|url.open` grep = 0）；数据全在 system 分区。小坑复核：引擎引用 6 个 `*_utpp.bin` 用户词表 los14 未随包、`/sdcard/svox/` 用户词表路径 Z1 无外置存储——均为 log 级告警，不影响合成（12 主数据 sg/ta 齐全，so 内 locale 表 24 处命中与 .bin 一致）。

## T3. r5k 谱系部署方案

### T3.1 r5k 现状（本轮只读取证）

`debugfs`（只读）+ sudo ro 挂载于 `system.img.r5k_final`（943,718,400 B，只读铁律件，播放器线 §6.2 录 sha `b5678857…`）：`/tts`、`/app/PicoTts`、`/lib/libttspico.so`、`/lib/libttscompat.so` **全不存在**（stat → `File not found by ext2_lookup`，对照件 `/lib/libc++.so` 命中排除语法误判）；`/etc/permissions` 24 件**无 handheld_core_hardware.xml、无 audio.output/software.tts 声明**；标签邻件（getfattr）：`/lib`、`/app`、`/app/Terminal`、`/lib/libRS.so`、`/priv-app/Settings/Settings.apk`、`/etc/permissions/platform.xml` = `system_file`，`/vendor/etc/audio_policy_configuration.xml` = `vendor_configs_file`——与播放器线 §5.1 表一致，双会话互认。

### T3.2 落位表 + 本轮实跑（不是纸面方案）

TTS 组件（脚本 C 段；目录 0755，文件 0644，root:root）：

| 镜像内路径 | 标签 | 依据 |
|---|---|---|
| `/app/PicoTts/PicoTts.apk` | system_file | 邻 `/app/Terminal` 实测 |
| `/lib/libttspico.so`、`/lib/libttscompat.so` | system_file | 邻 `/lib/libRS.so` 实测 |
| `/tts/lang_pico/{en-US_lh0_sg,en-US_ta,en-GB_kh0_sg,en-GB_ta}.bin` | system_file | `plat_file_contexts:45 /system(/.*)?`；A9 sepolicy 无 tts_file（上轮 0 命中） |
| `/etc/permissions/z1-media-features.xml`（**本轮新增**，1,158 B，sha `60ecd69dd3669bd5…`） | system_file | 邻 platform.xml 实测 |

z1-media-features.xml 声明 `android.hardware.audio.output`（格式照抄 los14 handheld_core_hardware.xml:26）+ `android.software.tts`；**不搬 handheld 全集**（会连带声明 camera/compass 等不存在的 feature）。注：los14 全树 grep `android.software.tts` = 0——7.1 出厂同样不声明，此行是超出 los14 基线的增强而非对齐项。`/etc/public.libraries.txt` 追加维持上轮"可选保险"定位，未实施（先例：base 出厂 Eleven 同样依赖 /lib 下非 public 的 librsjni.so）。

**实跑（2026-10-31 01:51，全量 A+B+C+D，在播放器线脚本修正之上叠加 feature 三处）**：

```
sudo bash out/z1_media_stage/deploy_media.sh
→ 输出 out/z1_r5_system/system.img.r5k_final.media.img（943,718,400 B）
  sha256 c0a9cf172c2c362a0dfc0ce9267c250d262a2d2f55831fc9086b5afc99ed8579
```

- 脚本自检链：素材 sha256 -c 23 项成功 → 23 条 xattr 写入回读 → **13 项内容回读全 OK**（TTS 6 项含 feature xml）→ 权限断言无异常 → e2fsck -fn clean；
- **独立复核（不依赖脚本断言）**：`debugfs stat` 六 TTS 件 Inode 2681/2682/2683/2685/2688/2689 全 Mode 0644；`debugfs cat` feature xml 内容读回一致；
- 归档 `bridge_backups/products/products_media_r5k_20261031_0151/`（硬链）；输入备份 `bridge_backups/system.img.r5k_final.pre_media_20261031_015115.bak`；
- 与播放器线 §6.2 轮 1（/tmp 测试轮，22 条 xattr/12 项回读）差值恰为 feature xml 一件——两轮互证幂等框架稳定。

**上板验收（本轮未刷、不碰设备）**：`settings get secure tts_default_synth`（期望 `com.svox.pico`；为空时 TextToSpeech 走第一引擎兜底）+ `am start -a android.speech.tts.engine.CHECK_TTS_DATA -n com.svox.pico/.CheckVoiceData` + 日志 grep `pico`。**前置纪律（继承上轮）：先验音频出声再判 TTS——audio.primary.mt6572.so 是 mixer_ 命中数 0 的 PoC，TTS 的 PCM 落不到喇叭时失败是静默的。**

### T3.3 备选方案（无网环境）

- **备选 A（主方案即是）**：引擎+数据全预置 system 分区，零网络依赖（T2.5 实证）——无网不是障碍是默认状态。
- **备选 B（设备侧手工）**：root shell `mount -o remount,rw /system` 后按 T3.2 表 `cp + chown 0:0 + chmod 644` + 标签同表执行；无幂等/回读断言，仅单板应急。
- **备选 C（不可行，留证）**：apk 走 `adb install` 进 /data、不动 /system——引擎 native 硬编码 `/system/tts/lang_pico/`（T2.1），数据缺失则 CheckVoiceData 失败、引擎初始化失败；数据无法绕开 system 分区。

### T3.4 TTS 线文件改动

| 路径 | 动作 |
|---|---|
| `out/z1_media_stage/stage/etc/permissions/z1-media-features.xml` | **新增**（1,158 B，sha `60ecd69d…`） |
| `out/z1_media_stage/deploy_media.sh` | C 段 put / LABELS / expect 三处补 feature xml（改前备份 `bridge_backups/deploy_media.sh.pre_tts_features_20261031_*`） |
| `out/z1_media_stage/SHA256SUMS` | 加 feature xml 一行（备份 `bridge_backups/SHA256SUMS.pre_tts_features_20261031_*`） |
| `out/z1_r5_system/system.img.r5k_final.media.img` | 产出（大文件记 sha 不进 git；归档见 T3.2） |
| 四件只读基线 | **零改动**（只读挂载/debugfs -R 取证） |
