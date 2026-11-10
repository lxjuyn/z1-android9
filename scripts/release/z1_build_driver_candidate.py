#!/usr/bin/env python3
"""Offline RAW ext4 copy/replace; inspection is default, --build enables output.

Manifest v1 (paths are relative to the manifest directory unless absolute):
{"version":1,"base":{"path":"system.img","sha256":"<64 hex>"},
 "replacements":[{"path":"/usr/keylayout/mt6779-keypad.kl","source":"keys.kl",
 "sha256":"<replacement hash>","original_sha256":"<original hash>",
 "mode":"0644","uid":0,"gid":0,"selinux":"u:object_r:system_file:s0"}]}
Only existing key assets, vendor libraries, and system modules are supported.
No mounting, formatting, device access, commands from manifests, or /data edits.
Accept only exit 0 + final image/report + no .INVALID names + matching image SHA.
The .manifest.json.INVALID marker never contains success. Recovery is best effort;
rollback attempts both final unlinks even if preserving the partial fails.
"""
import argparse
from contextlib import contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile

ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}
NAME = r"[A-Za-z0-9_][A-Za-z0-9_.+-]*"
ALLOW = re.compile(r"/(?:system/)?(?:usr/(?:keylayout/" + NAME + r"\.kl|keychars/" +
                   NAME + r"\.kcm|idc/" + NAME + r"\.idc)|vendor/lib(?:64)?/(?:hw/|egl/)?" +
                   NAME + r"\.so|lib/modules/" + NAME + r"\.ko)")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def host_path(value, root):
    require(isinstance(value, str) and value and not any(ord(c) < 32 for c in value),
            "Invalid host path")
    p = Path(value)
    require(".." not in p.parts, "Host traversal rejected")
    p = root / p if not p.is_absolute() else p
    for q in (p, *p.parents):
        require(not q.is_symlink(), f"Host symlink rejected: {q}")
    return p


@contextmanager
def parent_fd(path):
    """Pin every directory component without following symlinks (including races)."""
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts, "Unsafe host path")
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


def regular_open(path):
    path = Path(path)
    with parent_fd(path) as directory:
        require(stat.S_ISREG(os.stat(path.name, dir_fd=directory, follow_symlinks=False).st_mode),
                f"Not a regular file: {path}")
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    f = os.fdopen(fd, "rb")
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        f.close()
        raise ValueError(f"Not a regular file: {path}")
    return f


def digest(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value), "Invalid SHA256")


def unique_json(pairs):
    result = {}
    for k, v in pairs:
        require(k not in result, f"Duplicate manifest key: {k}")
        result[k] = v
    return result


def load_manifest(path):
    with regular_open(path) as f:
        m = json.load(f, object_pairs_hook=unique_json)
    require(isinstance(m, dict) and set(m) == {"version", "base", "replacements"}
            and type(m["version"]) is int and m["version"] == 1, "Invalid manifest schema")
    b = m["base"]
    require(isinstance(b, dict) and set(b) == {"path", "sha256"}, "Invalid base schema")
    digest(b["sha256"])
    b["path"] = str(host_path(b["path"], path.parent))
    require(isinstance(m["replacements"], list) and 0 < len(m["replacements"]) <= 64,
            "Need 1..64 explicit replacements")
    seen = set()
    for r in m["replacements"]:
        require(isinstance(r, dict) and set(r) == {"path", "source", "sha256", "original_sha256",
                "mode", "uid", "gid", "selinux"}, "Invalid replacement schema")
        p = r["path"]
        require(isinstance(p, str) and ALLOW.fullmatch(p) and p not in seen,
                f"Disallowed or duplicate image path: {p!r}")
        seen.add(p)
        digest(r["sha256"])
        digest(r["original_sha256"])
        require(isinstance(r["mode"], str) and re.fullmatch(r"0[0-7]{3,4}", r["mode"]),
                "Mode must be octal permission bits")
        require(all(type(r[x]) is int and 0 <= r[x] <= 0xffffffff for x in ("uid", "gid")),
                "Invalid uid/gid")
        require(isinstance(r["selinux"], str) and re.fullmatch(
            r"u:object_r:[A-Za-z0-9_]+:s0(?:[c:,0-9.]*)?", r["selinux"]), "Invalid SELinux label")
        r["source"] = str(host_path(r["source"], path.parent))
    return m


def run(tool, args, cwd, fd):
    executable = shutil.which(tool, path=ENV["PATH"])
    require(executable is not None, f"Missing host tool: {tool}")
    p = subprocess.run([executable, *args], cwd=cwd, env=ENV, pass_fds=(fd,),
                       capture_output=True, text=True, timeout=300)
    require(p.returncode == 0, f"{tool} exit {p.returncode}: {p.stdout} {p.stderr}")
    return p


def debug(fd, cwd, command, write=False, output=False):
    p = run("debugfs", (["-w"] if write else []) + ["-R", command, f"/proc/self/fd/{fd}"], cwd, fd)
    diagnostics = [s for s in p.stderr.splitlines() if s and not re.fullmatch(
        r"debugfs [0-9]+(?:\.[0-9]+)+(?:[-+~][\w.]+)? \([0-9]{1,2}-[A-Za-z]{3}-[0-9]{4}\)", s)]
    require(not diagnostics, "debugfs diagnostic: " + "\n".join(diagnostics))
    if not output:
        require(not p.stdout.strip() or (command.startswith("write ") and
                re.fullmatch(r"Allocated inode: [0-9]+\s*", p.stdout)), "debugfs unexpected output: " + p.stdout)
    return p.stdout


def fsck(fd, cwd):
    return run("e2fsck", ["-fn", f"/proc/self/fd/{fd}"], cwd, fd).stdout


def raw_ext4(f):
    f.seek(1024)
    sb = f.read(1024)
    require(len(sb) == 1024 and sb[56:58] == b"\x53\xef", "Not a RAW ext filesystem")
    incompat = int.from_bytes(sb[96:100], "little")
    require(incompat & 0x40, "RAW ext4 extents feature required")
    f.seek(0)


def metadata(fd, cwd, path):
    text = debug(fd, cwd, f"stat {path}", output=True)
    match = re.search(r"Type: (\w+)\s+Mode:\s+([0-7]+)", text)
    ids = re.search(r"User:\s+(\d+)\s+Group:\s+(\d+)", text)
    links = re.search(r"Links:\s+(\d+)", text)
    require(match and ids and links, f"Unparseable inode: {path}")
    return {"type": match[1], "mode": int(match[2], 8), "uid": int(ids[1]),
            "gid": int(ids[2]), "links": int(links[1])}


def inspect(fd, cwd, r, expected_hash):
    parts = PurePosixPath(r["path"])
    for parent in reversed(parts.parents):
        require(metadata(fd, cwd, str(parent))["type"] == "directory", f"Non-directory parent: {parent}")
    info = metadata(fd, cwd, r["path"])
    require(info == {"type": "regular", "mode": int(r["mode"], 8), "uid": r["uid"],
                     "gid": r["gid"], "links": 1}, f"Metadata mismatch or hardlink: {r['path']}")
    attrs = debug(fd, cwd, f"ea_list {r['path']}", output=True)
    lines = attrs.splitlines()
    require(len(lines) == 2 and lines[0] == "Extended attributes:" and re.fullmatch(
        r'  security\.selinux \([0-9]+\) = ".*"', lines[1]),
        "Require SELinux-only xattrs; additional attrs unsupported")
    for name in ("readback", "label-readback"):
        (cwd / name).unlink(missing_ok=True)
    debug(fd, cwd, f"dump {r['path']} readback")
    require(sha(cwd / "readback") == expected_hash, f"Readback SHA256 mismatch: {r['path']}")
    debug(fd, cwd, f"ea_get -f label-readback {r['path']} security.selinux")
    label = (cwd / "label-readback").read_bytes()
    require(label in (r["selinux"].encode(), r["selinux"].encode() + b"\0"), "SELinux mismatch")
    return label


def same_bytes(a, b):
    with open(a, "rb") as x, open(b, "rb") as y:
        while True:
            p, q = x.read(1024 * 1024), y.read(1024 * 1024)
            if p != q:
                return False
            if not p:
                return True


def write_report(f, report):
    f.seek(0)
    json.dump(report, f, indent=2)
    f.truncate()
    f.flush()
    os.fsync(f.fileno())


def withdraw(directory, output, partial, reportpath, invalidreport, owned_output, owned_report, invalid_state, error):
    opener = lambda name, flags: os.open(name, flags | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    failures = []
    for owned, final, recovery in ((owned_output, output, partial),
                                   (owned_report, reportpath, None)):
        if not owned:
            continue
        if recovery is not None:
            try:
                os.link(final.name, recovery.name, src_dir_fd=directory,
                        dst_dir_fd=directory, follow_symlinks=False)
            except FileExistsError:
                pass
            except OSError as e:
                failures.append(str(e))  # Recovery is best effort; withdrawal is independent.
        try:
            os.unlink(final.name, dir_fd=directory)
        except OSError as e:
            failures.append(str(e))
    if owned_output or owned_report:
        try:
            with open(invalidreport.name, "x+", opener=opener) as marker:
                write_report(marker, invalid_state)
        except FileExistsError:
            pass  # Existing immutable marker is already INVALID.
        except OSError as e:
            failures.append(str(e))
    try:
        os.fsync(directory)
    except OSError as e:
        failures.append(str(e))
    if failures:
        error.add_note("Rollback limitations: " + "; ".join(failures))


def execute(manifest, output=None, build=False):
    manifest = host_path(str(manifest), Path.cwd())
    m = load_manifest(manifest)
    require(not build or output is not None, "--build requires --output")
    require(build or output is None, "--output requires explicit --build")
    output = host_path(str(output), Path.cwd()) if output is not None else None
    paths = []
    if output:
        paths = [output, Path(str(output) + ".INVALID"), Path(str(output) + ".manifest.json"),
                 Path(str(output) + ".manifest.json.INVALID")]
        inputs = [manifest, Path(m["base"]["path"]), *(Path(r["source"]) for r in m["replacements"])]
        require(output.parent.is_dir(), "Output parent must already exist")
        require(all(not os.path.lexists(p) and p not in inputs for p in paths),
                "Output, partial, or report already exists / aliases input")
    report = {"status": "INSPECTED_ONLY_NOT_BUILT", "manifest": m}
    partial, reportpath, invalidreport = paths[1:] if output else (None, None, None)
    invalid_state = {"status": "INVALID", "error": "Build incomplete or aborted",
                     "protocol": "Accept only exit 0, final image/report and no INVALID names"}
    owned_report, owned_output = False, False
    directory_identity = None
    try:
        with parent_fd(output) if build else nullcontext(None) as directory:
            opener = lambda name, flags: os.open(name, flags | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            directory_identity = os.fstat(directory) if build else None
            try:
                with regular_open(m["base"]["path"]) as base, tempfile.TemporaryDirectory(prefix="z1-candidate-") as td:
                    cwd = Path(td)
                    raw_ext4(base)
                    require(sha(f"/proc/self/fd/{base.fileno()}") == m["base"]["sha256"], "Base SHA256 mismatch")
                    report["e2fsck_before"] = fsck(base.fileno(), cwd)
                    for i, r in enumerate(m["replacements"]):
                        with regular_open(r["source"]) as source, (cwd / f"payload{i}").open("xb") as staged:
                            shutil.copyfileobj(source, staged)
                        require(sha(cwd / f"payload{i}") == r["sha256"], "Replacement SHA256 mismatch")
                        label = inspect(base.fileno(), cwd, r, r["original_sha256"])
                        (cwd / f"label{i}").write_bytes(label)
                    if not build:
                        require(sha(f"/proc/self/fd/{base.fileno()}") == m["base"]["sha256"], "Base changed during inspection")
                        return report
                    # Immutable failure marker: NEVER write success into this inode.
                    with open(invalidreport.name, "x+", opener=opener) as marker:
                        write_report(marker, invalid_state)
                    with open(partial.name, "x+b", opener=opener) as candidate:
                        base.seek(0)
                        shutil.copyfileobj(base, candidate)
                        candidate.flush()
                        os.fsync(candidate.fileno())
                        require(sha(f"/proc/self/fd/{candidate.fileno()}") == m["base"]["sha256"], "Base changed during copy")
                        for i, r in enumerate(m["replacements"]):
                            for cmd in (f"rm {r['path']}", f"write payload{i} {r['path']}",
                                        f"sif {r['path']} mode 0{(stat.S_IFREG | int(r['mode'], 8)):o}",
                                        f"sif {r['path']} uid {r['uid']}", f"sif {r['path']} gid {r['gid']}",
                                        f"ea_set -f label{i} {r['path']} security.selinux"):
                                debug(candidate.fileno(), cwd, cmd, write=True)
                        report["e2fsck_after"] = fsck(candidate.fileno(), cwd)
                        for i, r in enumerate(m["replacements"]):
                            label = inspect(candidate.fileno(), cwd, r, r["sha256"])
                            require(label == (cwd / f"label{i}").read_bytes(), "SELinux byte preservation failed")
                            require(same_bytes(cwd / "readback", cwd / f"payload{i}"), "Readback bytes differ")
                        os.fsync(candidate.fileno())
                        require(sha(f"/proc/self/fd/{base.fileno()}") == m["base"]["sha256"], "Base changed during build")
                        report.update(status="HOST_VERIFIED_NOT_DEVICE_TESTED", output=str(output),
                                      sha256=sha(f"/proc/self/fd/{candidate.fileno()}"))
                        image_identity = os.fstat(candidate.fileno())
                # Candidate, base and scratch files are closed/cleaned before publication.
                with open(reportpath.name, "x+", opener=opener) as reportfile:
                    owned_report = True
                    write_report(reportfile, report)
                    report_identity = os.fstat(reportfile.fileno())
                with parent_fd(output) as current:
                    require(os.path.samestat(os.fstat(directory), os.fstat(current)), "Output parent changed")
                require(os.path.samestat(report_identity, os.stat(reportpath.name, dir_fd=directory,
                        follow_symlinks=False)), "Report identity changed")
                os.link(partial.name, output.name, src_dir_fd=directory, dst_dir_fd=directory,
                        follow_symlinks=False)
                owned_output = True
                require(os.path.samestat(image_identity, os.stat(output.name, dir_fd=directory,
                        follow_symlinks=False)), "Candidate identity changed")
                os.fsync(directory)
                os.unlink(partial.name, dir_fd=directory)
                os.fsync(directory)
                os.unlink(invalidreport.name, dir_fd=directory)  # Commit marker removed last.
                os.fsync(directory)
            except BaseException as error:
                if build:
                    withdraw(directory, output, partial, reportpath, invalidreport,
                             owned_output, owned_report, invalid_state, error)
                    owned_output = owned_report = False
                raise
    except BaseException as error:
        # Directory close can fail after the body and publication both succeeded.
        if owned_output or owned_report:
            try:
                with parent_fd(output) as recovery:
                    require(os.path.samestat(directory_identity, os.fstat(recovery)),
                            "Rollback directory identity changed")
                    withdraw(recovery, output, partial, reportpath, invalidreport,
                             owned_output, owned_report, invalid_state, error)
            except (OSError, ValueError) as recovery_error:
                error.add_note("Rollback unavailable: " + str(recovery_error))
        raise
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--build", action="store_true", help="Explicitly copy and replace after inspection")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(execute(args.manifest, args.output, args.build), indent=2))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as e:
        print(f"INVALID: {e}", file=sys.stderr)
        for note in getattr(e, '__notes__', ()):
            print(note, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
