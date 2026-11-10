#!/usr/bin/env python3
"""Analyze passive z1_battery_sample TSV evidence on the host.

Usage: python3 z1_battery_analyze.py samples.tsv > analysis.json
Use '-' for stdin. No nonstandard dependencies, device access, or file writes.
Voltage is uV; capacity is only the driver's reported percent, never inferred.
Online edges are observations consistent with plug/unplug, not physical proof.
Missing data breaks continuity: no edge is inferred across a missing sweep.
"""
import argparse
from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
import re
import sys

HEADER = ["sample", "monotonic_s", "supply", "type", "voltage_now_uV",
          "capacity_pct", "status", "online"]
FIELDS = HEADER[3:]
MARKERS = {"missing", "invalid", "unreadable", "timeout"}
STATUSES = {"Unknown", "Charging", "Discharging", "Not charging", "Full"}
MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 600 * 64


def number(value, maximum=999999999):
    if not re.fullmatch(r"[0-9]{1,9}", value) or int(value) > maximum:
        raise ValueError("invalid numeric value")
    return int(value)


def parse(text):
    lines = text.splitlines()
    if not lines or lines[0].split("\t") != HEADER:
        raise ValueError("unexpected TSV header")
    frames = []
    footer = None
    rows = 0
    for line_number, line in enumerate(lines[1:], 2):
        if footer is not None:
            raise ValueError("data after completion footer")
        if line.startswith("# end\t"):
            match = re.fullmatch(r"# end\tsamples=([0-9]+)", line)
            if not match:
                raise ValueError("invalid completion footer")
            footer = number(match[1], 600)
            continue
        cells = line.split("\t")
        if len(cells) != len(HEADER):
            raise ValueError(f"line {line_number}: expected eight columns")
        rows += 1
        if rows > MAX_ROWS:
            raise ValueError("row limit exceeded")
        row = dict(zip(HEADER, cells))
        sample = number(row["sample"], 599)
        stamp = row["monotonic_s"]
        if not re.fullmatch(r"[0-9]{1,9}\.[0-9]{1,9}", stamp):
            raise ValueError(f"line {line_number}: invalid monotonic timestamp")
        clock = Decimal(stamp)
        if not frames or sample != frames[-1]["sample"]:
            if sample != len(frames):
                raise ValueError("samples must start at zero and be contiguous")
            if frames and clock < frames[-1]["clock"]:
                raise ValueError("monotonic clock moved backwards")
            frames.append({"sample": sample, "clock": clock, "supplies": {}})
        frame = frames[-1]
        if clock != frame["clock"]:
            raise ValueError("inconsistent timestamp within a sweep")
        name = row["supply"]
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,255}", name):
            raise ValueError("invalid supply name")
        if name in frame["supplies"]:
            raise ValueError("duplicate supply in sweep")
        for field in FIELDS:
            value = row[field]
            if value in MARKERS:
                row[field] = None
            elif field == "voltage_now_uV":
                row[field] = number(value)
            elif field == "capacity_pct":
                row[field] = number(value, 100)
            elif field == "online":
                if value not in {"0", "1"}:
                    raise ValueError("invalid online value")
                row[field] = int(value)
            elif field == "status" and value not in STATUSES:
                raise ValueError("invalid status")
            elif field == "type" and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
                raise ValueError("invalid supply type")
        row["quality"] = {field: cells[HEADER.index(field)]
                          for field in FIELDS if row[field] is None}
        if name == "missing" and any(row[field] is not None for field in FIELDS):
            raise ValueError("missing-supply row contains values")
        frame["supplies"][name] = row
        if len(frame["supplies"]) > 64:
            raise ValueError("supply limit exceeded")
        if "missing" in frame["supplies"] and len(frame["supplies"]) != 1:
            raise ValueError("missing-supply row mixed with supplies")
    if not frames:
        raise ValueError("no sample rows")
    if footer is not None and footer != len(frames):
        raise ValueError("completion footer count mismatch")
    return frames, footer is not None


def stats(observations, field):
    points = [(frame, row[field]) for frame, row in observations if row[field] is not None]
    if not points:
        return {"count": 0, "first": None, "last": None, "min": None,
                "max": None, "delta": None, "first_sample": None, "last_sample": None}
    values = [value for _, value in points]
    return {"count": len(values), "first": values[0], "last": values[-1],
            "min": min(values), "max": max(values), "delta": values[-1] - values[0],
            "first_sample": points[0][0]["sample"], "last_sample": points[-1][0]["sample"]}


def battery_context(before, after):
    names = sorted({name for frame in (before, after)
                    for name, row in frame["supplies"].items() if row["type"] == "Battery"})
    result = {}
    for name in names:
        item = {}
        for field in ("voltage_now_uV", "capacity_pct", "status"):
            old = before["supplies"].get(name, {}).get(field)
            new = after["supplies"].get(name, {}).get(field)
            item[field] = {"before": old, "after": new}
            if field != "status":
                item[field]["delta"] = new - old if old is not None and new is not None else None
        result[name] = item
    return result


def analyze(text):
    frames, complete = parse(text)
    names = sorted({name for frame in frames for name in frame["supplies"] if name != "missing"})
    supplies = {}
    for name in names:
        observations = [(frame, frame["supplies"][name]) for frame in frames if name in frame["supplies"]]
        quality = {field: dict(Counter(row["quality"][field] for _, row in observations
                                       if field in row["quality"])) for field in FIELDS}
        supplies[name] = {
            "observed_sweeps": len(observations),
            "absent_sweeps": len(frames) - len(observations),
            "types": sorted({row["type"] for _, row in observations if row["type"] is not None}),
            "quality": quality,
            "voltage_now_uV": stats(observations, "voltage_now_uV"),
            "capacity_pct": stats(observations, "capacity_pct"),
            "reported_statuses": sorted({row["status"] for _, row in observations if row["status"] is not None}),
        }
    changes = []
    events = []
    for before, after in zip(frames, frames[1:]):
        for name in sorted((before["supplies"].keys() & after["supplies"].keys()) - {"missing"}):
            old, new = before["supplies"][name], after["supplies"][name]
            for field in ("status", "online"):
                if old[field] is None or new[field] is None or old[field] == new[field]:
                    continue
                change = {"supply": name, "field": field, "before": old[field], "after": new[field],
                          "from_sample": before["sample"], "to_sample": after["sample"],
                          "window_start_s": float(before["clock"]), "window_end_s": float(after["clock"])}
                changes.append(change)
                # Battery.online is not evidence of a charger being plugged in.
                if field == "online" and old["type"] == new["type"] and new["type"] not in {None, "Battery", "Unknown"}:
                    events.append({**change, "observation": "online_rise" if new[field] else "online_fall",
                                   "battery_context": battery_context(before, after)})
    return {"schema": 1, "complete": complete, "sweep_count": len(frames),
            "start_monotonic_s": float(frames[0]["clock"]),
            "end_monotonic_s": float(frames[-1]["clock"]),
            "elapsed_s": float(frames[-1]["clock"] - frames[0]["clock"]),
            "no_supply_sweeps": sum("missing" in frame["supplies"] for frame in frames),
            "supplies": supplies, "changes": changes, "plug_observations": events,
            "notes": ["capacity_pct contains driver-reported values only; no SOC is inferred from voltage.",
                      "Online edges are consistent with plug/unplug but do not prove physical cable state.",
                      "Edges require adjacent sweeps with known values; missing values break continuity.",
                      "Timestamps are sweep starts from /proc/uptime (includes suspend); reads are sequential.",
                      "No completion footer means sampling was interrupted or incomplete."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="sampler TSV, or - for stdin")
    args = parser.parse_args(argv)
    try:
        if args.input == "-":
            data = sys.stdin.buffer.read(MAX_BYTES + 1)
        else:
            with Path(args.input).open("rb") as stream:
                data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("input exceeds 8 MiB")
        result = analyze(data.decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        print(f"z1_battery_analyze: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
