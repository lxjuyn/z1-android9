#!/usr/bin/env python3
"""Verified MTK byte-range transfers. All device access goes through mtk.py.

The state is a hint, never proof of the current device contents: resumed blocks
are read again. Legacy /tmp/z1chunk_*.state files are deliberately not consumed.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path("/home/lxj/claude/6572")
PARTITIONS = {
    "preloader": (0x0, 0x0C00000), "pro_info": (0xD00000, 0x300000),
    "nvram": (0x1000000, 0x500000), "seccfg": (0x2900000, 0x20000),
    "uboot": (0x2920000, 0x60000), "bootimg": (0x2980000, 0x600000),
    "recovery": (0x2F80000, 0x600000), "misc": (0x35C0000, 0x80000),
    "logo": (0x3640000, 0x300000), "android": (0x4340000, 0x38400000),
    "cache": (0x3C740000, 0xE200000), "usrdata": (0x4A940000, 0x9DDC0000),
}
FAILED = re.compile(
    r"fail(?:ed|ure)?|USBError|Traceback|Exception|\berror\b|timed out|timeout|"
    r"aborted|disconnect|no device|nothing connected|no preloader|not (?:found|detected)|"
    r"couldn.t|cannot|can.t", re.I)
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def positive_int(value):
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise argparse.ArgumentTypeError("must be a positive decimal integer")
    return int(value)


def atomic_json(path, data):
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextlib.contextmanager
def device_lock(path):
    with open(path, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f"another flash/readback holds device lock {path}")
        yield lock.fileno()


def stop_process(proc):
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


class Backend:
    def __init__(self, lockfd, logdir):
        self.lockfd, self.logdir, self.counter = lockfd, logdir, 0
        self.directory = Path(os.environ.get("Z1_MTK_DIR", ROOT / "mtkclient_v2141"))
        self.python = os.environ.get("Z1_MTK_PY", str(ROOT / "mtkclient_venv/bin/python"))
        self.timeout = float(os.environ.get("Z1_CHUNK_TIMEOUT", "300"))
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("Z1_CHUNK_TIMEOUT must be finite and positive")
        if not (self.directory / "mtk.py").is_file():
            raise ValueError(f"mtk.py missing in {self.directory}")
        if not os.access(self.python, os.X_OK):
            raise ValueError(f"MTK python is not executable: {self.python}")

    def transfer(self, operation, offset, length, filename):
        self.counter += 1
        logfile = self.logdir / f"{self.counter:04d}_{operation}_{offset:x}_{length:x}.log"
        cmd = [self.python, str(self.directory / "mtk.py"), operation,
               hex(offset), hex(length), str(filename)]
        with logfile.open("wb") as log:
            proc = subprocess.Popen(cmd, cwd=self.directory, stdout=log,
                                    stderr=subprocess.STDOUT, start_new_session=True,
                                    pass_fds=(self.lockfd,))
            try:
                rc = proc.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                stop_process(proc)
                rc = 124
            except BaseException:
                stop_process(proc)
                raise
        output = ANSI.sub("", logfile.read_text(errors="replace")).replace("\r", "\n")
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        expected = (f"Wrote {filename} to offset {hex(offset)} with length {hex(length)}."
                    if operation == "wo" else
                    f"Dumped offset {hex(offset)} with length {hex(length)} as {filename}.")
        ok = rc == 0 and not FAILED.search(output) and bool(lines) and lines[-1] == expected
        if not ok:
            print(f"  {operation} rejected (rc={rc}); log={logfile}", flush=True)
        return ok


def manifest(source, chunk):
    source.seek(0)
    whole, chunks = hashlib.sha256(), []
    size = 0
    while True:
        data = source.read(chunk)
        if not data:
            break
        whole.update(data)
        chunks.append(hashlib.sha256(data).hexdigest())
        size += len(data)
    return size, whole.hexdigest(), chunks


def run(args):
    image = Path(args.image).resolve(strict=True)
    offset, capacity = PARTITIONS[args.partition]
    chunk = args.chunk_mb * 1024 * 1024
    delay = float(os.environ.get("Z1_CHUNK_RETRY_DELAY", "3"))
    if not math.isfinite(delay) or delay < 0:
        raise ValueError("Z1_CHUNK_RETRY_DELAY must be finite and nonnegative")
    with device_lock(os.environ.get("Z1_FLASH_LOCK", "/tmp/z1flash.lock")) as lockfd, image.open("rb") as source:
        st = os.fstat(source.fileno())
        if not stat.S_ISREG(st.st_mode) or not 0 < st.st_size <= capacity:
            raise ValueError(f"image must be a nonempty regular file <= partition size {capacity}")
        if st.st_size % 512:
            raise ValueError("raw image size must be a multiple of the 512-byte eMMC sector")
        size, sha, hashes = manifest(source, chunk)
        if size != st.st_size:
            raise ValueError("image changed while hashing")
        meta = {"version": 2, "partition": args.partition, "offset": offset,
                "capacity": capacity, "image_size": size, "image_sha256": sha,
                "chunk_bytes": chunk, "chunk_sha256": hashes}
        statedir = Path(os.environ.get("Z1_CHUNK_STATE_DIR", "/tmp/z1chunk-v2"))
        statedir.mkdir(mode=0o700, parents=True, exist_ok=True)
        statefile = statedir / f"{args.partition}_{sha}_{chunk}.json"
        state = {"identity": meta, "verified": {}, "complete": False}
        if args.mode == "write" and statefile.exists():
            try:
                old = json.loads(statefile.read_text())
                if old.get("identity") == meta and isinstance(old.get("verified"), dict):
                    state["verified"] = old["verified"]
                else:
                    print("Ignoring incompatible state; blocks will be written and verified.")
            except (ValueError, AttributeError):
                print("Ignoring unreadable state; blocks will be written and verified.")
        logs = Path(os.environ.get("Z1_CHUNK_LOG_DIR", ROOT / "mainline_recon/flash_chunks"))
        logs.mkdir(parents=True, exist_ok=True)
        logdir = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%d_%H%M%S_") + args.mode + "_", dir=logs))
        backend = Backend(lockfd, logdir)
        atomic_json(logdir / "manifest.json", {"identity": meta, "image": str(image), "mode": args.mode})
        print(f"{args.mode}: {args.partition} <- {image}\nSHA256 {sha}\nstate={statefile}\nlogs={logdir}", flush=True)
        if args.mode == "write":
            atomic_json(statefile, state)
        with tempfile.TemporaryDirectory(prefix="z1chunk-transfer-") as tmpdir:
            expected = Path(tmpdir) / "expected.bin"
            readback = Path(tmpdir) / "readback.bin"
            for index, digest in enumerate(hashes):
                start = index * chunk
                length = min(chunk, size - start)
                source.seek(start)
                data = source.read(length)
                if len(data) != length or hashlib.sha256(data).hexdigest() != digest:
                    raise RuntimeError("source image changed; refusing further transfers")
                expected.write_bytes(data)
                address = offset + start

                def verify():
                    readback.unlink(missing_ok=True)
                    if not backend.transfer("ro", address, length, readback):
                        return False
                    if not readback.is_file() or readback.stat().st_size != length:
                        print(f"  short/oversized readback for block {index}", flush=True)
                        return False
                    # Byte equality, not just backend acknowledgement or a digest.
                    if readback.read_bytes() != data:
                        print(f"  byte mismatch for block {index}", flush=True)
                        return False
                    return True

                verified = False
                if args.mode == "write" and state["verified"].get(str(index)) == digest:
                    verified = verify()
                    if not verified:
                        state["verified"].pop(str(index), None)
                        atomic_json(statefile, state)
                for attempt in range(1, args.maxtries + 1):
                    if verified:
                        break
                    print(f"block {index + 1}/{len(hashes)} attempt {attempt}: {hex(address)} +{hex(length)}", flush=True)
                    if args.mode == "write" and not backend.transfer("wo", address, length, expected):
                        if attempt < args.maxtries:
                            time.sleep(delay)
                        continue
                    # A failed read can be transient; retry reads before rewriting.
                    for read_attempt in range(args.maxtries if args.mode == "write" else 1):
                        if verify():
                            verified = True
                            break
                        if read_attempt + 1 < args.maxtries and args.mode == "write":
                            time.sleep(delay)
                    if verified:
                        break
                    if attempt < args.maxtries:
                        time.sleep(delay)
                if not verified:
                    atomic_json(logdir / "result.json", {"ok": False, "failed_block": index})
                    print(f"FAILED at block {index}; no completion recorded. logs={logdir}", flush=True)
                    return 2
                if args.mode == "write":
                    state["verified"][str(index)] = digest
                    atomic_json(statefile, state)
                print(f"block {index + 1}/{len(hashes)}: read back byte-identical", flush=True)
        final_size, final_sha, _ = manifest(source, chunk)
        if (final_size, final_sha) != (size, sha):
            raise RuntimeError("source image changed during transfer; result is not accepted")
        if args.mode == "write":
            state["complete"] = True
            atomic_json(statefile, state)
        atomic_json(logdir / "result.json", {"ok": True, "verified_bytes": size, "image_sha256": sha})
        print(f"ALL {len(hashes)} blocks / {size} bytes read back byte-identical; SHA256 {sha}", flush=True)
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("write", "verify"))
    parser.add_argument("partition", choices=PARTITIONS)
    parser.add_argument("image")
    parser.add_argument("chunk_mb", type=positive_int, nargs="?", default=48)
    parser.add_argument("maxtries", type=positive_int, nargs="?", default=6)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        return run(args)
    except KeyboardInterrupt:
        print("Interrupted; incomplete block was not accepted.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
