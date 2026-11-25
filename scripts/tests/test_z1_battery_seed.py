#!/usr/bin/env python3
"""Compile and run the Z1 RTC SOC seeding policy on the host."""

import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


SUPPLY = Path(__file__).resolve().parents[3] / "fork_linux/drivers/power/supply"

HARNESS = r"""
#include <assert.h>
#include "z1_battery_oam.h"
#include "z1_battery_seed.h"

int main(void)
{
	const struct z1_oam_point points[] = {
		{ 4200, 0, 100 },
		{ 3900, 40, 200 },
		{ 3800, 40, 250 },
		{ 3400, 100, 300 },
	};
	const struct z1_oam_profile profile = { points, 4 };
	struct z1_oam_state state = {};

	assert(z1_battery_seed_soc(2, 59, 3582, true) == 2);
	assert(z1_battery_seed_soc(59, 2, 3759, true) == 59);
	assert(z1_battery_seed_soc(60, 99, 3759, true) == 99);
	assert(z1_battery_seed_soc(60, 100, 3759, true) == 60);
	assert(z1_battery_seed_soc(61, 100, 3759, true) == 100);
	assert(z1_battery_seed_soc(40, 1, 3759, true) == 1);
	assert(z1_battery_seed_soc(40, 0, 3759, true) == 40);
	assert(z1_battery_seed_soc(80, 101, 3759, true) == 80);
	assert(z1_battery_seed_soc(60, -1, 3759, true) == 60);
	assert(z1_battery_seed_soc(5, 39, 3550, false) == 5);
	assert(z1_battery_seed_soc(6, 39, 3550, false) == 39);
	assert(z1_battery_seed_soc(5, 39, 3550, true) == 39);
	assert(z1_battery_seed_soc(2, 39, 3450, true) == 2);
	assert(z1_battery_seed_soc(2, 39, 3400, true) == 2);
	assert(z1_battery_seed_soc(2, 39, 3451, true) == 39);

	assert(z1_oam_init(&state, &profile, 3800));
	assert(state.d0 == 40 && state.d5 == 40 && state.r1_mohms == 250);
	assert(z1_oam_init_soc(&state, &profile, 3800, 61));
	assert(state.d0 == 39 && state.d1 == 39 && state.d2 == 39);
	assert(state.d3 == 39 && state.d4 == 39 && state.d5 == 39);
	assert(state.r1_mohms == 250 && state.r2_mohms == 250);
	assert(state.ocv1_mv == state.ocv2_mv && state.ocv1_mv == 3908);
	assert(!z1_oam_init_soc(&state, &profile, 3800, -1));
	assert(!z1_oam_init_soc(&state, &profile, 3800, 101));
	assert(state.d0 == 39 && state.d5 == 39);
	return 0;
}
"""


class Z1BatterySeedTest(unittest.TestCase):
    def test_host_vectors(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "z1_battery_seed"
            compiler = shlex.split(os.environ.get("CC", "cc"))
            subprocess.run(
                [*compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                 "-I", str(SUPPLY), "-x", "c", "-", "-o", str(binary)],
                input=HARNESS,
                text=True,
                check=True,
            )
            subprocess.run([binary], check=True)


if __name__ == "__main__":
    unittest.main()
