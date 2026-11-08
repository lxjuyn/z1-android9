#!/system/bin/sh
# v38: 首启 ART 无法使用 boot image 的兜底。
# 板上证据：刷完 system 的第一次启动，dex2oat 报
#   Could not create image space with image file '/system/framework/boot.art'
#   Error was: Cannot relocate image ... to /data/dalvik-cache/arm/system@framework@boot.art: Patching disabled.
# 结果是 PMS 无法 dexopt -> lineagesettings provider 起不来 -> PhoneWindowManager NPE
# -> system_server 无限重启 -> 永远停在二屏（重启一次就正常）。
# 这里在 zygote 之前把 /system/framework/arm 里的 boot 镜像按 dalvik-cache 的命名复制好，
# 让 ART 直接命中现成文件、不再走 "relocate/patching" 路径。
log() { printf '<6>[z1art] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }
SRC=/system/framework/arm
DST=/data/dalvik-cache/arm
[ -d "$SRC" ] || { log "no $SRC; nothing to seed"; exit 0; }
mkdir -p "$DST" 2>/dev/null

seed() {
    src=$1 dest=$2
    [ -s "$src" ] || { log "missing $src"; return 0; }
    [ -s "$dest" ] && { log "already present: $dest"; return 0; }
    if cp -f "$src" "$dest" 2>/dev/null; then
        chown 0:0 "$dest" 2>/dev/null
        chmod 0644 "$dest" 2>/dev/null
        log "seeded $(basename "$dest") ($(wc -c < "$dest" 2>/dev/null) bytes)"
    else
        log "copy FAILED: $src -> $dest"
    fi
}

seed "$SRC/boot.art" "$DST/system@framework@boot.art"
seed "$SRC/boot.oat" "$DST/system@framework@boot.oat"
# 某些构建还有 .rel 变体
seed "$SRC/boot.art.rel" "$DST/system@framework@boot.art.rel"
exit 0
