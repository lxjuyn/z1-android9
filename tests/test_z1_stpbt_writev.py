import hashlib
from pathlib import Path
import shutil
import subprocess
import unittest


HERE = Path(__file__).resolve().parent
REPO = HERE.parent
WORKSPACE = HERE.parents[1]
PATCH = (
    REPO
    / "scripts/btgps/bt/patches/0002-stpbt-coalesce-h4-writev.patch"
)
PACKAGES = sorted(WORKSPACE.glob("out/r7_bt_writev_transport_*"))
PACKAGE = PACKAGES[-1] if PACKAGES else None
MODULE = PACKAGE / "mtk_stp_bt_soc.ko" if PACKAGE else None
EXPECTED_MODULE_SHA256 = (
    "af2f849794e26272716dbc79ba7239803a05d1ecbce53e55eb98f60c3c76203f"
)


class StpbtWritevPatchTest(unittest.TestCase):
    def test_patch_combines_h4_iovecs_in_one_stp_message(self):
        text = PATCH.read_text()
        added = [line for line in text.splitlines() if line.startswith("+")]
        self.assertIn(
            "+static ssize_t BT_write_iter(struct kiocb *iocb, "
            "struct iov_iter *from)",
            added,
        )
        self.assertIn(
            "+		if (!copy_from_iter_full(o_buf, write_size, from)) {",
            added,
        )
        self.assertIn("+	.write_iter = BT_write_iter,", added)
        self.assertFalse(
            any(line.startswith("+") and ".write = BT_write" in line for line in added)
        )


@unittest.skipUnless(
    MODULE is not None and MODULE.is_file(),
    "materialized writev Bluetooth module is not present",
)
class StpbtWritevModuleTest(unittest.TestCase):
    def test_module_identity_and_dependency_metadata(self):
        digest = hashlib.sha256(MODULE.read_bytes()).hexdigest()
        self.assertEqual(digest, EXPECTED_MODULE_SHA256)
        readelf = shutil.which("arm-linux-gnueabihf-readelf")
        if readelf is None:
            self.skipTest("ARM readelf is not installed")
        modinfo = subprocess.run(
            [readelf, "-p", ".modinfo", str(MODULE)],
            check=True,
            text=True,
            capture_output=True,
        ).stdout
        self.assertIn(
            "vermagic=7.0.0-rc7-z1+ SMP mod_unload ARMv7 p2v8",
            modinfo,
        )
        self.assertIn("depends=mtk_stp_wmt_soc", modinfo)
        relocations = subprocess.run(
            [readelf, "-r", str(MODULE)],
            check=True,
            text=True,
            capture_output=True,
        ).stdout
        self.assertIn("BT_write_iter", relocations)


if __name__ == "__main__":
    unittest.main()
