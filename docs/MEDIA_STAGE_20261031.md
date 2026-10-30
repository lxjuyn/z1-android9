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
