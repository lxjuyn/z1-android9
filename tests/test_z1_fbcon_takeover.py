import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "stock/z1_fbcon_takeover.sh"


class MainlineDisplayTakeoverTests(unittest.TestCase):
    def test_mainline_profile_copies_both_scripts_to_ramdisk_root(self):
        makefile = (ROOT / "device.mk").read_text()
        mainline = makefile.split("else\nPRODUCT_COPY_FILES += \\", 1)[1]
        mainline = mainline.split("endif", 1)[0]
        for script in ("z1_display_takeover.sh", "z1_fbcon_takeover.sh"):
            self.assertIn(
                f"$(DEVICE_PATH)/stock/{script}:root/{script}", mainline)

    def test_mainline_runs_synchronously_after_system_mount_before_boot(self):
        init_rc = (ROOT / "rootdir/init.z1.rc").read_text()
        fs_action = init_rc.split("on fs\n", 1)[1].split("\non ", 1)[0]
        self.assertIn("mount_all /fstab.z1", fs_action)
        post_fs_data = init_rc.split("on post-fs-data\n", 1)[1]
        # Pie triggers boot after post-fs-data; boot starts core/main services,
        # including SurfaceFlinger. Unlike a service or exec_background, exec
        # completes this takeover before the later class starts.
        self.assertEqual(post_fs_data.count(
            "    exec u:r:init:s0 root root -- "
            "/system/bin/sh /z1_display_takeover.sh\n"), 1)

    def test_existing_wrapper_uses_graphics_mode_then_fbcon_fallback(self):
        display_script = (ROOT / "stock/z1_display_takeover.sh").read_text()
        graphics_mode = "/z1_bootdiag --graphics-mode"
        fbcon_fallback = "/system/bin/sh /z1_fbcon_takeover.sh"
        self.assertIn(graphics_mode, display_script)
        self.assertIn(fbcon_fallback, display_script)
        self.assertLess(display_script.index(graphics_mode),
                        display_script.index(fbcon_fallback))
        helper_source = (ROOT / "tools/z1_android9_bootdiag.c").read_text()
        self.assertIn(
            'if (argc == 2 && !strcmp(argv[1], "--graphics-mode")) '
            'return graphics_mode();', helper_source)


class FbconTakeoverTests(unittest.TestCase):
    def add_console(self, root, index, name, bound):
        node = root / f"vtcon{index}"
        node.mkdir()
        (node / "name").write_text(name + "\n")
        (node / "bind").write_text(str(bound) + "\n")
        return node

    def run_helper(self, root):
        env = os.environ.copy()
        env["Z1_VTCONSOLE_ROOT"] = str(root)
        return subprocess.run(["/bin/sh", str(SCRIPT)], env=env,
                              capture_output=True, text=True)

    def test_unbinds_only_framebuffer_console_and_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="z1-vtconsole-") as temp:
            root = Path(temp)
            dummy = self.add_console(root, 0, "dummy device", 1)
            fbcon = self.add_console(root, 1, "frame buffer device", 1)
            serial = self.add_console(root, 2, "serial device", 1)
            second_fbcon = self.add_console(root, 3, "frame buffer device", 1)

            first = self.run_helper(root)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual((fbcon / "bind").read_text().strip(), "0")
            self.assertEqual((second_fbcon / "bind").read_text().strip(), "0")
            self.assertEqual((dummy / "bind").read_text().strip(), "1")
            self.assertEqual((serial / "bind").read_text().strip(), "1")

            second = self.run_helper(root)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual((fbcon / "bind").read_text().strip(), "0")
            self.assertEqual((second_fbcon / "bind").read_text().strip(), "0")

    def test_no_framebuffer_console_is_a_safe_noop(self):
        with tempfile.TemporaryDirectory(prefix="z1-vtconsole-") as temp:
            root = Path(temp)
            dummy = self.add_console(root, 0, "dummy device", 1)
            result = self.run_helper(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((dummy / "bind").read_text().strip(), "1")

    def test_rejects_unrecognized_bind_state(self):
        with tempfile.TemporaryDirectory(prefix="z1-vtconsole-") as temp:
            root = Path(temp)
            fbcon = self.add_console(root, 1, "frame buffer device", 2)
            result = self.run_helper(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((fbcon / "bind").read_text().strip(), "2")


if __name__ == "__main__":
    unittest.main()
