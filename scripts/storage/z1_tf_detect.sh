#!/bin/sh
# Passive MMC/SD metadata only; no mount, GPIO, device opens or service queries.
# POSIX shell for Android toybox; no reads without an explicit source.
set -u
usage() {
    cat <<'USAGE'
Usage: sh z1_tf_detect.sh --root SNAPSHOT [--vold-state RELATIVE_FILE]
       sh z1_tf_detect.sh --live

--root requires an offline directory with proc/mounts and sys/class/mmc_host
(including card/block children or in-tree sysfs links). Escaping links are rejected.
--live explicitly selects /; do not run until board access is authorized.
Optional vold state is a previously captured file inside the snapshot containing
init.svc.vold=STATE. No getprop, dumpsys, vdc or process enumeration is invoked.
Output: tab-separated records. Unknown does not mean an absent physical card.
SD type proves enumeration, not the TF socket or its wiring. MMC numbering
is only used to enumerate and correlate mounts, never to infer identity.
Exit 0: report produced (including unknown); exit 2: invalid invocation.
USAGE
}
root=
mode=
vold_file=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --root)
            [ "$#" -ge 2 ] && [ -z "$mode" ] || { usage >&2; exit 2; }
            root=$2; mode=offline; shift 2 ;;
        --live)
            [ -z "$mode" ] || { usage >&2; exit 2; }
            root=/; mode=live; shift ;;
        --vold-state)
            [ "$#" -ge 2 ] && [ -z "$vold_file" ] || { usage >&2; exit 2; }
            vold_file=$2; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
[ -n "$mode" ] || { usage >&2; exit 2; }
root=$(readlink -f "$root" 2>/dev/null) || { echo 'Invalid root' >&2; exit 2; }
[ -d "$root" ] || { echo 'Root is not a directory' >&2; exit 2; }
if [ "$mode" = offline ] && [ "$root" = / ]; then
    echo 'Use --live explicitly for /' >&2; exit 2
fi
case "$vold_file" in
    /*|../*|*/../*|*/..|..) echo 'vold state must stay inside the root' >&2; exit 2 ;;
esac
# Resolve before opening or enumerating; offline links must stay in the fixture.
inside() {
    resolved=$(readlink -f "$1" 2>/dev/null) || return 1
    if [ "$root" = / ]; then
        printf '%s\n' "$resolved"
    else
        case "$resolved" in "$root"|"$root"/*) printf '%s\n' "$resolved" ;; *) return 1 ;; esac
    fi
}
text() (
    path=$(inside "$1") || exit 1
    [ -f "$path" ] || exit 1
    IFS= read -r value < "$path" || :
    printf '%s\n' "${value:-}"
)
attribute() {
    value=$(text "$1" 2>/dev/null) || value=unknown
    case "$value" in ''|*[!A-Za-z0-9_:+.-]*) value=unknown ;; esac
    printf '%s' "$value"
}
mounts=
mount_status=unknown
mounts=$(inside "$root/proc/mounts") || mounts=
if [ -n "$mounts" ] && [ -f "$mounts" ] && [ -r "$mounts" ]; then
    mount_status=available
fi
# Only exact associated node paths or Android public MAJOR,MINOR aliases match.
# Fuse/dm/by-name aliases are deliberately unresolved without linking evidence.
report_mounts() {
    node=$1; device_id=$2; card_id=$3
    if [ "$mount_status" != available ]; then
        printf 'block\tmount_state\tunknown\tnode\t%s\treason\tmounts_unavailable\n' "$node"
        return
    fi
    awk -v node="$node" -v devid="$device_id" -v card="$card_id" '
        function decode(s) {
            gsub(/\\040/, " ", s);
            return s;
        }
        NF >= 6 {
            source=$1; matched=(source=="/dev/" node || source=="/dev/block/" node);
            split(devid, nums, ":");
            if (devid ~ /^[0-9]+:[0-9]+$/ &&
                source=="/dev/block/vold/public:" nums[1] "," nums[2]) matched=1;
            if (!matched) next;
            found=1;
            ro=0; rw=0; n=split($4, options, ",");
            for (i=1;i<=n;i++) { if(options[i]=="ro") ro=1; if(options[i]=="rw") rw=1; }
            access=(ro && !rw) ? "ro" : ((rw && !ro) ? "rw" : "unknown");
            printf "mount\t%s\tcard\t%s\ttarget\t%s\tfstype\t%s\taccess\t%s\tsource\t%s\n",
                   node,card,decode($2),$3,access,source;
        }
        END {
            if (!found) printf "block\tmount_state\tunknown\tnode\t%s\treason\tno_correlated_mount\n", node;
        }' "$mounts"
}
printf 'report\tformat\tz1-tf-v1\nreport\tmode\t%s\n' "$mode"
printf 'summary\tmounts\t%s\n' "$mount_status"
hosts=
hosts=$(inside "$root/sys/class/mmc_host") || hosts=
host_count=0
card_count=0
sd_count=0
mmc1=unknown
if [ -n "$hosts" ] && [ -d "$hosts" ] && [ -r "$hosts" ]; then
    for host_link in "$hosts"/mmc*; do
        host_id=${host_link##*/}
        case "$host_id" in mmc[0-9]*) ;; *) continue ;; esac
        number=${host_id#mmc}
        case "$number" in ''|*[!0-9]*) continue ;; esac
        host=$(inside "$host_link") || continue
        [ -d "$host" ] && [ -r "$host" ] || continue
        host_count=$((host_count + 1))
        [ "$host_id" != mmc1 ] || mmc1=present
        host_cards=0
        printf 'host\t%s\tenumeration\tpresent\n' "$host_id"
        for card_link in "$host"/"$host_id":*; do
            card_id=${card_link##*/}
            address=${card_id#*:}
            case "$address" in ''|*[!A-Fa-f0-9]*) continue ;; esac
            card=$(inside "$card_link") || continue
            [ -d "$card" ] && [ -r "$card" ] || continue
            host_cards=$((host_cards + 1))
            card_count=$((card_count + 1))
            card_type=$(attribute "$card/type")
            case "$card_type" in
                SD) kind=sd_memory; sd_count=$((sd_count + 1)) ;;
                MMC) kind=mmc_memory ;;
                SDIO) kind=sdio_not_sd_memory ;;
                *) kind=unknown ;;
            esac
            printf 'card\t%s\thost\t%s\ttype\t%s\tkind\t%s\n' "$card_id" "$host_id" "$card_type" "$kind"
            blocks=$(inside "$card/block") || blocks=
            block_count=0
            if [ -n "$blocks" ] && [ -d "$blocks" ] && [ -r "$blocks" ]; then
                for block_link in "$blocks"/*; do
                    block_id=${block_link##*/}
                    case "$block_id" in ''|*[!A-Za-z0-9_.-]*) continue ;; esac
                    block=$(inside "$block_link") || continue
                    [ -d "$block" ] || continue
                    block_count=$((block_count + 1))
                    removable=$(attribute "$block/removable")
                    case "$removable" in 0|1) ;; *) removable=unknown ;; esac
                    identity=$kind
                    if [ "$card_type" = MMC ] && [ "$removable" = 0 ]; then identity=emmc_candidate; fi
                    devid=$(attribute "$block/dev")
                    printf 'block\t%s\tcard\t%s\tidentity\t%s\tremovable\t%s\tdev\t%s\n' "$block_id" "$card_id" "$identity" "$removable" "$devid"
                    report_mounts "$block_id" "$devid" "$card_id"
                    for part_link in "$block"/*; do
                        part_id=${part_link##*/}
                        case "$part_id" in ''|*[!A-Za-z0-9_.-]*) continue ;; esac
                        part=$(inside "$part_link") || continue
                        [ -d "$part" ] || continue
                        partition=$(attribute "$part/partition")
                        case "$partition" in ''|*[!0-9]*) continue ;; esac
                        part_dev=$(attribute "$part/dev")
                        printf 'partition\t%s\tparent\t%s\tdev\t%s\n' "$part_id" "$block_id" "$part_dev"
                        report_mounts "$part_id" "$part_dev" "$card_id"
                    done
                done
            fi
            [ "$block_count" -gt 0 ] || printf 'card\t%s\tblock_state\tunknown\n' "$card_id"
        done
        if [ "$host_cards" -eq 0 ]; then printf 'host\t%s\tcard_state\tno_card_enumerated\n' "$host_id"; fi
    done
fi
sd_state=unknown
[ "$sd_count" -eq 0 ] || sd_state=present
printf 'summary\thosts_enumerated\t%s\nsummary\tcards_enumerated\t%s\n' "$host_count" "$card_count"
printf 'summary\tmmc1_host\t%s\nsummary\tsd_card\t%s\n' "$mmc1" "$sd_state"
printf 'summary\ttf_slot_mapping\tunknown\nsummary\tcard_detect\tunknown\nsummary\tpower\tunknown\n'
vold_state=unknown
if [ -n "$vold_file" ]; then
    captured=$(inside "$root/$vold_file") || captured=
    if [ -n "$captured" ] && [ -f "$captured" ] && [ -r "$captured" ]; then
        vold_state=$(awk -F= '
            $1=="init.svc.vold" && $2 ~ /^(running|stopped|restarting|stopping)$/ && NF==2 { state=$2; count++ }
            END { print count==1 ? state : "unknown" }' "$captured")
    fi
fi
printf 'vold\tcaptured_service_state\t%s\n' "$vold_state"
printf 'summary\tphysical_card_absence\tunknown\nsummary\tmutation_performed\tno\n'
