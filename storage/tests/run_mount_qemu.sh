#!/bin/bash
# Run the installed ARM32 Z1 FUSE daemon on the host kernel in private namespaces.
# This validates daemon/kernel protocol and ordinary I/O; it does not validate
# Z1's Linux 7 module loading, SELinux policy, vold timing, or quota accounting.
set -euo pipefail
if [[ ${EUID} != 0 ]]; then
    echo "Run as root: sudo $0 [installed-system-tree]" >&2
    exit 2
fi
if [[ ${1:-} != --inside ]]; then
    script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
    repo_root=$(cd -- "$script_dir/../../../../../.." && pwd)
    system_tree=${1:-$repo_root/android9/los16/out/target/product/z1/system}
    system_tree=$(realpath "$system_tree")
    test -x "$system_tree/bin/sdcard"
    command -v qemu-arm-static >/dev/null
    exec timeout 30 unshare --mount --pid --fork --mount-proc "$0" --inside "$system_tree"
fi
system_tree=$2
mount --make-rprivate /
root=$(mktemp -d /tmp/z1-m7-fuse-root.XXXXXX)
cleanup() {
 kill "${daemon:-}" 2>/dev/null || true
 for view in write read default; do umount -l "$root/mnt/runtime/$view/emulated" 2>/dev/null || true; done
 umount "$root/proc" 2>/dev/null || true
 umount "$root/dev" 2>/dev/null || true
 umount "$root/system" 2>/dev/null || true
 rm -rf "$root"
}
trap cleanup EXIT
chmod 0755 "$root"
mkdir -p "$root"/{system,dev,proc,data/media/0,data/system,mnt/runtime/{default,read,write}/emulated}
cp /usr/bin/qemu-arm-static "$root/qemu-arm-static"
mount --bind "$system_tree" "$root/system"
mount -o remount,bind,ro "$root/system"
mount --bind /dev "$root/dev"
mount -t proc proc "$root/proc"
printf '3\0' > "$root/data/.layout_version"
printf 'app.one 10001 0 /data/user/0/app.one default none\n' > "$root/data/system/packages.list"
chmod 0640 "$root/data/system/packages.list"
chown 0:1032 "$root/data/system/packages.list"
chown -R 1023:1023 "$root/data/media"
chmod 0775 "$root/data/media" "$root/data/media/0"
chroot "$root" /qemu-arm-static -L /system /system/bin/sdcard -u 1023 -g 1023 -m -w -i /data/media emulated &
daemon=$!
export FUSE_TEST_ROOT="$root"
timeout 15 python3 - <<'TEST'
import os,time,pathlib,errno,traceback
root=pathlib.Path(os.environ['FUSE_TEST_ROOT'])
for _ in range(30):
 if all(os.path.ismount(root / ('mnt/runtime/'+v+'/emulated')) for v in ['default','read','write']):break
 time.sleep(.1)
else: raise AssertionError('three mounts did not appear')
for v in ['default','read','write']:
 p=root/('mnt/runtime/'+v+'/emulated/0')
 print('view',v,'stat',oct(p.stat().st_mode),p.stat().st_gid,flush=True)
p=root/'mnt/runtime/write/emulated/0'
(p/'probe.txt').write_text('fuse-roundtrip')
assert (root/'data/media/0/probe.txt').read_text()=='fuse-roundtrip'
assert (root/'mnt/runtime/read/emulated/0/probe.txt').read_text()=='fuse-roundtrip'
(p/'probe.txt').rename(p/'renamed.txt')
assert 'renamed.txt' in os.listdir(p)
(p/'renamed.txt').unlink()
print('actual ARM daemon: three mounts + INIT + lookup + write + read + rename + readdir + unlink PASS',flush=True)

write_view=root/'mnt/runtime/write/emulated/0'
read_view=root/'mnt/runtime/read/emulated/0'
(write_view/'shared.txt').write_text('initial')

def denied(operation):
    try:
        operation()
    except PermissionError as error:
        assert error.errno in (errno.EACCES, errno.EPERM), error
    else:
        raise AssertionError('operation unexpectedly allowed')

def run_as(uid, groups, check):
    pid=os.fork()
    if pid == 0:
        try:
            os.setgroups(groups)
            os.setgid(uid)
            os.setuid(uid)
            check()
            print('non-root UID',uid,'groups',groups,'checks PASS',flush=True)
        except BaseException:
            traceback.print_exc()
            os._exit(1)
        os._exit(0)
    _,status=os.waitpid(pid,0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status)==0, (uid,groups,status)

# Legacy Android external-storage permission semantics: both authorized apps
# may access ordinary shared root files. This is not Android scoped storage.
def authorized_one():
    assert (read_view/'shared.txt').read_text() == 'initial'
    (write_view/'shared.txt').write_text('from-10001')
    assert (read_view/'shared.txt').read_text() == 'from-10001'
    (write_view/'app-one.txt').write_text('created-by-10001')
    assert (read_view/'app-one.txt').read_text() == 'created-by-10001'
    denied(lambda: (read_view/'shared.txt').write_text('bad-readview-write'))
    denied(lambda: (read_view/'readview-create.txt').write_text('bad-create'))
run_as(10001,[9997],authorized_one)
assert (root/'data/media/0/shared.txt').read_text() == 'from-10001'

def authorized_two():
    assert (read_view/'app-one.txt').read_text() == 'created-by-10001'
    (write_view/'shared.txt').write_text('from-10002')
    assert (read_view/'shared.txt').read_text() == 'from-10002'
    denied(lambda: (read_view/'shared.txt').write_text('bad-readview-write'))
run_as(10002,[9997],authorized_two)
assert (root/'data/media/0/shared.txt').read_text() == 'from-10002'

def no_external_permission():
    denied(lambda: (write_view/'shared.txt').read_text())
    denied(lambda: (write_view/'shared.txt').write_text('bad-unprivileged-write'))
    denied(lambda: (write_view/'no-permission-create.txt').write_text('bad-create'))
    denied(lambda: (read_view/'shared.txt').read_text())
run_as(10001,[],no_external_permission)
run_as(10002,[],no_external_permission)
assert (read_view/'shared.txt').read_text() == 'from-10002'
print('actual ARM daemon non-root shared-storage roundtrip, readview write denial and no-permission denial PASS',flush=True)
TEST
