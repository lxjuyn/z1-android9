/*
 * Copyright (C) 2026 Z1 Android 9 bring-up contributors
 * SPDX-License-Identifier: Apache-2.0
 */

#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include <drm/drm.h>
#include <hardware/gralloc.h>
#include <linux/dma-buf.h>
#include <log/log.h>

#include "lima_allocator.h"

struct drm_lima_gem_create_z1 {
    uint32_t size;
    uint32_t flags;
    uint32_t handle;
    uint32_t pad;
};

#define DRM_IOCTL_LIMA_GEM_CREATE_Z1 \
    DRM_IOWR(DRM_COMMAND_BASE + 0x01, struct drm_lima_gem_create_z1)

static int ioctl_retry(int fd, unsigned long request, void* data)
{
    int result;
    do {
        result = ioctl(fd, request, data);
    } while (result < 0 && errno == EINTR);
    return result;
}

int z1_lima_alloc_dmabuf(size_t size)
{
    if (!size || size > UINT32_MAX)
        return -EINVAL;

    int drm_fd = open("/dev/dri/renderD128", O_RDWR | O_CLOEXEC);
    if (drm_fd < 0)
        return -errno;

    struct drm_lima_gem_create_z1 create = {};
    create.size = static_cast<uint32_t>(size);
    if (ioctl_retry(drm_fd, DRM_IOCTL_LIMA_GEM_CREATE_Z1, &create) < 0) {
        const int error = errno;
        close(drm_fd);
        return -error;
    }

    struct drm_prime_handle prime = {};
    prime.handle = create.handle;
    prime.flags = DRM_CLOEXEC | DRM_RDWR;
    prime.fd = -1;
    int result = ioctl_retry(drm_fd, DRM_IOCTL_PRIME_HANDLE_TO_FD, &prime);
    int error = errno;

    struct drm_gem_close close_handle = {};
    close_handle.handle = create.handle;
    if (ioctl_retry(drm_fd, DRM_IOCTL_GEM_CLOSE, &close_handle) < 0 && result == 0) {
        error = errno;
        result = -1;
    }
    close(drm_fd);

    if (result < 0) {
        if (prime.fd >= 0)
            close(prime.fd);
        return -error;
    }
    return prime.fd;
}

int z1_lima_sync_dmabuf(int fd, bool start, int usage)
{
    uint64_t flags = start ? DMA_BUF_SYNC_START : DMA_BUF_SYNC_END;
    if (usage & GRALLOC_USAGE_SW_READ_MASK)
        flags |= DMA_BUF_SYNC_READ;
    if (usage & GRALLOC_USAGE_SW_WRITE_MASK)
        flags |= DMA_BUF_SYNC_WRITE;
    if (!(flags & DMA_BUF_SYNC_RW))
        return 0;

    struct dma_buf_sync sync = {};
    sync.flags = flags;
    if (ioctl_retry(fd, DMA_BUF_IOCTL_SYNC, &sync) < 0)
        return -errno;
    return 0;
}
