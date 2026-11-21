import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
REPO = HERE.parent
WORKSPACE = HERE.parents[1]
PATCH = (
    REPO
    / "scripts/btgps/bt/patches/0002-stpbt-coalesce-h4-writev.patch"
)
PAGE_CAP_PATCHER = REPO / "scripts/btgps/bt/patch_bluetooth_page_cap.py"
BLUETOOTH_BASE = WORKSPACE / "out/z1_bt_stage/a9/libbluetooth.so"
CANONICAL_MODULES = sorted(
    (WORKSPACE / "bridge_backups/products").glob(
        "products_r7_r5_stp_writev_*/mtk_stp_bt_soc.ko"
    )
)
MODULE = CANONICAL_MODULES[-1] if CANONICAL_MODULES else None
CANONICAL_PACKAGE = Path(
    "/media/lxj/1e749c56-0693-6644-a595-1b45353bc925/"
    "z1-archive/r7-r5-bt-h4-writev-lock-20261121"
)
R6_PACKAGE = Path(
    "/media/lxj/1e749c56-0693-6644-a595-1b45353bc925/"
    "z1-archive/r7-r6-bt-nvram-writev-20261121_231045"
)
R7_PACKAGE = Path(
    "/media/lxj/1e749c56-0693-6644-a595-1b45353bc925/"
    "z1-archive/r7-r7-bt-stack-compat-20261121_234156"
)
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
        BLUETOOTH_BASE.is_file(),
        "pinned R5 libbluetooth.so is not present",
    )
    def test_feature_page_cap_patch_is_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "libbluetooth.so"
            subprocess.run(
                ["python3", str(PAGE_CAP_PATCHER), str(BLUETOOTH_BASE), str(output)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                hashlib.sha256(output.read_bytes()).hexdigest(),
                "86f6c3f0de68d417273d7fc869712f2d01b993b7aa2e43e76336a9daee034833",
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


@unittest.skipUnless(
    (CANONICAL_PACKAGE / "boot.img").is_file()
    and (CANONICAL_PACKAGE / "system.img").is_file(),
    "materialized R5 boot/system package is not present",
)
class StpbtWritevPackageTest(unittest.TestCase):
    def test_package_has_pinned_boot_and_system(self):
        expected = {
            "boot.img": (
                "5ae4c69066d5285a6819d88f82ed11a4d3bce4b3bc9bd8775dc8425270a2be7d"
            ),
            "system.img": (
                "774a84d6ec72cac67221248e6c857a7da077073a3c2bab11720ae06c9aea8571"
            ),
        }
        for name, digest in expected.items():
            actual = hashlib.sha256(
                (CANONICAL_PACKAGE / name).read_bytes()
            ).hexdigest()
            self.assertEqual(actual, digest)


@unittest.skipUnless(
    (R6_PACKAGE / "boot.img").is_file()
    and (R6_PACKAGE / "system.img").is_file()
    and (R6_PACKAGE / "system_from_r5.img.z1delta").is_file(),
    "materialized R6 package is not present",
)
class StpbtR6PackageTest(unittest.TestCase):
    def test_package_has_pinned_boot_system_and_delta(self):
        expected = {
            "boot.img": (
                "5ae4c69066d5285a6819d88f82ed11a4d3bce4b3bc9bd8775dc8425270a2be7d"
            ),
            "system.img": (
                "5bfbb8eaf17803190b6c6c6a0bff149fc3d600dcdb4d9e6bb87f455f81719663"
            ),
            "system_from_r5.img.z1delta": (
                "46218a794bea5c3f834d175c9dccc46f4f3429d301c7b38eb0c2840d82abb1a9"
            ),
        }
        for name, digest in expected.items():
            actual = hashlib.sha256(
                (R6_PACKAGE / name).read_bytes()
            ).hexdigest()
            self.assertEqual(actual, digest)


@unittest.skipUnless(
    (R7_PACKAGE / "boot.img").is_file()
    and (R7_PACKAGE / "system.img").is_file()
    and (R7_PACKAGE / "libbluetooth.so").is_file(),
    "materialized R7 package is not present",
)
class StpbtR7PackageTest(unittest.TestCase):
    def test_package_and_two_byte_parser_patch_identity(self):
        expected = {
            "boot.img": (
                "0d19730f915bece3e2dd1110baeadcfd8a03ca58f7e371dfd3cb2492c6903d18"
            ),
            "system.img": (
                "a3fc264d50e9b8e9a8024622637517b70d031cb1d5fe985e3da50b04ce982565"
            ),
            "system_from_r6.img.z1delta": (
                "9778760171dd481260f78d6a2fd8fce2ea33039583b5105e5d57fa4af9973bfb"
            ),
            "libbt-vendor.so": (
                "b359b8e4088efa3cdd4b2d9aa3e0242351c4dc587f198d5afee514b249f310aa"
            ),
            "libbluetooth.so": (
                "86f6c3f0de68d417273d7fc869712f2d01b993b7aa2e43e76336a9daee034833"
            ),
        }
        for name, digest in expected.items():
            actual = hashlib.sha256(
                (R7_PACKAGE / name).read_bytes()
            ).hexdigest()
            self.assertEqual(actual, digest)
        self.assertEqual(
            (R7_PACKAGE / "libbluetooth.so").read_bytes()[0x10B60C:0x10B610],
            bytes.fromhex("01202070"),
        )


if __name__ == "__main__":
    unittest.main()
