/*
 * Copyright (C) 2008 The Android Open Source Project
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#include <cutils/ashmem.h>
#include <cutils/atomic.h>
#include <log/log.h>

#include <hardware/gralloc.h>
#include <hardware/hardware.h>

#include "gralloc_priv.h"
#include "gr.h"
#include "pixel_copy.h"
#ifdef Z1_USE_LIMA
#include "z1_heap.h"

/*
 * z1_heap.cpp is compiled by z1a9_gpt/scripts/z1_build_hal_stubs.sh.  The AOSP
 * Android.mk next to this file (outside this change's edit scope) lists only
 * lima_allocator.cpp, so reference the new entry point weakly: when z1_heap.o
 * is linked in (the harness) the physically contiguous CMA/dumb backends are
 * used, and when it is not, allocation falls through to ashmem instead of
 * breaking the link.
 */
extern "C" int z1_heap_alloc_ex(size_t, const char *, unsigned int *,
                                const char **) __attribute__((weak));

/* "PRIV_FLAGS_CONTIG" lives in z1_heap.h (gralloc_priv.h is outside this
 * change's edit scope).  It must not alias an existing flag; the 64-byte handle
 * ABI itself is untouched. */
static_assert((Z1_PRIV_FLAGS_CONTIG &
               (private_handle_t::PRIV_FLAGS_FRAMEBUFFER |
                private_handle_t::PRIV_FLAGS_DMABUF)) == 0,
              "Z1_PRIV_FLAGS_CONTIG collides with an existing handle flag");
#endif

/*****************************************************************************/

struct gralloc_context_t {
    alloc_device_t  device;
    /* our private data here */
};

static int gralloc_alloc_buffer(alloc_device_t* dev,
        size_t size, int usage, buffer_handle_t* pHandle);

/*****************************************************************************/

int fb_device_open(const hw_module_t* module, const char* name,
        hw_device_t** device);

static int gralloc_device_open(const hw_module_t* module, const char* name,
        hw_device_t** device);

extern int gralloc_lock(gralloc_module_t const* module,
        buffer_handle_t handle, int usage,
        int l, int t, int w, int h,
        void** vaddr);

extern int gralloc_unlock(gralloc_module_t const* module, 
        buffer_handle_t handle);

extern int gralloc_register_buffer(gralloc_module_t const* module,
        buffer_handle_t handle);

extern int gralloc_unregister_buffer(gralloc_module_t const* module,
        buffer_handle_t handle);

/*****************************************************************************/

static struct hw_module_methods_t gralloc_module_methods = {
        .open = gralloc_device_open
};

struct private_module_t HAL_MODULE_INFO_SYM = {
    .base = {
        .common = {
            .tag = HARDWARE_MODULE_TAG,
            .version_major = 1,
            .version_minor = 0,
            .id = GRALLOC_HARDWARE_MODULE_ID,
            .name = "Graphics Memory Allocator Module",
            .author = "The Android Open Source Project",
            .methods = &gralloc_module_methods
        },
        .registerBuffer = gralloc_register_buffer,
        .unregisterBuffer = gralloc_unregister_buffer,
        .lock = gralloc_lock,
        .unlock = gralloc_unlock,
    },
    .framebuffer = 0,
    .flags = 0,
    .numBuffers = 0,
    .bufferMask = 0,
    .lock = PTHREAD_MUTEX_INITIALIZER,
    .currentBuffer = 0,
};

/*****************************************************************************/

static int gralloc_alloc_framebuffer_locked(alloc_device_t* dev,
        size_t size, int usage, buffer_handle_t* pHandle)
{
    private_module_t* m = reinterpret_cast<private_module_t*>(
            dev->common.module);

    // allocate the framebuffer
    if (m->framebuffer == NULL) {
        // initialize the framebuffer, the framebuffer is mapped once
        // and forever.
        int err = mapFrameBufferLocked(m);
        if (err < 0) {
            return err;
        }
    }

    // Z1 is a single RGB565 framebuffer. The producer may request RGBA8888,
    // so allocate the requested pixel layout, never the physical fb size.
    // Always use a shadow buffer: this also keeps Binder imports mmap-able.
    int newUsage = (usage & ~GRALLOC_USAGE_HW_FB) | GRALLOC_USAGE_HW_2D;
    return gralloc_alloc_buffer(dev, size, newUsage, pHandle);
}

static int gralloc_alloc_framebuffer(alloc_device_t* dev,
        size_t size, int usage, buffer_handle_t* pHandle)
{
    private_module_t* m = reinterpret_cast<private_module_t*>(
            dev->common.module);
    pthread_mutex_lock(&m->lock);
    int err = gralloc_alloc_framebuffer_locked(dev, size, usage, pHandle);
    pthread_mutex_unlock(&m->lock);
    return err;
}

static int gralloc_alloc_buffer(alloc_device_t* dev,
        size_t size, int usage, buffer_handle_t* pHandle)
{
    int err = 0;
    int fd = -1;
    int flags = 0;

    if (size == 0 || size > size_t(INT_MAX) - (PAGE_SIZE - 1))
        return -EINVAL;
    size = roundUpToPageSize(size);
    
#ifdef Z1_USE_LIMA
    /*
     * Physically contiguous dma-buf (CMA dma-heap -> mediatek-drm dumb ->
     * lima).  This replaces the direct z1_lima_alloc_dmabuf() call: lima's GEM
     * is drm_gem_shmem (scattered pages), and mediatek-drm rejects a
     * non-contiguous sg_table in mtk_gem_prime_import_sg_table(), so OVL
     * composition could never scan those buffers out.  See z1_heap.cpp.
     */
    if (z1_heap_alloc_ex) {
        unsigned int caps = 0;

        fd = z1_heap_alloc_ex(size, "gralloc", &caps, NULL);
        if (fd >= 0) {
            flags |= private_handle_t::PRIV_FLAGS_DMABUF;
            if (caps & Z1_HEAP_CAP_CONTIGUOUS)
                flags |= Z1_PRIV_FLAGS_CONTIG;
        } else {
            ALOGW("dma-buf allocation failed (%s); using ashmem fallback",
                  strerror(-fd));
        }
    } else {
        ALOGW("z1_heap not linked; using ashmem fallback");
    }
#endif

    if (fd < 0)
        fd = ashmem_create_region("gralloc-buffer", size);
    if (fd < 0) {
        ALOGE("couldn't create ashmem (%s)", strerror(-errno));
        err = -errno;
    }

    if (err == 0) {
        private_handle_t* hnd = new private_handle_t(fd, size, flags);
        gralloc_module_t* module = reinterpret_cast<gralloc_module_t*>(
                dev->common.module);
        err = mapBuffer(module, hnd);
        if (err == 0) {
            *pHandle = hnd;
        } else {
            close(fd);
            delete hnd;
        }
    }
    
    ALOGE_IF(err, "gralloc failed err=%s", strerror(-err));
    
    return err;
}

/*****************************************************************************/

static int gralloc_alloc(alloc_device_t* dev,
        int width, int height, int format, int usage,
        buffer_handle_t* pHandle, int* pStride)
{
    if (!pHandle || !pStride || width <= 0 || height <= 0)
        return -EINVAL;

    int bytesPerPixel = 0;
    switch (format) {
        case HAL_PIXEL_FORMAT_RGBA_FP16:
            bytesPerPixel = 8;
            break;
        case HAL_PIXEL_FORMAT_RGBA_8888:
        case HAL_PIXEL_FORMAT_RGBX_8888:
        case HAL_PIXEL_FORMAT_BGRA_8888:
            bytesPerPixel = 4;
            break;
        case HAL_PIXEL_FORMAT_RGB_888:
            bytesPerPixel = 3;
            break;
        case HAL_PIXEL_FORMAT_RGB_565:
        case HAL_PIXEL_FORMAT_RAW16:
            bytesPerPixel = 2;
            break;
        default:
            return -EINVAL;
    }

    size_t stride, size;
    int layoutError = z1_buffer_layout(width, height, bytesPerPixel, &stride, &size);
    if (layoutError) return layoutError;

    int err;
    if (usage & GRALLOC_USAGE_HW_FB) {
        err = gralloc_alloc_framebuffer(dev, size, usage, pHandle);
    } else {
        err = gralloc_alloc_buffer(dev, size, usage, pHandle);
    }

    if (err < 0) {
        return err;
    }

    private_handle_t* hnd = const_cast<private_handle_t*>(
            static_cast<const private_handle_t*>(*pHandle));
    hnd->width = width;
    hnd->height = height;
    hnd->stride = stride;
    hnd->format = format;
    *pStride = stride;
    return 0;
}

static int gralloc_free(alloc_device_t* dev,
        buffer_handle_t handle)
{
    if (private_handle_t::validate(handle) < 0)
        return -EINVAL;

    private_handle_t const* hnd = reinterpret_cast<private_handle_t const*>(handle);
    if (hnd->flags & private_handle_t::PRIV_FLAGS_FRAMEBUFFER) {
        // free this buffer
        private_module_t* m = reinterpret_cast<private_module_t*>(
                dev->common.module);
        const size_t bufferSize = m->finfo.line_length * m->info.yres;
        int index = (hnd->base - m->framebuffer->base) / bufferSize;
        m->bufferMask &= ~(1<<index); 
    } else { 
        gralloc_module_t* module = reinterpret_cast<gralloc_module_t*>(
                dev->common.module);
        terminateBuffer(module, const_cast<private_handle_t*>(hnd));
    }

    close(hnd->fd);
    delete hnd;
    return 0;
}

/*****************************************************************************/

static int gralloc_close(struct hw_device_t *dev)
{
    gralloc_context_t* ctx = reinterpret_cast<gralloc_context_t*>(dev);
    if (ctx) {
        /* TODO: keep a list of all buffer_handle_t created, and free them
         * all here.
         */
        free(ctx);
    }
    return 0;
}

int gralloc_device_open(const hw_module_t* module, const char* name,
        hw_device_t** device)
{
    int status = -EINVAL;
    if (!strcmp(name, GRALLOC_HARDWARE_GPU0)) {
        gralloc_context_t *dev;
        dev = (gralloc_context_t*)malloc(sizeof(*dev));
        if (!dev) return -ENOMEM;

        /* initialize our state here */
        memset(dev, 0, sizeof(*dev));

        /* initialize the procs */
        dev->device.common.tag = HARDWARE_DEVICE_TAG;
        dev->device.common.version = 0;
        dev->device.common.module = const_cast<hw_module_t*>(module);
        dev->device.common.close = gralloc_close;

        dev->device.alloc   = gralloc_alloc;
        dev->device.free    = gralloc_free;

        *device = &dev->device.common;
        status = 0;
    } else {
        status = fb_device_open(module, name, device);
    }
    return status;
}
