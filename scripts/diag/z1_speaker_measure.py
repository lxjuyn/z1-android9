#!/usr/bin/env python3
"""Measure whether a known Z1 playback tone is present at the host microphone."""

from __future__ import annotations

import argparse
import array
import datetime
import json
import math
import re
import shlex
import statistics
import subprocess
import sys
import time
from pathlib import Path
import wave


SCHEMA = "z1-speaker-measurement-v1"
DEFAULT_FREQUENCIES = (500.0,)


def _quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * fraction)))
    return ordered[index]


def _goertzel_amplitude(samples: array.array, sample_rate: int, frequency: float) -> float:
    count = len(samples)
    if count == 0:
        return 0.0
    coefficient = 2.0 * math.cos(2.0 * math.pi * frequency / sample_rate)
    first = 0.0
    second = 0.0
    for sample in samples:
        current = sample + coefficient * first - second
        second = first
        first = current
    power = first * first + second * second - coefficient * first * second
    return 2.0 * math.sqrt(max(0.0, power)) / count


def _rms(samples: array.array) -> float:
    if not samples:
        return 0.0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


def _longest_run(
    values: list[float], threshold: float, baseline_windows: int
) -> tuple[int, int]:
    best_start = best_end = -1
    start = None
    for index, value in enumerate(values + [float("-inf")]):
        if value >= threshold:
            if start is None:
                start = index
        elif start is not None:
            preceding_quiet = start >= baseline_windows and all(
                value < threshold for value in values[start - baseline_windows:start]
            )
            if preceding_quiet and (best_start < 0 or index - start > best_end - best_start):
                best_start, best_end = start, index
            start = None
    return best_start, best_end


def analyze_wav(
    path: Path,
    frequencies: tuple[float, ...] = DEFAULT_FREQUENCIES,
    *,
    window_ms: int = 100,
    hop_ms: int = 50,
    min_amplitude: float = 0.25,
    min_snr_db: float = 12.0,
    min_run_ms: int = 500,
    baseline_ms: int = 250,
    floor_fraction: float = 0.25,
    require_all: bool = False,
    require_onset: bool = True,
) -> dict:
    if not frequencies or any(not math.isfinite(frequency) or frequency <= 0 for frequency in frequencies):
        raise ValueError("frequencies must be positive")
    if window_ms <= 0 or hop_ms <= 0 or min_run_ms <= 0 or baseline_ms < 0:
        raise ValueError("window, hop and run durations must be positive; baseline may be zero")
    if hop_ms > window_ms:
        raise ValueError("hop duration cannot exceed the analysis window")
    if not 0 < floor_fraction <= 1:
        raise ValueError("floor_fraction must be in (0, 1]")
    if any(not math.isfinite(n) or n < 0 for n in (min_amplitude, min_snr_db)):
        raise ValueError("amplitude and SNR thresholds cannot be negative")

    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        sample_width = source.getsampwidth()
        sample_rate = source.getframerate()
        frame_count = source.getnframes()
        compression = source.getcomptype()
        raw = source.readframes(frame_count)

    if channels <= 0 or sample_width != 2 or compression != "NONE":
        raise ValueError(
            f"{path}: expected uncompressed PCM16 WAV, got width={sample_width}, "
            f"compression={compression}"
        )

    if any(frequency >= sample_rate / 2 for frequency in frequencies):
        raise ValueError("test frequencies must be below the Nyquist limit")

    samples = array.array("h")
    samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()
    if len(samples) != frame_count * channels:
        raise ValueError(f"{path}: truncated PCM payload")

    deinterleaved = [samples[channel::channels] for channel in range(channels)]
    window_frames = max(1, round(sample_rate * window_ms / 1000))
    hop_frames = max(1, round(sample_rate * hop_ms / 1000))
    baseline_windows = math.ceil(baseline_ms / hop_ms) if baseline_ms else 0
    if window_frames > len(deinterleaved[0]):
        raise ValueError(f"{path}: recording is shorter than one {window_ms} ms window")

    frequency_reports = []
    for frequency in frequencies:
        channel_reports = []
        for channel, samples_in_channel in enumerate(deinterleaved):
            amplitudes = []
            broadband = []
            starts = range(0, len(samples_in_channel) - window_frames + 1, hop_frames)
            for start in starts:
                window = samples_in_channel[start:start + window_frames]
                amplitudes.append(_goertzel_amplitude(window, sample_rate, frequency))
                broadband.append(_rms(window))

            noise_floor = _quantile(amplitudes, floor_fraction)
            protected_floor = max(noise_floor, 1.0e-9)
            threshold = max(
                min_amplitude,
                protected_floor * 10.0 ** (min_snr_db / 20.0),
            )
            run_start, run_end = _longest_run(
                amplitudes, threshold, baseline_windows if require_onset else 0
            )
            onset = run_start >= baseline_windows and all(
                value < threshold for value in amplitudes[run_start - baseline_windows:run_start]
            )
            if run_start < 0:
                run_duration_ms = 0
                run_amplitude = 0.0
                run_rms = 0.0
                run_start_s = run_end_s = None
            else:
                run_duration_ms = (
                    ((run_end - run_start - 1) * hop_frames + window_frames)
                    * 1000 / sample_rate
                )
                run_values = amplitudes[run_start:run_end]
                run_rms_values = broadband[run_start:run_end]
                run_amplitude = statistics.median(run_values)
                run_rms = statistics.median(run_rms_values)
                run_start_s = round(run_start * hop_frames / sample_rate, 3)
                run_end_s = round(
                    ((run_end - 1) * hop_frames + window_frames) / sample_rate, 3
                )
            snr_db = 20.0 * math.log10(max(run_amplitude, 1.0e-9) / protected_floor)
            passed = (
                run_start >= 0
                and run_duration_ms >= min_run_ms
                and snr_db >= min_snr_db
                and run_amplitude >= min_amplitude
                and (onset or not require_onset)
            )
            channel_reports.append(
                {
                    "channel": channel,
                    "passed": passed,
                    "run_start_s": run_start_s,
                    "run_end_s": run_end_s,
                    "run_duration_ms": run_duration_ms,
                    "tone_amplitude_lsb": round(run_amplitude, 4),
                    "broadband_rms_lsb": round(run_rms, 4),
                    "noise_floor_amplitude_lsb": round(noise_floor, 4),
                    "snr_db": round(snr_db, 2),
                    "onset_after_quiet_baseline": onset,
                    "threshold_amplitude_lsb": round(threshold, 4),
                }
            )

        best = max(
            channel_reports,
            key=lambda item: (
                item["passed"],
                item["run_duration_ms"],
                item["snr_db"],
                item["tone_amplitude_lsb"],
                -item["channel"],
            ),
        )
        frequency_reports.append(
            {
                "frequency_hz": frequency,
                "passed": best["passed"],
                "best_channel": best,
                "channels": channel_reports,
            }
        )

    passed_count = sum(item["passed"] for item in frequency_reports)
    overall_pass = (
        passed_count == len(frequency_reports)
        if require_all
        else passed_count > 0
    )
    return {
        "schema": SCHEMA,
        "input": str(path),
        "audio": {
            "sample_rate_hz": sample_rate,
            "channels": channels,
            "sample_width_bytes": sample_width,
            "frames": frame_count,
            "duration_s": round(frame_count / sample_rate, 3),
        },
        "parameters": {
            "window_ms": window_ms,
            "hop_ms": hop_ms,
            "min_amplitude_lsb": min_amplitude,
            "min_snr_db": min_snr_db,
            "min_run_ms": min_run_ms,
            "baseline_ms": baseline_ms,
            "floor_fraction": floor_fraction,
            "require_all_frequencies": require_all,
            "require_onset": require_onset,
        },
        "frequencies": frequency_reports,
        "result": "PASS" if overall_pass else "FAIL",
        "scope": (
            "Acoustic evidence at the configured host microphone. "
            "Use a quiet baseline and identify the Z1 as the only tone source."
        ),
    }


def _write_report(report: dict, report_path: Path | None) -> None:
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if report_path is None:
        sys.stdout.write(text)
        return
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(text)
    passing = [
        f"{item['frequency_hz']:g} Hz ({item['best_channel']['snr_db']:.1f} dB SNR)"
        for item in report["frequencies"]
        if item["passed"]
    ]
    detail = ", ".join(passing) if passing else "no sustained expected tone"
    print(
        f"SPEAKER_MEASURE_RESULT={report['result']} {detail}; report={report_path}",
        file=sys.stderr,
    )


def _frequency_values(values: list[float] | None) -> tuple[float, ...]:
    return tuple(values) if values else DEFAULT_FREQUENCIES


def _record_command(args: argparse.Namespace) -> list[str]:
    return [
        "arecord",
        "-D",
        args.device,
        "-q",
        "-f",
        "S16_LE",
        "-r",
        str(args.rate),
        "-c",
        str(args.channels),
        "-d",
        str(args.seconds),
        str(args.output),
    ]


def _run_capture(args: argparse.Namespace) -> int:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    command = _record_command(args)
    print("RECORD_COMMAND=" + shlex.join(command), flush=True)
    return subprocess.run(command, check=False).returncode


def _serial_command_succeeded(stdout: str) -> bool:
    # z1push's exit code reports transport completion. The target command's
    # status is in the root daemon's separate marker.
    return re.findall(r"^###RC=(\d+) ###DONE\s*$", stdout, re.MULTILINE) == ["0"]


def _run_with_command(args: argparse.Namespace) -> int:
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        raise SystemExit("run: provide a command after '--'")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    recorder = subprocess.Popen(_record_command(args))
    command_log = args.command_log or args.output.with_suffix(".command.log")
    command_log.parent.mkdir(parents=True, exist_ok=True)
    started = datetime.datetime.now().astimezone().isoformat()
    try:
        # Give arecord time to reach its read loop before the board trigger.
        time.sleep(0.4)
        if recorder.poll() is not None:
            print("record process exited before the board command started", file=sys.stderr)
            return 2
        print("BOARD_COMMAND=" + shlex.join(command), flush=True)
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=args.command_timeout,
            )
        except subprocess.TimeoutExpired as error:
            with command_log.open("w") as log:
                log.write(f"START={started}\n")
                log.write(f"COMMAND={shlex.join(command)}\n")
                log.write(f"TIMEOUT_SECONDS={args.command_timeout}\n")
                log.write("STDOUT_BEGIN\n")
                log.write((error.stdout or b"").decode(errors="replace"))
                log.write("\nSTDOUT_END\nSTDERR_BEGIN\n")
                log.write((error.stderr or b"").decode(errors="replace"))
                log.write("\nSTDERR_END\n")
            print(
                f"board command exceeded {args.command_timeout}s; capture preserved",
                file=sys.stderr,
            )
            return 2

        with command_log.open("w") as log:
            log.write(f"START={started}\n")
            log.write(f"COMMAND={shlex.join(command)}\n")
            log.write(f"EXIT_STATUS={result.returncode}\n")
            log.write("STDOUT_BEGIN\n")
            log.write(result.stdout)
            log.write("\nSTDOUT_END\nSTDERR_BEGIN\n")
            log.write(result.stderr)
            log.write("\nSTDERR_END\n")
        if result.stdout:
            print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
        if result.stderr:
            print(
                result.stderr,
                end="" if result.stderr.endswith("\n") else "\n",
                file=sys.stderr,
            )
    finally:
        try:
            record_result = recorder.wait(timeout=args.seconds + 10)
        except subprocess.TimeoutExpired:
            recorder.terminate()
            try:
                record_result = recorder.wait(timeout=5)
            except subprocess.TimeoutExpired:
                recorder.kill()
                record_result = recorder.wait()

    if record_result != 0:
        print(f"arecord exited with status {record_result}", file=sys.stderr)
        return 2
    if result.returncode != 0:
        print(f"board command exited with status {result.returncode}", file=sys.stderr)
        return 2

    if args.require_rootcmd_success and not _serial_command_succeeded(result.stdout):
        print("board command did not produce one successful rootcmd status", file=sys.stderr)
        return 2

    frequencies = _frequency_values(args.frequency)
    try:
        report = analyze_wav(
            args.output,
            frequencies,
            window_ms=args.window_ms,
            hop_ms=args.hop_ms,
            min_amplitude=args.min_amplitude,
            min_snr_db=args.min_snr_db,
            min_run_ms=args.min_run_ms,
            baseline_ms=args.baseline_ms,
            require_all=args.require_all,
            require_onset=not args.allow_preexisting_tone,
        )
    except (OSError, ValueError, wave.Error) as error:
        print(f"analyze: {error}", file=sys.stderr)
        return 2
    report_path = args.report or args.output.with_suffix(".json")
    _write_report(report, report_path)
    return 0 if report["result"] == "PASS" else 1


def _add_analysis_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-f",
        "--frequency",
        type=float,
        action="append",
        help="expected tone frequency in Hz; repeat for alternatives (default: 500)",
    )
    parser.add_argument("--window-ms", type=int, default=100)
    parser.add_argument("--hop-ms", type=int, default=50)
    parser.add_argument("--min-amplitude", type=float, default=0.25)
    parser.add_argument("--min-snr-db", type=float, default=12.0)
    parser.add_argument("--min-run-ms", type=int, default=500)
    parser.add_argument("--baseline-ms", type=int, default=250)
    parser.add_argument(
        "--require-all",
        action="store_true",
        help="require every expected frequency; default passes if any one is present",
    )
    parser.add_argument(
        "--allow-preexisting-tone",
        action="store_true",
        help="do not require a quiet-to-tone onset inside the recording",
    )


def _add_capture_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--device", default="default", help="arecord PCM device")
    parser.add_argument("--rate", type=int, default=48000)
    parser.add_argument("--channels", type=int, default=2)
    parser.add_argument("--seconds", type=int, default=25)
    parser.add_argument("--output", type=Path, required=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)

    analyze = subparsers.add_parser("analyze", help="analyze an existing PCM16 WAV")
    analyze.add_argument("wav", type=Path)
    analyze.add_argument("--report", type=Path)
    _add_analysis_arguments(analyze)

    capture = subparsers.add_parser("capture", help="record from the host microphone")
    _add_capture_arguments(capture)

    run = subparsers.add_parser(
        "run",
        help="record while running a board command, then analyze the recording",
    )
    _add_capture_arguments(run)
    run.add_argument("--command-timeout", type=int, default=60)
    run.add_argument("--command-log", type=Path)
    run.add_argument("--require-rootcmd-success", action="store_true",
                     help="require the Z1 root daemon to report remote exit code 0")
    run.add_argument("--report", type=Path)
    _add_analysis_arguments(run)
    run.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    if args.mode == "capture":
        if args.seconds <= 0 or args.rate <= 0 or args.channels <= 0:
            parser.error("capture durations, rates and channel counts must be positive")
        return _run_capture(args)
    if args.mode == "run":
        if min(args.seconds, args.rate, args.channels, args.command_timeout) <= 0:
            parser.error("rates, channel counts and durations must be positive")
        return _run_with_command(args)

    frequencies = _frequency_values(args.frequency)
    try:
        report = analyze_wav(
            args.wav,
            frequencies,
            window_ms=args.window_ms,
            hop_ms=args.hop_ms,
            min_amplitude=args.min_amplitude,
            min_snr_db=args.min_snr_db,
            min_run_ms=args.min_run_ms,
            baseline_ms=args.baseline_ms,
            require_all=args.require_all,
            require_onset=not args.allow_preexisting_tone,
        )
    except (OSError, ValueError, wave.Error) as error:
        print(f"analyze: {error}", file=sys.stderr)
        return 2
    _write_report(report, args.report)
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
