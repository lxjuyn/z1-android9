#!/system/bin/sh
# Z1 FM 收音机：加载 mtk_fm_drv 并打开音频出口。
#
# 2026-11-05: the driver has been shipping in /lib/modules since 2026-11-02 but
# NOTHING ever insmod'ed it -- no rc service, no script, and load_audio.sh does
# not mention fm at all.  So FM has never been functional on any build.
#
# Hardware facts (fork_linux/.../fmradio/PORTING_NOTES.md):
#   Z1 is MT6572 SoC-integrated CONSYS with an MT6627N RF transceiver; FM
#   commands/RDS go over the WMT/STP control plane on BTIF, and the audio
#   leaves the chip digitally as FM-I2S into the AFE (ASRC -> DL1).
#   So the prerequisites are: the WMT stack up (mtk_stp_wmt_soc, already
#   loaded by the radio chain) and nothing else.
#
# 2026-11-05 (v17): KO used to be /lib/modules/... -- there is no /lib at
#   runtime (init.rc only symlinks /bin, /etc, /d, /vendor), so the module was
#   never found and FM stayed dead.  Same fix as load_audio.sh.
log() { printf '<6>[z1fm] %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }

KO=/system/lib/modules/mtk_fm_drv.ko
[ -f "$KO" ] || { log "no $KO; FM unavailable"; exit 0; }

if [ -d /sys/module/mtk_fm_drv ]; then
    log "already loaded"
else
    err=$(insmod "$KO" 2>&1)
    rc=$?
    if [ $rc -ne 0 ]; then
        log "insmod FAILED (rc=$rc): $err"
        exit 0
    fi
    log "insmod OK"
fi

# device_create() makes the node via ueventd; give it a moment.
i=0
while [ ! -e /dev/fm ] && [ $i -lt 40 ]; do sleep 1; i=$((i+1)); done
if [ ! -e /dev/fm ]; then
    log "/dev/fm did not appear after ${i}s"
    exit 0
fi
chmod 0666 /dev/fm 2>/dev/null
chown system system /dev/fm 2>/dev/null
log "/dev/fm ready"

# Audio path: FM-I2S -> ASRC -> FM Playback -> DL1.  Set it up but do NOT
# enable the switch until someone actually tunes, otherwise the AFE feeds an
# idle I2S into the speaker.
if [ -e /dev/snd/controlC0 ]; then
    tinymix "FM Playback Volume" 0 >/dev/null 2>&1
    log "mixer touched; tune in the app, then enable 'FM Playback Switch'"
else
    log "no controlC0 yet; audio path not configured"
fi
exit 0
