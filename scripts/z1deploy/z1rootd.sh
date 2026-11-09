#!/system/bin/sh
# ---------------------------------------------------------------------------
# z1rootd.sh -- Z1 deployment channel: root command daemon (serial console).
#
# Runs as ROOT from Android init (see init_fragment.rc):
#     service z1_rootd /system/bin/sh /z1rootd.sh
#     class core, user root, group root, seclabel u:r:init:s0, no oneshot
#
# The serial console shell is uid 2000 (shell) and cannot write /vendor, so
# all privileged work is delegated to this daemon:
#
#   $STAGE/cmd.sh   operator writes this (as shell; world-writable dir).
#                   The daemon renames it to cmd.run (atomic claim), runs it
#                   with /system/bin/sh as root, captures stdout+stderr to
#                   $STAGE/out.txt, appends "###RC=<rc> ###DONE" and deletes
#                   cmd.run.
#   $STAGE/install.sh  optional; executed once at every daemon start (i.e.
#                   once per boot) for boot-time re-application of staged
#                   binaries.  Output goes to $STAGE/install.out.txt.
#
# Robustness rules
#   - the daemon never dies because a command hangs: toybox `timeout` is used
#     when present (it is, /system/bin/timeout), otherwise a 1 s watchdog
#     kills the child.  Every step is logged to /dev/kmsg with the "z1rootd:"
#     prefix (the only channel that reliably reaches the serial console).
#   - cmd.sh is only claimed once its size is stable across two polls, so a
#     `cat > cmd.sh <<EOF` upload cannot be executed half-written.  z1push.sh
#     additionally uploads to cmd.sh.tmp and renames it into place.
#   - a stale cmd.run (daemon was killed mid-run) is preserved as
#     cmd.run.stale and never re-executed blindly.
#   - output files and the stage directory stay world-writable so the shell
#     user can read the results and delete them.
#
# Operator protocol (host side): z1push.sh --rootcmd '<script>' does all of
# the above and prints out.txt; see out/Z1_DEPLOY_CHANNEL_20261110.md.
# ---------------------------------------------------------------------------

STAGE=/data/local/tmp/z1stage
OUT=$STAGE/out.txt
CMD=$STAGE/cmd.sh
RUN=$STAGE/cmd.run
INSTALL=$STAGE/install.sh
INSTALL_OUT=$STAGE/install.out.txt
KMSG=/dev/kmsg

MAX_SECS=${Z1ROOTD_MAX_SECS:-120}     # per-command wall-clock cap
SHELL_UID=2000                        # AID_SHELL
SHELL_GID=2000

MKDIR=/system/bin/mkdir
CHMOD=/system/bin/chmod
CHOWN=/system/bin/chown
RM=/system/bin/rm
MV=/system/bin/mv
TIMEOUT=/system/bin/timeout
SH=/system/bin/sh
WC=/system/bin/wc
DATE=/system/bin/date

log() { printf 'z1rootd: %s\n' "$*" >> "$KMSG" 2>/dev/null; }

uptime_s() { read -r up _rest < /proc/uptime 2>/dev/null; printf '%s' "${up:-?}"; }

file_size() { set -- $($WC -c < "$1" 2>/dev/null); printf '%s' "${1:-0}"; }

# Keep the shared files usable by the shell user (uid 2000).
fix_perms() {
    $CHMOD 0777 "$STAGE" 2>/dev/null
    $CHMOD 0777 "$STAGE/active" 2>/dev/null
    $CHMOD 0666 "$OUT" "$INSTALL_OUT" 2>/dev/null
    $CHOWN "$SHELL_UID:$SHELL_GID" "$STAGE" 2>/dev/null
    $CHOWN "$SHELL_UID:$SHELL_GID" "$STAGE/active" 2>/dev/null
    return 0
}

# run_script <script> <output> <label>
# Executes <script> as root with a wall-clock cap and always terminates the
# output with "###RC=<rc> ###DONE" (rc=124 when the cap killed it).
run_script() {
    _f=$1
    _o=$2
    _l=$3
    _killed=0
    _rc=0

    : > "$_o" 2>/dev/null
    $CHMOD 0666 "$_o" 2>/dev/null
    _t0=$($DATE +%s 2>/dev/null)
    log "$_l start: ${_f} ($(file_size "$_f") B) at uptime $(uptime_s)s"

    if [ -x "$TIMEOUT" ]; then
        "$TIMEOUT" "$MAX_SECS" "$SH" "$_f" > "$_o" 2>&1
        _rc=$?
    else
        # No toybox timeout: 1 s watchdog on the child pid.  Grandchildren are
        # not killed (no process-group juggling), so keep commands simple.
        "$SH" "$_f" > "$_o" 2>&1 &
        _pid=$!
        _n=0
        while kill -0 "$_pid" 2>/dev/null; do
            sleep 1
            _n=$((_n + 1))
            if [ "$_n" -ge "$MAX_SECS" ]; then
                log "watchdog: $_l exceeded ${MAX_SECS}s, killing pid ${_pid}"
                kill -TERM "$_pid" 2>/dev/null
                sleep 2
                kill -KILL "$_pid" 2>/dev/null
                _killed=1
                break
            fi
        done
        wait "$_pid" 2>/dev/null
        _rc=$?
        [ "$_killed" = 1 ] && _rc=124
    fi

    printf '###RC=%s ###DONE\n' "$_rc" >> "$_o" 2>/dev/null
    _t1=$($DATE +%s 2>/dev/null)
    $CHMOD 0666 "$_o" 2>/dev/null
    $CHOWN "$SHELL_UID:$SHELL_GID" "$_o" 2>/dev/null
    log "$_l done: rc=${_rc} elapsed=$((_t1 - _t0))s out=$(file_size "$_o")B"
    return 0
}

# --- start-up --------------------------------------------------------------
trap 'log "terminating on signal"; exit 0' TERM INT HUP

$MKDIR -p "$STAGE/active" 2>/dev/null
if [ ! -d "$STAGE" ]; then
    log "FATAL cannot create ${STAGE}"
    exit 0
fi
fix_perms
printf '%s\n' "$$" > "$STAGE/rootd.pid" 2>/dev/null
$CHMOD 0666 "$STAGE/rootd.pid" 2>/dev/null

if [ -e "$RUN" ]; then
    if $MV -f "$RUN" "$RUN.stale" 2>/dev/null; then
        log "stale cmd.run preserved as cmd.run.stale (not re-executed)"
    fi
fi

log "started pid $$ at uptime $(uptime_s)s (stage=${STAGE} cap=${MAX_SECS}s timeout=$([ -x "$TIMEOUT" ] && printf present || printf absent))"

if [ -f "$INSTALL" ]; then
    run_script "$INSTALL" "$INSTALL_OUT" install
else
    log "no install.sh (skipping boot-time re-application hook)"
fi

# --- main poll loop --------------------------------------------------------
# cmd.sh is claimed only after its size is unchanged for one full poll cycle.
PREV_SIZE=-1
while : ; do
    if [ -f "$CMD" ]; then
        _sz=$(file_size "$CMD")
        if [ "$_sz" = "$PREV_SIZE" ]; then
            if $MV -f "$CMD" "$RUN" 2>/dev/null; then
                log "claimed cmd.sh (${_sz} B) -> cmd.run"
                run_script "$RUN" "$OUT" cmd
                $RM -f "$RUN" 2>/dev/null
                fix_perms
                PREV_SIZE=-1
            else
                log "WARN claim of cmd.sh failed (race with the writer?), retrying"
                PREV_SIZE=-1
            fi
        else
            PREV_SIZE=$_sz
        fi
    else
        PREV_SIZE=-1
    fi
    sleep 1
done
