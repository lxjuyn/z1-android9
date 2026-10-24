import os
import subprocess
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts/z1_apply_wallpaper_system.py"


class WallpaperPackagingTests(unittest.TestCase):
    def test_png_is_screen_sized_rgb(self):
        import struct, zlib
        p = (ROOT / "graphics/wallpaper/z1_default.png").read_bytes()
        self.assertEqual(p[:8], b"\x89PNG\r\n\x1a\n")
        width, height, depth, color = struct.unpack_from(">IIBB", p, 16)
        self.assertEqual((width, height, depth, color), (240, 320, 8, 2))
        self.assertEqual(p[26], 0)
        self.assertEqual(p[27], 0)

    def test_existing_product_includes_exactly_once(self):
        mk = (ROOT / "device.mk").read_text()
        self.assertEqual(mk.count("graphics/wallpaper/default.mk"), 1)
        self.assertIn("ro.config.wallpaper=/system/etc/z1_wallpaper.png", (ROOT / "graphics/wallpaper/default.mk").read_text())

    def test_packager_fails_closed_for_wrong_input_and_existing_output(self):
        with tempfile.TemporaryDirectory(prefix="z1-wallpaper-gate-") as temp:
            d = Path(temp)
            (d / "candidate").mkdir()
            result = subprocess.run(["python3", str(TOOL), str(d), str(d / "candidate")], capture_output=True, text=True, cwd=ROOT)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("manifest.json", [p.name for p in (d / "candidate").iterdir()])


if __name__ == "__main__":
    unittest.main()
