"""Resolve the real Android.mk with GNU make; no Android tree/device needed.

Only CLEAR_VARS and BUILD_SHARED_LIBRARY are fixtures. The production makefile
selects sources, flags and dependencies, which are captured at its build include.
Run with: python3 graphics/gralloc/tests/test_build_sources.py
"""

import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

GRALLOC = Path(__file__).resolve().parents[1]
ROOT = GRALLOC.parents[1]
STUB_BUILD = ROOT / "scripts/z1_build_hal_stubs.sh"
BASE_SOURCES = ["gralloc.cpp", "framebuffer.cpp", "mapper.cpp"]
MAINLINE_SOURCES = BASE_SOURCES + ["lima_allocator.cpp", "z1_heap.cpp"]
# Android 9 core/binary.mk supplies libc/libm/libdl; cxx_stl_setup.mk selects
# libc++ for target C++ shared libraries. These are not extra gralloc deps.
ANDROID_DEFAULT_LIBRARIES = {"libc", "libm", "libdl", "libc++"}
CAPTURED = (
    "LOCAL_MODULE", "LOCAL_MODULE_PATH", "LOCAL_SRC_FILES",
    "LOCAL_SHARED_LIBRARIES", "LOCAL_HEADER_LIBRARIES", "LOCAL_CFLAGS",
    "LOCAL_C_INCLUDES", "LOCAL_CXX_STL", "LOCAL_SYSTEM_SHARED_LIBRARIES",
)


def resolve_module(variant, pan_display="", android_mk=None):
    """Return GNU make's evaluated values, not a regex approximation of ifeq."""
    with tempfile.TemporaryDirectory(prefix="z1-gralloc-make-") as temp:
        work = Path(temp)
        (work / "gralloc").symlink_to(GRALLOC, target_is_directory=True)
        (work / "clear.mk").write_text(
            "$(foreach var,$(filter-out LOCAL_PATH,$(filter LOCAL_%,$(.VARIABLES))),"
            "$(eval $(var) :=))\n"
            "LOCAL_CXX_STL := default\n"
            "LOCAL_SYSTEM_SHARED_LIBRARIES := none\n"
        )
        (work / "capture.mk").write_text("\n".join(
            f"$(file {'>' if i == 0 else '>>'}module.txt,{name}=$(strip $({name})))"
            for i, name in enumerate(CAPTURED)
        ) + "\n")
        # Poison values detect accidental inheritance across Android modules.
        (work / "fixture.mk").write_text(
            "my-dir = $(patsubst %/,%,$(dir $(lastword $(MAKEFILE_LIST))))\n"
            "CLEAR_VARS := clear.mk\n"
            "BUILD_SHARED_LIBRARY := capture.mk\n"
            "TOP := /fixture/aosp\n"
            "TARGET_OUT_SHARED_LIBRARIES := /fixture/system/lib\n"
            "LOCAL_SRC_FILES := stale.cpp\n"
            "LOCAL_SHARED_LIBRARIES := libstale\n"
            "LOCAL_CFLAGS := -DSTALE\n"
            "LOCAL_C_INCLUDES := /stale\n"
            f"include {android_mk or 'gralloc/Android.mk'}\n"
            ".PHONY: capture\ncapture:\n\t@:\n"
        )
        env = os.environ.copy()
        for name in tuple(env):
            if name.startswith("LOCAL_") or name in (
                "MAKEFLAGS", "MFLAGS", "MAKEFILES", "GNUMAKEFLAGS",
            ):
                env.pop(name)
        result = subprocess.run(
            ["make", "-rR", "--no-print-directory", "-f", "fixture.mk", "capture",
             f"Z1_KERNEL_VARIANT={variant}", f"TARGET_USE_PAN_DISPLAY={pan_display}"],
            cwd=work, env=env, capture_output=True, text=True, timeout=10,
        )
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        return dict(line.split("=", 1) for line in
                    (work / "module.txt").read_text().splitlines())


def stub_build_arguments():
    """Read the actual script's C++ command without running either HAL build."""
    commands = STUB_BUILD.read_text().replace("\\\n", " ").splitlines()
    commands = [line for line in commands if line.startswith("$CXX ")]
    if len(commands) != 1:
        raise AssertionError("Expected one gralloc C++ command in stub builder")
    return shlex.split(commands[0])


class BuildSourcesTests(unittest.TestCase):
    def test_mainline_includes_heap_and_lima_exactly_once(self):
        module = resolve_module("mainline")
        self.assertEqual(module["LOCAL_SRC_FILES"].split(), MAINLINE_SOURCES)
        self.assertEqual(module["LOCAL_CFLAGS"].split().count("-DZ1_USE_LIMA=1"), 1)
        self.assertEqual(module["LOCAL_C_INCLUDES"].split(),
                         ["/fixture/aosp/external/libdrm/include"])

    def test_stock_and_unselected_variants_keep_original_sources(self):
        for variant in ("stock", "", "linux7", "other"):
            with self.subTest(variant=variant):
                module = resolve_module(variant)
                self.assertEqual(module["LOCAL_SRC_FILES"].split(), BASE_SOURCES)
                self.assertNotIn("-DZ1_USE_LIMA", module["LOCAL_CFLAGS"])
                self.assertEqual(module["LOCAL_C_INCLUDES"], "")
                self.assertEqual(module["LOCAL_SHARED_LIBRARIES"].split(),
                                 ["liblog", "libcutils"])

    def test_selected_sources_exist(self):
        for variant in ("stock", "mainline"):
            module = resolve_module(variant)
            for source in module["LOCAL_SRC_FILES"].split():
                with self.subTest(variant=variant, source=source):
                    self.assertTrue((GRALLOC / source).is_file(), source)

    def test_mainline_sources_match_standalone_builder(self):
        sources = [arg.removeprefix("$GRALLOC/")
                   for arg in stub_build_arguments()
                   if arg.startswith("$GRALLOC/") and arg.endswith(".cpp")]
        self.assertEqual(sources, MAINLINE_SOURCES)
        self.assertEqual(resolve_module("mainline")["LOCAL_SRC_FILES"].split(), sources)

    def test_link_dependencies_cover_standalone_builder(self):
        required = {"lib" + arg[2:] for arg in stub_build_arguments()
                    if arg.startswith("-l")}
        self.assertTrue({"libcutils", "liblog", "libc++"}.issubset(required))
        module = resolve_module("mainline")
        explicit = module["LOCAL_SHARED_LIBRARIES"].split()
        self.assertEqual(len(explicit), len(set(explicit)), "Duplicate dependencies")
        self.assertTrue(required.issubset(set(explicit) | ANDROID_DEFAULT_LIBRARIES),
                        f"Missing link dependencies: {required - set(explicit) - ANDROID_DEFAULT_LIBRARIES}")
        self.assertIn(module["LOCAL_CXX_STL"], ("default", "libc++"))
        self.assertEqual(module["LOCAL_SYSTEM_SHARED_LIBRARIES"], "none")
        self.assertTrue({"liblog", "libcutils"}.issubset(explicit))

    def test_module_identity_and_hardware_headers_are_preserved(self):
        for variant in ("stock", "mainline"):
            with self.subTest(variant=variant):
                module = resolve_module(variant)
                self.assertEqual(module["LOCAL_MODULE"], "gralloc.z1")
                self.assertEqual(module["LOCAL_MODULE_PATH"], "/fixture/system/lib/hw")
                self.assertEqual(module["LOCAL_HEADER_LIBRARIES"], "libhardware_headers")
                self.assertIn("-Wno-missing-field-initializers", module["LOCAL_CFLAGS"])
                self.assertIn('LOG_TAG=\\"gralloc\\"', module["LOCAL_CFLAGS"])

    def test_pan_display_flag_is_independent_of_kernel_variant(self):
        for variant in ("stock", "mainline"):
            for pan_display in ("", "false", "true"):
                with self.subTest(variant=variant, pan_display=pan_display):
                    module = resolve_module(variant, pan_display)
                    self.assertEqual("-DUSE_PAN_DISPLAY=1" in module["LOCAL_CFLAGS"].split(),
                                     pan_display == "true")
                    self.assertEqual(module["LOCAL_SRC_FILES"].split(),
                                     MAINLINE_SOURCES if variant == "mainline" else BASE_SOURCES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
