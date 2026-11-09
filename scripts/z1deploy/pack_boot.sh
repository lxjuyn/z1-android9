#!/bin/bash
# ---------------------------------------------------------------------------
# pack_boot.sh -- build a Z1 boot.img that carries the z1deploy channel.
#
# What gets baked in (all as ramdisk root-level records):
#   /z1stage_boot.sh          boot-time stage seeder + active/ HAL applier
#   /z1rootd.sh               root command daemon for the serial console
#   /hwcomposer.z1.so         staged candidate (only if present in --stage-dir)
#   /gralloc.z1.so            staged candidate (only if present in --stage-dir)
#   /z1kmstest                staged candidate (only if present in --stage-dir)
#   /init.z1.rc               init_fragment.rc appended verbatim at EOF
#
# Every untouched cpio record is kept byte-identical (z1_ramdisk_edit.py does
# the record-level edit; this script only ever appends at the end of
# /init.z1.rc and replaces/creates the records listed above).
#
# Verification performed here (any mismatch aborts with a nonzero status):
#   1. /init.z1.rc is extracted from the base ramdisk, the fragment is
#      appended, and the result is re-parsed: every `service` has a `class`,
#      every action command / service option is a known Android 9 keyword, no
#      tabs, LF only, ends with a newline, and no existing byte was modified.
#   2. The produced ramdisk is re-parsed record by record: the staged names
#      exist with the expected size/sha256/mode, the record count grew by
#      exactly the number of added records, all other records are
#      byte-identical, cpio trailer preserved.
#   3. The boot.img is re-parsed: ANDROID! magic, kernel_size == MTK KERNEL
#      size + 512, ramdisk_size == MTK ROOTFS size + 512, kernel payload ==
#      zImage+dtb, embedded ROOTFS == produced rootfs, Android sha1 field,
#      size < --max-size (default 6,000,000) and below the 6 MiB partition.
#   4. MANDATORY reverse check: /init.z1.rc is extracted from the FINAL
#      boot.img, its appended deployment block is printed, and the z1_rootd
#      service + the /z1stage_boot.sh exec are asserted present.
#
# Usage:
#   pack_boot.sh --out out/z1_vNN/boot.img --stage-dir <dir> [options]
#
# Options:
#   --zimage PATH        kernel zImage            (default fork_linux zImage)
#   --dtb PATH           device tree blob         (default mt6572-z1.dtb)
#   --base-rootfs PATH   MTK ROOTFS blob to edit  (default /tmp/rootfs_v79.bin)
#   --template PATH      boot.img to copy headers from (default out/z1_v79)
#   --stage-dir DIR      directory with hwcomposer.z1.so / gralloc.z1.so /
#                        z1kmstest (each optional; missing ones are reported
#                        and simply not baked in)
#   --out PATH           output boot.img (required)
#   --name NAME          Android header name, <=16 chars (default: basename of
#                        the output directory)
#   --cmdline STRING     override the kernel cmdline (default: keep template's)
#   --max-size BYTES     fail if the image is not below this (default 6000000)
#   --report PATH        report file (default <outdir>/pack_boot_report.txt)
#   --keep-work          keep the temporary work directory
#   -h, --help           this text
#
# Examples:
#   # release candidate with the current HWC2 shim staged
#   z1a9_gpt/scripts/z1deploy/pack_boot.sh \
#       --stage-dir out/z1_v83stage --out out/z1_v83/boot.img
#
#   # the exact end-to-end test used on 2026-11-10
#   z1a9_gpt/scripts/z1deploy/pack_boot.sh \
#       --stage-dir out/z1_v83test/stage --out out/z1_v83test/boot.img \
#       --name z1_v83test
#
# The script never flashes anything and never touches the board.
# ---------------------------------------------------------------------------
set -uo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
TOP=$(cd -- "$SCRIPT_DIR/../../.." && pwd)

DEFAULT_ZIMAGE=$TOP/fork_linux/arch/arm/boot/zImage
DEFAULT_DTB=$TOP/fork_linux/arch/arm/boot/dts/mediatek/mt6572-z1.dtb
DEFAULT_ROOTFS=/tmp/rootfs_v79.bin
DEFAULT_TEMPLATE=$TOP/out/z1_v79/boot.img
RAMDISK_EDITOR=$TOP/z1a9_gpt/scripts/z1_ramdisk_edit.py
BOOT_PACKER=$TOP/tools/z1_pack_mtk_bootimg.py

ZIMAGE=$DEFAULT_ZIMAGE
DTB=$DEFAULT_DTB
BASE_ROOTFS=$DEFAULT_ROOTFS
TEMPLATE=$DEFAULT_TEMPLATE
STAGE_DIR=""
OUT=""
NAME=""
CMDLINE=""
MAX_SIZE=6000000
REPORT=""
KEEP_WORK=0
WORK=""

die() { printf 'pack_boot: ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
    awk 'NR > 1 { if ($0 ~ /^set -uo pipefail/) exit; sub(/^# ?/, ""); print }' "$0"
    exit 0
}

cleanup() {
    if [ "$KEEP_WORK" = 1 ] && [ -n "$WORK" ]; then
        printf 'pack_boot: work dir kept at %s\n' "$WORK" >&2
    elif [ -n "$WORK" ] && [ -d "$WORK" ]; then
        rm -rf -- "$WORK"
    fi
}
trap cleanup EXIT

say()  { printf '%s\n' "$*" | tee -a "$REPORT"; }
row()  { printf '  %-56s %s\n' "$1" "$2" | tee -a "$REPORT"; }
run_and_log() {
    "$@" 2>&1 | tee -a "$REPORT"
    return "${PIPESTATUS[0]}"
}

# --- argument parsing ------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --zimage)       [ $# -ge 2 ] || die "--zimage needs a path"; ZIMAGE=$2; shift ;;
        --dtb)          [ $# -ge 2 ] || die "--dtb needs a path"; DTB=$2; shift ;;
        --base-rootfs)  [ $# -ge 2 ] || die "--base-rootfs needs a path"; BASE_ROOTFS=$2; shift ;;
        --template)     [ $# -ge 2 ] || die "--template needs a path"; TEMPLATE=$2; shift ;;
        --stage-dir)    [ $# -ge 2 ] || die "--stage-dir needs a path"; STAGE_DIR=$2; shift ;;
        --out)          [ $# -ge 2 ] || die "--out needs a path"; OUT=$2; shift ;;
        --name)         [ $# -ge 2 ] || die "--name needs a value"; NAME=$2; shift ;;
        --cmdline)      [ $# -ge 2 ] || die "--cmdline needs a value"; CMDLINE=$2; shift ;;
        --max-size)     [ $# -ge 2 ] || die "--max-size needs a value"; MAX_SIZE=$2; shift ;;
        --report)       [ $# -ge 2 ] || die "--report needs a path"; REPORT=$2; shift ;;
        --keep-work)    KEEP_WORK=1 ;;
        -h|--help)      usage ;;
        *)              die "unknown option: $1 (try --help)" ;;
    esac
    shift
done

[ -n "$OUT" ] || die "--out is required"
[ -n "$STAGE_DIR" ] || die "--stage-dir is required (directory with the staged binaries)"
case "$MAX_SIZE" in ''|*[!0-9]*) die "--max-size must be a number" ;; esac

OUT_DIR=$(dirname -- "$OUT")
mkdir -p -- "$OUT_DIR" || die "cannot create output directory $OUT_DIR"
REPORT=${REPORT:-$OUT_DIR/pack_boot_report.txt}
: > "$REPORT" || die "cannot write report $REPORT"

if [ -z "$NAME" ]; then
    NAME=$(basename -- "$OUT_DIR")
fi
case "$NAME" in
    ''|*[!A-Za-z0-9._-]*) die "invalid --name '$NAME' (use [A-Za-z0-9._-])" ;;
esac
[ "${#NAME}" -le 16 ] || die "--name '$NAME' is longer than 16 characters"

for f in "$ZIMAGE" "$DTB" "$BASE_ROOTFS" "$TEMPLATE" "$RAMDISK_EDITOR" "$BOOT_PACKER" "$SCRIPT_DIR/init_fragment.rc"; do
    [ -f "$f" ] || die "required input missing: $f"
done
[ -d "$STAGE_DIR" ] || die "stage dir missing: $STAGE_DIR"

WORK=$(mktemp -d "${TMPDIR:-/tmp}/z1pack.XXXXXX") || die "cannot create a work dir"

# The verifier below is embedded so this script is self-contained.
cat > "$WORK/rdtool.py" <<'Z1RDPY_EOF'
#!/usr/bin/env python3
"""Verify the Z1 deployment ramdisk / boot.img produced by pack_boot.sh.

Subcommands (all paths are host paths):
  names <rootfs>                        record names, one per line
  list <rootfs>                         name|size|mode_octal|sha256_12 table
  mode <rootfs> <record>                full st_mode in octal (e.g. 100750)
  extract <rootfs> <record> <outfile>   dump one record's bytes
  patch-rc <orig-rc> <fragment> <out>   append the fragment, validate the rc
  verify-rootfs <orig> <new> <spec>     record-level diff + untouched check
  image <boot.img> <rootfs> <zimage> <dtb> <spec> <fragment>
        [--max-size N] [--extract-rc-out FILE]

<spec> lines are  name|size|sha256|mode_octal  ("0100644").
Exit status is nonzero on the first mismatch, after printing why.
"""
import sys, os, gzip, struct, hashlib, difflib

ALIGN = 4
MTK_MAGIC = bytes.fromhex('88168858')
PAGE = 2048
PART_LIMIT = 6291456          # 6 MiB boot partition


def die(msg):
    print("ERROR: " + msg, file=sys.stderr)
    sys.exit(1)


def say(msg=""):
    print(msg)


def ok(msg):
    print("  OK   " + msg)


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def align_up(v, a=ALIGN):
    return (v + a - 1) & ~(a - 1)


def parse_cpio(body):
    recs = []
    pos = 0
    while pos < len(body) and body[pos:pos + 6] == b'070701':
        if pos + 110 > len(body):
            die("corrupt cpio archive: truncated header at offset %d" % pos)
        fields = [int(body[pos + 6 + i * 8:pos + 14 + i * 8], 16) for i in range(13)]
        namesize, filesize = fields[11], fields[6]
        if namesize <= 1 or pos + 110 + namesize > len(body):
            die("corrupt cpio archive: bad name size at offset %d" % pos)
        name = body[pos + 110:pos + 110 + namesize - 1].decode('utf-8', 'replace')
        hdr_end = align_up(pos + 110 + namesize)
        file_end = align_up(hdr_end + filesize)
        if file_end > len(body):
            die("corrupt cpio archive: record %r runs past the end" % name)
        recs.append(dict(name=name, size=filesize, mode=fields[1], uid=fields[2],
                         gid=fields[3], nlink=fields[4], ino=fields[0],
                         pos=pos, hdr_end=hdr_end, file_end=file_end,
                         hdr=body[pos:hdr_end], data=body[hdr_end:hdr_end + filesize]))
        pos = file_end
        if name == 'TRAILER!!!':
            break
    return recs, pos


def load_rootfs(path, what='rootfs'):
    if not os.path.isfile(path):
        die("%s not found: %s" % (what, path))
    blob = open(path, 'rb').read()
    if len(blob) < 513 or blob[:4] != MTK_MAGIC or blob[8:14] != b'ROOTFS':
        die("%s is not an MTK ROOTFS blob (512-byte header + gzip): %s" % (what, path))
    size_field = struct.unpack_from('<I', blob, 4)[0]
    if size_field != len(blob) - 512:
        die("%s MTK ROOTFS size field %d != gzip length %d" % (what, size_field, len(blob) - 512))
    try:
        body = gzip.decompress(blob[512:])
    except Exception as exc:
        die("%s gzip payload does not decompress: %s" % (what, exc))
    recs, end = parse_cpio(body)
    if not recs or recs[-1]['name'] != 'TRAILER!!!':
        die("%s cpio archive has no TRAILER record" % what)
    return dict(path=path, blob=blob, gz=blob[512:], body=body, recs=recs,
                end=end, tail=body[end:])


def by_name(recs, name):
    for r in recs:
        if r['name'] == name:
            return r
    return None


def read_spec(path):
    spec = []
    for line in open(path, 'r', encoding='utf-8'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split('|')
        if len(parts) != 4:
            die("bad spec line %r (want name|size|sha256|mode)" % line)
        name, size, digest, mode = parts
        spec.append(dict(name=name, size=int(size), sha=digest.lower(),
                         mode=int(mode, 8)))
    if not spec:
        die("empty spec: %s" % path)
    return spec


def check_spec(recs, spec, what):
    """Assert every spec entry is present with the expected size/sha/mode."""
    bad = 0
    for e in spec:
        r = by_name(recs, e['name'])
        if r is None:
            print("  FAIL %-24s missing" % e['name'])
            bad += 1
            continue
        problems = []
        if r['size'] != e['size']:
            problems.append("size %d != %d" % (r['size'], e['size']))
        got = sha256(r['data'])
        if got != e['sha']:
            problems.append("sha256 %s != %s" % (got[:12], e['sha'][:12]))
        if r['mode'] != e['mode']:
            problems.append("mode 0%o != 0%o" % (r['mode'], e['mode']))
        if problems:
            print("  FAIL %-24s %s" % (e['name'], "; ".join(problems)))
            bad += 1
        else:
            print("  OK   %-24s %8d B  mode 0%o  sha256 %s"
                  % (e['name'], r['size'], r['mode'], got[:12]))
    if bad:
        die("%s: %d record(s) failed verification" % (what, bad))
    return 0


# ---------------------------------------------------------------------------
# rc parsing / validation (Android 9 init syntax)

# Action commands of AOSP Pie init (system/core/init/builtins.cpp).  Presence of
# most of these was cross-checked against the strings of this board's /init.
BUILTINS = set("""
bootchart chmod chown class_reset class_start class_stop copy cp domainname
enable exec exec_background exec_start export hostname ifup init_user0 insmod
installkey load_persist_props load_system_props loadall_props loglevel mkdir
mount mount_all mount_with_redirection parse_trigger powerctl readahead
restorecon restorecon_recursive rm rmdir setprop setrlimit start stop swapon_all
symlink sysclktz trigger umount umount_all verity_load_state verity_update_state
wait wait_for_prop write enter_default_mount_ns
""".split())

SERVICE_OPTIONS = set("""
capabilities class console critical disabled enter_namespace file group interface
ioprio keycodes oneshot onrestart override priority reboot_on_failure rlimit
seclabel setenv shutdown socket stdio_to_kmsg timeout_period updatable user
writepid
""".split())


def parse_rc(text, label):
    """Return (actions, services) with structural checks; dies on syntax errors."""
    actions = []      # list of dict(trigger, line, commands=[(lineno, cmd)])
    services = []     # list of dict(name, line, options={opt: [(lineno, args)]})
    trigger_names = set()
    cur = None
    cur_kind = None
    for lineno, raw in enumerate(text.split('\n'), 1):
        line = raw
        if '\t' in line:
            die("%s:%d: TAB character (init rc files use spaces)" % (label, lineno))
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if not line[0].isspace():
            toks = stripped.split()
            head = toks[0]
            if head == 'on':
                if len(toks) < 2:
                    die("%s:%d: 'on' without a trigger" % (label, lineno))
                cur = dict(trigger=' '.join(toks[1:]), line=lineno, commands=[])
                actions.append(cur)
                cur_kind = 'action'
                trigger_names.add(toks[1])
            elif head == 'service':
                if len(toks) < 3:
                    die("%s:%d: 'service' needs a name and a path" % (label, lineno))
                cur = dict(name=toks[1], line=lineno, args=toks[2:], options={})
                services.append(cur)
                cur_kind = 'service'
            elif head == 'import':
                if len(toks) != 2:
                    die("%s:%d: 'import' takes exactly one path" % (label, lineno))
                cur = None
                cur_kind = None
            else:
                die("%s:%d: unexpected top-level keyword %r" % (label, lineno, head))
            continue
        # indented line -> belongs to the current section
        if cur_kind is None:
            die("%s:%d: indented line outside any on/service block: %r"
                % (label, lineno, stripped))
        toks = stripped.split()
        if cur_kind == 'action':
            if toks[0] not in BUILTINS:
                die("%s:%d: unknown action command %r" % (label, lineno, toks[0]))
            cur['commands'].append((lineno, toks[0]))
        else:
            if toks[0] not in SERVICE_OPTIONS:
                die("%s:%d: unknown service option %r" % (label, lineno, toks[0]))
            cur['options'].setdefault(toks[0], []).append((lineno, toks[1:]))
    return actions, services


def validate_rc(text, label, fragment=None):
    if not text.endswith('\n'):
        die("%s: file does not end with a newline" % label)
    if '\r' in text:
        die("%s: CR byte found (must be LF only)" % label)
    actions, services = parse_rc(text, label)
    for svc in services:
        if 'class' not in svc['options']:
            die("%s:%d: service %r has no 'class' option" % (label, svc['line'], svc['name']))
        if svc['line'] <= 0:
            die("%s: service %r has a bad line number" % (label, svc['name']))
    names = {}
    for svc in services:
        names[svc['name']] = names.get(svc['name'], 0) + 1
    dups = [n for n, c in names.items() if c > 1]
    if dups:
        die("%s: duplicate service definition(s): %s" % (label, ', '.join(sorted(dups))))
    if fragment is not None:
        if fragment not in text:
            die("%s: the init_fragment text is not present verbatim" % label)
    cmd_count = sum(len(a['commands']) for a in actions)
    print("  rc syntax: %d on-blocks, %d action commands, %d services, all services have a class"
          % (len(actions), cmd_count, len(services)))
    print("  action commands used: %s"
          % ', '.join(sorted({c for a in actions for _, c in a['commands']})))
    return actions, services


def find_block_lines(text, needle):
    """Line numbers (1-based) of lines containing needle."""
    return [i for i, line in enumerate(text.split('\n'), 1) if needle in line]


# ---------------------------------------------------------------------------
def cmd_names(argv):
    rd = load_rootfs(argv[0])
    for r in rd['recs']:
        print(r['name'])


def cmd_list(argv):
    rd = load_rootfs(argv[0])
    print("  %-32s %10s  %-8s %s" % ("record", "size", "mode", "sha256"))
    for r in rd['recs']:
        print("  %-32s %10d  %-8s %s"
              % (r['name'], r['size'], "0%o" % r['mode'], sha256(r['data'])[:12]))
    print("  records=%d body=%d trailing=%d"
          % (len(rd['recs']), len(rd['body']), len(rd['tail'])))


def cmd_mode(argv):
    rd = load_rootfs(argv[0])
    r = by_name(rd['recs'], argv[1])
    if r is None:
        die("record %r not found" % argv[1])
    print("%o" % r['mode'])


def cmd_extract(argv):
    rd = load_rootfs(argv[0])
    r = by_name(rd['recs'], argv[1])
    if r is None:
        die("record %r not found" % argv[1])
    with open(argv[2], 'wb') as fh:
        fh.write(r['data'])
    print("  extracted %s: %d bytes -> %s" % (argv[1], r['size'], argv[2]))


def cmd_patch_rc(argv):
    orig_path, frag_path, out_path = argv[0], argv[1], argv[2]
    orig = open(orig_path, 'rb').read()
    frag = open(frag_path, 'rb').read()
    if not frag.endswith(b'\n'):
        frag += b'\n'
    if not orig.endswith(b'\n'):
        orig += b'\n'
    patched = orig + b'\n' + frag

    orig_text = orig.decode('utf-8', 'replace')
    frag_text = frag.decode('utf-8', 'replace')
    new_text = patched.decode('utf-8', 'replace')

    say("== init.z1.rc patch ==")
    print("  original: %d bytes, %d lines" % (len(orig), orig_text.count('\n')))
    print("  fragment: %d bytes, appended at EOF (a separate 'on post-fs-data' block)"
          % len(frag))
    validate_rc(orig_text, "orig init.z1.rc")
    actions, services = validate_rc(new_text, "patched init.z1.rc", fragment=frag_text)

    # The append must not have changed a single existing byte.
    if not new_text.startswith(orig_text):
        die("patched rc does not start with the original bytes")
    if new_text[:len(orig_text)] != orig_text:
        die("internal: append changed the original content")

    # Required wiring of the deployment channel.
    for needle in ("exec u:r:init:s0 root root -- /system/bin/sh /z1stage_boot.sh",
                   "start z1_rootd",
                   "service z1_rootd /system/bin/sh /z1rootd.sh"):
        lines = find_block_lines(new_text, needle)
        if not lines:
            die("patched rc lacks the required line: %s" % needle)
        print("  found line %d: %s" % (lines[0], needle.strip()))
    rootd = [s for s in services if s['name'] == 'z1_rootd']
    if len(rootd) != 1:
        die("expected exactly one 'service z1_rootd', found %d" % len(rootd))
    if 'class' not in rootd[0]['options']:
        die("service z1_rootd has no class")

    with open(out_path, 'wb') as fh:
        fh.write(patched)
    print("  wrote patched rc: %s (%d bytes)" % (out_path, len(patched)))

    diff = difflib.unified_diff(orig_text.splitlines(keepends=True),
                                new_text.splitlines(keepends=True),
                                fromfile="init.z1.rc (original)",
                                tofile="init.z1.rc (patched)",
                                n=2)
    say("")
    say("== diff against the original ==")
    for line in diff:
        sys.stdout.write(line if line.endswith('\n') else line + '\n')
    say("")


def cmd_verify_rootfs(argv):
    orig_path, new_path, spec_path = argv[0], argv[1], argv[2]
    orig = load_rootfs(orig_path, "base rootfs")
    new = load_rootfs(new_path, "produced rootfs")
    spec = read_spec(spec_path)
    targets = {e['name'] for e in spec}
    orig_names = {r['name'] for r in orig['recs']}
    added = [e for e in spec if e['name'] not in orig_names]
    replaced = [e for e in spec if e['name'] in orig_names]

    say("== produced ramdisk records ==")
    for i, r in enumerate(new['recs']):
        print("  %2d %-32s %9d B  mode 0%-7o sha256 %s"
              % (i, r['name'], r['size'], r['mode'], sha256(r['data'])[:12]))
    print("  records=%d body=%d trailing=%d bytes" % (len(new['recs']), len(new['body']), len(new['tail'])))

    say("")
    say("== expected staged records ==")
    check_spec(new['recs'], spec, "produced rootfs")

    # Step-4 requirement: the rc record in the produced rootfs must already
    # carry the deployment wiring (the final boot.img is re-checked later).
    rc = by_name(new['recs'], 'init.z1.rc')
    if rc is not None:
        rc_text = rc['data'].decode('utf-8', 'replace')
        for needle in ('exec u:r:init:s0 root root -- /system/bin/sh /z1stage_boot.sh',
                       'start z1_rootd',
                       'service z1_rootd /system/bin/sh /z1rootd.sh'):
            hits = find_block_lines(rc_text, needle)
            if not hits:
                die("produced ramdisk init.z1.rc lacks: %s" % needle)
            print("  OK   init.z1.rc line %d: %s" % (hits[0], needle))

    say("")
    say("== untouched-record integrity ==")
    o_wo = [r for r in orig['recs'] if r['name'] != 'TRAILER!!!']
    n_wo = [r for r in new['recs'] if r['name'] != 'TRAILER!!!']
    if len(n_wo) != len(o_wo) + len(added):
        die("record count %d != %d original + %d added" % (len(n_wo), len(o_wo), len(added)))
    for o, n in zip(o_wo, n_wo[:len(o_wo)]):
        if o['name'] != n['name']:
            die("record order changed: %r vs %r" % (o['name'], n['name']))
        if o['name'] in targets:
            if o['mode'] != n['mode']:
                die("mode of replaced record %r changed: 0%o -> 0%o"
                    % (o['name'], o['mode'], n['mode']))
            continue
        if o['hdr'] != n['hdr'] or o['data'] != n['data']:
            die("untouched record %r is not byte-identical" % o['name'])
    print("  OK   %d replaced, %d added, %d untouched records byte-identical"
          % (len(replaced), len(added), len(o_wo) - len(replaced)))
    rest = n_wo[len(o_wo):]
    if sorted(r['name'] for r in rest) != sorted(e['name'] for e in added):
        die("added record set mismatch: %s" % [r['name'] for r in rest])
    print("  OK   new records inserted before TRAILER: %s"
          % ', '.join(r['name'] for r in rest))
    if orig['tail'] != new['tail']:
        die("cpio trailer tail bytes changed")
    print("  OK   cpio trailer preserved (%d trailing bytes)" % len(new['tail']))
    print("  OK   gzip blob: %d bytes (MTK ROOTFS header size field matches)" % len(new['gz']))
    print("RESULT|rootfs=%s|records=%d|added=%d|replaced=%d|untouched=%d|gz=%d|blob=%d"
          % (new_path, len(new['recs']), len(added), len(replaced),
             len(o_wo) - len(replaced), len(new['gz']), len(new['blob'])))


def check_image_header(img):
    if len(img) < 2048 or img[:8] != b'ANDROID!':
        die("boot.img does not start with the ANDROID! magic")
    kernel_size = struct.unpack_from('<I', img, 8)[0]
    ramdisk_size = struct.unpack_from('<I', img, 16)[0]
    page = struct.unpack_from('<I', img, 36)[0]
    name = img[48:64].split(b'\x00')[0].decode('utf-8', 'replace')
    cmdline = img[64:576].split(b'\x00')[0].decode('utf-8', 'replace')
    if page != PAGE:
        die("boot.img page size is %d, expected %d" % (page, PAGE))
    return dict(kernel_size=kernel_size, ramdisk_size=ramdisk_size, page=page,
                name=name, cmdline=cmdline)


def cmd_image(argv):
    img_path, rootfs_path, zimg_path, dtb_path, spec_path, frag_path = argv[:6]
    max_size = 6000000
    extract_rc_out = None
    rest = argv[6:]
    while rest:
        if rest[0] == '--max-size' and len(rest) >= 2:
            max_size = int(rest[1]); rest = rest[2:]
        elif rest[0] == '--extract-rc-out' and len(rest) >= 2:
            extract_rc_out = rest[1]; rest = rest[2:]
        else:
            die("unknown image option %r" % rest[0])

    img = open(img_path, 'rb').read()
    rootfs = open(rootfs_path, 'rb').read()
    zimage = open(zimg_path, 'rb').read()
    dtb = open(dtb_path, 'rb').read()
    frag = open(frag_path, 'rb').read().decode('utf-8', 'replace')
    if not frag.endswith('\n'):
        frag += '\n'
    spec = read_spec(spec_path)

    say("== boot.img header ==")
    hdr = check_image_header(img)
    print("  image     %d bytes  name=%r  page=%d" % (len(img), hdr['name'], hdr['page']))
    print("  cmdline   %s" % hdr['cmdline'])

    say("")
    say("== MTK headers ==")
    kh = hdr['page']
    if img[kh:kh + 4] != MTK_MAGIC or img[kh + 8:kh + 14] != b'KERNEL':
        die("MTK KERNEL header magic/name missing at offset %d" % kh)
    mtk_ksize = struct.unpack_from('<I', img, kh + 4)[0]
    if hdr['kernel_size'] != mtk_ksize + 512:
        die("Android kernel_size %d != MTK KERNEL size %d + 512"
            % (hdr['kernel_size'], mtk_ksize))
    print("  KERNEL    MTK size %d, Android kernel_size %d = MTK + 512  OK"
          % (mtk_ksize, hdr['kernel_size']))

    kernel_payload = img[kh + 512:kh + 512 + mtk_ksize]
    expected_kernel = zimage + dtb
    if kernel_payload != expected_kernel:
        die("kernel payload (%d B, sha256 %s) != zImage+dtb (%d B, sha256 %s)"
            % (len(kernel_payload), sha256(kernel_payload)[:12],
               len(expected_kernel), sha256(expected_kernel)[:12]))
    print("  kernel    zImage %d + dtb %d = %d B, sha256 %s  OK"
          % (len(zimage), len(dtb), len(expected_kernel), sha256(expected_kernel)))

    ro = align_up(kh + 512 + mtk_ksize, PAGE)
    if img[ro:ro + 4] != MTK_MAGIC or img[ro + 8:ro + 14] != b'ROOTFS':
        die("MTK ROOTFS header magic/name missing at offset %d" % ro)
    mtk_rsize = struct.unpack_from('<I', img, ro + 4)[0]
    if hdr['ramdisk_size'] != mtk_rsize + 512:
        die("Android ramdisk_size %d != MTK ROOTFS size %d + 512"
            % (hdr['ramdisk_size'], mtk_rsize))
    print("  ROOTFS    MTK size %d, Android ramdisk_size %d = MTK + 512  OK"
          % (mtk_rsize, hdr['ramdisk_size']))

    embedded_rootfs = img[ro:ro + 512 + mtk_rsize]
    if embedded_rootfs != rootfs:
        die("embedded ROOTFS blob (sha256 %s) != %s (sha256 %s)"
            % (sha256(embedded_rootfs)[:12], rootfs_path, sha256(rootfs)[:12]))
    print("  rootfs    embedded blob sha256 %s  OK" % sha256(embedded_rootfs))

    # z1_pack_mtk_bootimg.py stores sha1(kernel+rootfs)[:8] at offset 576.
    # This is NOT the legacy mkbootimg id rule (which hashes kernel||ksize||
    # ramdisk||rsize||...); tools/validate_mtk_boot.py therefore reports an id
    # mismatch for every image this project has ever produced, including the
    # known-good v79 baseline.  LK does not consume the field, so the check
    # below only asserts that the header was not corrupted.
    android_sha1 = hashlib.sha1(expected_kernel + embedded_rootfs).digest()[:8]
    if img[576:584] != android_sha1:
        die("Android header id field %s != sha1(kernel+rootfs)[:8] %s"
            % (img[576:584].hex(), android_sha1.hex()))
    print("  id field  hdr[576:584]=%s == sha1(kernel+rootfs)[:8]  OK"
          % android_sha1.hex())
    print("            (z1_pack_mtk_bootimg.py scheme; not the legacy mkbootimg"
          " rule, which LK ignores)")

    if len(img) >= max_size:
        die("boot.img is %d bytes, not below the %d-byte limit" % (len(img), max_size))
    print("  size      %d bytes < %d limit (partition %d)  OK"
          % (len(img), max_size, PART_LIMIT))
    if len(img) > PART_LIMIT:
        die("boot.img %d bytes exceeds the 6 MiB partition (%d)" % (len(img), PART_LIMIT))

    # --- reverse check: re-parse the rootfs that is actually inside boot.img ---
    say("")
    say("== reverse check: records inside the FINAL boot.img ==")
    rd = dict(blob=embedded_rootfs, gz=embedded_rootfs[512:],
              body=gzip.decompress(embedded_rootfs[512:]))
    rd['recs'], rd['end'] = parse_cpio(rd['body'])
    for i, r in enumerate(rd['recs']):
        print("  %2d %-32s %9d B  mode 0%-7o sha256 %s"
              % (i, r['name'], r['size'], r['mode'], sha256(r['data'])[:12]))
    check_spec(rd['recs'], spec, "boot.img ramdisk")

    rc = by_name(rd['recs'], 'init.z1.rc')
    if rc is None:
        die("init.z1.rc is missing from the boot.img ramdisk")
    rc_text = rc['data'].decode('utf-8', 'replace')
    say("")
    say("== reverse check: init.z1.rc extracted from the FINAL boot.img ==")
    print("  %d bytes, sha256 %s" % (rc['size'], sha256(rc['data'])))
    if not rc_text.endswith(frag):
        die("the appended deployment block is NOT at the end of the embedded init.z1.rc")
    actions, services = validate_rc(rc_text, "embedded init.z1.rc", fragment=frag)
    start_line = rc_text[:len(rc_text) - len(frag)].count('\n') + 1
    print("  deployment block = lines %d..%d of the embedded rc:"
          % (start_line, rc_text.count('\n')))
    for i, line in enumerate(frag.split('\n')[:-1], start_line):
        print("    %3d  %s" % (i, line))
    rootd = [s for s in services if s['name'] == 'z1_rootd']
    if len(rootd) != 1:
        die("embedded rc must define 'service z1_rootd' exactly once")
    opts = rootd[0]['options']
    for need in ('class', 'user', 'group', 'seclabel'):
        if need not in opts:
            die("z1_rootd service in the embedded rc lacks the %r option" % need)
    execs = [i for i, line in enumerate(rc_text.split('\n'), 1)
             if 'sh /z1stage_boot.sh' in line and line.strip().startswith('exec ')]
    if len(execs) != 1:
        die("expected exactly one exec of /z1stage_boot.sh, found %d" % len(execs))
    print("  exec of /z1stage_boot.sh at line %d" % execs[0])
    print("  service z1_rootd options: %s"
          % ', '.join("%s %s" % (k, ' '.join(v[0][1])) for k, v in
                      sorted(opts.items())))
    if extract_rc_out:
        with open(extract_rc_out, 'wb') as fh:
            fh.write(rc['data'])
        print("  dumped embedded rc -> %s" % extract_rc_out)

    say("")
    say("== result ==")
    print("  boot.img   %s" % os.path.abspath(img_path))
    print("  size       %d bytes (limit %d, partition %d, headroom %d)"
          % (len(img), max_size, PART_LIMIT, PART_LIMIT - len(img)))
    print("  sha256     %s" % sha256(img))
    print("  ALL CHECKS PASSED")
    print("RESULT|image=%s|size=%d|sha256=%s|kernel_size=%d|mtk_kernel=%d"
          "|ramdisk_size=%d|mtk_rootfs=%d|records=%d|name=%s"
          % (os.path.abspath(img_path), len(img), sha256(img), hdr['kernel_size'],
             mtk_ksize, hdr['ramdisk_size'], mtk_rsize, len(rd['recs']), hdr['name']))


def main():
    if len(sys.argv) < 2:
        die("usage: rdtool.py <names|list|extract|patch-rc|verify-rootfs|image> ...")
    cmd = sys.argv[1]
    argv = sys.argv[2:]
    funcs = dict(names=cmd_names, list=cmd_list, mode=cmd_mode, extract=cmd_extract,
                 **{'patch-rc': cmd_patch_rc, 'verify-rootfs': cmd_verify_rootfs,
                    'image': cmd_image})
    if cmd not in funcs:
        die("unknown subcommand %r" % cmd)
    funcs[cmd](argv)


if __name__ == '__main__':
    main()
Z1RDPY_EOF

python3 -c 'import py_compile,sys; py_compile.compile(sys.argv[1], doraise=True)' "$WORK/rdtool.py" \
    || die "the embedded verifier has a Python syntax error"

fsize() { stat -c %s -- "$1"; }
fhash() { local h; h=$(sha256sum -- "$1" 2>/dev/null) || return 1; printf '%s' "${h%% *}"; }
field() { printf '%s\n' "$1" | tr '|' '\n' | sed -n "s/^$2=//p" | head -n 1; }

say "== pack_boot: ${NAME} =="
say ""
say "  template     ${TEMPLATE}"
say "               $(fsize "$TEMPLATE") B, sha256 $(fhash "$TEMPLATE")"
say "  base rootfs  ${BASE_ROOTFS}"
say "               $(fsize "$BASE_ROOTFS") B, sha256 $(fhash "$BASE_ROOTFS")"
say "  zImage       ${ZIMAGE}"
say "               $(fsize "$ZIMAGE") B, sha256 $(fhash "$ZIMAGE")"
say "  dtb          ${DTB}"
say "               $(fsize "$DTB") B, sha256 $(fhash "$DTB")"
say "  init fragment ${SCRIPT_DIR}/init_fragment.rc ($(fsize "$SCRIPT_DIR/init_fragment.rc") B)"
say "  stage dir    ${STAGE_DIR}"
say "  output       ${OUT}"
say "  report       ${REPORT}"
say ""

# ---------------------------------------------------------------------------
say "== 1. /init.z1.rc: extract, append the fragment, re-parse =="
python3 "$WORK/rdtool.py" extract "$BASE_ROOTFS" init.z1.rc "$WORK/init.z1.rc.orig" \
    || die "cannot extract /init.z1.rc from ${BASE_ROOTFS}"
if grep -q 'service z1_rootd' "$WORK/init.z1.rc.orig"; then
    die "${BASE_ROOTFS} already contains 'service z1_rootd' -- use a clean base rootfs"
fi
run_and_log python3 "$WORK/rdtool.py" patch-rc \
    "$WORK/init.z1.rc.orig" "$SCRIPT_DIR/init_fragment.rc" "$WORK/init.z1.rc.patched" \
    || die "init.z1.rc patch/validation failed"

# ---------------------------------------------------------------------------
say ""
say "== 2. record set to bake in =="
RC_MODE=$(python3 "$WORK/rdtool.py" mode "$BASE_ROOTFS" init.z1.rc) || die "cannot read init.z1.rc mode"
[ -n "$RC_MODE" ] || die "empty mode for init.z1.rc"

ENTRIES=(
    "init.z1.rc|$WORK/init.z1.rc.patched|$RC_MODE"
    "z1stage_boot.sh|$SCRIPT_DIR/z1stage_boot.sh|100755"
    "z1rootd.sh|$SCRIPT_DIR/z1rootd.sh|100755"
)
for n in hwcomposer.z1.so gralloc.z1.so z1kmstest; do
    if [ -f "$STAGE_DIR/$n" ]; then
        case "$n" in
            *.so) m=100644 ;;
            *)    m=100755 ;;
        esac
        ENTRIES+=("$n|$STAGE_DIR/$n|$m")
    else
        say "  skip         $n (not in ${STAGE_DIR}; any base record is kept unchanged)"
    fi
done

python3 "$WORK/rdtool.py" names "$BASE_ROOTFS" > "$WORK/base_names.txt" \
    || die "cannot list the base ramdisk records"

declare -a EDIT=()
declare -a SPEC=()
for e in "${ENTRIES[@]}"; do
    ename=${e%%|*}
    erest=${e#*|}
    epath=${erest%%|*}
    emode=${erest##*|}
    [ -f "$epath" ] || die "record ${ename}: file missing ${epath}"
    esize=$(fsize "$epath") || die "record ${ename}: cannot stat ${epath}"
    esha=$(fhash "$epath") || die "record ${ename}: sha256sum failed"
    if grep -Fxq -- "$ename" "$WORK/base_names.txt"; then
        EDIT+=(--replace "$ename=$epath")
        eaction=replace
    else
        EDIT+=(--add "$ename=$epath")
        eaction=add
    fi
    EDIT+=(--mode "$ename=$emode")
    SPEC+=("$ename|$esize|$esha|$emode")
    say "  $(printf '%-7s' "$eaction") $ename  ${esize} B  mode 0${emode}  sha256 ${esha}"
done
printf '%s\n' "${SPEC[@]}" > "$WORK/records.spec" || die "cannot write ${WORK}/records.spec"

# ---------------------------------------------------------------------------
say ""
say "== 3. rebuilding the ramdisk (record-level edit) =="
run_and_log python3 "$RAMDISK_EDITOR" "$BASE_ROOTFS" "$WORK/rootfs.new.bin" "${EDIT[@]}" \
    || die "z1_ramdisk_edit.py failed"

say ""
say "== 4. verifying the produced ramdisk =="
run_and_log python3 "$WORK/rdtool.py" verify-rootfs \
    "$BASE_ROOTFS" "$WORK/rootfs.new.bin" "$WORK/records.spec" \
    || die "produced rootfs verification failed"

# ---------------------------------------------------------------------------
say ""
say "== 5. packing boot.img =="
PACK=(--template "$TEMPLATE" --zimage "$ZIMAGE" --dtb "$DTB"
      --rootfs "$WORK/rootfs.new.bin" --name "$NAME" -o "$OUT")
if [ -n "$CMDLINE" ]; then
    PACK+=(--cmdline "$CMDLINE")
fi
run_and_log python3 "$BOOT_PACKER" "${PACK[@]}" || die "z1_pack_mtk_bootimg.py failed"

# ---------------------------------------------------------------------------
say ""
say "== 6. verifying the final boot.img (reverse check of the embedded rc) =="
run_and_log python3 "$WORK/rdtool.py" image \
    "$OUT" "$WORK/rootfs.new.bin" "$ZIMAGE" "$DTB" "$WORK/records.spec" \
    "$SCRIPT_DIR/init_fragment.rc" --max-size "$MAX_SIZE" \
    --extract-rc-out "$OUT_DIR/embedded_init.z1.rc" \
    || die "final boot.img verification failed"

# ---------------------------------------------------------------------------
RRESULT=$(grep '^RESULT|rootfs=' "$REPORT" | tail -n 1)
IRESULT=$(grep '^RESULT|image=' "$REPORT" | tail -n 1)
[ -n "$RRESULT" ] || die "internal: ramdisk RESULT line missing from the report"
[ -n "$IRESULT" ] || die "internal: boot.img RESULT line missing from the report"
IMG_SIZE=$(field "$IRESULT" size)
IMG_SHA=$(field "$IRESULT" sha256)
KS=$(field "$IRESULT" kernel_size)
MKS=$(field "$IRESULT" mtk_kernel)
RS=$(field "$IRESULT" ramdisk_size)
MRS=$(field "$IRESULT" mtk_rootfs)
NREC=$(field "$IRESULT" records)
RGZ=$(field "$RRESULT" gz)
RADD=$(field "$RRESULT" added)
RREP=$(field "$RRESULT" replaced)
RUNT=$(field "$RRESULT" untouched)

say ""
say "== SUMMARY =="
row "step" "result"
row "inputs (template/rootfs/zImage/dtb)" "OK"
row "init.z1.rc patched" "OK, no tabs, LF only, trailing newline, fragment at EOF"
row "init.z1.rc re-parsed" "OK, every service has a class, all keywords known"
row "ramdisk rebuilt" "OK, ${NREC} records, gzip ${RGZ} B"
row "staged records present" "OK, $((${#SPEC[@]})) records: size+sha256+mode all match"
row "record diff" "OK, ${RREP} replaced, ${RADD} added, ${RUNT} untouched byte-identical"
row "boot.img MTK/Android size rule" "OK, kernel ${KS} = ${MKS}+512, ramdisk ${RS} = ${MRS}+512"
row "kernel payload" "OK, == zImage+dtb (sha256 verified)"
row "embedded ROOTFS" "OK, == produced rootfs (sha256 verified)"
row "reverse check of the embedded rc" "OK, z1_rootd service + /z1stage_boot.sh exec present"
row "image size" "OK, ${IMG_SIZE} B < ${MAX_SIZE} (partition 6291456, headroom $((6291456 - IMG_SIZE)))"
row "image sha256" "${IMG_SHA}"
say ""
say "boot.img  ${OUT}"
say "report    ${REPORT}"
say "embedded init.z1.rc dumped to ${OUT_DIR}/embedded_init.z1.rc"
say "done."
exit 0
