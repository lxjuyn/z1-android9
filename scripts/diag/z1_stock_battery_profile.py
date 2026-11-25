#!/usr/bin/env python3
"""Validate and print MTK battery profile data from the exact Z1 stock kernel."""
import argparse
import hashlib
from pathlib import Path
import struct
import sys


STOCK_SHA256 = "3128816c91314be52586f45a592a1f642c8a80baabae3c08d19b451c755b2500"
STOCK_VMA = 0xC0008000
PROFILE_COUNT = 128
PROFILE_BYTES = PROFILE_COUNT * 8
PROFILE_TABLES = (
    (0, 0xC097B894),
    (25, 0xC097BC94),
    (50, 0xC097B494),
    (255, 0xC097C094),
)
PROFILE_SHA256 = "8bbd60699536a8ed92f372f504731d85b8d7a410da7366d96fedddbd48b26e67"
RESISTANCE_TABLES = (
    (0, 0xC097C894),
    (25, 0xC097CC94),
    (50, 0xC097D094),
    (255, 0xC097CC94),
    (110, 0xC097C494),  # Working table selector and initial contents.
)
RESISTANCE_SHA256 = "ec26e3e627a04736545f020456e46f89032a5fa56f49c383cf7f2eaed328ce21"


def read_profile(data, vma, expected_sha256):
    offset = vma - STOCK_VMA
    if offset < 0 or offset + PROFILE_BYTES > len(data):
        raise ValueError(f"profile address {vma:#x} is outside the kernel image")
    raw = data[offset:offset + PROFILE_BYTES]
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError(f"unexpected profile bytes at {vma:#x}")
    return list(struct.iter_unpack("<II", raw))


def verify_words(data, anchors):
    for vma, expected in anchors.items():
        actual, = struct.unpack_from("<I", data, vma - STOCK_VMA)
        if actual != expected:
            raise ValueError(f"stock word at {vma:#010x}: expected {expected:#010x}, got {actual:#010x}")


def verify_soc_path(data):
    verify_words(data, {
        # Resistance table pointers returned by fgauge_get_profile_r_table.
        0xC036DB7C: 0xC097C894,
        0xC036DB80: 0xC097CC94,
        0xC036DB84: 0xC097C494,
        0xC036DB88: 0xC097D094,
        # battery supply registration, CAPACITY=31 and its callback dispatch.
        0xC097D8CC: 0xC097DD88,
        0xC097D8D0: 17,
        0xC097D8DC: 0xC036FE0C,
        0xC097DD98: 31,
        0xC036FE1C: 0x979FF101,
        0xC036FEA0: 0xC036FF5C,
        0xC036FF5C: 0xE5903088,  # Read BAT_CAPACITY from the battery supply.
        0xC036FF64: 0xE5823000,  # Store it to the property result.
        # battery_work_callback stores raw SOC, then publishes tracked UI_SOC.
        0xC0372EA0: 0xE59F55A4,  # Load the publication base into r5.
        0xC0372EBC: 0xE59F4590,  # Load BMT_status into r4.
        0xC0372F48: 0xEBFFF259,  # Call battery_meter_get_battery_percentage.
        0xC0372F4C: 0xE5840050,  # Store raw SOC at BMT_status+0x50.
        0xC03730AC: 0xE5941054,  # Read UI_SOC at BMT_status+0x54.
        0xC03730D4: 0xE5851384,  # Publish at battery supply+0x88.
        0xC037344C: 0xC097D5C8,  # Publication base plus 0x384 is 0xC097D94C.
        0xC0373454: 0xC0B522D0,  # BMT_status base.
        0xC036F8D4: 0xEBFFFCCB,  # Call oam_run.
        0xC036F8DC: 0xE593014C,  # Load oam_d_5.
        0xC036F8E0: 0xE2600064,  # Raw SOC = 100 - oam_d_5.
        # oam_init calls force_get_tbat and fgauge_get_Q_max, then stores Qmax.
        0xC036EA04: 0xEBFFF9EC,
        0xC036EA0C: 0xEBFFFBB9,
        0xC036EA14: 0xE58301F8,
        0xC036D1E0: 0xE3A00019,  # Forced gauge temperature is 25 °C.
        0xC036D90C: 0xE3500019,  # At 25 °C use the 0–25 °C branch.
        0xC036D910: 0xCA000010,
        0xC036D914: 0xE3E030B3,  # Multiply temperature by -180.
        0xC036D918: 0xE308451F,  # Signed division by 25.
        0xC036D920: 0xE34541EB,
        0xC036D930: 0xE2844FF5,  # 0–25 °C branch base is 980 mAh.
        0xC036D988: 0xE2844E32,  # 25–50 °C branch base is 800 mAh.
        # This getter reads the n-percent checkpoint used by UI tracking.
        0xC036FB90: 0xE5930200,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kernel", type=Path, help="Decompressed Z1 stock ARM kernel")
    parser.add_argument("--resistance", action="store_true", help="Print the validated resistance curve")
    args = parser.parse_args()
    data = args.kernel.read_bytes()
    if hashlib.sha256(data).hexdigest() != STOCK_SHA256:
        parser.error("kernel SHA-256 does not match the known Z1 stock build")

    try:
        verify_soc_path(data)
        profiles = [
            read_profile(data, vma, PROFILE_SHA256) for _, vma in PROFILE_TABLES
        ]
        resistance_profiles = [
            read_profile(data, vma, RESISTANCE_SHA256) for _, vma in RESISTANCE_TABLES
        ]
    except ValueError as err:
        parser.error(str(err))

    for (temp_bucket, vma), entries in zip(PROFILE_TABLES, profiles):
        dod = [entry[0] for entry in entries]
        voltage_mv = [entry[1] for entry in entries]
        if dod[0] != 0 or dod[-1] != 100 or dod != sorted(dod):
            parser.error(f"invalid DOD ordering in profile at {vma:#x}")
        if any(a <= b for a, b in zip(voltage_mv, voltage_mv[1:])):
            parser.error(f"voltage is not strictly descending at {vma:#x}")
        print(f"bucket_c={temp_bucket} table_vma={vma:#010x} file_offset={vma - STOCK_VMA:#x}")
        print(f"entries={len(entries)} distinct_dod={len(set(dod))} distinct_voltage={len(set(voltage_mv))}")
        print("dod_pct\tvoltage_mV\tvoltage_derived_soc_pct")
        for depth, voltage in entries:
            print(f"{depth}\t{voltage}\t{100 - depth}")

    if any(profile != profiles[0] for profile in profiles[1:]):
        parser.error("temperature bucket profiles differ; expected stock image invariant failed")

    # fgauge_read_capacity_by_v returns 100 - interpolated DOD.
    capacity_code = data[0x365B8C:0x365C38]
    capacity_words = struct.unpack(f"<{len(capacity_code) // 4}I", capacity_code)
    if 0xE2600064 not in capacity_words:
        parser.error("capacity getter no longer contains 100 - interpolated DOD")
    resistance = resistance_profiles[0]
    if any(profile != resistance for profile in resistance_profiles[1:]):
        parser.error("resistance profiles differ")
    if any(value <= 0 for value, _ in resistance):
        parser.error("resistance must be positive")
    if any(left[1] <= right[1] for left, right in zip(resistance, resistance[1:])):
        parser.error("resistance profile voltages are not strictly descending")

    print("temperature_tables_identical=true")
    print("voltage_curve_semantics=DOD_percent; capacity_by_voltage=100-DOD")
    print("UI_SOC=BMT_status+0x54; published as battery CAPACITY")
    print("nPercent_UI_SOC=low-battery tracking checkpoint read by battery_meter_get_battery_nPercent_UI_SOC")
    print("raw_SOC=100-oam_d_5")
    print("startup_gauge_temperature_c=25")
    print("startup_qmax_mAh=800")
    print("resistance_temperature_tables_identical=true")
    print(f"resistance_entries={len(resistance)} resistance_sha256={RESISTANCE_SHA256}")
    if args.resistance:
        for selector, vma in RESISTANCE_TABLES:
            print(f"resistance_selector={selector} table_vma={vma:#010x} file_offset={vma - STOCK_VMA:#x}")
        print("resistance_mOhm\tvoltage_mV")
        for milliohms, voltage in resistance:
            print(f"{milliohms}\t{voltage}")


if __name__ == "__main__":
    main()
