#!/usr/bin/env python3
"""Host-side fault tests for the Z1 MT6323 charger state machine.

The production functions are extracted from z1_charger.c and compiled against
a mock regmap. No kernel build or board is needed. Pass --charger-source to
exercise an archived driver revision.
"""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


def extract_braced_at(source, start):
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        if end >= len(source):
            raise ValueError(f"unterminated block at offset {start}")
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + ";"


def extract_braced(source, marker):
    start = source.index(marker)
    return extract_braced_at(source, start)


def extract_function(source, signature):
    start = 0
    while True:
        start = source.index(signature, start)
        opening = source.find("{", start)
        semicolon = source.find(";", start)
        if semicolon != -1 and semicolon < opening:
            start = semicolon + 1
            continue
        return extract_braced_at(source, start)


def extract_define(source, name):
    match = re.search(
        rf"^#define\s+{re.escape(name)}\b.*$", source, re.MULTILINE
    )
    if match is None:
        raise ValueError(f"missing define: {name}")
    return match.group(0)


def require(source, fragment, label):
    if fragment not in source:
        raise ValueError(f"missing {label}: {fragment}")


def main():
    workspace = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--charger-source",
        type=Path,
        default=workspace / "fork_linux/drivers/power/supply/z1_charger.c",
    )
    args = parser.parse_args()

    source = args.charger_source.read_text()
    for fragment, label in (
        ("bool disable_pending;", "pending-cleanup state"),
        ("bool stopping;", "stop-state guard"),
        ('static const char *const z1_charger_psy_name = "usb";',
         "health HAL power-supply name"),
        (".shutdown = z1_charger_shutdown,", "shutdown callback"),
    ):
        require(source, fragment, label)

    charger_struct = extract_braced(source, "struct z1_charger {")
    temp_enum = extract_braced(source, "enum z1_temp_gate {")
    functions = "\n\n".join(
        extract_function(source, signature)
        for signature in (
            "static int z1_charger_read_online(",
            "static int z1_charger_hw_init(",
            "static int z1_charger_enable(",
            "static int z1_charger_disable(",
            "static int z1_charger_feed_wdt(",
            "static enum z1_temp_gate z1_charger_temp_check(",
            "static void z1_charger_apply(",
            "static void z1_charger_update(",
            "static void z1_charger_stop(",
            "static void z1_charger_remove(",
            "static void z1_charger_shutdown(",
        )
    )
    define_names = (
        "Z1_CHR_CON0_RGS_CHRDET_SHIFT",
        "Z1_CHR_CON0_VCDT_HV_EN",
        "Z1_CHR_CON0_CSDAC_EN",
        "Z1_CHR_CON0_CHR_EN",
        "Z1_CHR_CON1_VCDT_HV_EN",
        "Z1_CHR_CON2_VBAT_CV_EN",
        "Z1_CHR_CON2_CS_EN",
        "Z1_CHR_CON3_VBAT_CV_VTH_MASK",
        "Z1_CHR_CON4_CS_VTH_MASK",
        "Z1_CHR_CON6_VBAT_OV_EN",
        "Z1_CHR_CON6_VBAT_OV_VTH_MASK",
        "Z1_CHR_CON6_VBAT_OV_VTH_SHIFT",
        "Z1_CHR_CON7_BATON_EN",
        "Z1_CHR_CON7_BATON_HT_EN",
        "Z1_CHR_CON10_PCHR_FLAG_EN",
        "Z1_CHR_CON13_CHRWDT_TD_MASK",
        "Z1_CHR_CON13_CHRWDT_EN",
        "Z1_CHR_CON13_CHRWDT_WR",
        "Z1_CHR_CON15_CHRWDT_INT_EN",
        "Z1_CHR_CON15_CHRWDT_FLAG_WR",
        "Z1_CHR_CON16_USBDL_RST",
        "Z1_CHR_CON16_USBDL_SET",
        "Z1_CHR_CON18_BC11_BB_CTRL",
        "Z1_CHR_CON18_BC11_RST",
        "Z1_CHR_CON20_CSDAC_STP_INC_MASK",
        "Z1_CHR_CON20_CSDAC_STP_DEC_MASK",
        "Z1_CHR_CON20_CSDAC_STP_DEC_SHIFT",
        "Z1_CHR_CON21_CSDAC_DLY_MASK",
        "Z1_CHR_CON21_CSDAC_STP_MASK",
        "Z1_CHR_CON21_CSDAC_STP_SHIFT",
        "Z1_CHR_CON22_LOW_ICH_DB_MASK",
        "Z1_CHR_CON23_VCDT_MODE",
        "Z1_CHR_CON23_CSDAC_MODE",
        "Z1_CHR_CON23_HWCV_EN",
        "Z1_CHR_CON23_ULC_DET_EN",
        "Z1_CV_VTH_4V2",
        "Z1_CS_VTH_450MA",
        "Z1_VBAT_OV_VTH_4V3",
        "Z1_TEMP_MIN",
        "Z1_TEMP_MAX",
        "Z1_BATTERY_PSY_NAME",
    )
    defines = "\n".join(extract_define(source, name) for name in define_names)

    harness = r'''
#include <errno.h>
#include <limits.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct device { int unused; };
struct delayed_work { int unused; };
struct iio_channel { int unused; };
struct mutex { int held; };
struct power_supply { int unused; };
struct z1_charger;
struct platform_device {
	struct z1_charger *drvdata;
};

CHARGER_STRUCT

static void mutex_lock(struct mutex *lock);
static void mutex_unlock(struct mutex *lock);
static void cancel_delayed_work_sync(struct delayed_work *work);
static struct z1_charger *platform_get_drvdata(struct platform_device *pdev);
static void power_supply_put(struct power_supply *psy);
static void check_impl(int condition, const char *expression, int line);
#define CHECK(expression) check_impl(!!(expression), #expression, __LINE__)

union power_supply_propval {
	int intval;
};

enum power_supply_property {
	POWER_SUPPLY_PROP_TEMP = 0,
};

#define BIT(n) (1u << (n))

#define MT6323_CHR_CON0		0x0000
#define MT6323_CHR_CON1		0x0002
#define MT6323_CHR_CON2		0x0004
#define MT6323_CHR_CON3		0x0006
#define MT6323_CHR_CON4		0x0008
#define MT6323_CHR_CON6		0x000c
#define MT6323_CHR_CON7		0x000e
#define MT6323_CHR_CON8		0x0010
#define MT6323_CHR_CON10	0x0014
#define MT6323_CHR_CON13	0x001a
#define MT6323_CHR_CON15	0x001e
#define MT6323_CHR_CON16	0x0020
#define MT6323_CHR_CON18	0x0024
#define MT6323_CHR_CON20	0x0028
#define MT6323_CHR_CON21	0x002a
#define MT6323_CHR_CON22	0x002c
#define MT6323_CHR_CON23	0x002e
#define MT6323_CHRSTATUS	0x0142

CHARGER_DEFINES

enum operation_kind {
	OP_READ,
	OP_UPDATE,
};

struct operation {
	enum operation_kind kind;
	unsigned int reg;
	unsigned int mask;
	unsigned int val;
};

#define MAX_OPERATIONS 64

struct regmap {
	unsigned int regs[MT6323_CHRSTATUS + 1];
	struct operation operations[MAX_OPERATIONS];
	int operation_count;
	int fault_at;
	int second_fault_at;
	int fault_error;
	int drop_master_updates;
	unsigned int master_sticky_mask;
};

static struct regmap map;
static int battery_present;
static int battery_temp = 250;
static int battery_temp_error;
static int battery_lookups;
static int battery_reads;
static int power_supply_changes;
static int power_supply_puts;
static int cancel_calls;
static struct z1_charger *lifecycle_charger;
static int lifecycle_checks;
static struct power_supply battery;

static void record_operation(enum operation_kind kind, unsigned int reg,
			     unsigned int mask, unsigned int val)
{
	int index = map.operation_count++;

	if (index < MAX_OPERATIONS) {
		map.operations[index].kind = kind;
		map.operations[index].reg = reg;
		map.operations[index].mask = mask;
		map.operations[index].val = val;
	}
}

static int injected_fault(void)
{
	int index = map.operation_count - 1;

	if (index == map.fault_at || index == map.second_fault_at)
		return map.fault_error;
	return 0;
}

static int regmap_read(struct regmap *regmap, unsigned int reg,
		       unsigned int *value)
{
	int ret;

	record_operation(OP_READ, reg, 0, 0);
	ret = injected_fault();
	if (ret)
		return ret;
	if (reg > MT6323_CHRSTATUS) {
		fprintf(stderr, "FAIL unexpected regmap read: 0x%x\n", reg);
		exit(1);
	}
	*value = regmap->regs[reg];
	return 0;
}

static int regmap_update_bits(struct regmap *regmap, unsigned int reg,
			      unsigned int mask, unsigned int value)
{
	unsigned int effective_mask = mask;
	int ret;

	record_operation(OP_UPDATE, reg, mask, value);
	if (lifecycle_checks && reg == MT6323_CHR_CON0) {
		CHECK(lifecycle_charger->stopping);
		CHECK(cancel_calls == 1);
		CHECK(lifecycle_charger->lock.held);
	}
	ret = injected_fault();
	if (ret)
		return ret;
	if (reg > MT6323_CHRSTATUS) {
		fprintf(stderr, "FAIL unexpected regmap update: 0x%x\n", reg);
		exit(1);
	}
	if (reg == MT6323_CHR_CON0) {
		if (regmap->drop_master_updates)
			effective_mask = 0;
		else if (mask &
			 (Z1_CHR_CON0_CSDAC_EN | Z1_CHR_CON0_CHR_EN))
			effective_mask &=
				~regmap->master_sticky_mask;
	}
	regmap->regs[reg] =
		(regmap->regs[reg] & ~effective_mask) |
		(value & effective_mask);
	return 0;
}

static struct power_supply *power_supply_get_by_name(const char *name)
{
	if (strcmp(name, Z1_BATTERY_PSY_NAME)) {
		fprintf(stderr, "FAIL battery lookup used wrong name: %s\n", name);
		exit(1);
	}
	battery_lookups++;
	return battery_present ? &battery : NULL;
}

static int power_supply_get_property(struct power_supply *psy,
				     enum power_supply_property property,
				     union power_supply_propval *value)
{
	if (psy != &battery || property != POWER_SUPPLY_PROP_TEMP) {
		fprintf(stderr, "FAIL unexpected battery property read\n");
		exit(1);
	}
	battery_reads++;
	if (battery_temp_error)
		return battery_temp_error;
	value->intval = battery_temp;
	return 0;
}

static void power_supply_changed(struct power_supply *psy)
{
	if (psy != (struct power_supply *)&map) {
		fprintf(stderr, "FAIL power_supply_changed got wrong supply\n");
		exit(1);
	}
	power_supply_changes++;
}

#define dev_dbg(...) ((void)0)
#define dev_info(...) ((void)0)
#define dev_warn(...) ((void)0)
#define dev_warn_ratelimited(...) ((void)0)
#define dev_err(...) ((void)0)
#define dev_err_once(...) ((void)0)
#define dev_err_ratelimited(...) ((void)0)

TEMP_ENUM

static struct device device;

CHARGER_FUNCTIONS

static void reset_harness(void)
{
	memset(&map, 0, sizeof(map));
	map.fault_at = -1;
	map.second_fault_at = -1;
	map.fault_error = -EIO;
	/* A reset mock has normal hardware; fault tests opt into sticky bits. */
	map.master_sticky_mask = 0;
	battery_present = 0;
	battery_temp = 250;
	battery_temp_error = 0;
	battery_lookups = 0;
	battery_reads = 0;
	power_supply_changes = 0;
	power_supply_puts = 0;
	cancel_calls = 0;
	lifecycle_charger = NULL;
	lifecycle_checks = 0;
}

static void reset_charger(struct z1_charger *chg)
{
	reset_harness();
	memset(chg, 0, sizeof(*chg));
	chg->dev = &device;
	chg->regmap = &map;
	chg->psy = (struct power_supply *)&map;
}

static void check_impl(int condition, const char *expression, int line)
{
	if (condition)
		return;
	fprintf(stderr, "FAIL line %d: %s\n", line, expression);
	exit(1);
}

static void mutex_lock(struct mutex *lock)
{
	CHECK(!lock->held);
	lock->held = 1;
}

static void mutex_unlock(struct mutex *lock)
{
	CHECK(lock->held);
	lock->held = 0;
}

static void cancel_delayed_work_sync(struct delayed_work *work)
{
	CHECK(lifecycle_charger != NULL);
	CHECK(work == &lifecycle_charger->poll_work);
	CHECK(lifecycle_charger->stopping);
	CHECK(!lifecycle_charger->lock.held);
	cancel_calls++;
}

static struct z1_charger *platform_get_drvdata(struct platform_device *pdev)
{
	return pdev->drvdata;
}

static void power_supply_put(struct power_supply *psy)
{
	CHECK(psy == &battery);
	power_supply_puts++;
}

static int count_operations(enum operation_kind kind, unsigned int reg)
{
	int count = 0;
	int i;

	for (i = 0; i < map.operation_count && i < MAX_OPERATIONS; i++)
		if (map.operations[i].kind == kind &&
		    map.operations[i].reg == reg)
			count++;
	return count;
}

static void check_operation(int index, enum operation_kind kind,
			    unsigned int reg)
{
	CHECK(index < map.operation_count);
	CHECK(index < MAX_OPERATIONS);
	CHECK(map.operations[index].kind == kind);
	CHECK(map.operations[index].reg == reg);
}

static void test_enable_success(void)
{
	struct z1_charger chg;

	reset_charger(&chg);
	map.regs[MT6323_CHR_CON0] = BIT(Z1_CHR_CON0_RGS_CHRDET_SHIFT);
	CHECK(z1_charger_enable(&chg) == 0);
	CHECK((map.regs[MT6323_CHR_CON0] &
	       (Z1_CHR_CON0_CSDAC_EN | Z1_CHR_CON0_CHR_EN)) ==
	      (Z1_CHR_CON0_CSDAC_EN | Z1_CHR_CON0_CHR_EN));
	CHECK((map.regs[MT6323_CHR_CON2] &
	       (Z1_CHR_CON2_CS_EN | Z1_CHR_CON2_VBAT_CV_EN)) ==
	      (Z1_CHR_CON2_CS_EN | Z1_CHR_CON2_VBAT_CV_EN));
	CHECK((map.regs[MT6323_CHR_CON3] &
	       Z1_CHR_CON3_VBAT_CV_VTH_MASK) == Z1_CV_VTH_4V2);
	CHECK((map.regs[MT6323_CHR_CON4] &
	       Z1_CHR_CON4_CS_VTH_MASK) == Z1_CS_VTH_450MA);
	CHECK(map.regs[MT6323_CHR_CON23] & Z1_CHR_CON23_HWCV_EN);
	CHECK(count_operations(OP_READ, MT6323_CHR_CON8) == 1);
	puts("PASS enable-success-setpoints");
}

static void test_enable_fault_injection(void)
{
	struct operation baseline[MAX_OPERATIONS];
	struct z1_charger chg;
	int baseline_count;
	int i;

	reset_charger(&chg);
	map.regs[MT6323_CHR_CON0] = BIT(Z1_CHR_CON0_RGS_CHRDET_SHIFT);
	CHECK(z1_charger_enable(&chg) == 0);
	baseline_count = map.operation_count;
	CHECK(baseline_count > 0 && baseline_count <= MAX_OPERATIONS);
	memcpy(baseline, map.operations, sizeof(baseline));

	for (i = 0; i < baseline_count; i++) {
		int ret;

		reset_charger(&chg);
		map.regs[MT6323_CHR_CON0] =
			BIT(Z1_CHR_CON0_RGS_CHRDET_SHIFT);
		map.fault_at = i;
		ret = z1_charger_enable(&chg);
		if (baseline[i].kind == OP_READ &&
		    baseline[i].reg == MT6323_CHR_CON8) {
			CHECK(ret == 0);
		} else {
			CHECK(ret == -EIO);
		}
		CHECK(map.operation_count == i + 1);
	}

	printf("PASS enable-fault-injection points=%d\n", baseline_count);
}

static void test_enable_master_readback(void)
{
	const unsigned int sticky[] = {
		Z1_CHR_CON0_CHR_EN,
		Z1_CHR_CON0_CSDAC_EN,
	};
	unsigned int i;

	for (i = 0; i < sizeof(sticky) / sizeof(sticky[0]); i++) {
		struct z1_charger chg;

		reset_charger(&chg);
		map.regs[MT6323_CHR_CON0] =
			BIT(Z1_CHR_CON0_RGS_CHRDET_SHIFT);
		map.master_sticky_mask = sticky[i];
		CHECK(z1_charger_enable(&chg) == -EIO);
		CHECK(count_operations(OP_READ, MT6323_CHR_CON0) == 3);
		CHECK(count_operations(OP_UPDATE, MT6323_CHR_CON0) == 3);
		CHECK(count_operations(OP_READ, MT6323_CHR_CON8) == 0);
	}

	puts("PASS enable-requires-both-master-bits");
}

static void test_disable_success(void)
{
	struct z1_charger chg;

	reset_charger(&chg);
	chg.charging = true;
	chg.disable_pending = true;
	map.regs[MT6323_CHR_CON0] =
		BIT(Z1_CHR_CON0_RGS_CHRDET_SHIFT) |
		Z1_CHR_CON0_CHR_EN | Z1_CHR_CON0_CSDAC_EN;
	map.regs[MT6323_CHR_CON23] = Z1_CHR_CON23_HWCV_EN;
	map.regs[MT6323_CHR_CON15] =
		Z1_CHR_CON15_CHRWDT_INT_EN |
		Z1_CHR_CON15_CHRWDT_FLAG_WR;
	map.regs[MT6323_CHR_CON13] = Z1_CHR_CON13_CHRWDT_EN;
	CHECK(z1_charger_disable(&chg) == 0);
	CHECK(!chg.charging);
	CHECK(!chg.disable_pending);
	CHECK(!(map.regs[MT6323_CHR_CON0] &
		(Z1_CHR_CON0_CHR_EN | Z1_CHR_CON0_CSDAC_EN)));
	CHECK(!(map.regs[MT6323_CHR_CON23] & Z1_CHR_CON23_HWCV_EN));
	CHECK(!(map.regs[MT6323_CHR_CON15] &
		(Z1_CHR_CON15_CHRWDT_INT_EN |
		 Z1_CHR_CON15_CHRWDT_FLAG_WR)));
	CHECK(!(map.regs[MT6323_CHR_CON13] & Z1_CHR_CON13_CHRWDT_EN));
	CHECK(map.operation_count == 5);
	check_operation(0, OP_UPDATE, MT6323_CHR_CON0);
	check_operation(1, OP_READ, MT6323_CHR_CON0);
	check_operation(2, OP_UPDATE, MT6323_CHR_CON23);
	check_operation(3, OP_UPDATE, MT6323_CHR_CON15);
	check_operation(4, OP_UPDATE, MT6323_CHR_CON13);
	puts("PASS disable-stop-before-watchdog-cleanup");
}

static void test_disable_master_faults(void)
{
	struct z1_charger chg;
	const unsigned int stuck_bits[] = {
		Z1_CHR_CON0_CHR_EN,
		Z1_CHR_CON0_CSDAC_EN,
	};
	unsigned int i;

	reset_charger(&chg);
	chg.charging = true;
	map.regs[MT6323_CHR_CON0] =
		Z1_CHR_CON0_CHR_EN | Z1_CHR_CON0_CSDAC_EN;
	map.fault_at = 0;
	CHECK(z1_charger_disable(&chg) == -EIO);
	CHECK(chg.charging);
	CHECK(chg.disable_pending);
	CHECK(map.operation_count == 1);

	reset_charger(&chg);
	chg.charging = true;
	map.regs[MT6323_CHR_CON0] =
		Z1_CHR_CON0_CHR_EN | Z1_CHR_CON0_CSDAC_EN;
	map.fault_at = 1;
	CHECK(z1_charger_disable(&chg) == -EIO);
	CHECK(chg.charging);
	CHECK(chg.disable_pending);
	CHECK(map.operation_count == 2);

	reset_charger(&chg);
	chg.charging = true;
	map.regs[MT6323_CHR_CON0] =
		Z1_CHR_CON0_CHR_EN | Z1_CHR_CON0_CSDAC_EN;
	map.drop_master_updates = 1;
	CHECK(z1_charger_disable(&chg) == -EIO);
	CHECK(chg.charging);
	CHECK(chg.disable_pending);
	CHECK(map.operation_count == 2);
	for (i = 0; i < sizeof(stuck_bits) / sizeof(stuck_bits[0]); i++) {
		unsigned int watchdog15 = Z1_CHR_CON15_CHRWDT_INT_EN |
			Z1_CHR_CON15_CHRWDT_FLAG_WR;

		reset_charger(&chg);
		chg.charging = true;
		chg.disable_pending = false;
		map.regs[MT6323_CHR_CON0] = stuck_bits[i];
		map.regs[MT6323_CHR_CON13] = Z1_CHR_CON13_CHRWDT_EN;
		map.regs[MT6323_CHR_CON15] = watchdog15;
		map.master_sticky_mask = stuck_bits[i];
		CHECK(z1_charger_disable(&chg) == -EIO);
		CHECK((map.regs[MT6323_CHR_CON0] &
		       (Z1_CHR_CON0_CHR_EN | Z1_CHR_CON0_CSDAC_EN)) ==
		      stuck_bits[i]);
		CHECK(chg.charging);
		CHECK(chg.disable_pending);
		CHECK(map.regs[MT6323_CHR_CON13] & Z1_CHR_CON13_CHRWDT_EN);
		CHECK((map.regs[MT6323_CHR_CON15] & watchdog15) == watchdog15);
		CHECK(map.operation_count == 2);
	}
	puts("PASS disable-master-failures-retain-charging-state");
}

static void run_stop_callback(void (*callback)(struct platform_device *),
			      int expect_put)
{
	struct z1_charger chg;
	struct platform_device pdev;
	int operations_before;

	reset_charger(&chg);
	chg.charging = true;
	chg.bat_psy = expect_put ? &battery : NULL;
	map.regs[MT6323_CHR_CON0] =
		Z1_CHR_CON0_CHR_EN | Z1_CHR_CON0_CSDAC_EN;
	map.regs[MT6323_CHR_CON13] = Z1_CHR_CON13_CHRWDT_EN;
	map.regs[MT6323_CHR_CON15] = Z1_CHR_CON15_CHRWDT_INT_EN;
	pdev.drvdata = &chg;
	lifecycle_charger = &chg;
	lifecycle_checks = 1;
	if (callback)
		callback(&pdev);
	else
		z1_charger_stop(&chg);
	CHECK(chg.stopping);
	CHECK(cancel_calls == 1);
	CHECK(!chg.lock.held);
	CHECK(!chg.charging);
	CHECK(!chg.disable_pending);
	CHECK(!(map.regs[MT6323_CHR_CON0] &
		(Z1_CHR_CON0_CHR_EN | Z1_CHR_CON0_CSDAC_EN)));
	CHECK(!(map.regs[MT6323_CHR_CON13] & Z1_CHR_CON13_CHRWDT_EN));
	CHECK(!(map.regs[MT6323_CHR_CON15] & Z1_CHR_CON15_CHRWDT_INT_EN));
	operations_before = map.operation_count;
	z1_charger_update(&chg);
	z1_charger_apply(&chg, true);
	CHECK(map.operation_count == operations_before);
	CHECK(chg.stopping);
	CHECK(!chg.charging);
	CHECK(power_supply_puts == expect_put);
	lifecycle_checks = 0;
}

static void test_stop_remove_shutdown_lifecycle(void)
{
	run_stop_callback(NULL, 0);
	run_stop_callback(z1_charger_remove, 1);
	run_stop_callback(z1_charger_shutdown, 0);
	puts("PASS stop-remove-shutdown-order-and-no-reenable");
}

static void test_disable_cleanup_faults(void)
{
	int i;

	for (i = 2; i < 5; i++) {
		struct z1_charger chg;

		reset_charger(&chg);
		chg.charging = true;
		map.regs[MT6323_CHR_CON0] =
			Z1_CHR_CON0_CHR_EN | Z1_CHR_CON0_CSDAC_EN;
		map.regs[MT6323_CHR_CON23] = Z1_CHR_CON23_HWCV_EN;
		map.regs[MT6323_CHR_CON15] =
			Z1_CHR_CON15_CHRWDT_INT_EN |
			Z1_CHR_CON15_CHRWDT_FLAG_WR;
		map.regs[MT6323_CHR_CON13] =
			Z1_CHR_CON13_CHRWDT_EN;
		map.fault_at = i;
		CHECK(z1_charger_disable(&chg) == -EIO);
		CHECK(!chg.charging);
		CHECK(chg.disable_pending);
		CHECK(map.operation_count == i + 1);
		CHECK(!(map.regs[MT6323_CHR_CON0] &
			(Z1_CHR_CON0_CHR_EN |
			 Z1_CHR_CON0_CSDAC_EN)));
	}

	puts("PASS disable-cleanup-failures-stay-pending");
}

static void test_feed_watchdog_faults(void)
{
	struct z1_charger chg = {
		.dev = &device,
		.regmap = &map,
	};
	int baseline;
	int i;

	reset_harness();
	chg.regmap = &map;
	CHECK(z1_charger_feed_wdt(&chg) == 0);
	baseline = map.operation_count;
	CHECK(baseline == 4);

	for (i = 0; i < baseline; i++) {
		reset_harness();
		chg.regmap = &map;
		map.fault_at = i;
		CHECK(z1_charger_feed_wdt(&chg) == -EIO);
		CHECK(map.operation_count == i + 1);
	}

	printf("PASS watchdog-feed-fault-injection points=%d\n", baseline);
}

static void test_stopping_blocks_apply(void)
{
	struct z1_charger chg;

	reset_charger(&chg);
	chg.stopping = true;
	chg.disable_pending = true;
	chg.charging = true;
	battery_present = 1;
	z1_charger_apply(&chg, true);
	CHECK(map.operation_count == 0);
	CHECK(chg.charging);
	puts("PASS stopping-blocks-all-charger-io");
}

static void test_pending_cleanup_precedes_policy(void)
{
	struct z1_charger chg;

	reset_charger(&chg);
	chg.charging = true;
	chg.disable_pending = true;
	map.regs[MT6323_CHR_CON0] =
		Z1_CHR_CON0_CHR_EN | Z1_CHR_CON0_CSDAC_EN;
	battery_present = 1;
	map.fault_at = 0;
	z1_charger_apply(&chg, true);
	CHECK(map.operation_count == 1);
	CHECK(battery_lookups == 0);
	CHECK(battery_reads == 0);
	CHECK(chg.charging);
	CHECK(chg.disable_pending);

	reset_charger(&chg);
	chg.charging = true;
	chg.disable_pending = true;
	map.regs[MT6323_CHR_CON0] =
		Z1_CHR_CON0_CHR_EN | Z1_CHR_CON0_CSDAC_EN;
	battery_present = 1;
	z1_charger_apply(&chg, true);
	CHECK(chg.charging);
	CHECK(!chg.disable_pending);
	CHECK(battery_lookups == 1);
	CHECK(battery_reads == 1);
	CHECK((map.regs[MT6323_CHR_CON0] &
	       (Z1_CHR_CON0_CHR_EN | Z1_CHR_CON0_CSDAC_EN)) ==
	      (Z1_CHR_CON0_CHR_EN | Z1_CHR_CON0_CSDAC_EN));
	puts("PASS pending-cleanup-precedes-enable");
}

static void test_enable_failure_rolls_back(void)
{
	struct operation baseline[MAX_OPERATIONS];
	struct z1_charger chg;
	int baseline_count;
	int failure_index;
	int i;

	reset_charger(&chg);
	CHECK(z1_charger_enable(&chg) == 0);
	baseline_count = map.operation_count;
	memcpy(baseline, map.operations, sizeof(baseline));
	failure_index = -1;
	for (i = 0; i < baseline_count; i++) {
		if (baseline[i].kind == OP_UPDATE &&
		    baseline[i].reg == MT6323_CHR_CON2 &&
		    baseline[i].mask == Z1_CHR_CON2_CS_EN &&
		    baseline[i].val == Z1_CHR_CON2_CS_EN) {
			failure_index = i;
			break;
		}
	}
	CHECK(failure_index > 0);

	reset_charger(&chg);
	battery_present = 1;
	map.fault_at = failure_index;
	z1_charger_apply(&chg, true);
	CHECK(!chg.charging);
	CHECK(!chg.disable_pending);
	check_operation(failure_index + 1, OP_UPDATE, MT6323_CHR_CON0);
	CHECK(!(map.regs[MT6323_CHR_CON0] &
		(Z1_CHR_CON0_CHR_EN | Z1_CHR_CON0_CSDAC_EN)));

	reset_charger(&chg);
	battery_present = 1;
	map.fault_at = failure_index;
	map.second_fault_at = failure_index + 1;
	z1_charger_apply(&chg, true);
	CHECK(!chg.charging);
	CHECK(chg.disable_pending);
	CHECK(map.operation_count == failure_index + 2);
	puts("PASS partial-enable-rollback-and-pending-retry");
}

static void test_existing_charge_heartbeat_faults(void)
{
	struct z1_charger chg;
	int i;

	reset_charger(&chg);
	chg.charging = true;
	chg.bat_psy = &battery;
	map.regs[MT6323_CHR_CON0] =
		Z1_CHR_CON0_CHR_EN | Z1_CHR_CON0_CSDAC_EN;
	map.fault_at = 0;
	z1_charger_apply(&chg, true);
	CHECK(chg.disable_pending);
	CHECK(count_operations(OP_UPDATE, MT6323_CHR_CON13) == 0);

	reset_charger(&chg);
	chg.charging = true;
	chg.bat_psy = &battery;
	map.regs[MT6323_CHR_CON0] =
		BIT(Z1_CHR_CON0_RGS_CHRDET_SHIFT) |
		Z1_CHR_CON0_CHR_EN;
	z1_charger_apply(&chg, true);
	CHECK(chg.disable_pending);
	CHECK(count_operations(OP_UPDATE, MT6323_CHR_CON13) == 0);

	for (i = 1; i <= 4; i++) {
		reset_charger(&chg);
		chg.charging = true;
		chg.bat_psy = &battery;
		map.regs[MT6323_CHR_CON0] =
			Z1_CHR_CON0_CHR_EN | Z1_CHR_CON0_CSDAC_EN;
		map.fault_at = i;
		z1_charger_apply(&chg, true);
		CHECK(chg.disable_pending);
		CHECK(map.operation_count == i + 1);
	}

	puts("PASS heartbeat-readback-and-feed-faults");
}

static void test_unreadable_temperature_stops_charge(void)
{
	struct z1_charger chg;

	reset_charger(&chg);
	chg.charging = true;
	chg.bat_psy = &battery;
	map.regs[MT6323_CHR_CON0] =
		Z1_CHR_CON0_CHR_EN | Z1_CHR_CON0_CSDAC_EN;
	battery_temp_error = -ENODATA;
	z1_charger_apply(&chg, true);
	CHECK(!chg.charging);
	CHECK(!chg.disable_pending);
	CHECK(!(map.regs[MT6323_CHR_CON0] &
		(Z1_CHR_CON0_CHR_EN | Z1_CHR_CON0_CSDAC_EN)));
	CHECK(count_operations(OP_UPDATE, MT6323_CHR_CON13) == 1);
	puts("PASS unreadable-temperature-fails-safe");
}

static void test_update_faults(void)
{
	struct z1_charger chg;

	reset_charger(&chg);
	chg.stopping = true;
	chg.online = true;
	z1_charger_update(&chg);
	CHECK(map.operation_count == 0);
	CHECK(chg.online);

	reset_charger(&chg);
	chg.online = true;
	map.fault_at = 0;
	z1_charger_update(&chg);
	CHECK(chg.online);
	CHECK(map.operation_count == 1);
	CHECK(power_supply_changes == 0);

	reset_charger(&chg);
	chg.online = true;
	chg.charging = true;
	chg.disable_pending = true;
	map.regs[MT6323_CHR_CON0] =
		Z1_CHR_CON0_CHR_EN | Z1_CHR_CON0_CSDAC_EN;
	map.fault_at = 0;
	z1_charger_update(&chg);
	CHECK(!chg.charging);
	CHECK(!chg.disable_pending);
	CHECK(map.operation_count == 6);
	CHECK(power_supply_changes == 0);

	reset_charger(&chg);
	map.regs[MT6323_CHR_CON0] =
		BIT(Z1_CHR_CON0_RGS_CHRDET_SHIFT);
	z1_charger_update(&chg);
	CHECK(chg.online);
	CHECK(power_supply_changes == 1);
	CHECK(map.operation_count >= 1);

	reset_charger(&chg);
	chg.online = true;
	map.regs[MT6323_CHR_CON0] =
		BIT(Z1_CHR_CON0_RGS_CHRDET_SHIFT);
	z1_charger_update(&chg);
	CHECK(chg.online);
	CHECK(power_supply_changes == 0);
	puts("PASS detection-read-failure-and-cache-recovery");
}

int main(void)
{
	test_enable_success();
	test_enable_fault_injection();
	test_enable_master_readback();
	test_disable_success();
	test_disable_master_faults();
	test_stop_remove_shutdown_lifecycle();
	test_disable_cleanup_faults();
	test_feed_watchdog_faults();
	test_stopping_blocks_apply();
	test_pending_cleanup_precedes_policy();
	test_enable_failure_rolls_back();
	test_existing_charge_heartbeat_faults();
	test_unreadable_temperature_stops_charge();
	test_update_faults();
	return 0;
}
'''
    harness = (
        harness.replace("CHARGER_DEFINES", defines)
        .replace("CHARGER_STRUCT", charger_struct)
        .replace("TEMP_ENUM", temp_enum)
        .replace("CHARGER_FUNCTIONS", functions)
    )

    with tempfile.TemporaryDirectory(prefix="z1_charger_fault_test_") as temp:
        c_file = Path(temp) / "z1_charger_fault_test.c"
        executable = Path(temp) / "z1_charger_fault_test"
        c_file.write_text(harness)
        subprocess.run(
            shlex.split(os.environ.get("HOSTCC", "cc"))
            + [
                "-std=gnu11",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-Wno-unused-parameter",
                str(c_file),
                "-o",
                str(executable),
            ],
            check=True,
        )
        subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    main()
