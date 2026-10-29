#!/bin/bash
# apply_r5h_xattr.sh — 幂等补齐 system.img 内缺失的 security.selinux xattr（r5h 轮）
#
# 背景（2026-10-29 取证，见同目录 R5H_XATTR_PLAN.md）:
#   r5 系列补丁全部用 debugfs write/rm+write 落盘 —— debugfs 生成**全新 inode**，
#   而 debugfs 不会写 security.selinux xattr，所以每个补丁写入的文件（含覆盖写）
#   在镜像里都**无 SELinux 标签**。全镜像扫描 r5f_final：2649 个 inode 中 69 个
#   无标签（r5b 清单 15 路径 + r5f 清单 1 路径 = 16 个全部命中，100% 缺失率）。
#   当前 androidboot.selinux=permissive 不受影响；init 的 restorecon_recursive
#   **不覆盖 /system 与 /vendor**（system-as-root 无独立 vendor 分区），xattr 是
#   唯一标签来源 —— **转 enforcing 前必须补齐**。
#
# 标签依据（对齐"邻文件真实标签"，不发明；类型存在性已对 ramdisk /sepolicy 逐一验证）:
#   ① r5_final 基线原标签回收（6 个被 rm+write 覆盖的路径，比邻文件更硬）
#   ② 同目录同类型邻文件实测标签（ro 挂载 getfattr 实扫）
#   ③ ramdisk 内 plat_file_contexts / vendor_file_contexts 精确条目
#      （plat:L12 /(vendor)/etc(/.*)?→vendor_configs_file；vendor_fc:L15 gnss、L24 sensors）
#   ④ 上述规则的兜底：plat:L6 /(vendor)(/.*)?→vendor_file、plat:L45 /system(/.*)?→system_file
#   弃用项：wifi_hal 先例片段里的 wifi_vendor_data_file **不在本镜像策略中**
#   （strings sepolicy 计数 0），照搬会写入未定义类型，故 /vendor/etc/wifi 用
#   vendor_configs_file（plat:L12 覆盖 + 兄弟目录 /vendor/etc/vintf 实测同标签）。
#
# 用法:
#   sudo bash apply_r5h_xattr.sh <in.img> [out.img] [--force]
#     out.img 缺省 = <in.img>.r5h_xattr.img；绝不原地改输入。
#     --force: 目标文件已有**不同**标签时覆盖（缺省只报告冲突、不覆盖）。
#
# 纪律（本项目固定规则）:
#   - 四件只读基线绝不做输出路径（硬拒绝）；输入只读（cp 读取不写）。
#   - 大操作前 df 检查：可用 < max(1.2G, 输入大小+200M) 则拒绝。
#   - 修补走 mount+setfattr（对齐项目规则），写完 getfattr 回读断言；
#     umount 后二次 ro 挂载全量回读（防本项目已知的"盘满/静默失败 rc=0"）。
#   - 收尾 e2fsck -fn + sha256sum。
set -euo pipefail

BASELINE=(
  /home/lxj/claude/6572/out/z1_r5_system/system.img.r5f_final
  /home/lxj/claude/6572/out/z1_r5_system/system.img.r5_final
  /home/lxj/claude/6572/out/z1_r5c_boot/boot.img
  /home/lxj/claude/6572/out/z1_r41_final/boot.img
)

# path<TAB>label<TAB>类别<TAB>锚点依据
#   类别 R5=r5b/r5f 补丁清单内(16)  DIR=8重点目录内、先轮新增(30)  OUT=8重点目录外(23...实为20文件+3目录)
DATA='R5	/usr/keylayout/mt6779-keypad.kl	u:object_r:system_file:s0	邻:usr/keylayout/Generic.kl; plat_fc L45 /system(/.*)?
R5	/etc/permissions/z1-mainline-features.xml	u:object_r:system_file:s0	r5_final 原标签回收
R5	/lib/modules/load_audio.sh	u:object_r:system_file:s0	r5_final 原标签回收
R5	/vendor/etc/media_codecs.xml	u:object_r:vendor_configs_file:s0	邻:vendor/etc/audio_policy_configuration.xml; plat_fc L12
R5	/vendor/etc/media_codecs_google_audio.xml	u:object_r:vendor_configs_file:s0	同上
R5	/vendor/etc/media_codecs_google_performance.xml	u:object_r:vendor_configs_file:s0	同上
R5	/vendor/etc/media_codecs_google_telephony.xml	u:object_r:vendor_configs_file:s0	同上
R5	/vendor/etc/media_codecs_google_video_le.xml	u:object_r:vendor_configs_file:s0	同上
R5	/vendor/etc/media_codecs_performance.xml	u:object_r:vendor_configs_file:s0	同上
R5	/vendor/bin/mnld	u:object_r:vendor_file:s0	r5_final 原标签回收
R5	/xbin/libmnlp_mt6572	u:object_r:system_file:s0	r5_final 原标签回收; 邻:xbin/bash
R5	/vendor/lib/hw/gps.default.so	u:object_r:vendor_file:s0	邻:vendor/lib/hw/local_time.default.so 等 default 族
R5	/lib/libxlog.so	u:object_r:system_file:s0	邻:lib 内 .so; plat_fc L45
R5	/vendor/lib/libxlog.so	u:object_r:vendor_file:s0	邻:vendor/lib 全 27 个 .so 同标签
R5	/lib/modules/z1-radio/mtk_stp_gps_soc.ko	u:object_r:system_file:s0	邻:z1-radio/cfg80211.ko 等 5 枚
R5	/lib/modules/z1-radio/mtk_stp_wmt_soc.ko	u:object_r:system_file:s0	r5_final 原标签回收
DIR	/vendor/lib/egl/libEGL_mesa.so	u:object_r:same_process_hal_file:s0	邻:egl/libEGL_swiftshader.so; vendor_fc L24 egl(/.*)?
DIR	/vendor/lib/dri/lima_dri.so	u:object_r:vendor_file:s0	所在目录自身实测 vendor_file; plat_fc L6 兜底
DIR	/usr/keylayout/mt6779_keypad.kl	u:object_r:system_file:s0	邻:Generic.kl
DIR	/usr/keylayout/z1_cst836u.kl	u:object_r:system_file:s0	邻:Generic.kl
DIR	/usr/keylayout/z1_cst836u.vk	u:object_r:system_file:s0	邻:Generic.kl
DIR	/lib/modules/snd.ko	u:object_r:system_file:s0	邻:z1_charger.ko/mt6572-afe-pcm.ko
DIR	/lib/modules/snd-pcm.ko	u:object_r:system_file:s0	同上
DIR	/lib/modules/snd-soc-core.ko	u:object_r:system_file:s0	同上
DIR	/lib/modules/snd-soc-mt6323.ko	u:object_r:system_file:s0	同上
DIR	/lib/modules/snd-soc-mtk-common.ko	u:object_r:system_file:s0	同上
DIR	/lib/modules/snd-soc-simple-amplifier.ko	u:object_r:system_file:s0	同上
DIR	/lib/modules/snd-timer.ko	u:object_r:system_file:s0	同上
DIR	/lib/modules/soundcore.ko	u:object_r:system_file:s0	同上
DIR	/lib/hw/radio.fm.mt6572.so	u:object_r:system_file:s0	邻:lib/hw/gralloc.z1.so 等 4 枚
DIR	/vendor/bin/hw/android.hardware.gnss@1.0-service	u:object_r:hal_gnss_default_exec:s0	ramdisk vendor_fc L15 精确条目; 类型在 sepolicy
DIR	/vendor/bin/hw/android.hardware.sensors@1.0-service	u:object_r:hal_sensors_default_exec:s0	ramdisk vendor_fc L24 精确条目; 类型在 sepolicy
DIR	/vendor/etc/gps.conf	u:object_r:vendor_configs_file:s0	plat_fc L12 /vendor/etc(/.*)?
DIR	/vendor/etc/agps_profiles_conf.xml	u:object_r:vendor_configs_file:s0	plat_fc L12
DIR	/vendor/etc/permissions	u:object_r:vendor_configs_file:s0	plat_fc L12; 兄弟目录 vintf/selinux 实测同标签
DIR	/vendor/etc/permissions/android.hardware.location.gps.xml	u:object_r:vendor_configs_file:s0	plat_fc L12
DIR	/vendor/etc/permissions/android.hardware.sensor.light.xml	u:object_r:vendor_configs_file:s0	plat_fc L12
DIR	/vendor/etc/permissions/android.hardware.sensor.proximity.xml	u:object_r:vendor_configs_file:s0	plat_fc L12
DIR	/vendor/etc/sensors	u:object_r:vendor_configs_file:s0	plat_fc L12
DIR	/vendor/etc/sensors/_hals.conf	u:object_r:vendor_configs_file:s0	plat_fc L12
DIR	/vendor/etc/wifi	u:object_r:vendor_configs_file:s0	plat_fc L12; wifi_vendor_data_file 不在策略(弃用)
DIR	/vendor/etc/wifi/wpa_supplicant.conf	u:object_r:vendor_configs_file:s0	plat_fc L12
DIR	/vendor/etc/wifi/wpa_supplicant_overlay.conf	u:object_r:vendor_configs_file:s0	plat_fc L12
DIR	/vendor/etc/vintf/manifest.xml	u:object_r:vendor_configs_file:s0	plat_fc L12/L20; 父目录实测同标签
DIR	/vendor/lib/hw/android.hardware.gnss@1.0-impl.so	u:object_r:vendor_file:s0	邻:hw/audio@2.0-impl.so 等同构 impl
DIR	/vendor/lib/hw/android.hardware.graphics.mapper@2.0-impl-2.1.so	u:object_r:same_process_hal_file:s0	邻:精确同名族 mapper@2.0-impl.so; vendor_fc L58
DIR	/vendor/lib/hw/android.hardware.sensors@1.0-impl.so	u:object_r:vendor_file:s0	邻:hw/audio@2.0-impl.so 等
DIR	/vendor/lib/hw/lights.default.so	u:object_r:vendor_file:s0	邻:hw/local_time.default.so 等
DIR	/vendor/lib/hw/sensors.mt6572.so	u:object_r:vendor_file:s0	邻:hw/power.default.so 等
OUT	/build.prop	u:object_r:system_file:s0	邻:根/compatibility_matrix.xml; plat_fc L45
OUT	/bin/z1diag	u:object_r:system_file:s0	plat_fc L45; 父目录/bin 实测
OUT	/bin/z1diag/README.md	u:object_r:system_file:s0	plat_fc L45
OUT	/bin/z1diag/z1_diag_all.sh	u:object_r:system_file:s0	plat_fc L45; 非服务入口不配 *_exec
OUT	/bin/z1diag/z1_diag_audio.sh	u:object_r:system_file:s0	同上
OUT	/bin/z1diag/z1_diag_bt.sh	u:object_r:system_file:s0	同上
OUT	/bin/z1diag/z1_diag_gps.sh	u:object_r:system_file:s0	同上
OUT	/bin/z1diag/z1_diag_keys.sh	u:object_r:system_file:s0	同上
OUT	/bin/z1diag/z1_diag_onboot.sh	u:object_r:system_file:s0	同上
OUT	/bin/z1diag/z1_diag_wifi.sh	u:object_r:system_file:s0	同上
OUT	/etc/firmware/nvram	u:object_r:system_file:s0	plat_fc L45; 兄弟 regulatory.db 邻
OUT	/etc/firmware/nvram/WIFI	u:object_r:system_file:s0	plat_fc L45
OUT	/etc/firmware/regulatory.db	u:object_r:system_file:s0	plat_fc L45
OUT	/etc/gps.conf	u:object_r:system_file:s0	plat_fc L45
OUT	/etc/wifi	u:object_r:system_file:s0	plat_fc L45; wifi_data_file 无 fc 条目故不用
OUT	/etc/wifi/p2p_supplicant_overlay.conf	u:object_r:system_file:s0	plat_fc L45
OUT	/etc/wifi/wpa_supplicant.conf	u:object_r:system_file:s0	plat_fc L45
OUT	/etc/wifi/wpa_supplicant_overlay.conf	u:object_r:system_file:s0	plat_fc L45
OUT	/etc/z1_wallpaper.png	u:object_r:system_file:s0	plat_fc L45
OUT	/usr/idc/z1_cst836u.idc	u:object_r:system_file:s0	邻:usr/idc/qwerty.idc; plat_fc L45'

usage() { echo "用法: sudo bash $0 <in.img> [out.img] [--force]" >&2; }
[ $# -ge 1 ] || { usage; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo "需要 root（mount/setfattr）。请: sudo bash $0 $*" >&2; exit 2; }

IN=""; OUT=""; FORCE=0
for a in "$@"; do
  case "$a" in
    --force) FORCE=1 ;;
    *) if [ -z "$IN" ]; then IN=$a; elif [ -z "$OUT" ]; then OUT=$a; else echo "多余参数: $a" >&2; usage; exit 2; fi ;;
  esac
done
[ -n "$IN" ] || { usage; exit 2; }
[ -f "$IN" ] || { echo "输入不存在: $IN" >&2; exit 2; }
[ "${OUT:-}" ] || OUT="${IN%.img}.r5h_xattr.img"

IN_R=$(realpath "$IN"); OUT_R=$(realpath -m "$OUT")
[ "$IN_R" != "$OUT_R" ] || { echo "拒绝原地改: 输出必须不同于输入" >&2; exit 1; }
for b in "${BASELINE[@]}"; do
  [ "$OUT_R" != "$(realpath -m "$b")" ] || { echo "拒绝: 输出路径是只读基线 $b" >&2; exit 1; }
done

# ---- 磁盘检查（本项目多次因盘满静默失败）----
need=$(( $(stat -c %s "$IN_R") / 1024 + 204800 ))          # 输入大小 + 200M
avail=$(df -k / | awk 'NR==2{print $4}')
min_need=$(( need > 1228800 ? need : 1228800 ))            # 铁律: 可用 <1.2G 不动
if [ "$avail" -lt "$min_need" ]; then
  echo "磁盘不足: 可用 ${avail}K < 需要 ${min_need}K。只出脚本不出镜像，中止。" >&2; exit 1
fi

MP=$(mktemp -d /tmp/r5h_mnt.XXXXXX)
cleanup() { mountpoint -q "$MP" && umount "$MP" || true; rmdir "$MP" 2>/dev/null || true; }
trap cleanup EXIT
mnt_rw()   { mount -o loop,rw "$OUT_R" "$MP"; }
mnt_ro()   { mount -o loop,ro "$OUT_R" "$MP"; }
# 注意: 无标签时 getfattr 返回非 0 —— 必须吞掉, 否则 set -e + pipefail 会让
# 赋值语句 cur=$(label_of ...) 触发 errexit 静默退出（2026-10-29 合成镜像实测踩中）
label_of() { getfattr --only-values -n security.selinux -h "$1" 2>/dev/null | tr -d '\0' || true; }

n_patch=0; n_skip=0; n_conflict=0; n_missing_path=0; conflict_list=""

# 已有输出则先验证：全部正确 → 幂等快路径
if [ -f "$OUT_R" ]; then
  mnt_ro
  ok=1
  while IFS=$'\t' read -r catg path want anchor; do
    f="$MP$path"
    if [ ! -e "$f" ]; then continue; fi
    cur=$(label_of "$f")
    [ "$cur" = "$want" ] && continue
    # 与阶段2一致的豁免: FORCE=0 时已有不同标签的文件是有意保留的冲突
    if [ -n "$cur" ] && [ "$FORCE" -ne 1 ]; then continue; fi
    ok=0; break
  done < <(printf '%s\n' "$DATA")
  umount "$MP"
  if [ "$ok" -eq 1 ]; then echo "已是正确标签，无需修补: $OUT_R"; exit 0; fi
  echo "输出存在但标签不全，重建: $OUT_R"
  rm -f "$OUT_R"
fi

cp -a "$IN_R" "$OUT_R"

# 镜像必须是 clean 状态：rw 挂载会回放 journal，可能回退掉 debugfs 裸编辑
state=$(dumpe2fs -h "$OUT_R" 2>/dev/null | awk -F': *' '/^Filesystem state/{print $2}')
[ "$state" = "clean" ] || { echo "镜像 fs 状态非 clean ('$state')，为避免 journal 回放吞掉 debugfs 编辑，中止。" >&2; exit 1; }

echo "== 阶段1: rw 挂载补标签 =="
mnt_rw
while IFS=$'\t' read -r catg path want anchor; do
  f="$MP$path"
  if [ ! -e "$f" ]; then n_missing_path=$((n_missing_path+1)); continue; fi
  cur=$(label_of "$f")
  if [ "$cur" = "$want" ]; then n_skip=$((n_skip+1)); continue; fi
  if [ -n "$cur" ] && [ "$FORCE" -ne 1 ]; then
    n_conflict=$((n_conflict+1)); conflict_list="$conflict_list  $path 现有=$cur 目标=$want"$'\n'
    continue
  fi
  setfattr -n security.selinux -v "$want" "$f"
  got=$(label_of "$f")                                  # 写完回读断言
  if [ "$got" != "$want" ]; then echo "FATAL: $path 回读不符: '$got' != '$want'" >&2; umount "$MP"; exit 1; fi
  echo "  置[$catg] $path = $want"
  n_patch=$((n_patch+1))
done < <(printf '%s\n' "$DATA")
sync; umount "$MP"

echo "== 阶段2: umount 后 ro 复挂全量回读（防静默失败）=="
mnt_ro
n_bad=0; n_conflict2=0
while IFS=$'\t' read -r catg path want anchor; do
  f="$MP$path"
  [ -e "$f" ] || continue
  cur=$(label_of "$f")
  [ "$cur" = "$want" ] && continue
  # FORCE=0 时冲突文件有意不动，回读豁免（阶段1 已列出明细）
  if [ -n "$cur" ] && [ "$FORCE" -ne 1 ]; then n_conflict2=$((n_conflict2+1)); continue; fi
  echo "  回读失败: $path '$cur' != '$want'" >&2; n_bad=$((n_bad+1))
done < <(printf '%s\n' "$DATA")
umount "$MP"

if [ "$n_bad" -ne 0 ]; then echo "FATAL: $n_bad 个回读不符" >&2; exit 1; fi
e2fsck -fn "$OUT_R" >/dev/null || { echo "FATAL: e2fsck 未通过" >&2; exit 1; }

echo "== 结果 =="
echo "  补写: $n_patch  已正确跳过: $n_skip  路径不存在跳过: $n_missing_path  冲突未覆盖: $n_conflict/$n_conflict2"
[ -z "$conflict_list" ] || printf '冲突明细(未覆盖, --force 可覆盖):\n%s' "$conflict_list"
echo "  e2fsck -fn: 通过"
echo "  产物: $OUT_R"
sha256sum "$OUT_R"
