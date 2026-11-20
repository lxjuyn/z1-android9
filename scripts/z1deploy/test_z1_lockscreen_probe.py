#!/usr/bin/env python3
from pathlib import Path
import os
import subprocess
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "z1_lockscreen_probe.sh"


class LockscreenProbeTest(unittest.TestCase):
    def fixture(self, stuck=False):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        led = root / "sys/class/leds/lcd-backlight"
        led.mkdir(parents=True)
        (led / "display_blank").write_text("0\n")
        (led / "brightness").write_text("2\n")
        (root / "awake").write_text("Awake\n")
        fake = root / "bin"
        fake.mkdir()

        commands = {
            "input": """#!/bin/sh
if [ "$1" = keyevent ] && [ "$2" = 26 ] && [ "${FAKE_STUCK:-0}" != 1 ]; then
    if [ "$(cat "$Z1_ROOT/sys/class/leds/lcd-backlight/display_blank")" = 0 ]; then
        echo 1 > "$Z1_ROOT/sys/class/leds/lcd-backlight/display_blank"
        echo Asleep > "$Z1_ROOT/awake"
    else
        echo 0 > "$Z1_ROOT/sys/class/leds/lcd-backlight/display_blank"
        echo Awake > "$Z1_ROOT/awake"
    fi
elif [ "$1" = keyevent ] && [ "$2" = 224 ]; then
    echo 0 > "$Z1_ROOT/sys/class/leds/lcd-backlight/display_blank"
    echo Awake > "$Z1_ROOT/awake"
fi
""",
            "dumpsys": """#!/bin/sh
echo "  mWakefulness=$(cat "$Z1_ROOT/awake")"
""",
            "settings": """#!/bin/sh
case "$1 $2 $3" in
    "get system screen_off_timeout") echo 7200000 ;;
    "get global stay_on_while_plugged_in") echo 7 ;;
    put*) echo "$*" >> "$Z1_ROOT/settings.log" ;;
esac
""",
            "wm": "#!/bin/sh\nexit 0\n",
            "sleep": "#!/bin/sh\nexit 0\n",
        }
        for name, body in commands.items():
            path = fake / name
            path.write_text(body)
            path.chmod(0o755)

        env = os.environ.copy()
        env["PATH"] = str(fake) + os.pathsep + env["PATH"]
        env["Z1_ROOT"] = str(root)
        env["Z1_LOCK_TIMEOUT"] = "2"
        env["FAKE_STUCK"] = "1" if stuck else "0"
        return root, env

    def test_script_is_android_shell_syntax(self):
        result = subprocess.run(
            ["sh", "-n", str(SCRIPT)], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_lock_wake_cycles_pass_and_restore_settings(self):
        root, env = self.fixture()
        result = subprocess.run(
            ["sh", str(SCRIPT), "2"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("summary pass=4 fail=0 status=PASS", result.stdout)
        self.assertIn("physical_blank_confirmation=MANUAL_REQUIRED", result.stdout)
        settings = (root / "settings.log").read_text()
        self.assertIn("put system screen_off_timeout 7200000", settings)
        self.assertIn("put global stay_on_while_plugged_in 7", settings)

    def test_stuck_blank_gate_fails(self):
        _, env = self.fixture(stuck=True)
        result = subprocess.run(
            ["sh", str(SCRIPT), "1"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("reason=display_blank_did_not_become_1", result.stdout)
        self.assertIn("summary pass=0 fail=1 status=FAIL", result.stdout)

    def test_zero_cycles_are_rejected(self):
        _, env = self.fixture()
        result = subprocess.run(
            ["sh", str(SCRIPT), "0"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("positive-cycle-count", result.stderr)


if __name__ == "__main__":
    unittest.main()
