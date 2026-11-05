#!/bin/bash
# z1_build_v17_system.sh — 从 v16 重放出 v17(可复现, 不依赖 900MB 归档)
#
# v17 = v16 +
#   1) /lib/modules/z1_charger.ko  ← fork_linux/drivers/power/supply/z1_charger.ko
#      (CHR_EN 校验/重试 + 厂商漏写的三条 + 风暴/失败 dump)
#   2) /etc/init/z1_fm.rc          ← /system/lib/modules/load_fm.sh (原来是 /lib, 不存在)
#   3) /lib/modules/load_fm.sh     ← KO=/system/lib/modules/mtk_fm_drv.ko (同上)
#   4) /vendor/etc/vintf/manifest.xml ← rootdir/manifest_v17.xml (补 android.hardware.light@2.0)
#   5) /bin/z1_bootimg_build.sh 加"每个 system id 只尝试一次"的标记(避免失败后每次开机
#      重跑 30+ 分钟 dex2oat); /etc/z1_system_id 同步到 v17
#   6) SELinux 标签: light HAL -> hal_light_default_exec;
#      4 个用 vendor_init_rc(策略里不存在)的 rc -> vendor_configs_file;
#      15 个无 xattr 的文件 -> system_file
#
# 期望输出 sha256 = 560727cbc8b77ee45ae01b0339e97f600aad70e99804f00bf88188eb5cd29d44
set -euo pipefail
cd /home/lxj/claude/6572

SRC=out/z1_v16_system/system_v16.img
DST=out/z1_v17_system/system_v17.img
KO=fork_linux/drivers/power/supply/z1_charger.ko
FM_RC=z1a9_gpt/rootdir/z1_fm.rc
FM_SH=z1a9_gpt/rootdir/load_fm.sh
[ -f "$SRC" ] || { echo "ERR: $SRC 不存在"; exit 1; }
[ -f "$KO" ]  || { echo "ERR: $KO 不存在(先 make drivers/power/supply/z1_charger.ko)"; exit 1; }

mkdir -p "$(dirname "$DST")"
tools/z1_make_system_image.sh "$SRC" "$DST" \
    /lib/modules/z1_charger.ko:"$KO":0100664 \
    /etc/init/z1_fm.rc:"$FM_RC":0100644 \
    /lib/modules/load_fm.sh:"$FM_SH":0100755 \
    /vendor/etc/vintf/manifest.xml:z1a9_gpt/rootdir/manifest_v17.xml:0100644 \
    /bin/z1_bootimg_build.sh:z1a9_gpt/rootdir/z1_bootimg_build.sh:0100755 \
    /etc/z1_system_id:z1a9_gpt/rootdir/z1_system_id:0100644

relabel() { debugfs -w -R "ea_set $1 security.selinux $2" "$DST" >/dev/null 2>&1; }

relabel /vendor/bin/hw/android.hardware.light@2.0-service u:object_r:hal_light_default_exec:s0
relabel /vendor/etc/vintf/manifest.xml u:object_r:vendor_configs_file:s0
for f in /vendor/etc/init/hostapd.android.rc \
         /vendor/etc/init/android.hardware.wifi@1.0-service.rc \
         /vendor/etc/init/android.hardware.gnss@1.0-service.rc \
         /vendor/etc/init/wpa_supplicant.rc \
         /vendor/etc/init/android.hardware.sensors@1.0-service.rc; do
    relabel "$f" u:object_r:vendor_configs_file:s0
done
for f in /etc/prop.default /etc/z1_wallpaper.png /bin/z1_accept.sh /bin/z1_touch_load.sh \
         /lib/firmware /lib/modules/z1_charger.ko /lib/modules/z1_cst836u.ko \
         /priv-app/Trebuchet /priv-app/Trebuchet/Trebuchet.apk \
         /.r5f_gpu_backup /.r5f_gpu_backup/build.prop \
         /.r5f_gpu_backup/vendor_lib_dri_lima_dri.so \
         /.r5f_gpu_backup/vendor_lib_egl_libEGL_mesa.so \
         /etc/init/z1_fm.rc /lib/modules/load_fm.sh; do
    relabel "$f" u:object_r:system_file:s0
done

# 注意: 整幅镜像的 sha256 每次构建都会不同(debugfs 会更新 ext4 超级块的写时间),
# 所以不要用整镜像 hash 判定"重放成功"。判定标准是下面三个文件的 content hash
# 与 xattr, 这三个是稳定可比的。
echo "--- 判定用 content hash (必须与 EXPECT 一致) ---"
for f in /lib/modules/z1_charger.ko /etc/init/z1_fm.rc /lib/modules/load_fm.sh /vendor/etc/vintf/manifest.xml /bin/z1_bootimg_build.sh /etc/z1_system_id; do
    tmp=$(mktemp); rm -f "$tmp"
    debugfs -R "dump $f $tmp" "$DST" >/dev/null 2>&1
    printf '%-28s %s  %s\n' "$f" "$(sha256sum "$tmp" | cut -d' ' -f1)" \
           "$(debugfs -R "ea_list $f" "$DST" 2>/dev/null | grep -o 'u:object_r:[a-z_]*:s0')"
    rm -f "$tmp"
done
echo "EXPECT z1_charger.ko = 8967b0ba5cb3ac592dcd047a96b8fb48e4ae19ddc8d75a4f7fe852d0aa6cf2fe"
echo "EXPECT z1_fm.rc      = e0be383b927811bb6f3e47a1426d422927266048625cba7f12b900b85178aacd"
echo "EXPECT load_fm.sh    = fad51b3789fbbc77a7a177d1fcab28e4249c16e4ed5506b8baaf63a247ca8ada"
echo "EXPECT manifest.xml  = 554e08cdbca31343e79e4647449877849c818b0889e4955bafb11ba2cc855a08"
echo "EXPECT z1_bootimg_build.sh = f77d7ace711bd7d046abb8cdf66f55eb4945b162e563d8c3c67662a8b711e06b"
echo "EXPECT z1_system_id        = 3b1ea5e8719b345e143758b7af9d0d75678ec46fe7af29f530971d9518c5e06f"
echo "整镜像 sha256(不用于判定) = $(sha256sum "$DST" | cut -d' ' -f1)"
