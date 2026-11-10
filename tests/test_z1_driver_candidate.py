"""Host-only tests: mkfs operates exclusively on NEW regular temporary files."""
import copy
import errno
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/release/z1_build_driver_candidate.py"
SPEC = importlib.util.spec_from_file_location("candidate", SCRIPT)
candidate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(candidate)


class CandidateTests(unittest.TestCase):
    def setUp(self):
        for name in ("mkfs.ext4", "debugfs", "e2fsck"):
            if not shutil.which(name, path=candidate.ENV["PATH"]):
                self.skipTest(f"Missing {name}")
        self.temp = tempfile.TemporaryDirectory(prefix="z1-candidate-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "base.img"
        with self.base.open("xb") as f:  # Never a device or an existing image.
            f.truncate(16 * 1024 * 1024)
        subprocess.run([shutil.which("mkfs.ext4", path=candidate.ENV["PATH"]), "-q", "-F",
                        str(self.base)], env=candidate.ENV, check=True, capture_output=True)
        self.payload = self.root / "new payload;not-a-command"
        self.payload.write_bytes(b"replacement\x00\xff" * 1000)
        (self.root / "old").write_bytes(b"original content\n")
        (self.root / "keep").write_bytes(b"unrelated sentinel\x00\x7f")
        self.label = b"u:object_r:vendor_file:s0\x00"
        (self.root / "label").write_bytes(self.label)
        self.dst = "/vendor/lib/libfixture.so"
        for cmd in ("mkdir /vendor", "mkdir /vendor/lib", f"write old {self.dst}",
                    f"sif {self.dst} mode 0100640", f"sif {self.dst} uid 1234",
                    f"sif {self.dst} gid 5678", f"ea_set -f label {self.dst} security.selinux",
                    "write keep /untouched", "symlink /untouched-link /untouched"):
            self.dbg(cmd, write=True)
        self.manifest = self.root / "manifest.json"
        self.output = self.root / "candidate.img"
        self.data = {"version": 1, "base": {"path": "base.img", "sha256": candidate.sha(self.base)},
                     "replacements": [{"path": self.dst, "source": self.payload.name,
                     "sha256": candidate.sha(self.payload), "original_sha256": candidate.sha(self.root / "old"),
                     "mode": "0640", "uid": 1234, "gid": 5678, "selinux": self.label[:-1].decode()}]}
        self.save()
        self.initial = candidate.sha(self.base)

    def dbg(self, cmd, write=False, image=None):
        return subprocess.run([shutil.which("debugfs", path=candidate.ENV["PATH"]),
            *(["-w"] if write else []), "-R", cmd, str(image or self.base)], cwd=self.root,
            env=candidate.ENV, capture_output=True, text=True, check=True)

    def save(self):
        self.manifest.write_text(json.dumps(self.data))

    def reject(self, pattern=None):
        before = candidate.sha(self.base)
        with self.assertRaisesRegex((ValueError, OSError), pattern or ".*"):
            candidate.execute(self.manifest, self.output, True)
        self.assertFalse(self.output.exists())
        self.assertFalse(Path(str(self.output) + ".INVALID").exists())
        self.assertEqual(candidate.sha(self.base), before)

    def test_inspection_default_cli_never_creates_candidate(self):
        result = subprocess.run([sys.executable, str(SCRIPT), str(self.manifest)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "INSPECTED_ONLY_NOT_BUILT")
        self.assertFalse(self.output.exists())
        self.assertEqual(candidate.sha(self.base), self.initial)
        with self.assertRaisesRegex(ValueError, "explicit --build"):
            candidate.execute(self.manifest, self.output)
        with self.assertRaisesRegex(ValueError, "requires --output"):
            candidate.execute(self.manifest, build=True)

    def test_real_build_readback_metadata_and_untouched_content(self):
        before_stat = self.dbg("stat /untouched").stdout
        before_link = self.dbg("stat /untouched-link").stdout
        result = candidate.execute(self.manifest, self.output, True)
        self.assertEqual(result["status"], "HOST_VERIFIED_NOT_DEVICE_TESTED")
        self.assertEqual(result["sha256"], candidate.sha(self.output))
        self.assertEqual(candidate.sha(self.base), self.initial)
        self.assertIn("e2fsck_before", result)
        self.assertIn("e2fsck_after", result)
        self.dbg(f"dump {self.dst} actual", image=self.output)
        self.assertEqual((self.root / "actual").read_bytes(), self.payload.read_bytes())
        self.dbg(f"ea_get -f actual-label {self.dst} security.selinux", image=self.output)
        self.assertEqual((self.root / "actual-label").read_bytes(), self.label)
        with self.output.open("rb") as f:
            self.assertEqual(candidate.metadata(f.fileno(), self.root, self.dst),
                {"type": "regular", "mode": 0o640, "uid": 1234, "gid": 5678, "links": 1})
        self.dbg("dump /untouched untouched-readback", image=self.output)
        self.assertEqual((self.root / "untouched-readback").read_bytes(), (self.root / "keep").read_bytes())
        self.assertEqual(self.dbg("stat /untouched", image=self.output).stdout, before_stat)
        self.assertEqual(self.dbg("stat /untouched-link", image=self.output).stdout, before_link)
        self.assertEqual(json.loads(Path(str(self.output) + ".manifest.json").read_text()), result)
        self.assertFalse(Path(str(self.output) + ".INVALID").exists())

    def test_hash_and_metadata_failures(self):
        original = copy.deepcopy(self.data)
        for key, val in [("base", "0" * 64), ("sha256", "0" * 64), ("original_sha256", "0" * 64),
                         ("mode", "0600"), ("uid", 1), ("gid", 1), ("selinux", "u:object_r:wrong:s0")]:
            with self.subTest(key=key):
                self.data = copy.deepcopy(original)
                if key == "base":
                    self.data["base"]["sha256"] = val
                else:
                    self.data["replacements"][0][key] = val
                self.save()
                self.reject()

    def test_malformed_image_paths_and_missing_target(self):
        for path in ["/data/x", "/vendor/lib/../x.so", "/vendor/lib//x.so", "/vendor/lib/x.so\nrm /x",
                     '/vendor/lib/x".so', "/vendor/lib/x\\.so", "/vendor/lib/x;.so", "vendor/lib/x.so",
                     "/vendor/lib/x.so ", "/vendor/lib/<14>.so", "/vendor/lib/missing.so"]:
            with self.subTest(path=path):
                self.data["replacements"][0]["path"] = path
                self.save()
                self.reject()

    def test_schema_and_unknown_fields(self):
        original = copy.deepcopy(self.data)
        for change in (lambda m: m.update(command="/bin/true"), lambda m: m.update(version=True),
                       lambda m: m.update(replacements=[]), lambda m: m["replacements"].append(m["replacements"][0]),
                       lambda m: m["replacements"][0].pop("mode"),
                       lambda m: m["replacements"][0].update(uid=True),
                       lambda m: m["replacements"][0].update(sha256="bad")):
            self.data = copy.deepcopy(original)
            change(self.data)
            self.save()
            self.reject()
        self.manifest.write_text('{"version":1,"version":1}')
        self.reject("Duplicate")

    def test_host_symlink_fifo_device_and_output_collisions(self):
        (self.root / "link").symlink_to(self.payload)
        os.mkfifo(self.root / "fifo")
        self.addCleanup(lambda: (self.root / "fifo").unlink(missing_ok=True))
        for source in ("link", "fifo", "/dev/null", "../outside", "missing"):
            self.data["replacements"][0]["source"] = source
            self.save()
            self.reject()
        self.data["replacements"][0]["source"] = self.payload.name
        self.save()
        for suffix in ("", ".INVALID", ".manifest.json", ".manifest.json.INVALID"):
            p = Path(str(self.output) + suffix)
            p.write_bytes(b"do not overwrite")
            with self.assertRaisesRegex(ValueError, "already exists"):
                candidate.execute(self.manifest, self.output, True)
            self.assertEqual(p.read_bytes(), b"do not overwrite")
            p.unlink()
        with self.assertRaises(ValueError):
            candidate.execute(self.manifest, self.base, True)
        (self.root / "dirlink").symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            candidate.execute(self.manifest, self.root / "dirlink/out.img", True)
        self.output.symlink_to(self.root / "absent")
        with self.assertRaisesRegex(ValueError, "symlink"):
            candidate.execute(self.manifest, self.output, True)
        self.assertEqual(candidate.sha(self.base), self.initial)

    def test_image_symlink_and_extra_xattrs_rejected(self):
        self.dbg("ea_set " + self.dst + " user.extra unsupported", write=True)
        self.data["base"]["sha256"] = candidate.sha(self.base)
        self.save()
        self.reject("additional attrs")
        self.dbg("ea_rm " + self.dst + " user.extra", write=True)
        self.dbg("rm " + self.dst, write=True)
        self.dbg("symlink " + self.dst + " /untouched", write=True)
        self.data["base"]["sha256"] = candidate.sha(self.base)
        self.save()
        self.reject("Metadata mismatch")

    def test_actual_debugfs_zero_exit_error_rejected(self):
        raw = self.dbg("stat /missing")
        self.assertEqual(raw.returncode, 0)
        self.assertIn("File not found", raw.stderr)
        with self.base.open("rb") as f:
            with self.assertRaisesRegex(ValueError, "debugfs diagnostic"):
                candidate.debug(f.fileno(), self.root, "stat /missing", output=True)

    def test_pre_fsck_nonzero_aborts_before_copy(self):
        self.dbg("sif /untouched links_count 7", write=True)
        self.data["base"]["sha256"] = candidate.sha(self.base)
        self.save()
        self.reject("e2fsck exit")

    def test_post_fsck_failure_retains_only_invalid_partial(self):
        actual = candidate.fsck
        count = 0
        def corrupt_then_check(fd, cwd):
            nonlocal count
            count += 1
            if count == 2:
                candidate.debug(fd, cwd, "sif /untouched links_count 7", write=True)
            return actual(fd, cwd)
        with patch.object(candidate, "fsck", side_effect=corrupt_then_check):
            with self.assertRaisesRegex(ValueError, "e2fsck exit"):
                candidate.execute(self.manifest, self.output, True)
        self.assertFalse(self.output.exists())
        self.assertTrue(Path(str(self.output) + ".INVALID").is_file())
        self.assertEqual(json.loads(Path(str(self.output) + ".manifest.json.INVALID").read_text())["status"], "INVALID")
        self.assertEqual(candidate.sha(self.base), self.initial)

    def test_readback_detects_silent_wrong_write(self):
        actual = candidate.debug
        def wrong_write(fd, cwd, command, **kwargs):
            if command.startswith("write payload"):
                (cwd / "wrong").write_bytes(b"wrong bytes")
                command = command.replace("payload0", "wrong")
            return actual(fd, cwd, command, **kwargs)
        with patch.object(candidate, "debug", side_effect=wrong_write):
            with self.assertRaisesRegex(ValueError, "Readback SHA256"):
                candidate.execute(self.manifest, self.output, True)
        self.assertFalse(self.output.exists())
        self.assertTrue(Path(str(self.output) + ".INVALID").exists())
        self.assertEqual(candidate.sha(self.base), self.initial)

    def test_whitespace_xattr_rejected_during_inspection(self):
        self.dbg('ea_set ' + self.dst + ' "user.extra attr" unsupported', write=True)
        self.data["base"]["sha256"] = candidate.sha(self.base)
        self.save()
        with self.assertRaisesRegex(ValueError, "attrs"):
            candidate.execute(self.manifest)

    def test_ancestor_symlink_swap_before_open_rejected(self):
        source_dir = self.root / "source-dir"
        source_dir.mkdir()
        (source_dir / "payload").write_bytes(self.payload.read_bytes())
        self.data["replacements"][0]["source"] = "source-dir/payload"
        self.save()
        actual = candidate.regular_open
        def swapped(path):
            if str(path).endswith("source-dir/payload"):
                source_dir.rename(self.root / "real-source")
                source_dir.symlink_to(self.root / "real-source", target_is_directory=True)
            return actual(path)
        with patch.object(candidate, "regular_open", side_effect=swapped):
            self.reject()

    def test_cleanup_failure_rolls_back_publication(self):
        actual = os.unlink
        def fail_cleanup(path, *args, **kwargs):
            if Path(path).name == self.output.name + ".manifest.json.INVALID":
                raise OSError("injected cleanup failure")
            return actual(path, *args, **kwargs)
        with patch.object(os, "unlink", side_effect=fail_cleanup):
            with self.assertRaisesRegex(OSError, "injected cleanup failure"):
                candidate.execute(self.manifest, self.output, True)
        self.assertFalse(self.output.exists())
        self.assertFalse(Path(str(self.output) + ".manifest.json").exists())
        self.assertTrue(Path(str(self.output) + ".INVALID").is_file())
        self.assertEqual(json.loads(Path(str(self.output) + ".manifest.json.INVALID").read_text())["status"], "INVALID")
        self.assertEqual(candidate.sha(self.base), self.initial)

    def test_swapped_partial_name_cannot_publish_unverified_inode(self):
        actual = os.link
        def swap_before_link(src, dst, *args, **kwargs):
            if Path(dst).name == self.output.name:
                partial = Path(str(self.output) + ".INVALID")
                partial.rename(self.root / "real-partial")
                partial.write_bytes(b"unverified replacement")
            return actual(src, dst, *args, **kwargs)
        with patch.object(os, "link", side_effect=swap_before_link):
            with self.assertRaisesRegex(ValueError, "identity"):
                candidate.execute(self.manifest, self.output, True)
        self.assertFalse(self.output.exists())
        self.assertFalse(Path(str(self.output) + ".manifest.json").exists())
        self.assertTrue(Path(str(self.output) + ".INVALID").exists())

    def test_report_write_failure_still_rolls_back(self):
        actual_link, actual_report = os.link, candidate.write_report
        publication_failed = False
        def fail_link(src, dst, *args, **kwargs):
            nonlocal publication_failed
            if Path(dst).name == self.output.name:
                publication_failed = True
                raise OSError("publication failure")
            return actual_link(src, dst, *args, **kwargs)
        def fail_report(f, data):
            if publication_failed:
                raise OSError("report I/O failure")
            return actual_report(f, data)
        with patch.object(os, "link", side_effect=fail_link), patch.object(
                candidate, "write_report", side_effect=fail_report):
            with self.assertRaisesRegex(OSError, "^publication failure$"):
                candidate.execute(self.manifest, self.output, True)
        self.assertFalse(self.output.exists())
        self.assertFalse(Path(str(self.output) + ".manifest.json").exists())
        self.assertTrue(Path(str(self.output) + ".INVALID").is_file())
        invalid = json.loads(Path(str(self.output) + ".manifest.json.INVALID").read_text())
        self.assertEqual(invalid["status"], "INVALID")
        self.assertNotIn("HOST_VERIFIED", json.dumps(invalid))

    def test_post_write_metadata_corruption_rejected(self):
        actual = candidate.debug
        for field, wrong in (("mode", "0100600"), ("uid", "9"), ("gid", "9"),
                             ("selinux", "u:object_r:wrong:s0")):
            with self.subTest(field=field):
                output = self.root / (field + ".img")
                def corrupt(fd, cwd, command, **kwargs):
                    if command.startswith(f"sif {self.dst} {field} "):
                        command = f"sif {self.dst} {field} {wrong}"
                    if field == "selinux" and command.startswith("ea_set -f"):
                        command = f"ea_set {self.dst} security.selinux {wrong}"
                    return actual(fd, cwd, command, **kwargs)
                with patch.object(candidate, "debug", side_effect=corrupt):
                    with self.assertRaisesRegex(ValueError, "Metadata mismatch|SELinux mismatch"):
                        candidate.execute(self.manifest, output, True)
                self.assertFalse(output.exists())
                self.assertTrue(Path(str(output) + ".INVALID").exists())
                self.assertEqual(json.loads(Path(str(output) + ".manifest.json.INVALID").read_text())["status"], "INVALID")
        self.assertEqual(candidate.sha(self.base), self.initial)

    def test_image_parent_symlink_and_hardlink_rejected(self):
        self.dbg("link " + self.dst + " /alias", write=True)
        self.dbg("sif " + self.dst + " links_count 2", write=True)
        self.data["base"]["sha256"] = candidate.sha(self.base)
        self.save()
        self.reject("hardlink")
        self.dbg("symlink /system /", write=True)
        self.data["replacements"][0]["path"] = "/system" + self.dst
        self.data["base"]["sha256"] = candidate.sha(self.base)
        self.save()
        self.reject("Non-directory parent")

    def test_interrupted_copy_retains_invalid_partial(self):
        actual = shutil.copyfileobj
        def interrupted(source, dest, *args, **kwargs):
            if str(dest.name).endswith(".INVALID"):
                dest.write(source.read(4096))
                raise OSError("injected copy interruption")
            return actual(source, dest, *args, **kwargs)
        with patch.object(shutil, "copyfileobj", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "copy interruption"):
                candidate.execute(self.manifest, self.output, True)
        self.assertFalse(self.output.exists())
        self.assertEqual(Path(str(self.output) + ".INVALID").stat().st_size, 4096)
        self.assertEqual(json.loads(Path(str(self.output) + ".manifest.json.INVALID").read_text())["status"], "INVALID")
        self.assertEqual(candidate.sha(self.base), self.initial)

    def test_cli_build_preserves_non_nul_label_and_two_replacements(self):
        (self.root / "label").write_bytes(self.label[:-1])
        self.dbg("ea_set -f label " + self.dst + " security.selinux", write=True)
        second = copy.deepcopy(self.data["replacements"][0])
        second["path"] = "/vendor/lib/second.so"
        for cmd in ("write old " + second["path"], "sif " + second["path"] + " mode 0100640",
                    "sif " + second["path"] + " uid 1234", "sif " + second["path"] + " gid 5678",
                    "ea_set -f label " + second["path"] + " security.selinux"):
            self.dbg(cmd, write=True)
        self.data["replacements"].append(second)
        self.data["base"]["sha256"] = candidate.sha(self.base)
        self.save()
        result = subprocess.run([sys.executable, str(SCRIPT), str(self.manifest), "--build",
                                 "--output", str(self.output)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["sha256"], candidate.sha(self.output))
        for r in self.data["replacements"]:
            self.dbg("dump " + r["path"] + " multi-readback", image=self.output)
            self.assertEqual((self.root / "multi-readback").read_bytes(), self.payload.read_bytes())
            self.dbg("ea_get -f multi-label " + r["path"] + " security.selinux", image=self.output)
            self.assertEqual((self.root / "multi-label").read_bytes(), self.label[:-1])
        self.assertEqual(candidate.sha(self.base), self.data["base"]["sha256"])

    def test_late_output_collision_preserves_other_owner(self):
        actual = os.link
        def collide(src, dst, *args, **kwargs):
            if Path(dst).name == self.output.name:
                self.output.write_bytes(b"concurrent-owner")
            return actual(src, dst, *args, **kwargs)
        with patch.object(os, "link", side_effect=collide):
            with self.assertRaises(FileExistsError):
                candidate.execute(self.manifest, self.output, True)
        self.assertEqual(self.output.read_bytes(), b"concurrent-owner")
        self.assertFalse(Path(str(self.output) + ".manifest.json").exists())
        self.assertEqual(json.loads(Path(str(self.output) + ".manifest.json.INVALID").read_text())["status"], "INVALID")

    def test_zero_exit_stdout_and_banner_errors_rejected(self):
        for stdout, stderr in (("write: allocation failed", ""),
                               ("", "debugfs 1.47.0 (5-Feb-2023) error: failure")):
            result = subprocess.CompletedProcess([], 0, stdout=stdout, stderr=stderr)
            with self.base.open("rb") as f, patch.object(candidate, "run", return_value=result):
                with self.assertRaises(ValueError):
                    candidate.debug(f.fileno(), self.root, "write payload0 " + self.dst, write=True)

    def test_flush_and_close_failures_remove_formal_names(self):
        actual = open
        for suffix, operation in (('.manifest.json', 'close'), ('.INVALID', 'close'),
                                  ('.manifest.json.INVALID', 'close'), ('.manifest.json', 'flush')):
            with self.subTest(suffix=suffix, operation=operation):
                output = self.root / (suffix.replace('.', '_') + operation + '.img')
                class FaultFile:
                    def __init__(self, f): self.f = f
                    def __getattr__(self, name): return getattr(self.f, name)
                    def __enter__(self): return self
                    def __exit__(self, *args): self.close()
                    def close(self):
                        self.f.close()
                        if operation == 'close': raise OSError('injected close failure')
                    def flush(self):
                        if operation == 'flush': raise OSError('injected flush failure')
                        self.f.flush()
                def failing_open(path, *args, **kwargs):
                    f = actual(path, *args, **kwargs)
                    return FaultFile(f) if str(path) == output.name + suffix else f
                with patch.object(candidate, 'open', side_effect=failing_open, create=True):
                    with self.assertRaisesRegex(OSError, 'injected ' + operation + ' failure'):
                        candidate.execute(self.manifest, output, True)
                self.assertFalse(output.exists())
                self.assertFalse(Path(str(output) + '.manifest.json').exists())
                invalid = json.loads(Path(str(output) + '.manifest.json.INVALID').read_text())
                self.assertEqual(invalid['status'], 'INVALID')

    def test_final_directory_fsync_failure_withdraws_both_names(self):
        actual, calls = os.fsync, 0
        def fail_final(fd):
            nonlocal calls
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                calls += 1
                if calls == 3: raise OSError('injected final fsync failure')
            return actual(fd)
        with patch.object(os, 'fsync', side_effect=fail_final):
            with self.assertRaisesRegex(OSError, 'final fsync failure'):
                candidate.execute(self.manifest, self.output, True)
        self.assertFalse(self.output.exists())
        self.assertFalse(Path(str(self.output) + '.manifest.json').exists())
        self.assertTrue(Path(str(self.output) + '.INVALID').is_file())
        self.assertEqual(json.loads(Path(str(self.output) + '.manifest.json.INVALID').read_text())['status'], 'INVALID')

    def test_recovery_link_enospc_cannot_leave_formal_image(self):
        actual_link, actual_unlink = os.link, os.unlink
        def fail_recovery(src, dst, *args, **kwargs):
            if Path(dst).name == self.output.name + ".INVALID":
                raise OSError(errno.ENOSPC, "injected recovery link ENOSPC")
            return actual_link(src, dst, *args, **kwargs)
        def fail_cleanup(path, *args, **kwargs):
            if Path(path).name == self.output.name + ".manifest.json.INVALID":
                raise OSError("injected marker cleanup failure")
            return actual_unlink(path, *args, **kwargs)
        with patch.object(os, "link", side_effect=fail_recovery), patch.object(
                os, "unlink", side_effect=fail_cleanup):
            with self.assertRaises(OSError):
                candidate.execute(self.manifest, self.output, True)
        self.assertFalse(self.output.exists())
        self.assertFalse(Path(str(self.output) + ".manifest.json").exists())
        self.assertEqual(json.loads(Path(str(self.output) + ".manifest.json.INVALID").read_text())["status"], "INVALID")

    def test_final_directory_close_failure_withdraws_both_names(self):
        actual, injected = os.close, False
        def fail_close(fd):
            nonlocal injected
            fail = not injected and self.output.exists() and stat.S_ISDIR(os.fstat(fd).st_mode)
            actual(fd)
            if fail:
                injected = True
                raise OSError('injected directory close failure')
        with patch.object(os, 'close', side_effect=fail_close):
            with self.assertRaisesRegex(OSError, 'directory close failure'):
                candidate.execute(self.manifest, self.output, True)
        self.assertFalse(self.output.exists())
        self.assertFalse(Path(str(self.output) + '.manifest.json').exists())
        self.assertEqual(json.loads(Path(str(self.output) + '.manifest.json.INVALID').read_text())['status'], 'INVALID')

    def test_non_raw_base_rejected(self):
        bad = self.root / "not-ext4"
        bad.write_bytes(b"ANDROID!" + bytes(4096))
        self.data["base"] = {"path": str(bad), "sha256": candidate.sha(bad)}
        self.save()
        self.reject("RAW ext")


if __name__ == "__main__":
    unittest.main()
