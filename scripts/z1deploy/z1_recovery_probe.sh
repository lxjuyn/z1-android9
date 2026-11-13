#!/system/bin/sh
# Read-only recovery evidence. All output goes to stdout/stderr for host capture.
# No property writes, service control, filesystem repair, remount or reboot.
PATH=/system/bin:/system/xbin:/sbin:/bin
export PATH

FULL=0
[ "$#" -le 1 ] || { printf '%s\n' '[probe] ERROR too many arguments'; exit 2; }
case "${1:-}" in
    '') ;;
    --full-system-hashes) FULL=1 ;;
    --help)
        printf '%s\n' 'Usage: sh z1_recovery_probe.sh [--full-system-hashes]'
        printf '%s\n' 'Read-only Android sample; capture output on the host. Full hashes can be slow.'
        exit 0 ;;
    *) printf '[probe] ERROR unknown option: %s\n' "$1"; exit 2 ;;
esac
if [ ! -x /system/bin/getprop ]; then
    printf '%s\n' '[probe] ERROR Android /system/bin/getprop unavailable; no sample taken'
    exit 2
fi

read_value() {
    if [ ! -e "$1" ]; then
        printf '[probe] file=%s status=MISSING_OR_INACCESSIBLE\n' "$1"
    elif [ ! -r "$1" ]; then
        printf '[probe] file=%s status=PERMISSION_DENIED\n' "$1"
    elif _rv_value=$(cat "$1" 2>/dev/null); then
        printf '[probe] file=%s status=OK value=%s\n' "$1" "$_rv_value"
    else
        printf '[probe] file=%s status=READ_FAILED\n' "$1"
    fi
}

read_prop() {
    if _rp_value=$(getprop "$1" 2>/dev/null); then
        if [ -n "$_rp_value" ]; then
            printf '[probe] property=%s status=OK value=%s\n' "$1" "$_rp_value"
        else
            printf '[probe] property=%s status=UNSET\n' "$1"
        fi
    else
        printf '[probe] property=%s status=READ_FAILED\n' "$1"
    fi
}

hash_file() {
    if [ ! -e "$1" ]; then
        printf '[probe] hash=%s status=MISSING_OR_INACCESSIBLE\n' "$1"
    elif [ ! -f "$1" ]; then
        printf '[probe] hash=%s status=NOT_REGULAR\n' "$1"
    elif [ ! -r "$1" ]; then
        printf '[probe] hash=%s status=PERMISSION_DENIED\n' "$1"
    elif _hf_result=$(sha256sum "$1" 2>/dev/null); then
        printf '[probe] hash=%s status=OK sha256=%s\n' "$1" "${_hf_result%% *}"
    else
        printf '[probe] hash=%s status=READ_FAILED\n' "$1"
    fi
}

read_audio_listing() {
    if [ ! -r "$1" ]; then
        printf '[probe] audio_listing=%s status=MISSING_OR_PERMISSION_DENIED\n' "$1"
    else
        printf '[probe] audio_listing=%s status=BEGIN limit=40\n' "$1"
        awk -v path="$1" 'NR <= 40 { printf "[probe] audio_file=%s line=%s\n", path,$0 }
            NR == 41 { print "[probe] audio_listing=TRUNCATED"; exit }' "$1" ||
            printf '[probe] audio_listing=%s status=READ_FAILED\n' "$1"
        printf '[probe] audio_listing=%s status=END\n' "$1"
    fi
}

read_stage_hook() {
    # Never execute or print hook content. Refuse links and special files.
    if [ -L "$1" ]; then
        printf '[probe] hook=%s status=SYMLINK_SKIPPED\n' "$1"
    elif [ ! -e "$1" ]; then
        printf '[probe] hook=%s status=MISSING_OR_INACCESSIBLE\n' "$1"
    elif [ ! -f "$1" ]; then
        printf '[probe] hook=%s status=NOT_REGULAR\n' "$1"
    else
        printf '[probe] hook=%s status=REGULAR\n' "$1"
        if ! command -v stat >/dev/null 2>&1; then
            printf '[probe] hook_metadata=%s status=TOOL_MISSING stat\n' "$1"
        elif _hook_meta=$(stat -c '%a:%u:%g:%s' "$1" 2>/dev/null); then
            case "$_hook_meta" in
                ''|*[!0-9:]*) printf '[probe] hook_metadata=%s status=INVALID_OUTPUT\n' "$1" ;;
                *) printf '[probe] hook_metadata=%s status=OK mode_uid_gid_bytes=%s\n' "$1" "$_hook_meta" ;;
            esac
        else
            printf '[probe] hook_metadata=%s status=READ_FAILED\n' "$1"
        fi
        if command -v sha256sum >/dev/null 2>&1; then
            hash_file "$1"
        else
            printf '[probe] hash=%s status=TOOL_MISSING sha256sum\n' "$1"
        fi
    fi
}

read_stage_hooks() {
    # Check every ancestor before accessing the fixed stage whitelist.
    # Concurrent replacement is still possible; this is a diagnostic sample.
    for _hook_dir in /data /data/local /data/local/tmp /data/local/tmp/z1stage; do
        if [ -L "$_hook_dir" ]; then
            printf '[probe] hook_directory=%s status=SYMLINK_SKIPPED\n' "$_hook_dir"
            return
        elif [ ! -d "$_hook_dir" ]; then
            printf '[probe] hook_directory=%s status=MISSING_OR_NOT_DIRECTORY\n' "$_hook_dir"
            return
        fi
    done
    for _hook_name in install.sh cmd.sh cmd.run cmd.run.stale .disable_hwcomposer .disable_gralloc; do
        read_stage_hook "/data/local/tmp/z1stage/$_hook_name"
    done
    if [ -L /data/local/tmp/z1stage/active ]; then
        printf '%s\n' '[probe] hook_directory=/data/local/tmp/z1stage/active status=SYMLINK_SKIPPED'
    elif [ ! -d /data/local/tmp/z1stage/active ]; then
        printf '%s\n' '[probe] hook_directory=/data/local/tmp/z1stage/active status=MISSING_OR_NOT_DIRECTORY'
    else
        read_stage_hook /data/local/tmp/z1stage/active/hwcomposer.z1.so
        read_stage_hook /data/local/tmp/z1stage/active/gralloc.z1.so
    fi
}

printf '%s\n' '[probe] BEGIN version=3 read_only=1'
if _date=$(date -u '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null); then
    printf '[probe] utc=%s\n' "$_date"
else
    printf '%s\n' '[probe] utc=UNAVAILABLE'
fi
read_value /proc/uptime
read_value /proc/version
for _prop in sys.boot_completed sys.z1.storage_ready z1stage.status z1stage.fsck \
    z1stage.hwcomposer.sha z1stage.gralloc.sha z1stage.cpufreq z1stage.devfreq \
    init.svc.zygote init.svc.surfaceflinger init.svc.bootanim init.svc.z1_rootd \
    init.svc.vendor.hwcomposer-2-1 init.svc.vendor.gralloc-2-0; do
    read_prop "$_prop"
done

if [ -r /proc/mounts ]; then
    awk '$2 == "/system" || $2 == "/data" || $2 == "/cache" {
        seen[$2]++; printf "[probe] mount=%s status=OK source=%s type=%s options=%s\n", $2,$1,$3,$4
    } END {
        n=split("/system /data /cache", wanted, " ")
        for (i=1; i<=n; i++) if (!seen[wanted[i]])
            printf "[probe] mount=%s status=NOT_MOUNTED\n", wanted[i]
    }' /proc/mounts || printf '%s\n' '[probe] mounts=READ_FAILED'
else
    printf '%s\n' '[probe] mounts=MISSING_OR_PERMISSION_DENIED'
fi

if command -v sha256sum >/dev/null 2>&1; then
    for _file in /system/lib/libutilscallstack.so /system/lib/libc.so \
        /system/bin/app_process /system/bin/app_process32 \
        /hwcomposer.z1.so /gralloc.z1.so \
        /vendor/lib/hw/hwcomposer.z1.so /vendor/lib/hw/gralloc.z1.so \
        /system/lib/hw/hwcomposer.z1.so /system/lib/hw/gralloc.z1.so \
        /system/lib/modules/z1-radio/wlan_gen2.ko \
        /system/lib/modules/mt6572-afe-pcm.ko /system/lib/modules/snd-soc-mt6323.ko; do
        hash_file "$_file"
    done
    for _module in wlan_gen2 mt6572_afe_pcm snd_soc_mt6323 mt6572_mt6323; do
        printf '[probe] module_note=%s meaning=BUILD_ID_NOTE_ONLY\n' "$_module"
        hash_file "/sys/module/$_module/notes/.note.gnu.build-id"
    done
else
    printf '%s\n' '[probe] hashes=TOOL_MISSING sha256sum'
fi
read_stage_hooks

# A file hash identifies an installed file, not the code already loaded.
# Expose only these exact module names and omit kernel address columns.
if [ -r /proc/modules ]; then
    awk 'BEGIN {
        n=split("wlan_gen2 mt6572_afe_pcm snd_soc_mt6323 mt6572_mt6323", names," ")
        for (i=1; i<=n; i++) wanted[names[i]]=1
    }
    $1 in wanted {
        seen[$1]=1
        printf "[probe] loaded_module=%s status=PRESENT size=%s references=%s dependencies=%s state=%s\n", $1,$2,$3,$4,$5
    }
    END {
        for (i=1; i<=n; i++) if (!seen[names[i]])
            printf "[probe] loaded_module=%s status=NOT_LOADED\n", names[i]
    }' /proc/modules || printf '%s\n' '[probe] loaded_modules=READ_FAILED'
else
    printf '%s\n' '[probe] loaded_modules=MISSING_OR_PERMISSION_DENIED'
fi
read_audio_listing /proc/asound/cards
read_audio_listing /proc/asound/pcm

for _node in scaling_driver scaling_available_frequencies scaling_available_governors \
    scaling_cur_freq cpuinfo_cur_freq scaling_min_freq scaling_max_freq scaling_governor; do
    read_value "/sys/devices/system/cpu/cpu0/cpufreq/$_node"
done
for _node in available_frequencies available_governors cur_freq min_freq max_freq governor; do
    read_value "/sys/class/devfreq/13010000.gpu/$_node"
done
if [ -d /sys/class/net/wlan0 ]; then
    printf '%s\n' '[probe] wlan0=PRESENT'
    read_value /sys/class/net/wlan0/operstate
else
    printf '%s\n' '[probe] wlan0=MISSING_OR_INACCESSIBLE'
fi

# Restrict kernel evidence to known fault classes. Do not dump address, SSID,
# credential or unrelated kernel lines; redact hardware addresses defensively.
if ! command -v dmesg >/dev/null 2>&1; then
    printf '%s\n' '[probe] dmesg=TOOL_MISSING'
elif _dmesg=$(dmesg 2>&1); then
    printf '%s\n' '[probe] dmesg=OK filtered_tail_limit=80'
    printf '%s\n' "$_dmesg" | awk '
        {
            low=tolower($0)
            if (low !~ /ext4-fs.*(error|warning)|aborting journal|remounting filesystem read-only|eglswapbuffers.*failed|egl.*(error|failed)|surfaceflinger.*(abort|signal|exited|crash)|dev_addr_check|incorrect netdev->dev_addr|wlan_gen2.*(warning|error|failed)/) next
            if (low ~ /ssid|password|passphrase|credential|secret|(^|[^[:alnum:]_])(psk|key|pmk|ptk|gtk)[[:space:]]*[:=]/) {
                line="[probe] kernel_fault=[redacted wireless details]"
            } else {
                line=$0
                gsub(/[[:xdigit:]][[:xdigit:]]:[[:xdigit:]][[:xdigit:]]:[[:xdigit:]][[:xdigit:]]:[[:xdigit:]][[:xdigit:]]:[[:xdigit:]][[:xdigit:]]:[[:xdigit:]][[:xdigit:]]/, "[MAC redacted]", line)
                line="[probe] kernel_fault=" line
            }
            ring[count % 80]=line; count++
        }
        END {
            if (!count) print "[probe] kernel_fault=NO_MATCHING_LINES"
            first=count>80 ? count-80 : 0
            for (i=first; i<count; i++) print ring[i % 80]
        }'
else
    case "$_dmesg" in
        *'Permission denied'*|*'Operation not permitted'*) printf '%s\n' '[probe] dmesg=PERMISSION_DENIED' ;;
        *) printf '%s\n' '[probe] dmesg=READ_FAILED' ;;
    esac
fi
unset _dmesg

if [ "$FULL" -eq 1 ]; then
    if ! command -v find >/dev/null 2>&1 || ! command -v sha256sum >/dev/null 2>&1; then
        printf '%s\n' '[probe] full_hashes=TOOL_MISSING find_or_sha256sum'
    else
        # Default find behavior does not follow symlinks out of these trees.
        # Only hashes of regular system files are emitted, never their content.
        for _dir in /system/lib /system/bin /system/usr; do
            if [ ! -d "$_dir" ]; then
                printf '[probe] full_hash_dir=%s status=MISSING_OR_INACCESSIBLE\n' "$_dir"
                continue
            fi
            printf '[probe] full_hash_dir=%s status=BEGIN\n' "$_dir"
            find "$_dir" -type f -exec /system/bin/sh -c '
                if [ ! -r "$1" ]; then
                    printf "[probe] full_hash=%s status=PERMISSION_DENIED\n" "$1"
                elif ! sha256sum "$1"; then
                    printf "[probe] full_hash=%s status=READ_FAILED\n" "$1"
                fi
            ' sh '{}' \; || printf '[probe] full_hash_dir=%s status=TRAVERSAL_FAILED\n' "$_dir"
            printf '[probe] full_hash_dir=%s status=END\n' "$_dir"
        done
    fi
else
    printf '%s\n' '[probe] full_hashes=SKIPPED opt_in=--full-system-hashes'
fi
printf '%s\n' '[probe] END'
