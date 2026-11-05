#!/system/bin/sh
# Record that this system build has been fully booted, so the next boot shows
# the normal LineageOS animation instead of "正在优化系统".
TAG=/data/misc/z1_anim_tag
mkdir -p /data/misc 2>/dev/null
cur=$(md5sum /system/build.prop 2>/dev/null | cut -d' ' -f1)
[ -n "$cur" ] && printf '%s\n' "$cur" > "$TAG" 2>/dev/null
printf '<6>[z1anim] marker written: %s\n' "$cur" > /dev/kmsg 2>/dev/null
