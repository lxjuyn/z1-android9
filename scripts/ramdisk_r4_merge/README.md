# ramdisk_r4_merge — v11 × wifi_v10 合流记录集（2026-10-27）

R4 轮 ramdisk 记录级合流：以 **v11**（`out/z1_v11/boot.img`，sha 76e06674…，
GPS/按键线，72 条记录）为基线，合入 **wifi_v10**（`out/z1_wifi_v10/boot.img`，
sha 0dcbf89a…，基于旧 v8 的 WiFi 用户空间修复线）的分叉记录。
记录级 diff 已核实：两线共同 71 条中 68 条逐字节一致，分叉恰为 5 条 WiFi 记录。

## 最终记录集（74 条 = v11 72 + 2）

| 记录 | 决策 | 依据 |
|---|---|---|
| `init.z1.network.rc` | **内容级三路合并**（5871B，v11 4937 / w10 4914 / v8 3858，三方互异） | v11 的 netfilter insmod 链、audio 段、GPS 段（z1_mnld + z1_gps_prep.sh）、diag_onboot 段逐字保留；插入 w10 的 [B3] 同步预置段（`exec /z1_wifi_pre.sh`）；`z1_wpa_supplicant` exec 路径取 w10（[B1]：`/z1_wpa_start.sh`，v11 保留的 `/system/bin/z1_wpa_start.sh` 在 system.img 不存在，按"WiFi 功能优先"裁决）；seclabel（[B2]）双方同为 `u:r:init:s0` |
| `z1_wifi_probe.sh` | **合并**（4755B，v11 3359 / w10 3144） | v11 的 v10 重写版为骨架（6620_launcher 等待 + NVRAM/regdb 诊断 + IFF_UP 确认 + setprop 后诊断扫描）；v10 未含 w10 [B4] 的完整语义（扫描降级为纯诊断、无失败回灌），故把 w10 v9 的 scan gate（45×2s「扫描有输出」判就绪）+ 失败回灌决定性 dmesg 行放回 setprop 之前 |
| `z1_wpa_start.sh` | **整条取 w10**（2828B） | v11 版与 v8 逐字节相同（sha f956731b…），w10 v9 版含 [B10] ctrl_interface 改写为 `/data/misc/wifi/sockets` + 路径自适配 |
| `z1_wifi_pre.sh` | **w10 独有带入**（3103B） | [B3]/[B5]：NVRAM → `/etc/firmware/nvram/WIFI`、regulatory.db → `/lib/firmware`、WIFI_RAM_CODE 别名 |
| `z1_wifi_connect.sh` | **w10 独有带入**（3759B） | 手工连 AP + 静态 IP + ping（镜像无 dhcpcd） |
| 其余 69 条 | **v11 原样**（含 `z1_gps_prep.sh`，v11 独有） | 逐字节 sha 校验与 v11 一致 |

## 复现打包

```sh
REPO=/home/lxj/claude/6572
python3 $REPO/z1a9_gpt/scripts/z1_ramdisk_edit.py \
  <(split 出的 v11 ramdisk.rootfs) ramdisk.r4 \
  --replace init.z1.network.rc=$PWD/init.z1.network.rc \
  --replace z1_wifi_probe.sh=$PWD/z1_wifi_probe.sh \
  --replace z1_wpa_start.sh=$PWD/z1_wpa_start.sh \
  --add     z1_wifi_pre.sh=$PWD/z1_wifi_pre.sh \
  --add     z1_wifi_connect.sh=$PWD/z1_wifi_connect.sh \
  --mode z1_wifi_pre.sh=0755 --mode z1_wifi_connect.sh=0755
# → body 1977444B(文件)/1976932B(MTK头内), initrd-end 0x842e2a64
fdtput -t x dtb.r4 /chosen linux,initrd-end 0x842e2a64
python3 $REPO/z1a9_gpt/scripts/z1_make_android9_bootimg.py ramdisk.r4 out.img \
  --zimage <v11 zImage> --dtb dtb.r4
python3 $REPO/z1a9_gpt/scripts/z1_verify_boot_payload.py out/z1_v11/boot.img out.img
```

## 本轮成品与校验

- 成品：`out/z1_r4_merge/boot_mainline_r4.img`（6,209,536B ≤ 6MB，
  sha256 2804c947a4b60684657061539de34b7ae2ecc3e51419e1090dd9e91c39678f24）
- 内核段：zImage 与 v11 逐字节一致（59bc73c3efe3011083749819c5bd7fbfdc1782f9fc4a496631d4f6ddd3f18529）；
  dtb 全树 diff 仅 `linux,initrd-end`（0x842e1abf→0x842e2a64），bootargs/加载地址不变
- 独立校验：boot 头除 rsize 外逐字段 = v11；MTK ROOTFS 头 body=1976932 且
  0x84100000+body=0x842e2a64；74 条记录逐条 sha 对账（69 条 v11 原样 + 3 条替换 + 2 条带入）
- 改前备份：`bridge_backups/pre_r4_20261027/`（三枚源镜像）

## 电源键核查（本轮定案）

判据串 `MT6323 regulator-only diagnostic mode` 在 v11 zImage（=w10/v8 同枚）
解压 Image（15,377,208B，xz 流#1 @0xf088 完整解压，流#0 为 zImage 字符串区伪
magic）中 **0 命中** → 电源键已活，R4 **无需**换 R3f zImage（R3f af1a9f17… 与
当前枚不同，但电源键形态完全同构：诊断串同样 0 命中、`mtk-pmic-keys`/`mediatek,mt6323-keys`
built-in 串在场）。机制：`fork_linux/drivers/mfd/mt6397-core.c:418` 的打印受
`IS_ENABLED(CONFIG_MFD_MT6323_NO_IRQ_DIAGNOSTIC)` 编译期折叠，`.config:2944`
`=n` 时串不进 Image 且 MFD 走正常 IRQ 路径；`CONFIG_KEYBOARD_MTK_PMIC=y`
（.config:1986）+ dtb `mt6323keys` 节点（power 键 keycode 0x74=KEY_POWER，
wakeup-source）三证齐。
