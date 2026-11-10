"""Exercise make copy selection and R5b copying without opening any image."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
R5B = ROOT / "scripts/r5b_fixes/apply_r5b_system_fixes.sh"
STOCK_LAYOUTS = {"mtk-kpd.kl", "mtk-tpd.kl"}
MAINLINE_LAYOUTS = {"mt6779-keypad.kl", "z1_cst836u.kl"}
MATRIX_KEYS = {
    114: "VOLUME_DOWN", 251: "VOLUME_DOWN", 250: "VOLUME_UP",
    115: "VOLUME_UP", 252: "VOLUME_UP", 106: "DPAD_RIGHT",
    232: "DPAD_CENTER", 105: "DPAD_LEFT", 103: "DPAD_UP",
    158: "BACK", 108: "DPAD_DOWN", 254: "VOLUME_DOWN",
}
TOUCH_KEYS = {158: "BACK", 102: "HOME", 139: "APP_SWITCH"}


def key_entries(content):
    entries = {}
    for line in content.splitlines():
        fields = line.split("#", 1)[0].split()
        if not fields:
            continue
        if len(fields) < 3 or fields[0] != "key":
            raise AssertionError(f"Unexpected keylayout entry: {line}")
        code = int(fields[1])
        if code in entries:
            raise AssertionError(f"Duplicate keycode: {code}")
        entries[code] = (fields[2], fields[3:])
    return entries


def clean_env():
    env = os.environ.copy()
    for name in ("MAKEFLAGS", "MFLAGS", "MAKEFILES", "GNUMAKEFLAGS", "BASH_ENV", "ENV"):
        env.pop(name, None)
    return env


class KeylayoutPackagingTests(unittest.TestCase):
    def resolve_and_copy(self, variant):
        """Evaluate the real device.mk, then stage its keylayout copy pairs."""
        with tempfile.TemporaryDirectory(prefix="z1-keylayout-make-") as temp:
            work = Path(temp)
            device = work / "device/jiabo/z1"
            device.parent.mkdir(parents=True)
            device.symlink_to(ROOT, target_is_directory=True)
            # device.mk requires a stock guard to exist; it is never executed.
            (work / "stock-guard").write_text("test fixture; not executable\n")
            (work / "copy.mk").write_text(
                "TARGET_COPY_OUT_SYSTEM := system\n"
                "TARGET_COPY_OUT_VENDOR := system/vendor\n"
                "LOCAL_PATH := device/jiabo/z1\n"
                "Z1_STOCK_MOUNT_GUARD := stock-guard\n"
                "include device/jiabo/z1/device.mk\n"
                "$(foreach pair,$(PRODUCT_COPY_FILES),$(info COPY $(pair)))\n"
                ".PHONY: print-copies\n"
                "print-copies:\n\t@:\n"
            )
            result = subprocess.run(
                ["make", "-rR", "--no-print-directory", "-f", "copy.mk",
                 "print-copies", f"Z1_KERNEL_VARIANT={variant}"],
                cwd=work, env=clean_env(), capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            copied = {}
            for line in result.stdout.splitlines():
                if not line.startswith("COPY "):
                    continue
                source, destination = line[5:].split(":", 1)
                if "/usr/keylayout/" not in destination:
                    continue
                self.assertNotIn(destination, copied, "Duplicate keylayout destination")
                name = Path(destination).name
                self.assertEqual(destination, f"system/usr/keylayout/{name}")
                self.assertEqual(source, f"device/jiabo/z1/keylayout/{name}")
                target = work / "staged" / destination
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(work / source, target)
                copied[destination] = target.read_bytes()
                self.assertEqual(copied[destination], (ROOT / "keylayout" / name).read_bytes())
            return {Path(destination).name: data for destination, data in copied.items()}

    def test_mainline_make_packages_canonical_matrix_and_touch_layouts(self):
        for variant in ("linux7", ""):
            with self.subTest(variant=variant):
                copied = self.resolve_and_copy(variant)
                self.assertEqual(set(copied), STOCK_LAYOUTS | MAINLINE_LAYOUTS)
                self.assertEqual(
                    key_entries(copied["mt6779-keypad.kl"].decode()),
                    {code: (action, []) for code, action in MATRIX_KEYS.items()},
                )
                self.assertEqual(
                    key_entries(copied["z1_cst836u.kl"].decode()),
                    {code: (action, []) for code, action in TOUCH_KEYS.items()},
                )

    def test_stock_make_keeps_factory_layouts_without_mainline_layouts(self):
        copied = self.resolve_and_copy("stock")
        self.assertEqual(set(copied), STOCK_LAYOUTS)
        # All bytes are compared to their canonical sources by the copy helper.
        # These checks also protect the existing physical directions/F-keys.
        entries = key_entries(copied["mtk-kpd.kl"].decode())
        for code, action in {
            103: "DPAD_UP", 108: "DPAD_DOWN", 105: "DPAD_LEFT",
            106: "DPAD_RIGHT", 232: "DPAD_CENTER", 139: "APP_SWITCH",
            250: "F1", 251: "F2", 252: "F3", 253: "F4", 254: "F5", 255: "F6",
        }.items():
            self.assertEqual(entries[code][0], action)
        self.assertEqual(
            key_entries(copied["mtk-tpd.kl"].decode()),
            {code: (action, []) for code, action in TOUCH_KEYS.items()},
        )

    def test_r5b_shell_syntax(self):
        result = subprocess.run(
            ["bash", "-n", str(R5B)], env=clean_env(),
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_r5b_copies_canonical_matrix_from_any_working_directory(self):
        with tempfile.TemporaryDirectory(prefix="z1-keylayout-r5b-") as temp:
            work = Path(temp)
            mock_bin = work / "bin"
            mock_bin.mkdir()
            log = work / "calls.jsonl"
            staged = work / "staged"
            # Run the actual shell script with cp/debugfs replaced. No image is
            # created, opened or modified. Only keylayout bytes are staged.
            mock = mock_bin / "mock-tool"
            mock.write_text(f"#!{sys.executable}\n" + '''
import json
import os
from pathlib import Path
import shutil
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
record = {"tool": name, "args": args}
if name == "cp":
    if args != ["-a", os.environ["TEST_INPUT"], os.environ["TEST_OUTPUT"]]:
        raise SystemExit("Unexpected copy command")
elif name == "debugfs":
    if len(args) != 4 or args[:2] != ["-w", "-R"] or args[3] != os.environ["TEST_OUTPUT"]:
        raise SystemExit("Unexpected debugfs command")
    command = args[2].split()
    if command[0] == "write" and command[-1].startswith("/usr/keylayout/"):
        if len(command) != 3:
            raise SystemExit("Unexpected keylayout write")
        source, destination = Path(command[1]), Path(command[2])
        if destination.parent != Path("/usr/keylayout"):
            raise SystemExit("Unexpected keylayout destination")
        target = Path(os.environ["TEST_STAGED"]) / destination.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        record.update(source=str(source.resolve()), destination=str(destination))
else:
    raise SystemExit("Unexpected mock command")
with open(os.environ["TEST_LOG"], "a") as stream:
    stream.write(json.dumps(record) + "\\n")
''')
            mock.chmod(0o755)
            for name in ("cp", "debugfs"):
                (mock_bin / name).symlink_to(mock)
            input_path = work / "nonexistent-input.img"
            output_path = work / "nonexistent-output.img"
            env = clean_env()
            env.update(
                PATH=str(mock_bin) + os.pathsep + os.defpath,
                TEST_INPUT=str(input_path), TEST_OUTPUT=str(output_path),
                TEST_LOG=str(log), TEST_STAGED=str(staged),
            )
            for cwd in (ROOT, work):
                with self.subTest(cwd=str(cwd)):
                    log.write_text("")
                    result = subprocess.run(
                        ["bash", str(R5B), str(input_path), str(output_path)],
                        cwd=cwd, env=env, capture_output=True, text=True, timeout=20,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertFalse(input_path.exists())
                    self.assertFalse(output_path.exists())
                    calls = [json.loads(line) for line in log.read_text().splitlines()]
                    writes = [call for call in calls if "source" in call]
                    self.assertEqual(len(writes), 1)
                    self.assertEqual(
                        writes[0]["source"], str(ROOT / "keylayout/mt6779-keypad.kl")
                    )
                    self.assertEqual(writes[0]["destination"], "/usr/keylayout/mt6779-keypad.kl")
                    copied = (staged / "mt6779-keypad.kl").read_bytes()
                    self.assertEqual(copied, (ROOT / "keylayout/mt6779-keypad.kl").read_bytes())
                    self.assertEqual(
                        key_entries(copied.decode()),
                        {code: (action, []) for code, action in MATRIX_KEYS.items()},
                    )
                    self.assertTrue(any(
                        call["tool"] == "debugfs" and call["args"][2]
                        == "sif /usr/keylayout/mt6779-keypad.kl mode 0100644"
                        for call in calls
                    ), "The copied keylayout must retain regular-file mode 0644")


if __name__ == "__main__":
    unittest.main()
