#!/usr/bin/env python3
"""Host-side fault tests for MediaTek CRTC shadow-update failures.

The functions under test are extracted from the kernel sources and compiled
against a small mock DRM/MMIO environment. No board or kernel build is needed.
Use --crtc-source and --mutex-source to test archived kernel revisions.
"""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


def extract_function(source, signature):
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        if end >= len(source):
            raise ValueError(f"unterminated function: {signature}")
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def extract_define(source, name):
    match = re.search(rf"^#define\s+{re.escape(name)}\b.*$", source, re.MULTILINE)
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
        "--crtc-source",
        type=Path,
        default=workspace / "fork_linux/drivers/gpu/drm/mediatek/mtk_crtc.c",
    )
    parser.add_argument(
        "--mutex-source",
        type=Path,
        default=workspace / "fork_linux/drivers/soc/mediatek/mtk-mutex.c",
    )
    args = parser.parse_args()

    crtc_source = args.crtc_source.read_text()
    mutex_source = args.mutex_source.read_text()

    for fragment, label in (
        ("mtk_crtc->failed_fbs = devm_kcalloc", "failed_fbs allocation"),
        ("mtk_crtc->failed_async_fbs = devm_kcalloc", "failed_async_fbs allocation"),
        (".atomic_check", "atomic-check registration"),
        ("= mtk_crtc_atomic_check,", "atomic-check callback"),
    ):
        require(crtc_source, fragment, label)
    for fragment, label in (
        ("ret = mtk_mutex_acquire(mtk_crtc->mutex);", "checked mutex acquisition"),
        ("if (READ_ONCE(mtk_crtc->config_error))", "sticky error gate"),
    ):
        require(crtc_source, fragment, label)

    crtc_functions = "\n".join(
        extract_function(crtc_source, signature)
        for signature in (
            "static void mtk_crtc_finish_page_flip(",
            "static void mtk_crtc_fail_config_locked(",
            "static bool mtk_crtc_config_failed(",
            "static int mtk_crtc_atomic_check(",
            "static void mtk_crtc_atomic_begin(",
            "static void mtk_crtc_ddp_irq(",
            "int mtk_crtc_plane_check(",
        )
    )
    mutex_function = extract_function(mutex_source, "int mtk_mutex_acquire(")
    mutex_defines = "\n".join(
        extract_define(mutex_source, name)
        for name in ("DISP_REG_MUTEX_EN", "DISP_REG_MUTEX", "INT_MUTEX")
    )

    harness = r'''
#include <assert.h>
#include <errno.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef uint8_t u8;
typedef uint32_t u32;

#define BIT(n) (1u << (n))
#define READ_ONCE(value) (value)
#define WRITE_ONCE(target, value) ((target) = (value))
#define container_of(pointer, type, member) \
	((type *)((char *)(pointer) - offsetof(type, member)))
#define IS_REACHABLE(config) 0
#define CONFIG_MTK_CMDQ 0
#define lockdep_assert_held(lock) do { (void)(lock); } while (0)
#define spin_lock_irqsave(lock, flags) do { \
	(void)(lock); (flags) = 0; \
} while (0)
#define spin_unlock_irqrestore(lock, flags) do { \
	(void)(lock); (void)(flags); \
} while (0)

struct spinlock {
	int unused;
};

struct mutex {
	int unused;
};

struct dma_fence {
	int error;
};

struct drm_pending_vblank_event {
	unsigned int pipe;
	struct {
		struct dma_fence *fence;
	} base;
};

struct drm_framebuffer {
	unsigned int refs;
};

struct drm_crtc;

struct drm_plane_state {
	struct drm_crtc *crtc;
	struct drm_framebuffer *fb;
};

struct drm_plane {
	struct drm_plane_state *state;
};

struct drm_crtc_state {
	struct drm_pending_vblank_event *event;
};

struct drm_crtc {
	int index;
	struct drm_device *dev;
	struct drm_crtc_state *state;
};

struct drm_device {
	struct spinlock event_lock;
	void *dev_private;
};

struct drm_atomic_plane {
	struct drm_plane *plane;
	struct drm_plane_state *old_state;
	struct drm_plane_state *new_state;
};

struct drm_atomic_state {
	bool async_update;
	struct drm_atomic_plane planes[8];
	unsigned int plane_count;
	struct drm_crtc *crtc;
	struct drm_crtc_state *new_crtc_state;
};

struct mtk_plane_state {
	int unused;
};

struct mtk_ddp_comp {
	int unused;
};

struct cmdq_pkt;

struct mtk_drm_private_data {
	bool shadow_register;
};

struct mtk_drm_private {
	struct mtk_drm_private_data *data;
};

struct mtk_crtc {
	struct drm_crtc base;
	bool pending_needs_vblank;
	struct drm_pending_vblank_event *event;
	bool event_vblank_ref;
	struct drm_plane *planes;
	unsigned int layer_nr;
	bool config_updating;
	struct spinlock config_lock;
	int config_error;
	struct drm_framebuffer **failed_fbs;
	struct drm_framebuffer **failed_async_fbs;
};

static inline struct mtk_crtc *to_mtk_crtc(struct drm_crtc *crtc)
{
	return container_of(crtc, struct mtk_crtc, base);
}

static unsigned int framebuffer_gets;
static unsigned int vblank_gets;
static unsigned int vblank_puts;
static unsigned int error_logs;
static unsigned int ddp_configs;
static unsigned int ddp_finishes;
static unsigned int plane_dispatch;
static unsigned int layer_checks;
static struct drm_pending_vblank_event *sent[8];
static unsigned int sent_count;
static char last_error[256];
static struct mtk_ddp_comp *plane_comp;
static int vblank_get_ret;

static struct drm_plane_state *
lookup_plane_state(struct drm_atomic_state *state, struct drm_plane *plane,
		   bool new_state)
{
	unsigned int i;

	for (i = 0; i < state->plane_count; i++) {
		if (state->planes[i].plane != plane)
			continue;
		return new_state ? state->planes[i].new_state :
				   state->planes[i].old_state;
	}
	return NULL;
}

static struct drm_plane_state *
drm_atomic_get_old_plane_state(struct drm_atomic_state *state,
			       struct drm_plane *plane)
{
	return lookup_plane_state(state, plane, false);
}

static struct drm_plane_state *
drm_atomic_get_new_plane_state(struct drm_atomic_state *state,
			       struct drm_plane *plane)
{
	return lookup_plane_state(state, plane, true);
}

static struct drm_crtc_state *
drm_atomic_get_new_crtc_state(struct drm_atomic_state *state,
			      struct drm_crtc *crtc)
{
	return state->crtc == crtc ? state->new_crtc_state : NULL;
}

static unsigned int drm_crtc_index(struct drm_crtc *crtc)
{
	return crtc->index;
}

static void drm_framebuffer_get(struct drm_framebuffer *fb)
{
	framebuffer_gets++;
	fb->refs++;
}

static void dma_fence_set_error(struct dma_fence *fence, int error)
{
	assert(fence != NULL);
	fence->error = error;
}

static void drm_crtc_send_vblank_event(
	struct drm_crtc *crtc, struct drm_pending_vblank_event *event)
{
	assert(crtc != NULL);
	assert(event != NULL);
	assert(sent_count < sizeof(sent) / sizeof(sent[0]));
	sent[sent_count++] = event;
}

static void drm_crtc_vblank_put(struct drm_crtc *crtc)
{
	assert(crtc != NULL);
	vblank_puts++;
}

static int drm_crtc_vblank_get(struct drm_crtc *crtc)
{
	assert(crtc != NULL);
	vblank_gets++;
	return vblank_get_ret;
}

static void drm_err(const void *dev, const char *format, ...)
{
	va_list args;
	int length;

	assert(dev != NULL);
	error_logs++;
	va_start(args, format);
	length = vsnprintf(last_error, sizeof(last_error), format, args);
	va_end(args);
	assert(length >= 0 && (size_t)length < sizeof(last_error));
}

static void mtk_crtc_ddp_config(struct drm_crtc *crtc,
				struct cmdq_pkt *cmdq_handle)
{
	assert(crtc != NULL);
	assert(cmdq_handle == NULL);
	ddp_configs++;
}

static void mtk_drm_finish_page_flip(struct mtk_crtc *mtk_crtc)
{
	assert(mtk_crtc != NULL);
	ddp_finishes++;
}

static struct mtk_ddp_comp *
mtk_ddp_comp_for_plane(struct drm_crtc *crtc, struct drm_plane *plane,
		       unsigned int *local_layer)
{
	assert(crtc != NULL);
	assert(plane != NULL);
	assert(local_layer != NULL);
	plane_dispatch++;
	*local_layer = 0;
	return plane_comp;
}

static int mtk_ddp_comp_layer_check(struct mtk_ddp_comp *comp,
				    unsigned int local_layer,
				    struct mtk_plane_state *state)
{
	assert(comp != NULL);
	assert(local_layer == 0);
	assert(state != NULL);
	layer_checks++;
	return -EINVAL;
}

CRTC_FUNCTIONS

#define MTK_MUTEX_MAX_HANDLES 10

struct mtk_mutex {
	u8 id;
	bool claimed;
};

struct mtk_mutex_ctx {
	void *dev;
	void *clk;
	unsigned char regs[0x200];
	struct mtk_mutex mutex[MTK_MUTEX_MAX_HANDLES];
};

MUTEX_DEFINES

static unsigned int mutex_writes;
static unsigned int mutex_write_offsets[8];
static u32 mutex_write_values[8];
static unsigned char *mutex_register_base;
static unsigned int mutex_reads;
static unsigned int mutex_last_read_offset;
static u32 mutex_poll_value;
static unsigned int mutex_errors;

static u32 readl(const void *address)
{
	assert(address != NULL);
	mutex_reads++;
	mutex_last_read_offset =
		(unsigned int)((const unsigned char *)address -
			       mutex_register_base);
	return mutex_poll_value;
}

static void writel(u32 value, void *address)
{
	assert(address != NULL);
	assert(mutex_writes < sizeof(mutex_write_offsets) /
	       sizeof(mutex_write_offsets[0]));
	mutex_write_offsets[mutex_writes] =
		(unsigned int)((unsigned char *)address - mutex_register_base);
	mutex_write_values[mutex_writes++] = value;
}

#define readl_poll_timeout_atomic(address, value, condition, delay, timeout) \
	({ \
		(void)(delay); \
		(void)(timeout); \
		(value) = readl(address); \
		(condition) ? 0 : -ETIMEDOUT; \
	})

static void pr_err(const char *format, ...)
{
	va_list args;

	assert(format != NULL);
	mutex_errors++;
	va_start(args, format);
	va_end(args);
}

MUTEX_FUNCTION

struct fixture {
	struct mtk_crtc mtk;
	struct drm_device device;
	struct mtk_drm_private priv;
	struct mtk_drm_private_data priv_data;
	struct drm_plane planes[2];
	struct drm_plane_state plane_states[2];
	struct drm_framebuffer *failed[2];
	struct drm_framebuffer *failed_async[2];
};

static void reset_observers(void)
{
	framebuffer_gets = 0;
	vblank_gets = 0;
	vblank_puts = 0;
	error_logs = 0;
	ddp_configs = 0;
	ddp_finishes = 0;
	plane_dispatch = 0;
	layer_checks = 0;
	sent_count = 0;
	last_error[0] = '\0';
	plane_comp = NULL;
	vblank_get_ret = 0;
}

static void fixture_init(struct fixture *fixture, unsigned int layers)
{
	unsigned int i;

	memset(fixture, 0, sizeof(*fixture));
	assert(layers <= 2);
	fixture->mtk.base.dev = &fixture->device;
	fixture->mtk.base.index = 3;
	fixture->device.dev_private = &fixture->priv;
	fixture->priv.data = &fixture->priv_data;
	fixture->mtk.planes = fixture->planes;
	fixture->mtk.layer_nr = layers;
	fixture->mtk.failed_fbs = fixture->failed;
	fixture->mtk.failed_async_fbs = fixture->failed_async;
	for (i = 0; i < layers; i++)
		fixture->planes[i].state = &fixture->plane_states[i];
	reset_observers();
}

static void add_plane_state(struct drm_atomic_state *state,
			    struct drm_plane *plane,
			    struct drm_plane_state *old_state,
			    struct drm_plane_state *new_state)
{
	struct drm_atomic_plane *entry;

	assert(state->plane_count < sizeof(state->planes) /
	       sizeof(state->planes[0]));
	entry = &state->planes[state->plane_count++];
	entry->plane = plane;
	entry->old_state = old_state;
	entry->new_state = new_state;
}

static void test_mutex_timeout_is_returned_and_request_cancelled(void)
{
	struct mtk_mutex_ctx context;
	int ret;

	memset(&context, 0, sizeof(context));
	context.mutex[0].id = 0;
	mutex_register_base = context.regs;
	mutex_writes = 0;
	mutex_reads = 0;
	mutex_errors = 0;
	mutex_poll_value = 0;

	ret = mtk_mutex_acquire(&context.mutex[0]);
	assert(ret == -ETIMEDOUT);
	assert(mutex_errors == 1);
	assert(mutex_reads == 1);
	assert(mutex_last_read_offset == DISP_REG_MUTEX(0));
	assert(mutex_writes == 3);
	assert(mutex_write_offsets[0] == DISP_REG_MUTEX_EN(0));
	assert(mutex_write_values[0] == 1);
	assert(mutex_write_offsets[1] == DISP_REG_MUTEX(0));
	assert(mutex_write_values[1] == 1);
	assert(mutex_write_offsets[2] == DISP_REG_MUTEX(0));
	assert(mutex_write_values[2] == 0);

	mutex_writes = 0;
	mutex_reads = 0;
	mutex_errors = 0;
	mutex_poll_value = INT_MUTEX;
	ret = mtk_mutex_acquire(&context.mutex[0]);
	assert(ret == 0);
	assert(mutex_errors == 0);
	assert(mutex_reads == 1);
	assert(mutex_last_read_offset == DISP_REG_MUTEX(0));
	assert(mutex_writes == 2);
	assert(mutex_write_offsets[0] == DISP_REG_MUTEX_EN(0));
	assert(mutex_write_offsets[1] == DISP_REG_MUTEX(0));
	puts("PASS mutex-timeout-cancel");
}

static void test_finish_event_without_vblank_ref_never_puts(void)
{
	struct fixture fixture;
	struct dma_fence fence;
	struct drm_pending_vblank_event event;

	fixture_init(&fixture, 0);
	memset(&fence, 0, sizeof(fence));
	memset(&event, 0, sizeof(event));
	fixture.mtk.config_error = -EIO;
	fixture.mtk.event = &event;
	fixture.mtk.event_vblank_ref = false;
	event.base.fence = &fence;

	mtk_crtc_finish_page_flip(&fixture.mtk);
	assert(fence.error == -EIO);
	assert(sent_count == 1);
	assert(sent[0] == &event);
	assert(vblank_puts == 0);
	assert(fixture.mtk.event == NULL);
	puts("PASS finish-event-without-vblank-ref");
}

static void test_atomic_begin_vblank_get_failure_completes_event(void)
{
	struct fixture fixture;
	struct drm_atomic_state state;
	struct drm_plane_state old_state;
	struct drm_plane_state new_state;
	struct drm_crtc_state crtc_state;
	struct drm_framebuffer old_fb;
	struct dma_fence fence;
	struct drm_pending_vblank_event event;

	fixture_init(&fixture, 1);
	memset(&state, 0, sizeof(state));
	memset(&old_state, 0, sizeof(old_state));
	memset(&new_state, 0, sizeof(new_state));
	memset(&crtc_state, 0, sizeof(crtc_state));
	memset(&old_fb, 0, sizeof(old_fb));
	memset(&fence, 0xFF, sizeof(fence));
	memset(&event, 0, sizeof(event));

	old_state.crtc = &fixture.mtk.base;
	old_state.fb = &old_fb;
	fixture.plane_states[0] = old_state;
	add_plane_state(&state, &fixture.planes[0], &old_state, &new_state);
	state.crtc = &fixture.mtk.base;
	state.new_crtc_state = &crtc_state;
	event.base.fence = &fence;
	crtc_state.event = &event;
	vblank_get_ret = -ENOSPC;

	mtk_crtc_atomic_begin(&fixture.mtk.base, &state);

	assert(vblank_gets == 1);
	assert(vblank_puts == 0);
	assert(fixture.mtk.config_error == -ENOSPC);
	assert(fixture.mtk.event == NULL);
	assert(crtc_state.event == NULL);
	assert(!fixture.mtk.event_vblank_ref);
	assert(fixture.mtk.failed_fbs[0] == &old_fb);
	assert(old_fb.refs == 1);
	assert(fence.error == -ENOSPC);
	assert(sent_count == 1);
	assert(sent[0] == &event);
	assert(error_logs == 1);
	puts("PASS atomic-begin-vblank-get-failure");
}

static void test_atomic_begin_balances_successful_vblank_ref(void)
{
	struct fixture fixture;
	struct drm_atomic_state state;
	struct drm_crtc_state crtc_state;
	struct dma_fence fence;
	struct drm_pending_vblank_event event;

	fixture_init(&fixture, 0);
	memset(&state, 0, sizeof(state));
	memset(&crtc_state, 0, sizeof(crtc_state));
	memset(&fence, 0, sizeof(fence));
	memset(&event, 0, sizeof(event));
	state.crtc = &fixture.mtk.base;
	state.new_crtc_state = &crtc_state;
	event.base.fence = &fence;
	crtc_state.event = &event;

	mtk_crtc_atomic_begin(&fixture.mtk.base, &state);
	assert(vblank_gets == 1);
	assert(fixture.mtk.event == &event);
	assert(fixture.mtk.event_vblank_ref);
	assert(crtc_state.event == NULL);
	assert(event.pipe == 3);
	assert(fixture.mtk.config_error == 0);

	mtk_crtc_finish_page_flip(&fixture.mtk);
	assert(vblank_puts == 1);
	assert(sent_count == 1);
	assert(sent[0] == &event);
	assert(fixture.mtk.event == NULL);
	assert(!fixture.mtk.event_vblank_ref);
	assert(fence.error == 0);
	puts("PASS atomic-begin-balances-vblank-ref");
}

static void test_atomic_begin_duplicate_event_fails_both_events(void)
{
	struct fixture fixture;
	struct drm_atomic_state state;
	struct drm_plane_state old_state;
	struct drm_plane_state new_state;
	struct drm_crtc_state crtc_state;
	struct drm_framebuffer old_fb;
	struct dma_fence pending_fence;
	struct dma_fence queued_fence;
	struct drm_pending_vblank_event pending_event;
	struct drm_pending_vblank_event queued_event;

	fixture_init(&fixture, 1);
	memset(&state, 0, sizeof(state));
	memset(&old_state, 0, sizeof(old_state));
	memset(&new_state, 0, sizeof(new_state));
	memset(&crtc_state, 0, sizeof(crtc_state));
	memset(&old_fb, 0, sizeof(old_fb));
	memset(&pending_fence, 0xFF, sizeof(pending_fence));
	memset(&queued_fence, 0xFF, sizeof(queued_fence));
	memset(&pending_event, 0, sizeof(pending_event));
	memset(&queued_event, 0, sizeof(queued_event));

	old_state.crtc = &fixture.mtk.base;
	old_state.fb = &old_fb;
	fixture.plane_states[0] = old_state;
	add_plane_state(&state, &fixture.planes[0], &old_state, &new_state);
	state.crtc = &fixture.mtk.base;
	state.new_crtc_state = &crtc_state;
	pending_event.base.fence = &pending_fence;
	queued_event.base.fence = &queued_fence;
	fixture.mtk.event = &pending_event;
	fixture.mtk.event_vblank_ref = true;
	crtc_state.event = &queued_event;

	mtk_crtc_atomic_begin(&fixture.mtk.base, &state);

	assert(vblank_gets == 0);
	assert(vblank_puts == 1);
	assert(fixture.mtk.config_error == -EBUSY);
	assert(fixture.mtk.event == NULL);
	assert(crtc_state.event == NULL);
	assert(pending_fence.error == -EBUSY);
	assert(queued_fence.error == -EBUSY);
	assert(sent_count == 2);
	assert(sent[0] == &pending_event);
	assert(sent[1] == &queued_event);
	assert(error_logs == 2);
	puts("PASS atomic-begin-duplicate-event");
}

static void test_failure_pins_scanout_and_completes_events(void)
{
	struct fixture fixture;
	struct drm_atomic_state state;
	struct drm_plane_state plane0_old;
	struct drm_plane_state plane0_new;
	struct drm_crtc_state crtc_state;
	struct drm_framebuffer fb0_old;
	struct drm_framebuffer fb1_old;
	struct drm_framebuffer fb_new;
	struct dma_fence pending_fence;
	struct dma_fence queued_fence;
	struct drm_pending_vblank_event pending_event;
	struct drm_pending_vblank_event queued_event;

	fixture_init(&fixture, 2);
	memset(&state, 0, sizeof(state));
	memset(&plane0_old, 0, sizeof(plane0_old));
	memset(&plane0_new, 0, sizeof(plane0_new));
	memset(&crtc_state, 0, sizeof(crtc_state));
	memset(&fb0_old, 0, sizeof(fb0_old));
	memset(&fb1_old, 0, sizeof(fb1_old));
	memset(&fb_new, 0, sizeof(fb_new));
	memset(&pending_fence, 0xFF, sizeof(pending_fence));
	memset(&queued_fence, 0xFF, sizeof(queued_fence));
	memset(&pending_event, 0, sizeof(pending_event));
	memset(&queued_event, 0, sizeof(queued_event));

	plane0_old.crtc = &fixture.mtk.base;
	plane0_old.fb = &fb0_old;
	plane0_new.crtc = &fixture.mtk.base;
	plane0_new.fb = &fb_new;
	fixture.plane_states[0] = plane0_old;
	fixture.plane_states[1].crtc = &fixture.mtk.base;
	fixture.plane_states[1].fb = &fb1_old;
	add_plane_state(&state, &fixture.planes[0], &plane0_old, &plane0_new);
	state.crtc = &fixture.mtk.base;
	state.new_crtc_state = &crtc_state;

	pending_event.base.fence = &pending_fence;
	queued_event.base.fence = &queued_fence;
	crtc_state.event = &queued_event;
	fixture.mtk.event = &pending_event;
	fixture.mtk.event_vblank_ref = true;
	fixture.mtk.pending_needs_vblank = true;
	fixture.mtk.config_updating = true;

	mtk_crtc_fail_config_locked(&fixture.mtk, &state, -ETIMEDOUT);

	assert(fixture.mtk.config_error == -ETIMEDOUT);
	assert(fixture.mtk.failed_fbs[0] == &fb0_old);
	assert(fixture.mtk.failed_fbs[1] == &fb1_old);
	assert(fb0_old.refs == 1);
	assert(fb1_old.refs == 1);
	assert(fb_new.refs == 0);
	assert(framebuffer_gets == 2);
	assert(error_logs == 1);
	assert(strcmp(last_error,
		      "CRTC 3 configuration failed: -110; reboot required\n") == 0);
	assert(pending_fence.error == -ETIMEDOUT);
	assert(queued_fence.error == -ETIMEDOUT);
	assert(sent_count == 2);
	assert(sent[0] == &pending_event);
	assert(sent[1] == &queued_event);
	assert(vblank_puts == 1);
	assert(fixture.mtk.event == NULL);
	assert(crtc_state.event == NULL);
	assert(!fixture.mtk.event_vblank_ref);
	assert(!fixture.mtk.pending_needs_vblank);
	assert(!fixture.mtk.config_updating);

	assert(mtk_crtc_config_failed(&fixture.mtk, &state));
	assert(mtk_crtc_atomic_check(&fixture.mtk.base, &state) == -ETIMEDOUT);
	mtk_crtc_fail_config_locked(&fixture.mtk, &state, -ETIMEDOUT);
	assert(framebuffer_gets == 2);
	assert(fb0_old.refs == 1);
	assert(fb1_old.refs == 1);
	assert(error_logs == 1);
	assert(sent_count == 2);
	puts("PASS failure-pins-scanout-and-completes-events");
}

static void test_async_fault_keeps_distinct_swapped_out_fb(void)
{
	struct fixture fixture;
	struct drm_atomic_state first;
	struct drm_atomic_state later;
	struct drm_atomic_state repeated;
	struct drm_plane_state async_state;
	struct drm_plane_state later_state;
	struct drm_crtc_state crtc_state;
	struct drm_framebuffer first_fb;
	struct drm_framebuffer later_fb;

	fixture_init(&fixture, 1);
	memset(&first, 0, sizeof(first));
	memset(&later, 0, sizeof(later));
	memset(&repeated, 0, sizeof(repeated));
	memset(&async_state, 0, sizeof(async_state));
	memset(&later_state, 0, sizeof(later_state));
	memset(&crtc_state, 0, sizeof(crtc_state));
	memset(&first_fb, 0, sizeof(first_fb));
	memset(&later_fb, 0, sizeof(later_fb));

	async_state.crtc = &fixture.mtk.base;
	async_state.fb = &first_fb;
	later_state.crtc = &fixture.mtk.base;
	later_state.fb = &later_fb;
	first.async_update = true;
	first.crtc = &fixture.mtk.base;
	first.new_crtc_state = &crtc_state;
	add_plane_state(&first, &fixture.planes[0], NULL, &async_state);

	mtk_crtc_fail_config_locked(&fixture.mtk, &first, -EIO);
	assert(fixture.mtk.failed_fbs[0] == &first_fb);
	assert(fixture.mtk.failed_async_fbs[0] == NULL);
	assert(first_fb.refs == 1);
	assert(framebuffer_gets == 1);

	later.async_update = true;
	later.crtc = &fixture.mtk.base;
	later.new_crtc_state = &crtc_state;
	add_plane_state(&later, &fixture.planes[0], NULL, &later_state);
	mtk_crtc_fail_config_locked(&fixture.mtk, &later, -EIO);
	assert(fixture.mtk.failed_fbs[0] == &first_fb);
	assert(fixture.mtk.failed_async_fbs[0] == &later_fb);
	assert(first_fb.refs == 1);
	assert(later_fb.refs == 1);
	assert(framebuffer_gets == 2);

	repeated.async_update = true;
	repeated.crtc = &fixture.mtk.base;
	repeated.new_crtc_state = &crtc_state;
	add_plane_state(&repeated, &fixture.planes[0], NULL, &later_state);
	mtk_crtc_fail_config_locked(&fixture.mtk, &repeated, -EIO);
	assert(fixture.mtk.failed_async_fbs[0] == &later_fb);
	assert(later_fb.refs == 1);
	assert(framebuffer_gets == 2);
	assert(error_logs == 1);
	puts("PASS async-fault-keeps-distinct-swapped-out-fb");
}

static void test_post_error_irq_stops_reprogramming(void)
{
	struct fixture fixture;

	fixture_init(&fixture, 0);
	fixture.priv_data.shadow_register = false;

	mtk_crtc_ddp_irq(&fixture.mtk.base);
	assert(ddp_configs == 1);
	assert(ddp_finishes == 1);

	reset_observers();
	fixture.mtk.config_error = -ETIMEDOUT;
	mtk_crtc_ddp_irq(&fixture.mtk.base);
	assert(ddp_configs == 0);
	assert(ddp_finishes == 1);
	puts("PASS post-error-irq-stops-reprogramming");
}

static void test_plane_check_rejects_sticky_error_before_dispatch(void)
{
	struct fixture fixture;
	struct mtk_plane_state plane_state;
	struct mtk_ddp_comp component;

	fixture_init(&fixture, 1);
	memset(&plane_state, 0, sizeof(plane_state));
	memset(&component, 0, sizeof(component));

	fixture.mtk.config_error = -ETIMEDOUT;
	plane_comp = &component;
	assert(mtk_crtc_plane_check(&fixture.mtk.base, &fixture.planes[0],
				    &plane_state) == -ETIMEDOUT);
	assert(plane_dispatch == 0);
	assert(layer_checks == 0);

	fixture.mtk.config_error = 0;
	assert(mtk_crtc_plane_check(&fixture.mtk.base, &fixture.planes[0],
				    &plane_state) == -EINVAL);
	assert(plane_dispatch == 1);
	assert(layer_checks == 1);
	puts("PASS plane-check-rejects-sticky-error");
}

int main(void)
{
	test_mutex_timeout_is_returned_and_request_cancelled();
	test_finish_event_without_vblank_ref_never_puts();
	test_atomic_begin_vblank_get_failure_completes_event();
	test_atomic_begin_balances_successful_vblank_ref();
	test_atomic_begin_duplicate_event_fails_both_events();
	test_failure_pins_scanout_and_completes_events();
	test_async_fault_keeps_distinct_swapped_out_fb();
	test_post_error_irq_stops_reprogramming();
	test_plane_check_rejects_sticky_error_before_dispatch();
	return 0;
}
'''
    harness = (
        harness.replace("CRTC_FUNCTIONS", crtc_functions)
        .replace("MUTEX_DEFINES", mutex_defines)
        .replace("MUTEX_FUNCTION", mutex_function)
    )

    with tempfile.TemporaryDirectory(prefix="z1_crtc_fault_test_") as temp:
        c_file = Path(temp) / "crtc_fault_test.c"
        executable = Path(temp) / "crtc_fault_test"
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
