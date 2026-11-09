/*
 * Host unit test for z1_policy.h — the composition policy of the Z1 HWC2 HAL.
 *
 * Build & run (no Android toolchain needed, plain host g++):
 *   g++ -std=c++17 -O1 -Wall -Wextra -o /tmp/policy_test z1hwc2_policy_test.cpp && /tmp/policy_test
 *
 * The test deliberately mirrors the situations SurfaceFlinger produces:
 * initial INVALID types, client layers interleaved with device layers, more
 * layers than planes, layers that fail a hardware constraint, and the debug
 * "force client" switch.
 */

#include "../z1_policy.h"

#include <stdio.h>
#include <string.h>
#include <string>
#include <vector>

struct Expect {
	int type;
	int report;
};

struct Case {
	const char *name;
	std::vector<z1_policy_layer> layers;
	int slots;
	int force_client;
	int expect_used;
	std::vector<Expect> expect;	/* same order as layers */
};

static z1_policy_layer make_layer(uint64_t id, uint32_t z, int type)
{
	z1_policy_layer l;

	memset(&l, 0, sizeof(l));
	l.id = id;
	l.z = z;
	l.requested_type = type;
	l.handle_ok = 1;
	l.format_ok = 1;
	l.importable = 1;
	l.buf_w = 240;
	l.buf_h = 320;
	l.display_w = 240;
	l.display_h = 320;
	l.frame_l = 0;
	l.frame_t = 0;
	l.frame_r = 240;
	l.frame_b = 320;
	l.have_crop = 0;
	return l;
}

static int failures;

static void run(const Case &c)
{
	std::vector<z1_policy_result> r(c.layers.size());
	int used = z1_policy_decide(c.layers.data(), (int)c.layers.size(),
				    c.slots, c.force_client, r.data());
	bool bad = false;
	std::string detail;

	if (used != c.expect_used)
		bad = true;
	for (size_t i = 0; i < c.layers.size(); i++) {
		if (r[i].type != c.expect[i].type || r[i].report != c.expect[i].report)
			bad = true;
	}
	if (!bad) {
		printf("  PASS  %-46s used=%d\n", c.name, used);
		return;
	}
	failures++;
	printf("  FAIL  %-46s used=%d (want %d)\n", c.name, used,
	       c.expect_used);
	for (size_t i = 0; i < c.layers.size(); i++) {
		printf("          layer %zu z=%u req=%d -> %s report=%d why=%s"
		       "   (want %s report=%d)\n", i, c.layers[i].z,
		       c.layers[i].requested_type,
		       r[i].type == Z1_POLICY_DEVICE ? "DEVICE" : "CLIENT",
		       r[i].report, r[i].why ? r[i].why : "-",
		       c.expect[i].type == Z1_POLICY_DEVICE ? "DEVICE" : "CLIENT",
		       c.expect[i].report);
	}
	if (used != c.expect_used)
		printf("          used mismatch\n");
}

int main(void)
{
	std::vector<Case> cases;

	/* 1. plain full-screen stack, enough planes: everything on OVL */
	{
		Case c;
		c.name = "3 fullscreen DEVICE layers, 3 slots";
		c.layers = { make_layer(1, 0, Z1_TYPE_DEVICE),
			     make_layer(2, 1, Z1_TYPE_DEVICE),
			     make_layer(3, 2, Z1_TYPE_DEVICE) };
		c.slots = 3;
		c.force_client = 0;
		c.expect_used = 3;
		c.expect = { { Z1_POLICY_DEVICE, 0 }, { Z1_POLICY_DEVICE, 0 },
			     { Z1_POLICY_DEVICE, 0 } };
		cases.push_back(c);
	}

	/* 2. one layer too many: the bottom one falls back and is reported */
	{
		Case c;
		c.name = "4 fullscreen layers, 3 slots";
		c.layers = { make_layer(1, 0, Z1_TYPE_DEVICE),
			     make_layer(2, 1, Z1_TYPE_DEVICE),
			     make_layer(3, 2, Z1_TYPE_DEVICE),
			     make_layer(4, 3, Z1_TYPE_DEVICE) };
		c.slots = 3;
		c.force_client = 0;
		c.expect_used = 3;
		c.expect = { { Z1_POLICY_CLIENT, 1 }, { Z1_POLICY_DEVICE, 0 },
			     { Z1_POLICY_DEVICE, 0 }, { Z1_POLICY_DEVICE, 0 } };
		cases.push_back(c);
	}

	/* 3. client layer in the middle: everything below it must be client */
	{
		Case c;
		c.name = "client layer in the middle";
		c.layers = { make_layer(1, 0, Z1_TYPE_DEVICE),
			     make_layer(2, 1, Z1_TYPE_CLIENT),
			     make_layer(3, 2, Z1_TYPE_DEVICE) };
		c.slots = 3;
		c.force_client = 0;
		c.expect_used = 1;
		c.expect = { { Z1_POLICY_CLIENT, 1 }, { Z1_POLICY_CLIENT, 0 },
			     { Z1_POLICY_DEVICE, 0 } };
		cases.push_back(c);
	}

	/* 4. topmost layer is scaled: nothing can be device composited */
	{
		Case c;
		c.name = "top layer scaled (200x300 frame, 240x320 src)";
		c.layers = { make_layer(1, 0, Z1_TYPE_DEVICE),
			     make_layer(2, 1, Z1_TYPE_DEVICE),
			     make_layer(3, 2, Z1_TYPE_DEVICE) };
		c.layers[2].frame_r = 200;
		c.layers[2].frame_b = 300;
		c.layers[2].buf_w = 240;
		c.layers[2].buf_h = 320;
		c.slots = 3;
		c.force_client = 0;
		c.expect_used = 0;
		c.expect = { { Z1_POLICY_CLIENT, 1 }, { Z1_POLICY_CLIENT, 1 },
			     { Z1_POLICY_CLIENT, 1 } };
		cases.push_back(c);
	}

	/* 5. first frame: SurfaceFlinger sends INVALID for every layer */
	{
		Case c;
		c.name = "first frame: all types INVALID";
		c.layers = { make_layer(1, 0, Z1_TYPE_INVALID),
			     make_layer(2, 1, Z1_TYPE_INVALID),
			     make_layer(3, 2, Z1_TYPE_INVALID) };
		c.slots = 3;
		c.force_client = 0;
		c.expect_used = 3;
		c.expect = { { Z1_POLICY_DEVICE, 1 }, { Z1_POLICY_DEVICE, 1 },
			     { Z1_POLICY_DEVICE, 1 } };
		cases.push_back(c);
	}

	/* 6. debug switch forces client composition */
	{
		Case c;
		c.name = "force client (debug.z1.hwc.mode=0)";
		c.layers = { make_layer(1, 0, Z1_TYPE_DEVICE),
			     make_layer(2, 1, Z1_TYPE_DEVICE) };
		c.slots = 3;
		c.force_client = 1;
		c.expect_used = 0;
		c.expect = { { Z1_POLICY_CLIENT, 1 }, { Z1_POLICY_CLIENT, 1 } };
		cases.push_back(c);
	}

	/* 7. buffer cannot be imported (non-contiguous dma-buf / ashmem) */
	{
		Case c;
		c.name = "middle layer not importable";
		c.layers = { make_layer(1, 0, Z1_TYPE_DEVICE),
			     make_layer(2, 1, Z1_TYPE_DEVICE),
			     make_layer(3, 2, Z1_TYPE_DEVICE) };
		c.layers[1].importable = 0;
		c.slots = 3;
		c.force_client = 0;
		c.expect_used = 1;
		c.expect = { { Z1_POLICY_CLIENT, 1 }, { Z1_POLICY_CLIENT, 1 },
			     { Z1_POLICY_DEVICE, 0 } };
		cases.push_back(c);
	}

	/* 8. partially off-screen frame */
	{
		Case c;
		c.name = "top layer partially offscreen";
		c.layers = { make_layer(1, 0, Z1_TYPE_DEVICE),
			     make_layer(2, 1, Z1_TYPE_DEVICE) };
		c.layers[1].frame_l = -16;
		c.slots = 3;
		c.force_client = 0;
		c.expect_used = 0;
		c.expect = { { Z1_POLICY_CLIENT, 1 }, { Z1_POLICY_CLIENT, 1 } };
		cases.push_back(c);
	}

	/* 9. rotation/flip */
	{
		Case c;
		c.name = "top layer transformed";
		c.layers = { make_layer(1, 0, Z1_TYPE_DEVICE),
			     make_layer(2, 1, Z1_TYPE_DEVICE) };
		c.layers[1].transform = 1;
		c.slots = 3;
		c.force_client = 0;
		c.expect_used = 0;
		c.expect = { { Z1_POLICY_CLIENT, 1 }, { Z1_POLICY_CLIENT, 1 } };
		cases.push_back(c);
	}

	/* 10. status bar sized layer: position honoured, no scaling */
	{
		Case c;
		c.name = "status bar (240x24 at 0,0) + app + nav bar";
		c.layers = { make_layer(1, 0, Z1_TYPE_DEVICE),	/* wallpaper */
			     make_layer(2, 1, Z1_TYPE_DEVICE),	/* app */
			     make_layer(3, 2, Z1_TYPE_DEVICE),	/* status */
			     make_layer(4, 3, Z1_TYPE_DEVICE) };	/* nav */
		c.layers[1].frame_t = 24;
		c.layers[1].frame_b = 272;
		c.layers[1].buf_w = 240;
		c.layers[1].buf_h = 248;
		c.layers[1].have_crop = 1;
		c.layers[1].crop_r = 240;
		c.layers[1].crop_b = 248;
		c.layers[2].frame_b = 24;
		c.layers[2].buf_h = 24;
		c.layers[2].have_crop = 1;
		c.layers[2].crop_r = 240;
		c.layers[2].crop_b = 24;
		c.layers[3].frame_t = 272;
		c.layers[3].buf_h = 48;
		c.layers[3].have_crop = 1;
		c.layers[3].crop_r = 240;
		c.layers[3].crop_b = 48;
		c.slots = 3;
		c.force_client = 0;
		c.expect_used = 3;
		c.expect = { { Z1_POLICY_CLIENT, 1 }, { Z1_POLICY_DEVICE, 0 },
			     { Z1_POLICY_DEVICE, 0 }, { Z1_POLICY_DEVICE, 0 } };
		cases.push_back(c);
	}

	/* 11. top layer is CLIENT: the device layer below is demoted silently */
	{
		Case c;
		c.name = "client at bottom, device on top";
		c.layers = { make_layer(1, 0, Z1_TYPE_CLIENT),
			     make_layer(2, 1, Z1_TYPE_DEVICE) };
		c.slots = 3;
		c.force_client = 0;
		c.expect_used = 1;
		c.expect = { { Z1_POLICY_CLIENT, 0 }, { Z1_POLICY_DEVICE, 0 } };
		cases.push_back(c);
	}

	/* 12. single slot */
	{
		Case c;
		c.name = "single device slot, two layers";
		c.layers = { make_layer(1, 0, Z1_TYPE_DEVICE),
			     make_layer(2, 1, Z1_TYPE_DEVICE) };
		c.slots = 1;
		c.force_client = 0;
		c.expect_used = 1;
		c.expect = { { Z1_POLICY_CLIENT, 1 }, { Z1_POLICY_DEVICE, 0 } };
		cases.push_back(c);
	}

	/* 13. no buffer at all */
	{
		Case c;
		c.name = "top layer without buffer";
		c.layers = { make_layer(1, 0, Z1_TYPE_DEVICE),
			     make_layer(2, 1, Z1_TYPE_DEVICE) };
		c.layers[1].handle_ok = 0;
		c.slots = 3;
		c.force_client = 0;
		c.expect_used = 0;
		c.expect = { { Z1_POLICY_CLIENT, 1 }, { Z1_POLICY_CLIENT, 1 } };
		cases.push_back(c);
	}

	/* 14. cursor/solid-colour requests are demoted and reported */
	{
		Case c;
		c.name = "CURSOR + SOLID_COLOR requests";
		c.layers = { make_layer(1, 0, Z1_TYPE_SOLID_COLOR),
			     make_layer(2, 1, Z1_TYPE_CURSOR) };
		c.layers[0].handle_ok = 0;	/* solid colour has no buffer */
		c.slots = 3;
		c.force_client = 0;
		c.expect_used = 1;
		c.expect = { { Z1_POLICY_CLIENT, 1 }, { Z1_POLICY_DEVICE, 1 } };
		cases.push_back(c);
	}

	/* 15. sideband stream: unsupported, must go to the client */
	{
		Case c;
		c.name = "SIDEBAND layer";
		c.layers = { make_layer(1, 0, Z1_TYPE_DEVICE),
			     make_layer(2, 1, Z1_TYPE_SIDEBAND) };
		c.slots = 3;
		c.force_client = 0;
		c.expect_used = 0;
		c.expect = { { Z1_POLICY_CLIENT, 1 }, { Z1_POLICY_CLIENT, 1 } };
		cases.push_back(c);
	}

	printf("== z1 policy test: %zu cases\n", cases.size());
	for (size_t i = 0; i < cases.size(); i++)
		run(cases[i]);
	printf("%s (%d failure%s)\n", failures ? "RESULT: FAIL" : "RESULT: PASS",
	       failures, failures == 1 ? "" : "s");
	return failures ? 1 : 0;
}
