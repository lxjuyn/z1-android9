"""Host-only battery evidence tests; no board, sysfs, or service actions."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "scripts/diag/z1_battery_sample.sh"
ANALYZE = ROOT / "scripts/diag/z1_battery_analyze.py"
spec = importlib.util.spec_from_file_location("z1_battery_analyze", ANALYZE)
analyzer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analyzer)


def tsv(frames, complete=True):
    lines = ["\t".join(analyzer.HEADER)]
    for sample, (stamp, supplies) in enumerate(frames):
        for name, kind, voltage, capacity, status, online in supplies:
            lines.append("\t".join(str(value) for value in
                                   (sample, stamp, name, kind, voltage, capacity, status, online)))
    if complete:
        lines.append(f"# end\tsamples={len(frames)}")
    return "\n".join(lines) + "\n"


def battery(voltage=3880000, capacity="missing", status="Discharging", online="missing"):
    return ("battery", "Battery", voltage, capacity, status, online)


def usb(online, kind="USB"):
    return ("usb", kind, "missing", "missing", "missing", online)


class SamplerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="z1-battery-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.power = self.root / "sys/class/power_supply"
        self.power.mkdir(parents=True)
        (self.root / "proc").mkdir()
        (self.root / "proc/uptime").write_text("123.45 12.34\n")

    def supply(self, name="battery", **fields):
        directory = self.power / name
        directory.mkdir()
        for field, value in fields.items():
            (directory / field).write_text(str(value) + "\n")
        return directory

    def run_sampler(self, *args, env=None, shell=None, timeout=10):
        return subprocess.run((shell or ["/bin/sh"]) + [str(SAMPLE), "--root", str(self.root),
                              "--samples", "2", "--interval", "0", *args],
                              capture_output=True, text=True, env=env, timeout=timeout)

    def test_fixture_end_to_end_preserves_files_and_units(self):
        self.supply(type="Battery", voltage_now=3880000, status="Not charging")
        self.supply("usb", type="USB", online=0)
        self.supply("unused", type="Mains", online=1)
        # Non-selected IIO/charger-looking files are never read or written.
        (self.root / "iio_raw").write_text("sentinel\n")
        before = {p.relative_to(self.root): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                  for p in self.root.rglob("*") if p.is_file()}
        result = self.run_sampler()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = analyzer.analyze(result.stdout)
        self.assertTrue(data["complete"])
        self.assertEqual(data["sweep_count"], 2)
        self.assertEqual(data["elapsed_s"], 0)
        self.assertEqual(data["supplies"]["battery"]["voltage_now_uV"]["first"], 3880000)
        self.assertIsNone(data["supplies"]["battery"]["capacity_pct"]["first"])
        self.assertEqual(data["supplies"]["battery"]["quality"]["capacity_pct"], {"missing": 2})
        self.assertEqual(data["supplies"]["battery"]["reported_statuses"], ["Not charging"])
        after = {p.relative_to(self.root): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(data["plug_observations"], [])

    def test_missing_empty_invalid_and_fifo_fields(self):
        path = self.supply(type="Battery", voltage_now="bad\tvalue", capacity=101, status="")
        os.mkfifo(path / "online")
        result = self.run_sampler("--samples", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        quality = analyzer.analyze(result.stdout)["supplies"]["battery"]["quality"]
        self.assertEqual(quality["voltage_now_uV"], {"invalid": 1})
        self.assertEqual(quality["capacity_pct"], {"invalid": 1})
        self.assertEqual(quality["status"], {"missing": 1})
        self.assertEqual(quality["online"], {"unreadable": 1})

    def test_zero_capacity_and_voltage_are_preserved_as_reported(self):
        self.supply(type="Battery", voltage_now=0, capacity=0, online=0, status="Unknown")
        result = self.run_sampler()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = analyzer.analyze(result.stdout)
        self.assertEqual(report["supplies"]["battery"]["capacity_pct"]["first"], 0)
        self.assertEqual(report["supplies"]["battery"]["voltage_now_uV"]["first"], 0)

    def test_no_supplies_and_missing_directory_are_explicit(self):
        for missing_directory in (False, True):
            if missing_directory:
                self.power.rmdir()
            result = self.run_sampler()
            self.assertEqual(result.returncode, 0, result.stderr)
            report = analyzer.analyze(result.stdout)
            self.assertEqual(report["no_supply_sweeps"], 2)
            self.assertEqual(report["supplies"], {})

    def test_sysfs_style_class_symlink(self):
        target = self.root / "devices/virtual/power_supply/battery"
        target.mkdir(parents=True)
        (target / "type").write_text("Battery\n")
        (target / "capacity").write_text("42\n")
        (self.power / "battery").symlink_to(target, target_is_directory=True)
        result = self.run_sampler()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(analyzer.analyze(result.stdout)["supplies"]["battery"]["capacity_pct"]["last"], 42)

    def test_supply_and_byte_caps(self):
        self.supply("a", type="Battery", voltage_now="9" * 200)
        self.supply("b", type="USB", online=1)
        result = self.run_sampler("--max-supplies", "1")
        self.assertEqual(result.returncode, 3)
        self.assertIn("supply limit", result.stderr)
        self.assertNotIn("# end", result.stdout)
        report = analyzer.analyze(result.stdout)
        self.assertFalse(report["complete"])
        self.assertEqual(report["supplies"]["a"]["quality"]["voltage_now_uV"], {"invalid": 1})

    def test_missing_or_invalid_clock_fails_closed(self):
        for content in (None, "", "nan 0", "-1.0 0", "1 0", "1..0 0"):
            with self.subTest(content=content):
                clock = self.root / "proc/uptime"
                if content is None:
                    clock.unlink()
                else:
                    clock.write_text(content)
                result = self.run_sampler()
                self.assertEqual(result.returncode, 3)
                self.assertNotIn("# end", result.stdout)

    def test_bad_arguments_and_help(self):
        for args in (("--samples", "0"), ("--samples", "601"), ("--samples", "08"),
                     ("--interval", "-1"), ("--duration", "3601"), ("--read-timeout", "0"),
                     ("--max-supplies", "65"), ("--samples", "999999999999999"),
                     ("--root", "relative"), ("--what",), ("--samples",)):
            with self.subTest(args=args):
                self.assertEqual(self.run_sampler(*args).returncode, 2)
        help_result = subprocess.run(["/bin/sh", str(SAMPLE), "--help"], capture_output=True, text=True)
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("No voltage-to-capacity", help_result.stdout)

    def test_missing_timeout_has_no_unbounded_fallback(self):
        empty = self.root / "empty-bin"
        empty.mkdir()
        result = self.run_sampler(env={**os.environ, "PATH": str(empty)})
        self.assertEqual(result.returncode, 2)
        self.assertIn("required command missing: timeout", result.stderr)

    def test_field_timeout_is_reported_and_collection_continues(self):
        self.supply(type="Battery", voltage_now=3880000, capacity=42)
        bin_dir = self.root / "delayed-bin"
        bin_dir.mkdir()
        # The new fixture shim inherits executable mode from a host utility;
        # only this temporary file is replaced, never the real utility.
        shutil.copy2(shutil.which("head"), bin_dir / "head")
        (bin_dir / "head").write_text(
            '#!/bin/sh\ncase "$3" in */voltage_now) sleep 20;; esac\n'
            + 'exec "' + shutil.which("head") + '" "$@"\n')
        start = time.monotonic()
        result = self.run_sampler("--samples", "1", "--read-timeout", "1",
                                  env={**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]},
                                  timeout=4)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(time.monotonic() - start, 4)
        report = analyzer.analyze(result.stdout)
        self.assertEqual(report["supplies"]["battery"]["quality"]["voltage_now_uV"], {"timeout": 1})
        self.assertEqual(report["supplies"]["battery"]["capacity_pct"]["first"], 42)

    def test_fixture_sampler_to_analyzer_plug_cycle(self):
        self.supply(type="Battery", voltage_now=3800000, status="Discharging")
        self.supply("usb", type="USB", online=0)
        bin_dir = self.root / "cycle-bin"
        bin_dir.mkdir()
        shutil.copy2(shutil.which("sleep"), bin_dir / "sleep")
        # Only the test double changes fixture files, between real sweeps.
        (bin_dir / "sleep").write_text('''#!/bin/sh
read -r now rest < "$FIXTURE_ROOT/proc/uptime"
if [ "$now" = "123.45" ]; then
    printf '124.45 0.00\\n' > "$FIXTURE_ROOT/proc/uptime"
    printf '1\\n' > "$FIXTURE_ROOT/sys/class/power_supply/usb/online"
    printf 'Charging\\n' > "$FIXTURE_ROOT/sys/class/power_supply/battery/status"
    printf '3900000\\n' > "$FIXTURE_ROOT/sys/class/power_supply/battery/voltage_now"
else
    printf '125.45 0.00\\n' > "$FIXTURE_ROOT/proc/uptime"
    printf '0\\n' > "$FIXTURE_ROOT/sys/class/power_supply/usb/online"
    printf 'Discharging\\n' > "$FIXTURE_ROOT/sys/class/power_supply/battery/status"
    printf '3850000\\n' > "$FIXTURE_ROOT/sys/class/power_supply/battery/voltage_now"
fi
''')
        result = self.run_sampler("--samples", "3", "--interval", "1", env={
            **os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
            "FIXTURE_ROOT": str(self.root)})
        self.assertEqual(result.returncode, 0, result.stderr)
        report = analyzer.analyze(result.stdout)
        self.assertEqual(report["elapsed_s"], 2)
        events = report["plug_observations"]
        self.assertEqual([event["observation"] for event in events], ["online_rise", "online_fall"])
        self.assertEqual(events[0]["battery_context"]["battery"]["voltage_now_uV"]["delta"], 100000)
        self.assertIsNone(events[0]["battery_context"]["battery"]["capacity_pct"]["after"])
        self.assertEqual(events[1]["battery_context"]["battery"]["voltage_now_uV"]["delta"], -50000)

    def test_fractional_clock_regression_fails(self):
        self.supply(type="Battery")
        bin_dir = self.root / "clock-bin"
        bin_dir.mkdir()
        # Each fixture sleep substitutes a deterministic subsequent timestamp.
        shutil.copy2(shutil.which("sleep"), bin_dir / "sleep")
        (bin_dir / "sleep").write_text(
            '#!/bin/sh\nprintf "123.44 0.00\\n" > "$CLOCK_FIXTURE"\n')
        result = self.run_sampler("--interval", "1", env={
            **os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
            "CLOCK_FIXTURE": str(self.root / "proc/uptime")})
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("clock moved backwards", result.stderr)
        self.assertNotIn("# end", result.stdout)

    def test_wall_deadline_includes_sleep(self):
        self.supply(type="Battery")
        start = time.monotonic()
        result = self.run_sampler("--interval", "10", "--duration", "1", timeout=4)
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(time.monotonic() - start, 4)
        self.assertNotIn("# end", result.stdout)
        self.assertFalse(analyzer.analyze(result.stdout)["complete"])

    @unittest.skipUnless(shutil.which("busybox"), "BusyBox unavailable")
    def test_busybox_sh_and_utilities(self):
        bin_dir = self.root / "busybox-bin"
        bin_dir.mkdir()
        busybox = shutil.which("busybox")
        for name in ("sh", "timeout", "head", "sleep"):
            (bin_dir / name).symlink_to(busybox)
        self.supply(type="Battery", capacity=50)
        result = self.run_sampler(env={**os.environ, "PATH": str(bin_dir)}, shell=[busybox, "sh"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(analyzer.analyze(result.stdout)["complete"])


class AnalyzerTests(unittest.TestCase):
    def test_plug_unplug_windows_and_adjacent_battery_deltas(self):
        text = tsv([
            ("1.00", [battery(3800000, 40), usb(0)]),
            ("3.00", [battery(3890000, 41, "Charging"), usb(1)]),
            ("7.00", [battery(3850000, 41, "Discharging"), usb(0)]),
        ])
        result = analyzer.analyze(text)
        events = result["plug_observations"]
        self.assertEqual([event["observation"] for event in events], ["online_rise", "online_fall"])
        self.assertEqual(events[0]["window_start_s"], 1)
        self.assertEqual(events[0]["window_end_s"], 3)
        context = events[0]["battery_context"]["battery"]
        self.assertEqual(context["voltage_now_uV"]["delta"], 90000)
        self.assertEqual(context["capacity_pct"]["delta"], 1)
        self.assertEqual(result["elapsed_s"], 6)
        self.assertEqual(result["supplies"]["battery"]["voltage_now_uV"]["delta"], 50000)

    def test_missing_capacity_is_never_estimated(self):
        result = analyzer.analyze(tsv([("1.0", [battery(3000000), usb(0)]),
                                       ("2.0", [battery(4200000), usb(1)])]))
        self.assertIsNone(result["supplies"]["battery"]["capacity_pct"]["delta"])
        self.assertIsNone(result["plug_observations"][0]["battery_context"]["battery"]["capacity_pct"]["after"])

    def test_gaps_do_not_invent_edges(self):
        for middle in ([usb("missing")], [battery()], [usb("timeout")], [usb("unreadable")]):
            with self.subTest(middle=middle):
                report = analyzer.analyze(tsv([("1.0", [usb(0)]), ("2.0", middle), ("3.0", [usb(1)])]))
                self.assertEqual(report["plug_observations"], [])
                self.assertEqual(report["changes"], [])

    def test_unknown_type_or_battery_online_is_not_a_plug_event(self):
        for kind in ("missing", "Unknown", "Battery"):
            report = analyzer.analyze(tsv([("1.0", [usb(0, kind)]), ("2.0", [usb(1, kind)])]))
            self.assertEqual(report["plug_observations"], [])
            self.assertEqual(len(report["changes"]), 1)

    def test_incomplete_capture_keeps_partial_evidence(self):
        result = analyzer.analyze(tsv([("1.0", [battery()])], complete=False))
        self.assertFalse(result["complete"])

    def test_malformed_samples_are_rejected(self):
        valid = tsv([("1.00", [battery(), usb(0)]), ("2.00", [battery(), usb(1)])])
        cases = [
            "", valid.replace("sample\t", "wrong\t", 1),
            valid.replace("0\t1.00\tbattery", "1\t1.00\tbattery", 1),
            valid.replace("1\t2.00", "1\t0.99"),
            valid.replace("0\t1.00\tusb", "0\t1.01\tusb"),
            valid.replace("0\t1.00\tusb", "0\t1.00\tbattery"),
            valid.replace("3880000", "NaN", 1),
            valid.replace("samples=2", "samples=3"), valid + "junk\n",
            valid.replace("2.00", "nan"), valid.replace("Discharging", "bogus", 1),
            tsv([("1.0", [battery(capacity=101)])]),
            tsv([("1.0", [usb(2)])]),
        ]
        for text in cases:
            with self.subTest(text=text[:100]):
                with self.assertRaises(ValueError):
                    analyzer.analyze(text)

    def test_cli_stdin_and_errors(self):
        result = subprocess.run([sys.executable, str(ANALYZE), "-"],
                                input=tsv([("1.0", [battery()])]), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["complete"])
        result = subprocess.run([sys.executable, str(ANALYZE), "-"],
                                input=b"\xff", capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(b"Traceback", result.stderr)
        stream = type("Stream", (), {"buffer": io.BytesIO(b"x" * 33)})()
        with patch.object(analyzer, "MAX_BYTES", 32), patch.object(sys, "stdin", stream), \
                patch.object(sys, "stderr", io.StringIO()) as errors:
            self.assertEqual(analyzer.main(["-"]), 2)
            self.assertIn("input exceeds", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
