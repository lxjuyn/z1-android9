#!/system/bin/sh
# Pick which boot animation to show.
#
# 2026-11-05 (v2).  Two bugs in v1, both confirmed on the built images:
#   1. /media/bootanimation.zip and /media/bootanimation-optimizing.zip were
#      byte-identical (md5 4c9d2271...), i.e. the real LineageOS animation was
#      gone and the bind mount was a semantic no-op.
#   2. the switch used md5(/system/build.prop), which is IDENTICAL across
#      v13/v14/v15 (bc86c2f4...), so the "first boot after a flash" condition
#      never fired -- the tag never changed.
# v2 uses /system/etc/z1_system_id, a string stamped into each image at build
# time, so it really does change whenever the system changes.
TAG=/data/misc/z1_anim_tag
ID=/system/etc/z1_system_id
OPT=/system/media/bootanimation-optimizing.zip
LOS=/system/media/bootanimation.zip

cur=""
[ -f "$ID" ] && cur=$(cat "$ID" 2>/dev/null | tr -d ' \r\n')
old=""
[ -f "$TAG" ] && old=$(cat "$TAG" 2>/dev/null | tr -d ' \r\n')

if [ -n "$cur" ] && [ "$cur" != "$old" ] && [ -f "$OPT" ] && [ -f "$LOS" ]; then
    if mount --bind "$OPT" "$LOS"; then
        printf '<6>[z1anim] optimizing animation (id %s -> %s)\n' "${old:-none}" "$cur" > /dev/kmsg 2>/dev/null
    else
        printf '<6>[z1anim] bind mount FAILED, falling back to the shipped animation\n' > /dev/kmsg 2>/dev/null
    fi
else
    printf '<6>[z1anim] normal animation (id %s, tag %s)\n' "${cur:-none}" "${old:-none}" > /dev/kmsg 2>/dev/null
fi

exec /system/bin/bootanimation "$@"
