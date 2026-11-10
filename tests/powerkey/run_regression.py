#!/usr/bin/env python3
"""Compile exact current PMIC handler at O0/O2; no kernel tree writes.

Optional --baseline-source accepts the prior audit's mtk-pmic-keys.c.before.
Baseline and generated no-error-return controls must fail on emitted events.
The generated control initializes failed-read output to avoid testing UB.
Use --artifacts-dir for durable generated C, executables and JSON evidence.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

NAME = "mtk_pmic_keys_irq_handler_thread"
HERE = Path(__file__).resolve().parent
DEFAULT_SOURCE = HERE.parents[2] / "fork_linux/drivers/input/keyboard/mtk-pmic-keys.c"


def extract_handler(text):
    signature = re.compile(r"(?m)^static\s+irqreturn_t\s+" + NAME + r"\s*\(int irq, void \*data\)\s*\{")
    matches = list(signature.finditer(text))
    if len(matches) != 1:
        raise ValueError("expected one exact production handler definition")
    start = matches[0].start()
    opening = text.index("{", start)
    # Ignore braces in C strings, character literals, and comments.
    tokens = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|/\*[\s\S]*?\*/|//[^\n]*|[{}]')
    depth = 0
    for token in tokens.finditer(text, opening):
        if token.group() == "{": depth += 1
        elif token.group() == "}":
            depth -= 1
            if depth == 0:
                return text[start:token.end()] + "\n"
    raise ValueError("unterminated handler body")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def execute(cc, root, label, handler, negative):
    directory = root / label
    directory.mkdir()
    (directory / "handler.inc").write_text(handler)
    harness = HERE / "irq_harness.c"
    results = []
    for optimization in ("-O0", "-O2"):
        binary = directory / ("irq_test_" + optimization[1:])
        command = cc + ["-std=c11", optimization, "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-parameter"]
        if negative: command.append("-DNEGATIVE_CONTROL=1")
        command += ["-I", str(directory), str(harness), "-o", str(binary)]
        build = subprocess.run(command, text=True, capture_output=True, timeout=30)
        (directory / (binary.name + ".build.log")).write_text(build.stdout + build.stderr)
        if build.returncode:
            raise RuntimeError("compile failed: " + shlex.join(command) + "\n" + build.stdout + build.stderr)
        run = subprocess.run([str(binary)], text=True, capture_output=True, timeout=10)
        (directory / (binary.name + ".run.log")).write_text(run.stdout + run.stderr)
        if negative:
            expected = (run.returncode == 1
                        and "pressed: PASS" in run.stdout
                        and "released: PASS" in run.stdout
                        and "io-error-poison-pressed: FAIL" in run.stdout
                        and "reports=1 syncs=1" in run.stdout
                        and "failed_cases=7 total_cases=11" in run.stdout)
        else:
            expected = run.returncode == 0 and "failed_cases=0 total_cases=11" in run.stdout
        results.append({"label": label, "optimization": optimization,
                        "expected_result": expected, "returncode": run.returncode,
                        "command": command, "binary_sha256": sha(binary.read_bytes()),
                        "handler_sha256": sha(handler.encode()), "stdout": run.stdout})
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--baseline-source", type=Path)
    ap.add_argument("--artifacts-dir", type=Path)
    ap.add_argument("--cc", default=os.environ.get("CC", "cc"))
    args = ap.parse_args()
    source = args.source.resolve()
    before = source.read_bytes()
    handler = extract_handler(before.decode())
    # Remove only the early error block from the exact extracted handler.
    # A precise single match prevents a changed source from silently weakening
    # the control. No alternate production implementation lives in this test.
    pattern = r'\n\tif \(error\) \{\s*dev_err_ratelimited\([\s\S]*?\);\s*return IRQ_HANDLED;\s*\}'
    mutated, replacements = re.subn(pattern, "\n\t(void)error;", handler)
    if replacements != 1:
        raise ValueError("error guard changed; review negative-control extraction")
    temp = None
    if args.artifacts_dir:
        root = args.artifacts_dir.resolve()
        root.mkdir(parents=True, exist_ok=False)
    else:
        temp = tempfile.TemporaryDirectory(prefix="z1-pwrkey-regression-")
        root = Path(temp.name)
    try:
        results = execute(shlex.split(args.cc), root, "current", handler, False)
        results += execute(shlex.split(args.cc), root, "missing_error_guard", mutated, True)
        if args.baseline_source:
            baseline = args.baseline_source.resolve().read_bytes()
            results += execute(shlex.split(args.cc), root, "baseline", extract_handler(baseline.decode()), True)
        report = {"source": str(source), "source_sha256": sha(before),
                  "harness_sha256": sha((HERE / "irq_harness.c").read_bytes()),
                  "source_unchanged": source.read_bytes() == before,
                  "scope": "host handler logic only; no kernel ABI or board validation",
                  "results": results}
        if args.baseline_source:
            report["baseline_sha256"] = sha(baseline)
        passed = report["source_unchanged"] and all(x["expected_result"] for x in results)
        report["passed"] = passed
        output = json.dumps(report, sort_keys=True, indent=2) + "\n"
        (root / "results.json").write_text(output)
        print(output, end="")
        return 0 if passed else 1
    finally:
        if temp: temp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
