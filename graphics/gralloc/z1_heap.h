/*
 * z1_heap.h — physically contiguous dma-buf allocation for the Z1 gralloc HAL.
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * See z1_heap.cpp for the full rationale, the backend priority chain and the
 * kernel evidence (mediatek-drm only imports contiguous sg_tables).
 */

#ifndef Z1_HEAP_H_
#define Z1_HEAP_H_

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Capability bits reported by z1_heap_alloc_ex().
 *
 * Z1_HEAP_CAP_CONTIGUOUS means the returned dma-buf is backed by physically
 * contiguous memory, so mediatek-drm accepts it:
 *
 *   mtk_gem_prime_import_sg_table()
 *       drm_prime_get_contiguous_size(sgt) >= attach->dmabuf->size
 *
 * Only the CMA dma-heap and the mediatek-drm dumb-buffer backends set it.
 */
#define Z1_HEAP_CAP_CONTIGUOUS 0x0001u

/*
 * private_handle_t::flags bit 2 ("PRIV_FLAGS_CONTIG" in the HWC), set by
 * gralloc.cpp for buffers allocated from a contiguous backend so the custom
 * HWC2 HAL can log which layers OVL can really scan out.
 *
 * It is defined here instead of in gralloc_priv.h only because that header is
 * outside this change's edit scope.  The handle ABI is untouched: "flags" is
 * an existing int field, bit 2 is unused by PRIV_FLAGS_FRAMEBUFFER (0x1) and
 * PRIV_FLAGS_DMABUF (0x2), and gralloc.cpp static_asserts that they cannot
 * alias (see gralloc_alloc_buffer()).
 */
#define Z1_PRIV_FLAGS_CONTIG 0x00000004

/*
 * Allocate a dma-buf of at least `size` bytes.
 *
 * Returns a file descriptor (>= 0) or a negative errno.  The buffer is page
 * aligned up (dma-buf heaps require page aligned allocations anyway).
 *
 * `tag` is only used in log messages ("gralloc" from the HAL, "z1contigtest"
 * from the on-board verification tool).
 *
 * Chain: CMA dma-buf heap -> mediatek-drm dumb buffer -> lima GEM.  The caller
 * (gralloc.cpp) still falls back to ashmem when this returns < 0.
 */
int z1_heap_alloc(size_t size, const char *tag);

/*
 * Same as z1_heap_alloc(), plus:
 *   *out_caps     Z1_HEAP_CAP_* bitmask for the returned buffer (may be NULL),
 *   *out_backend  static backend name: "dma-heap", "drm-dumb", "lima" or
 *                 "none" on failure (may be NULL).  The string is valid for the
 *                 process lifetime.  The log line carries the detail (heap node
 *                 name / card path), e.g.
 *                 "dma-heap backend selected (default_cma_region): ...".
 *
 * Testing aid: if the environment variable Z1_HEAP_FORCE is set to "cma"
 * (or "dma-heap"/"heap"), "dumb" (or "drm") or "lima", only that backend is
 * tried and its failure is returned as-is.  gralloc never sets it (it runs in
 * SurfaceFlinger's environment); z1contigtest uses it to A/B the backends on
 * the board (`Z1_HEAP_FORCE=dumb ./z1contigtest`).
 */
int z1_heap_alloc_ex(size_t size, const char *tag, unsigned int *out_caps,
                     const char **out_backend);

#ifdef __cplusplus
}
#endif

#endif /* Z1_HEAP_H_ */
