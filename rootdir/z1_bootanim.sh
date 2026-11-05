#!/system/bin/sh
# Pick which boot animation to show.
#
# 2026-11-05: libbootanimation.so only searches /product/media, /oem/media and
# /system/media (strings-verified) -- there is no /data override, and /system is
# read-only, so the animation cannot be swapped by replacing a file.  Instead we
# bind-mount a different zip over /system/media/bootanimation.zip before exec'ing
# the real bootanimation.  mount(2) does not modify the read-only filesystem, so
# this works on the shipping image.
#
# When to show "正在优化系统": whenever the system image changed, because that is
# exactly what makes zygote prune the dalvik cache and recompile every system app
# (the 8-15 minute window).  The tag file records the build.prop checksum of the
# system we last fully booted; a flash changes it.
TAG=/data/misc/z1_anim_tag
PROP=/system/build.prop
OPT=/system/media/bootanimation-optimizing.zip
LOS=/system/media/bootanimation.zip

cur=""
[ -f "$PROP" ] && cur=$(md5sum "$PROP" 2>/dev/null | cut -d' ' -f1)
old=""
[ -f "$TAG" ] && old=$(cat "$TAG" 2>/dev/null)

if [ -f "$OPT" ] && [ "$cur" != "$old" ]; then
    mount --bind "$OPT" "$LOS" 2>/dev/null \
        && printf '<6>[z1anim] showing optimizing animation (build %s -> %s)\n' "$old" "$cur" > /dev/kmsg 2>/dev/null
fi

exec /system/bin/bootanimation "$@"
