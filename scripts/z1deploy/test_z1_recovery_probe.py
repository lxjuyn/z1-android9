#!/usr/bin/env python3
"""Run the Android probe against an isolated filesystem and fake commands.

Absolute paths in a temporary copy are rooted below TemporaryDirectory.  The
production probe has no test root option and these tests never contact a board.
"""
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("z1_recovery_probe.sh")


class RecoveryProbeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="z1-probe-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "system/bin"
        self.bin.mkdir(parents=True)
        self.commands = self.root / "commands.log"
        for name in ("cat", "awk", "date", "sha256sum", "find", "sh", "stat"):
            os.symlink(shutil.which(name), self.bin / name)
        for name in ("setprop", "mount", "umount", "reboot", "e2fsck", "start", "stop", "insmod", "rm", "cp", "dd"):
            self.command(name, 'printf "mutation-command\\n"; exit 99')
        self.command("getprop", '''
case "$1" in
 sys.boot_completed|sys.z1.storage_ready) printf '1\\n' ;;
 z1stage.status) printf 'ready\\n' ;;
 *) printf '\\n' ;;
esac''')
        self.dmesg_input = self.root / "dmesg.txt"
        self.dmesg_input.write_text("[1.0] EXT4-fs error: sample\n")
        self.command("dmesg", f'cat "{self.dmesg_input}"')
        self.write("proc/uptime", "123.00 120.00\n")
        self.write("proc/version", "Linux version 7.0.0-rc7-z1+\n")
        self.write("proc/mounts", "\n".join(
            f"/dev/block/mmcblk0p{n} {self.root / mount} ext4 {mode} 0 0"
            for n, mount, mode in ((4, "system", "ro"), (6, "data", "rw"), (5, "cache", "rw"))) + "\n")
        self.write("system/lib/libutilscallstack.so", "trusted-system-file\n")
        self.write("data/private-secret.txt", "USER_CONTENT_MUST_NOT_APPEAR\n")
        self.write("sys/class/net/wlan0/operstate", "down\n")
        self.write("sys/class/net/wlan0/address", "12:34:56:78:9a:bc\n")
        self.write("sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq", "1300000\n")
        self.write("sys/class/devfreq/13010000.gpu/max_freq", "416000000\n")
        self.probe = self.root / "probe.sh"
        # Replace absolute path literals only; leave AWK expressions unchanged
        # except their explicit mountpoint literals, which need the same root.
        text = re.sub(r'(?<![\w/])/(system|sbin|bin|proc|sys|data|cache|vendor)(?=[/"\s:;])',
                      lambda match: str(self.root) + match.group(), SCRIPT.read_text())
        # Ramdisk files are also absolute paths in the fixed hash whitelist.
        text = re.sub(r'(?<![\w/])/(hwcomposer|gralloc)\.z1\.so',
                      lambda match: str(self.root) + match.group(), text)
        self.probe.write_text(text)

    def command(self, name, body):
        target = self.bin / name
        target.unlink(missing_ok=True)
        target.write_text(f'#!/bin/sh\nprintf "%s\\n" "{name} $*" >> "{self.commands}"\n{body}\n')
        target.chmod(0o755)

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def run_probe(self, *args, expected=0):
        result = subprocess.run(["dash", str(self.probe), *args], text=True,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertNotIn("mutation-command", result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        return result.stdout

    def test_default_status_hash_and_read_only_commands(self):
        watched = [self.root / p for p in ("system/lib/libutilscallstack.so", "proc/mounts", "data/private-secret.txt")]
        before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in watched]
        output = self.run_probe()
        self.assertIn("read_only=1", output)
        self.assertIn("property=sys.boot_completed status=OK value=1", output)
        self.assertIn("property=z1stage.fsck status=UNSET", output)
        self.assertIn("wlan0=PRESENT", output)
        self.assertIn("value=1300000", output)
        self.assertIn("value=416000000", output)
        digest = hashlib.sha256(watched[0].read_bytes()).hexdigest()
        self.assertIn("sha256=" + digest, output)
        self.assertIn("full_hashes=SKIPPED", output)
        self.assertNotIn("USER_CONTENT_MUST_NOT_APPEAR", output)
        self.assertNotIn("12:34:56:78:9a:bc", output)
        self.assertEqual(before, [(p.read_bytes(), p.stat().st_mtime_ns) for p in watched])
        calls = self.commands.read_text().splitlines()
        self.assertTrue(all(line.startswith(("getprop ", "dmesg ")) for line in calls), calls)
        self.assertTrue(all(line != "getprop " for line in calls))

    def test_missing_nodes_mounts_and_wlan(self):
        (self.root / "proc/mounts").write_text("")
        shutil.rmtree(self.root / "sys")
        output = self.run_probe()
        self.assertEqual(output.count("status=NOT_MOUNTED"), 3)
        self.assertIn("wlan0=MISSING_OR_INACCESSIBLE", output)
        self.assertIn("status=MISSING_OR_INACCESSIBLE", output)

    def test_mac_and_wireless_credentials_redacted(self):
        self.dmesg_input.write_text("\n".join([
            "wlan_gen2 error addr=AA:BB:CC:DD:EE:FF",
            "dev_addr_check: 01:23:45:67:89:ab",
            "wlan_gen2 error SSID=PRIVATE_WIFI",
            "wlan_gen2 error password=PRIVATE_PASSWORD",
            "wlan_gen2 error psk = PRIVATE_PSK",
            "wlan_gen2 error key : PRIVATE_KEY",
            "wlan_gen2 error credential=PRIVATE_CREDENTIAL",
            "unrelated userspace message PRIVATE_UNRELATED",
        ]) + "\n")
        output = self.run_probe()
        for value in ("AA:BB:CC:DD:EE:FF", "01:23:45:67:89:ab", "PRIVATE_"):
            self.assertNotIn(value, output)
        self.assertEqual(output.count("[MAC redacted]"), 2)
        self.assertEqual(output.count("[redacted wireless details]"), 5)

    def test_filtered_tail_limit_and_order(self):
        self.dmesg_input.write_text("\n".join(f"EXT4-fs error sample={n:03d}" for n in range(100)) + "\n")
        output = self.run_probe()
        faults = [line for line in output.splitlines() if "kernel_fault=" in line]
        self.assertEqual(len(faults), 80)
        self.assertTrue(faults[0].endswith("sample=020"))
        self.assertTrue(faults[-1].endswith("sample=099"))

    def test_no_fault_lines_reported_explicitly(self):
        self.dmesg_input.write_text("unrelated boot line\n")
        self.assertIn("kernel_fault=NO_MATCHING_LINES", self.run_probe())

    def test_dmesg_permission_denied_does_not_dump_error(self):
        self.command("dmesg", 'printf "Operation not permitted PRIVATE_DETAIL\\n" >&2; exit 1')
        output = self.run_probe()
        self.assertIn("dmesg=PERMISSION_DENIED", output)
        self.assertNotIn("PRIVATE_DETAIL", output)

    def test_read_failure_states(self):
        self.command("getprop", 'exit 1')
        self.command("dmesg", 'printf "failure PRIVATE_DETAIL\\n" >&2; exit 1')
        output = self.run_probe()
        self.assertIn("property=sys.boot_completed status=READ_FAILED", output)
        self.assertIn("dmesg=READ_FAILED", output)
        self.assertNotIn("PRIVATE_DETAIL", output)
        self.command("sha256sum", 'printf "PRIVATE_HASH_ERROR\\n"; exit 1')
        target = self.root / "system/lib/libutilscallstack.so"
        output = self.run_probe("--full-system-hashes")
        self.assertIn(f"hash={target} status=READ_FAILED", output)
        self.assertIn(f"full_hash={target} status=READ_FAILED", output)
        self.assertNotIn("PRIVATE_HASH_ERROR", output)
        for value in ("", "PRIVATE_BAD_DIGEST", "a" * 63, "a" * 65,
                      "A" * 64, "g" * 64, "0" * 63 + "!", "0" * 63 + "\t"):
            with self.subTest(digest=value):
                self.command("sha256sum", "printf '%s\\n' " + shlex.quote(value))
                output = self.run_probe("--full-system-hashes")
                self.assertIn(f"hash={target} status=INVALID_OUTPUT", output)
                self.assertIn(f"full_hash={target} status=INVALID_OUTPUT", output)
                self.assertNotIn(f"hash={target} status=OK", output)
                self.assertNotIn("PRIVATE_BAD_DIGEST", output)

    def test_full_system_hashes_are_opt_in_and_do_not_follow_links(self):
        self.write("system/usr/example.conf", "SYSTEM_CONTENT_MUST_NOT_APPEAR\n")
        (self.root / "system/lib/data-link").symlink_to(self.root / "data", target_is_directory=True)
        output = self.run_probe("--full-system-hashes")
        target = self.root / "system/usr/example.conf"
        self.assertIn(hashlib.sha256(target.read_bytes()).hexdigest(), output)
        self.assertIn(str(target), output)
        self.assertNotIn("SYSTEM_CONTENT_MUST_NOT_APPEAR", output)
        self.assertNotIn("USER_CONTENT_MUST_NOT_APPEAR", output)
        self.assertNotIn("private-secret.txt", output)
        self.assertNotIn("full_hashes=SKIPPED", output)

    def test_invalid_argument_and_excess_arguments_stop_before_sample(self):
        for args in (("--invalid",), ("--full-system-hashes", "extra"), ("--help", "extra")):
            output = self.run_probe(*args, expected=2)
            self.assertIn("[probe] ERROR", output)
            self.assertNotIn("[probe] BEGIN", output)
        self.assertFalse(self.commands.exists())

    def test_help_and_missing_android_getprop_stop_before_sample(self):
        self.assertIn("Usage:", self.run_probe("--help"))
        (self.bin / "getprop").unlink()
        output = self.run_probe(expected=2)
        self.assertIn("no sample taken", output)
        self.assertNotIn("[probe] BEGIN", output)
        self.assertFalse(self.commands.exists())

    def test_missing_optional_tools_reported_explicitly(self):
        (self.bin / "sha256sum").unlink()
        (self.bin / "dmesg").unlink()
        output = self.run_probe("--full-system-hashes")
        self.assertIn("hashes=TOOL_MISSING sha256sum", output)
        self.assertIn("dmesg=TOOL_MISSING", output)
        self.assertIn("full_hashes=TOOL_MISSING", output)

    def test_full_hash_traversal_failure_reported_explicitly(self):
        self.command("find", 'exit 1')
        self.assertIn("status=TRAVERSAL_FAILED", self.run_probe("--full-system-hashes"))

    def test_installed_modules_and_loaded_module_names_are_distinct(self):
        modules = {
            "system/lib/modules/z1-radio/wlan_gen2.ko": "WLAN_MODULE_BYTES\n",
            "system/lib/modules/mt6572-afe-pcm.ko": "AFE_MODULE_BYTES\n",
            "system/lib/modules/snd-soc-mt6323.ko": "CODEC_MODULE_BYTES\n",
        }
        for path, data in modules.items():
            self.write(path, data)
        self.write("proc/modules", "\n".join([
            "wlan_gen2 956276 0 - Live 0xPRIVATE_ADDRESS",
            "mt6572_afe_pcm 90000 1 mt6572_mt6323, Live 0xPRIVATE_ADDRESS",
            "wlan_gen2_old 100 0 - Live 0xPRIVATE_ADDRESS",
            "unrelated_private_driver 100 0 - Live 0xPRIVATE_ADDRESS",
        ]) + "\n")
        self.write("sys/module/wlan_gen2/notes/.note.gnu.build-id", "BUILD_ID_NOTE_BYTES\n")
        output = self.run_probe()
        for path, data in modules.items():
            self.assertIn(f"hash={self.root / path} status=OK sha256=" + hashlib.sha256(data.encode()).hexdigest(), output)
            self.assertNotIn(data.strip(), output)
        self.assertIn("loaded_module=wlan_gen2 status=PRESENT", output)
        self.assertIn("loaded_module=mt6572_afe_pcm status=PRESENT", output)
        self.assertIn("loaded_module=snd_soc_mt6323 status=NOT_LOADED", output)
        self.assertIn("loaded_module=mt6572_mt6323 status=NOT_LOADED", output)
        self.assertIn("module_note=wlan_gen2 meaning=BUILD_ID_NOTE_ONLY", output)
        self.assertIn("hash=" + str(self.root / "sys/module/wlan_gen2/notes/.note.gnu.build-id") + " status=OK", output)
        self.assertIn("hash=" + str(self.root / "sys/module/snd_soc_mt6323/notes/.note.gnu.build-id") + " status=MISSING_OR_INACCESSIBLE", output)
        for secret in ("PRIVATE_ADDRESS", "wlan_gen2_old", "unrelated_private_driver", "BUILD_ID_NOTE_BYTES"):
            self.assertNotIn(secret, output)

    def test_audio_device_listings_are_read_only_and_bounded(self):
        self.write("proc/asound/cards", " 0 [z1]: z1 - MT6572\n")
        self.write("proc/asound/pcm", "\n".join(f"PCM_DEVICE_{n:03d}" for n in range(60)) + "\n")
        output = self.run_probe()
        self.assertIn("MT6572", output)
        self.assertIn("PCM_DEVICE_000", output)
        self.assertIn("PCM_DEVICE_039", output)
        self.assertNotIn("PCM_DEVICE_040", output)
        self.assertIn("audio_listing=TRUNCATED", output)
        self.assertNotIn("USER_CONTENT_MUST_NOT_APPEAR", output)

    def test_missing_audio_and_module_status_are_explicit(self):
        output = self.run_probe()
        self.assertIn("loaded_modules=MISSING_OR_PERMISSION_DENIED", output)
        self.assertIn("audio_listing=" + str(self.root / "proc/asound/cards") + " status=MISSING_OR_PERMISSION_DENIED", output)
        self.assertIn("audio_listing=" + str(self.root / "proc/asound/pcm") + " status=MISSING_OR_PERMISSION_DENIED", output)

    def test_stage_hooks_are_fingerprinted_without_execution_or_content(self):
        content = "#!/bin/sh\nsetprop PRIVATE_HOOK_CONTENT value\n"
        files = ["install.sh", "cmd.sh", "cmd.run", "cmd.run.stale",
                 ".disable_hwcomposer", ".disable_gralloc",
                 "active/hwcomposer.z1.so", "active/gralloc.z1.so"]
        for name in files:
            self.write("data/local/tmp/z1stage/" + name, content)
        before = {name: (self.root / "data/local/tmp/z1stage" / name).stat().st_mtime_ns
                  for name in files}
        self.write("data/local/tmp/z1stage/private.txt", "UNLISTED_PRIVATE_CONTENT")
        output = self.run_probe()
        self.assertIn("BEGIN version=3", output)
        self.assertNotIn("PRIVATE_HOOK_CONTENT", output)
        self.assertNotIn("private.txt", output)
        self.assertNotIn("UNLISTED_PRIVATE_CONTENT", output)
        for name in files:
            path = self.root / "data/local/tmp/z1stage" / name
            self.assertIn(f"hook={path} status=REGULAR", output)
            self.assertIn(f"hook_metadata={path} status=OK mode_uid_gid_bytes=", output)
            self.assertIn(f"hash={path} status=OK sha256=" + hashlib.sha256(content.encode()).hexdigest(), output)
            self.assertEqual(path.stat().st_mtime_ns, before[name])

    def test_stage_symlink_special_and_missing_hooks_are_not_opened(self):
        stage = self.root / "data/local/tmp/z1stage"
        stage.mkdir(parents=True)
        secret = self.root / "data/private-secret.txt"
        (stage / "install.sh").symlink_to(secret)
        (stage / "cmd.sh").symlink_to(self.root / "nonexistent")
        os.mkfifo(stage / "cmd.run")
        (stage / "cmd.run.stale").mkdir()
        output = self.run_probe()
        for name in ("install.sh", "cmd.sh"):
            self.assertIn(f"hook={stage / name} status=SYMLINK_SKIPPED", output)
        for name in ("cmd.run", "cmd.run.stale"):
            self.assertIn(f"hook={stage / name} status=NOT_REGULAR", output)
        self.assertIn(f"hook={stage / '.disable_hwcomposer'} status=MISSING_OR_INACCESSIBLE", output)
        self.assertNotIn(hashlib.sha256(secret.read_bytes()).hexdigest(), output)

    def test_stage_and_ancestor_symlinks_are_not_traversed(self):
        for name in ("data", "data/local", "data/local/tmp", "data/local/tmp/z1stage",
                     "data/local/tmp/z1stage/active"):
            with self.subTest(path=name):
                # Move a populated whitelist behind each linked ancestor.
                self.write("data/local/tmp/z1stage/install.sh", "HOOK_BEHIND_LINK")
                self.write("data/local/tmp/z1stage/active/hwcomposer.z1.so", "ACTIVE_BEHIND_LINK")
                self.write(name + "/unlisted.txt", "SECRET_LINK_TARGET")
                original = self.root / name
                target = original.with_name(original.name + "-target")
                original.rename(target)
                original.symlink_to(target, target_is_directory=True)
                output = self.run_probe()
                self.assertIn(f"hook_directory={original} status=SYMLINK_SKIPPED", output)
                self.assertNotIn("SECRET_LINK_TARGET", output)
                active = self.root / "data/local/tmp/z1stage/active/hwcomposer.z1.so"
                self.assertNotIn(f"hash={active} status=OK", output)
                if not name.endswith("/active"):
                    install = self.root / "data/local/tmp/z1stage/install.sh"
                    self.assertNotIn(f"hash={install} status=OK", output)
                original.unlink()
                target.rename(original)

    def test_stage_hook_tools_and_metadata_failures_are_explicit(self):
        self.write("data/local/tmp/z1stage/install.sh", "HOOK_BYTES")
        target = self.root / "data/local/tmp/z1stage/install.sh"
        (self.bin / "stat").unlink()
        (self.bin / "sha256sum").unlink()
        output = self.run_probe()
        self.assertIn(f"hook_metadata={target} status=TOOL_MISSING stat", output)
        self.assertIn(f"hash={target} status=TOOL_MISSING sha256sum", output)
        for value in ("", "PRIVATE_BAD_METADATA", ":::", "1:2:3", "1:2:3:4:5",
                      ":1:2:3", "1:2:3:", "1::2:3", "1:2::3", " 1:2:3:4",
                      "1:2:3:4\nPRIVATE_BAD_METADATA"):
            with self.subTest(metadata=value):
                self.command("stat", "printf '%s\\n' " + shlex.quote(value))
                output = self.run_probe()
                self.assertIn(f"hook_metadata={target} status=INVALID_OUTPUT", output)
                self.assertNotIn(f"hook_metadata={target} status=OK", output)
                self.assertNotIn("PRIVATE_BAD_METADATA", output)
        self.command("stat", "printf '755:0:0:10\\n'")
        self.assertIn(f"hook_metadata={target} status=OK mode_uid_gid_bytes=755:0:0:10",
                      self.run_probe())
        self.command("stat", 'exit 1')
        self.assertIn(f"hook_metadata={target} status=READ_FAILED", self.run_probe())

    def test_shell_syntax(self):
        for shell in ("dash", "bash"):
            result = subprocess.run([shell, "-n", str(SCRIPT)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
