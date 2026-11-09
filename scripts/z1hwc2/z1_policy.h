/*
 * z1_policy.h — pure composition policy for the Z1 HWC2 HAL.
 *
 * Kept free of Android/DRM dependencies so that it can be unit-tested on the
 * host (see test/z1hwc2_policy_test.cpp).  The HAL only supplies facts; every
 * decision is made here.
 *
 * The rules, in order of authority:
 *
 *  1. The client target is always composited on the bottom-most OVL plane, so
 *     CLIENT layers must form the bottom of the z order and DEVICE layers a
 *     contiguous *top* suffix.  Therefore the decision walk goes from the top
 *     of the stack downwards, and the first layer that cannot be device
 *     composition turns every layer below it into CLIENT as well.
 *
 *  2. A layer the client already marked HWC2_COMPOSITION_CLIENT is never
 *     changed (HWC2 spec: "The device must not request any composition type
 *     changes for layers of this type").
 *
 *  3. Only DEVICE / CURSOR / SOLID_COLOR / SIDEBAND / INVALID layers may be
 *     demoted to CLIENT (SurfaceFlinger's HWComposer::validateChange() accepts
 *     exactly that direction).
 *
 *  4. Layers that arrive as INVALID (Android 9 defaults every layer to
 *     Composition::Invalid and relies on the HAL for the first decision) get a
 *     type from this policy, which is then reported through
 *     getChangedCompositionTypes().
 *
 *  5. Hardware limits: one plane per layer, no scaling (source size must equal
 *     display-frame size), no transform, fully on-screen, supported format and
 *     a buffer that can actually be imported for scanout.
 *
 * hwc2_composition_t values (stable ABI):
 *   0 INVALID, 1 CLIENT, 2 DEVICE, 3 SOLID_COLOR, 4 CURSOR, 5 SIDEBAND
 */

#ifndef Z1_POLICY_H
#define Z1_POLICY_H

#include <stdint.h>

enum {
	Z1_TYPE_INVALID = 0,
	Z1_TYPE_CLIENT = 1,
	Z1_TYPE_DEVICE = 2,
	Z1_TYPE_SOLID_COLOR = 3,
	Z1_TYPE_CURSOR = 4,
	Z1_TYPE_SIDEBAND = 5,
};

enum {
	Z1_POLICY_DEVICE = 0,
	Z1_POLICY_CLIENT = 1,
};

struct z1_policy_layer {
	uint64_t id;		/* layer handle, only for stable tie-breaking */
	uint32_t z;
	int requested_type;	/* hwc2_composition_t as passed to validate */

	/* facts about the buffer */
	int handle_ok;		/* gralloc handle looks sane (magic/geometry/fd) */
	int format_ok;		/* bpp and DRM fourcc known */
	int importable;		/* DRM accepted it for scanout (contiguous) */
	int buf_w, buf_h;	/* buffer dimensions in pixels */

	/* layer geometry */
	int transform;		/* hwc_transform_t; 0 == none */
	int frame_l, frame_t, frame_r, frame_b;
	int have_crop;
	int crop_l, crop_t, crop_r, crop_b;

	/* display */
	int display_w, display_h;
};

struct z1_policy_result {
	int type;		/* Z1_POLICY_DEVICE / Z1_POLICY_CLIENT */
	int report;		/* 1: must be reported as a composition change */
	const char *why;	/* human readable reason when CLIENT */
};

/* Why a layer cannot be device-composited; NULL when it can. */
static inline const char *z1_policy_device_block_reason(
	const struct z1_policy_layer *l)
{
	int fw, fh, cw, ch;

	if (!l->handle_ok)
		return "no usable handle";
	if (!l->format_ok)
		return "unsupported format";
	if (!l->importable)
		return "not importable as contiguous dma-buf";
	if (l->transform != 0)
		return "transform";
	fw = l->frame_r - l->frame_l;
	fh = l->frame_b - l->frame_t;
	if (fw <= 0 || fh <= 0)
		return "empty display frame";
	if (l->frame_l < 0 || l->frame_t < 0 ||
	    l->frame_r > l->display_w || l->frame_b > l->display_h)
		return "partially offscreen";
	cw = l->have_crop ? l->crop_r - l->crop_l : l->buf_w;
	ch = l->have_crop ? l->crop_b - l->crop_t : l->buf_h;
	if (cw <= 0 || ch <= 0)
		return "empty source crop";
	if (cw != fw || ch != fh)
		return "scaling";
	if (cw > l->buf_w || ch > l->buf_h)
		return "crop outside buffer";
	if (fw > l->display_w || fh > l->display_h)
		return "larger than display";
	return 0;
}

/*
 * Decide the composition type of every layer.
 *
 * `layers` must be sorted ascending by z (ties by id) — the HAL does that.
 * `plane_slots` is the number of planes available to DEVICE layers, i.e. the
 * total plane count minus the one reserved for the client target.
 *
 * Returns the number of layers assigned to device composition.
 */
static inline int z1_policy_decide(const struct z1_policy_layer *layers, int n,
				   int plane_slots, int force_client,
				   struct z1_policy_result *out)
{
	int used = 0;
	int client_above = 0;
	int i;

	for (i = 0; i < n; i++) {
		out[i].type = Z1_POLICY_CLIENT;
		out[i].report = 0;
		out[i].why = "not considered";
	}

	/* Walk from the top of the stack downwards. */
	for (i = n - 1; i >= 0; i--) {
		const struct z1_policy_layer *l = &layers[i];
		const char *why;
		int ok;

		if (l->requested_type == Z1_TYPE_CLIENT) {
			/* The client owns this type; never touch it. */
			out[i].type = Z1_POLICY_CLIENT;
			out[i].report = 0;
			out[i].why = "client requested";
			client_above = 1;
			continue;
		}
		if (l->requested_type == Z1_TYPE_SOLID_COLOR ||
		    l->requested_type == Z1_TYPE_SIDEBAND) {
			/* We only know how to scan out buffers.  SOLID_COLOR
			 * would ignore setLayerColor() and SIDEBAND needs a
			 * decoder feed we do not have, so both must be
			 * composited by the client. */
			out[i].type = Z1_POLICY_CLIENT;
			out[i].report = 1;
			out[i].why = (l->requested_type == Z1_TYPE_SOLID_COLOR)
					     ? "solid colour unsupported"
					     : "sideband unsupported";
			client_above = 1;
			continue;
		}

		why = z1_policy_device_block_reason(l);
		ok = (why == 0);
		if (ok && client_above) {
			ok = 0;
			why = "a client layer is above it";
		}
		if (ok && used >= plane_slots) {
			ok = 0;
			why = "no free plane";
		}
		if (ok && force_client) {
			ok = 0;
			why = "forced client by debug property";
		}

		if (ok) {
			out[i].type = Z1_POLICY_DEVICE;
			out[i].why = 0;
			/* INVALID / CURSOR layers must be told about the
			 * decision; DEVICE stays DEVICE silently. */
			out[i].report =
				(l->requested_type != Z1_TYPE_DEVICE) ? 1 : 0;
			used++;
			continue;
		}

		out[i].type = Z1_POLICY_CLIENT;
		out[i].why = why;
		out[i].report = (l->requested_type != Z1_TYPE_CLIENT) ? 1 : 0;
		client_above = 1;
	}

	return used;
}

#endif /* Z1_POLICY_H */
