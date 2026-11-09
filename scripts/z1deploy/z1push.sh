#!/bin/bash
# ---------------------------------------------------------------------------
# z1push.sh -- host-side transport for the Z1 deployment channel.
#
# The board's serial console (CH340 /dev/ttyUSB0 at 921600 8N1) runs an
# interactive shell as uid 2000 (prompt "console:/ $ ").  Kernel messages are
# interleaved in the same stream, so every exchange is delimited by unique
# tags and read back from a capture file.
#
# Requirements on the host:
#   * Z1DEV  = serial device            (default /dev/ttyUSB0)
#   * Z1LOG  = append-only capture file of the same device, e.g.
#                  stty -F /dev/ttyUSB0 921600 raw -echo
#                  cat /dev/ttyUSB0 >> /tmp/z1_serial.log &      # Z1LOG=...
#     The capture reader must be the only reader; this script only writes.
#
# Subcommands
#   z1push.sh [--dry-run] [--mode M] [--retries N] <local-file> [remote-name]
#       base64 the file, push it through a quoted heredoc, decode with
#       toybox base64, compare sha256 with the host value, then mv it into
#       /data/local/tmp/z1stage/<remote-name>.
#   z1push.sh --cmd '<shell command>'
#       run one command as the shell user and print its output (kernel log
#       lines filtered out).  Exit status: 0 if the end tag was seen.
#   z1push.sh --rootcmd '<script>'     (or --rootcmd - to read stdin)
#       upload a script to /data/local/tmp/z1stage/cmd.sh.tmp, rename it to
#       cmd.sh (atomic), wait for the root daemon to run it and print out.txt.
#       This is the only way to write /vendor as the shell user.
#   z1push.sh --extract <tag> [logfile]
#       offline: extract one tagged block from a saved capture log.
#
# Options
#   --dry-run            print the payload plan; never touch the device
#   --mode M             mode applied to the pushed file after mv (default 0644)
#   --retries N          retry a failed upload N times (default 1 => 2 attempts)
#   --wait SECS          per-block timeout for --cmd/--rootcmd (default 30)
#   -h | --help          this text
#
# Environment
#   Z1DEV (default /dev/ttyUSB0)      Z1LOG (capture file, required online)
#   Z1BAUD (default 921600)           Z1NO_STTY=1 to keep the port settings
#   Z1B64CHARS (default 400)          Z1CHUNK_LINES (default 8)
#   Z1CHUNK_SLEEP (default 0.05)      Z1POLL (default 0.2)
#   Z1PUSH_TIMEOUT (override the computed heredoc timeout)
#
# The real device is NEVER touched in --dry-run mode.
# ---------------------------------------------------------------------------

set -u

PROG=${0##*/}
DEV=${Z1DEV:-/dev/ttyUSB0}
LOG=${Z1LOG:-}
BAUD=${Z1BAUD:-921600}
STAGE=/data/local/tmp/z1stage
B64CHARS=${Z1B64CHARS:-400}
CHUNK_LINES=${Z1CHUNK_LINES:-8}
CHUNK_SLEEP=${Z1CHUNK_SLEEP:-0.05}
POLL=${Z1POLL:-0.2}
MODE=0644
RETRIES=1
WAIT=30
DRYRUN=0
FD_OPEN=0
ECHO_OFF=0
TMPD=""

die()  { printf '%s: ERROR: %s\n' "$PROG" "$*" >&2; exit 1; }
note() { printf '%s: %s\n' "$PROG" "$*" >&2; }

usage() {
    cat <<'USAGE'
z1push.sh -- host-side transport for the Z1 deployment channel
            (serial console shell, uid 2000, path /data/local/tmp/z1stage)

Usage:
  z1push.sh [--dry-run] [--mode M] [--retries N] <local-file> [remote-name]
      base64 the file, push it through a quoted heredoc, decode it with toybox
      base64, compare its sha256 with the host value, then mv it into place
      as /data/local/tmp/z1stage/<remote-name>.
  z1push.sh --cmd '<shell command>'
      run a command as the shell user, print its output (kernel console lines
      filtered out).  Exit status 0 only if the closing tag was seen.
  z1push.sh --rootcmd '<script>'      (use '-' to read the script from stdin)
      upload the script to /data/local/tmp/z1stage/cmd.sh.tmp, rename it to
      cmd.sh (atomic), wait for the root daemon (service z1_rootd) to run it
      as root and print out.txt.  This is how you write /vendor.
  z1push.sh --extract <tag> [logfile]
      offline: print one tagged block from an existing capture log.
  z1push.sh --help

Options:
  --dry-run       print the payload plan, never open or write the serial device
  --mode M        mode applied after the final mv (default 0644, e.g. 0755)
  --retries N     retry a failed upload N times (default 1 => 2 attempts)
  --wait SECS     timeout per --cmd/--rootcmd block (default 30)
  -h, --help      this text

Environment:
  Z1DEV           serial device                   (default /dev/ttyUSB0)
  Z1LOG           append-only console capture     (required for online modes)
  Z1BAUD          baud rate                       (default 921600)
  Z1NO_STTY=1     do not touch the port settings
  Z1B64CHARS      base64 line width               (default 400)
  Z1CHUNK_LINES   lines per paced chunk           (default 8)
  Z1CHUNK_SLEEP   pause between chunks, seconds   (default 0.05)
  Z1POLL          capture-log poll interval       (default 0.2)
  Z1PUSH_TIMEOUT  override the computed heredoc timeout

The capture file must be produced by a reader that only appends:
  stty -F /dev/ttyUSB0 921600 raw -echo
  cat /dev/ttyUSB0 >> /tmp/z1_serial.log &
  export Z1LOG=/tmp/z1_serial.log
USAGE
    exit 0
}

cleanup() {
    local rc=$?
    if [ "$FD_OPEN" = 1 ] && [ "$ECHO_OFF" = 1 ]; then
        printf 'stty echo\r\n' >&3 2>/dev/null || true
    fi
    if [ "$FD_OPEN" = 1 ]; then exec 3>&- 2>/dev/null || true; fi
    FD_OPEN=0
    ECHO_OFF=0
    if [ -n "$TMPD" ] && [ -d "$TMPD" ]; then rm -rf -- "$TMPD"; fi
    return "$rc"
}
on_signal() { cleanup; exit 130; }
trap cleanup EXIT
trap on_signal INT TERM HUP

new_tag() {
    # Alphanumeric only: safe inside the shell line, unique per invocation.
    printf 'z1t%s%s' "$(date +%s)" "$$"
}

log_size() { local n; n=$(wc -c < "$LOG" 2>/dev/null | tr -d ' '); printf '%s' "${n:-0}"; }

send_line() {
    [ "$FD_OPEN" = 1 ] || die "internal: serial fd is not open"
    printf '%s\r\n' "$1" >&3 2>/dev/null || die "write to ${DEV} failed (serial gone?)"
}

# Extract the text between the unique <tag>_BEGIN / <tag>_END output markers
# from stdin.  Marker lines may carry a shell prompt in front of them, so the
# test is "line ends with the marker" rather than an exact match.  The echoed
# command line cannot match: the commands are sent as "T=<tag>;
# echo ${T}_BEGIN", so an echoed line contains the literal "${T}_BEGIN".
extract_stream() {
    awk -v b="${1}_BEGIN" -v e="${1}_END" '
        { sub(/\r$/, "") }
        !inblk {
            if (length($0) >= length(b) && substr($0, length($0) - length(b) + 1) == b) {
                inblk = 1; out = ""; next
            }
        }
        inblk {
            if (length($0) >= length(e) && substr($0, length($0) - length(e) + 1) == e) {
                done = 1; exit
            }
            out = out $0 "\n"
        }
        END { if (done) printf "%s", out; exit(done ? 0 : 1) }
    '
}

# Drop kernel console lines ("[   12.345678] ...") from a command's output.
filter_kernel() {
    awk '
        /^\[[ 0-9]*\.[0-9]+\]/ { next }
        /^\[[ 0-9]+\]/         { next }
        { sub(/\r$/, ""); print }
    '
}

field_of() {  # field_of "<block text>" KEY  -> value after "KEY="
    printf '%s\n' "$1" | sed -n "s/^${2}=//p" | head -n 1
}

hash_of() {  # first 64-hex token in the block
    printf '%s\n' "$1" | awk 'NF >= 1 && length($1) == 64 && $1 ~ /^[0-9a-f]+$/ { print $1; exit }'
}

# wait_end_marker <tag> <timeout_s> <log-offset>
wait_end_marker() {
    local tag=$1 tmo=$2 off=$3 deadline
    deadline=$(( $(date +%s) + tmo ))
    while : ; do
        if grep -a -q -- "${tag}_END" < <(tail -c +$(( off + 1 )) "$LOG" 2>/dev/null); then
            return 0
        fi
        [ "$(date +%s)" -ge "$deadline" ] && return 1
        sleep "$POLL"
    done
}

prepare_device() {
    [ -n "$LOG" ] || die "Z1LOG is not set.
  Z1LOG must be the path of an append-only capture of the board console, e.g.
      stty -F ${DEV} ${BAUD} raw -echo
      cat ${DEV} >> /tmp/z1_serial.log &
      export Z1LOG=/tmp/z1_serial.log"
    [ -c "$DEV" ] || die "serial device not found or not a character device: ${DEV} (set Z1DEV)"
    [ -f "$LOG" ] || die "capture log not found: ${LOG} (Z1LOG)"
    if [ "${Z1NO_STTY:-0}" != 1 ]; then
        stty -F "$DEV" "$BAUD" raw -echo -echoe -echok -echoctl -echoke 2>/dev/null \
            || note "WARN: stty failed on ${DEV} (continuing with its current settings)"
    fi
    exec 3>>"$DEV" || die "cannot open ${DEV} for writing"
    FD_OPEN=1
}

# send_marker_block <tag> <body> <timeout_s> [label]
# Sends:  T=<tag>; echo ${T}_BEGIN
#         <body>            (multi-line allowed)
#         echo ${T}_END
# then waits for the end tag and prints the filtered output.
send_marker_block() {
    local tag=$1 body=$2 tmo=$3 label=${4:-command} off out
    off=$(log_size)
    send_line "T=${tag}; echo \${T}_BEGIN"
    if [ -n "$body" ]; then
        printf '%s' "${body//$'\n'/$'\r\n'}" >&3 2>/dev/null || die "write to ${DEV} failed"
        printf '\r\n' >&3 2>/dev/null || die "write to ${DEV} failed"
    fi
    send_line 'echo ${T}_END'
    if ! wait_end_marker "$tag" "$tmo" "$off"; then
        die "no ${tag}_END after ${tmo}s (${label}): device shell not reachable or the command hung.
  Check: board powered on, console shell prompt 'console:/ \$ ' in ${LOG},
         ${DEV} at ${BAUD} 8N1, and that Z1LOG capture is running."
    fi
    out=$(tail -c +$(( off + 1 )) "$LOG" 2>/dev/null | extract_stream "$tag" | filter_kernel)
    printf '%s\n' "$out"
}

# capture_block <body> <timeout_s> <label>
# Runs send_marker_block in a command substitution.  A fatal error inside the
# substitution only exits the subshell, so the failure is re-raised here.
capture_block() {
    CAPTURED=""
    if ! CAPTURED=$(send_marker_block "$(new_tag)" "$1" "$2" "$3"); then
        exit 1
    fi
    return 0
}

send_lines_file() {  # paced CRLF upload of one line per row
    local n=0 line
    while IFS= read -r line || [ -n "$line" ]; do
        printf '%s\r\n' "$line" >&3 2>/dev/null || die "write to ${DEV} failed during upload"
        n=$((n + 1))
        if [ $((n % CHUNK_LINES)) -eq 0 ]; then sleep "$CHUNK_SLEEP"; fi
    done < "$1"
}

# upload_heredoc <remote-path> <lines-file> <terminator> <timeout_s> [label]
upload_heredoc() {
    local rpath=$1 lines=$2 term=$3 tmo=$4 label=${5:-upload} tag off
    tag=$(new_tag)
    off=$(log_size)
    {
        printf 'T=%s; echo ${T}_BEGIN\r\n' "$tag"
        printf "cat > %s <<'%s'\r\n" "$rpath" "$term"
    } >&3 2>/dev/null || die "write to ${DEV} failed"
    send_lines_file "$lines"
    printf '%s\r\n' "$term" >&3 2>/dev/null || die "write to ${DEV} failed"
    printf 'echo ${T}_END\r\n' >&3 2>/dev/null || die "write to ${DEV} failed"
    if ! wait_end_marker "$tag" "$tmo" "$off"; then
        die "heredoc upload did not finish within ${tmo}s (${label})
  Either the board console stalled or a base64 line was lost.
  Check the tail of ${LOG} for the prompt and for 'Invalid'. "
    fi
}

# ping_device <timeout_s>
ping_device() {
    capture_block 'true' "$1" ping
}

validate_name() {
    case "$1" in
        ''|.*|*[!A-Za-z0-9._-]*) die "invalid remote name '$1' (use [A-Za-z0-9._-], not starting with '.')" ;;
    esac
    [ "${#1}" -le 120 ] || die "remote name too long (>120 chars)"
}

normalise_mode() {
    case "$1" in
        [0-7][0-7][0-7])   printf '0%s' "$1" ;;
        [0-7][0-7][0-7][0-7]) printf '%s' "$1" ;;
        *) die "invalid mode '$1' (expect 644 / 0644 / 755 / 0755)" ;;
    esac
}

# ---------------------------------------------------------------------------
# --cmd
run_cmd() {
    local cmd=$1 tmo=$2
    if [ "$DRYRUN" = 1 ]; then
        printf 'Z1 --cmd plan (dry run, nothing is sent)\n'
        printf '  serial dev   %s @ %s (not opened)\n' "$DEV" "$BAUD"
        printf '  capture log  %s\n' "${LOG:-<unset>}"
        printf '  timeout      %ss\n' "$tmo"
        printf '  lines to send:\n'
        printf '    T=<tag>; echo ${T}_BEGIN\n'
        printf '%s\n' "$cmd" | sed 's/^/    /'
        printf '    echo ${T}_END\n'
        return 0
    fi
    prepare_device
    ping_device 6
    send_line 'stty -echo'; ECHO_OFF=1
    send_marker_block "$(new_tag)" "$cmd" "$tmo" "cmd"
    send_line 'stty echo'; ECHO_OFF=0
}

# ---------------------------------------------------------------------------
# --rootcmd
run_rootcmd() {
    local body=$1 tmo=$2 tag plan script_tmp
    plan="upload cmd.sh.tmp (heredoc), mv it to cmd.sh, wait for ###DONE, cat out.txt"
    if [ "$DRYRUN" = 1 ]; then
        printf 'Z1 --rootcmd plan (dry run, nothing is sent)\n'
        printf '  serial dev   %s @ %s (not opened)\n' "$DEV" "$BAUD"
        printf '  capture log  %s\n' "${LOG:-<unset>}"
        printf '  remote tmp   %s/cmd.sh.tmp\n' "$STAGE"
        printf '  remote final %s/cmd.sh   (root daemon z1_rootd claims it)\n' "$STAGE"
        printf '  wait         %ss for %s/out.txt to contain ###DONE\n' "$tmo" "$STAGE"
        printf '  %s\n' "$plan"
        printf '  script that would run as root:\n'
        printf '%s\n' "$body" | sed 's/^/    /'
        return 0
    fi

    TMPD=${TMPD:-$(mktemp -d "${TMPDIR:-/tmp}/z1push.XXXXXX")}
    script_tmp=$TMPD/rootcmd.sh
    printf '%s\n' "$body" > "$script_tmp" || die "cannot write ${script_tmp}"

    prepare_device
    ping_device 6
    send_line 'stty -echo'; ECHO_OFF=1

    # make sure the stage directory exists before the heredoc
    send_marker_block "$(new_tag)" "mkdir -p ${STAGE} && echo MKDIR_OK" 15 "mkdir" >/dev/null

    local term=Z1ROOT_EOF
    while grep -F -x -q -- "$term" "$script_tmp"; do term="${term}X"; done
    local htimeout=$(( 20 + $(wc -c < "$script_tmp") / 8000 ))
    upload_heredoc "$STAGE/cmd.sh.tmp" "$script_tmp" "$term" "$htimeout" "root script"

    # Install atomically, then wait on-device for the daemon to finish.
    local wait_loops=$(( tmo + 30 ))
    local body2
    body2=$(cat <<EOF
i=0; while [ -f ${STAGE}/cmd.run ] && [ \$i -lt 30 ]; do sleep 1; i=\$((i+1)); done
rm -f ${STAGE}/out.txt
mv -f ${STAGE}/cmd.sh.tmp ${STAGE}/cmd.sh; echo MV_CMD_RC=\$?
i=0
while [ \$i -lt ${wait_loops} ]; do
  if [ -f ${STAGE}/out.txt ] && grep -q '###DONE' ${STAGE}/out.txt; then break; fi
  sleep 1; i=\$((i+1))
done
echo "WAITED=\$i"
cat ${STAGE}/out.txt 2>/dev/null
EOF
)
    capture_block "$body2" $(( wait_loops + 30 )) "rootcmd"
    printf '%s\n' "$CAPTURED"

    send_line 'stty echo'; ECHO_OFF=0

    case "$CAPTURED" in
        *'###DONE'*) return 0 ;;
        *) note "FAIL: out.txt never contained ###DONE (is the z1_rootd service running? check: z1push.sh --cmd 'dmesg | grep z1rootd')"; return 1 ;;
    esac
}

# ---------------------------------------------------------------------------
# file push
run_push() {
    local src=$1 name=$2
    [ -f "$src" ] || die "no such file: ${src}"
    [ -r "$src" ] || die "not readable: ${src}"
    validate_name "$name"
    local mode
    if ! mode=$(normalise_mode "$MODE"); then die "invalid --mode '${MODE}'"; fi

    local size b64size lines_est sha term
    size=$(stat -c %s -- "$src") || die "cannot stat ${src}"
    sha=$(sha256sum -- "$src" 2>/dev/null) || die "sha256sum failed on ${src}"
    sha=${sha%% *}
    b64size=$(( (size + 2) / 3 * 4 ))
    lines_est=$(( (b64size + B64CHARS - 1) / B64CHARS ))

    local rbase="$STAGE/$name"
    local rb64="$rbase.b64" rnew="$rbase.new"

    TMPD=$(mktemp -d "${TMPDIR:-/tmp}/z1push.XXXXXX") || die "cannot create a temp dir"
    local b64file=$TMPD/payload.b64 termfile=$TMPD/payload.lines
    base64 -w "$B64CHARS" -- "$src" > "$b64file" || die "base64 failed on ${src}"
    cp -- "$b64file" "$termfile"
    # A single-line base64 chunk could theoretically equal the heredoc
    # terminator; extend the terminator until no payload line matches.
    term=Z1EOF
    while grep -F -x -q -- "$term" "$termfile"; do term="${term}X"; done

    local wire_s
    wire_s=$(awk -v b="$b64size" 'BEGIN { printf "%.1f", b / 92160.0 }')

    if [ "$DRYRUN" = 1 ]; then
        printf 'Z1 push plan (dry run, nothing is sent)\n'
        printf '  serial dev      %s @ %s (not opened)\n' "$DEV" "$BAUD"
        printf '  capture log     %s\n' "${LOG:-<unset>}"
        printf '  local file      %s\n' "$src"
        printf '  size / sha256   %s bytes / %s\n' "$size" "$sha"
        printf '  remote name     %s\n' "$name"
        printf '  remote .b64     %s\n' "$rb64"
        printf '  remote .new     %s\n' "$rnew"
        printf '  remote final    %s  (mode %s, owner stays shell)\n' "$rbase" "$mode"
        printf '  base64          %s chars = %s lines of <=%s chars\n' "$b64size" "$lines_est" "$B64CHARS"
        printf '  heredoc         cat > %s <<'"'"'%s'"'"' ... %s\n' "$rb64" "$term" "$term"
        printf '  wire estimate   %s bytes payload (~%ss at 921600 8N1)\n' "$b64size" "$wire_s"
        printf '  pacing          %s ms sleep every %s lines (~%s extra s)\n' \
            "$(awk -v s="$CHUNK_SLEEP" 'BEGIN{printf "%d", s*1000}')" "$CHUNK_LINES" \
            "$(awk -v l="$lines_est" -v c="$CHUNK_LINES" -v s="$CHUNK_SLEEP" 'BEGIN{printf "%.1f", (l/c)*s}')"
        printf '  remote dir      %s (created/checked with mkdir -p)\n' "$STAGE"
        printf '  first payload line  %s\n' "$(head -n 1 "$termfile")"
        printf '  last payload line   %s\n' "$(tail -n 1 "$termfile")"
        printf '  steps:\n'
        printf '    1. stty -echo\n'
        printf '    2. T=<tag>; echo ${T}_BEGIN\n'
        printf '    3. cat > %s <<'"'"'%s'"'"'   + %s payload lines + %s\n' "$rb64" "$term" "$lines_est" "$term"
        printf '    4. echo ${T}_END          (proves the heredoc completed)\n'
        printf '    5. base64 -d %s > %s; sha256sum %s\n' "$rb64" "$rnew" "$rnew"
        printf '    6. compare with host sha256 %s\n' "$sha"
        printf '    7. chmod %s %s; mv -f %s %s; sha256sum %s\n' "$mode" "$rnew" "$rnew" "$rbase" "$rbase"
        printf '    8. rm -f %s; stty echo\n' "$rb64"
        printf '  expected result  PASS with %s\n' "$sha"
        printf '  (device was not contacted; re-run without --dry-run when the board is up)\n'
        return 0
    fi

    prepare_device
    ping_device 6
    send_line 'stty -echo'; ECHO_OFF=1
    send_marker_block "$(new_tag)" "mkdir -p ${STAGE} && echo MKDIR_OK" 15 "mkdir" >/dev/null

    local tmo=${Z1PUSH_TIMEOUT:-$(( 20 + b64size / 8000 ))}
    local attempt=1 max=$(( RETRIES + 1 ))
    local ok=0 out

    while [ "$attempt" -le "$max" ]; do
        note "push ${name}: attempt ${attempt}/${max} (${b64size} base64 chars, timeout ${tmo}s)"
        upload_heredoc "$rb64" "$termfile" "$term" "$tmo" "payload ${name}"

        capture_block \
            "base64 -d ${rb64} > ${rnew}; echo DEC_RC=\$?; sha256sum ${rnew} 2>/dev/null; wc -c < ${rnew} 2>/dev/null" \
            60 "decode ${name}"
        out=$CAPTURED
        local dec_rc got_sha got_size
        dec_rc=$(field_of "$out" DEC_RC)
        got_sha=$(hash_of "$out")
        got_size=$(printf '%s\n' "$out" | awk 'NF == 1 && $1 ~ /^[0-9]+$/ { print $1; exit }')

        if [ "${dec_rc:-?}" != 0 ]; then
            note "  decode failed (DEC_RC=${dec_rc:-?}); output was:"
            printf '%s\n' "$out" | sed 's/^/    /' >&2
        elif [ "$got_sha" != "$sha" ]; then
            note "  sha256 mismatch: device=${got_sha:-<none>} host=${sha} (size device=${got_size:-?} host=${size})"
        elif [ "${got_size:-x}" != "$size" ]; then
            note "  size mismatch: device=${got_size:-?} host=${size}"
        else
            note "  decoded copy verified: ${size} bytes, sha256 ${sha}"
            ok=1
            break
        fi
        attempt=$((attempt + 1))
        [ "$attempt" -le "$max" ] && sleep 1
    done

    if [ "$ok" != 1 ]; then
        send_line 'stty echo'; ECHO_OFF=0
        die "FAIL: could not transfer ${src} -> ${rbase} after ${max} attempt(s)"
    fi

    capture_block \
        "chmod ${mode} ${rnew}; echo CHMOD_RC=\$?; mv -f ${rnew} ${rbase}; echo MV_RC=\$?; ls -l ${rbase} 2>/dev/null; sha256sum ${rbase} 2>/dev/null; rm -f ${rb64}; echo CLEAN_RC=\$?" \
        30 "install ${name}"
    out=$CAPTURED

    local mv_rc fin_sha
    mv_rc=$(field_of "$out" MV_RC)
    fin_sha=$(hash_of "$out")

    send_line 'stty echo'; ECHO_OFF=0

    if [ "${mv_rc:-?}" != 0 ] || [ "$fin_sha" != "$sha" ]; then
        printf '%s\n' "$out" | sed 's/^/  /' >&2
        die "FAIL: install step failed (MV_RC=${mv_rc:-?}, sha256=${fin_sha:-<none>}, expected ${sha})"
    fi

    printf 'PASS %s -> %s  %s bytes  mode %s  sha256 %s\n' "$src" "$rbase" "$size" "$mode" "$sha"
    printf '%s\n' "$out" | sed 's/^/  /' >&2
    return 0
}

# ---------------------------------------------------------------------------
# offline extraction (post-mortem on a saved capture)
run_extract() {
    local tag=$1 file=${2:-$LOG} body
    [ -n "$file" ] || die "--extract needs a log file (Z1LOG or second argument)"
    [ -f "$file" ] || die "no such log: ${file}"
    if ! body=$(extract_stream "$tag" < "$file"); then
        die "tag ${tag} not found in ${file} (no line ending with ${tag}_BEGIN / ${tag}_END)"
    fi
    printf '%s' "$body" | filter_kernel
}

# ---------------------------------------------------------------------------
# argument parsing
POSITIONAL=()
ACTION=""
ACTION_ARG=""

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRYRUN=1 ;;
        --mode)    [ $# -ge 2 ] || die "--mode needs a value"; MODE=$2; shift ;;
        --retries) [ $# -ge 2 ] || die "--retries needs a value"; RETRIES=$2; shift ;;
        --wait)    [ $# -ge 2 ] || die "--wait needs a value"; WAIT=$2; shift ;;
        --cmd)     [ $# -ge 2 ] || die "--cmd needs a command string"; ACTION=cmd; ACTION_ARG=$2; shift ;;
        --rootcmd) [ $# -ge 2 ] || die "--rootcmd needs a script string or '-'"; ACTION=rootcmd; ACTION_ARG=$2; shift ;;
        --extract) [ $# -ge 2 ] || die "--extract needs a tag"; ACTION=extract; ACTION_ARG=$2; shift ;;
        -h|--help) usage ;;
        --) shift; while [ $# -gt 0 ]; do POSITIONAL+=("$1"); shift; done; break ;;
        -*) die "unknown option: $1 (try --help)" ;;
        *) POSITIONAL+=("$1") ;;
    esac
    shift
done

case "${RETRIES:-x}" in ''|*[!0-9]*) die "--retries must be a number" ;; esac
case "${WAIT:-x}" in ''|*[!0-9]*) die "--wait must be a number" ;; esac
case "$B64CHARS" in ''|*[!0-9]*) die "Z1B64CHARS must be a number" ;; esac
[ "$B64CHARS" -ge 10 ] && [ "$B64CHARS" -le 1000 ] || die "Z1B64CHARS must be 10..1000 (line buffer safety)"

if [ "$ACTION" = extract ]; then
    run_extract "$ACTION_ARG" "${POSITIONAL[0]:-}"
    exit 0
fi

if [ "$ACTION" = cmd ]; then
    run_cmd "$ACTION_ARG" "$WAIT"
    exit $?
fi

if [ "$ACTION" = rootcmd ]; then
    if [ "$ACTION_ARG" = "-" ]; then
        ACTION_ARG=$(cat) || die "cannot read the root script from stdin"
    fi
    [ -n "$ACTION_ARG" ] || die "--rootcmd got an empty script"
    run_rootcmd "$ACTION_ARG" "$WAIT"
    exit $?
fi

case "${#POSITIONAL[@]}" in
    0) usage ;;
    1) run_push "${POSITIONAL[0]}" "$(basename -- "${POSITIONAL[0]}")" ;;
    2) run_push "${POSITIONAL[0]}" "${POSITIONAL[1]}" ;;
    *) die "too many arguments (try --help)" ;;
esac
