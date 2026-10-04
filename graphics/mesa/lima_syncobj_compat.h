/* SPDX-License-Identifier: MIT */
#ifndef Z1_MESA_LIMA_SYNCOBJ_COMPAT_H
#define Z1_MESA_LIMA_SYNCOBJ_COMPAT_H

#include <stdint.h>

#include <drm.h>
#include <xf86drm.h>

/* Android 9's libdrm predates these syncobj UAPI wrappers. The Linux7
 * kernel implements the stable DRM syncobj ioctls, so issue them directly. */
struct z1_drm_syncobj_create {
    uint32_t handle;
    uint32_t flags;
};

struct z1_drm_syncobj_destroy {
    uint32_t handle;
    uint32_t pad;
};

struct z1_drm_syncobj_handle {
    uint32_t handle;
    uint32_t flags;
    int32_t fd;
    uint32_t pad;
    uint64_t point;
};

struct z1_drm_syncobj_wait {
    uint64_t handles;
    int64_t timeout_nsec;
    uint32_t count_handles;
    uint32_t flags;
    uint32_t first_signaled;
    uint32_t pad;
    uint64_t deadline_nsec;
};

#define Z1_DRM_SYNCOBJ_CREATE_SIGNALED (1u << 0)
#define Z1_DRM_SYNCOBJ_FD_TO_HANDLE_IMPORT_SYNC_FILE (1u << 0)
#define Z1_DRM_SYNCOBJ_HANDLE_TO_FD_EXPORT_SYNC_FILE (1u << 0)

#define Z1_DRM_IOCTL_SYNCOBJ_CREATE \
    DRM_IOWR(0xBF, struct z1_drm_syncobj_create)
#define Z1_DRM_IOCTL_SYNCOBJ_DESTROY \
    DRM_IOWR(0xC0, struct z1_drm_syncobj_destroy)
#define Z1_DRM_IOCTL_SYNCOBJ_HANDLE_TO_FD \
    DRM_IOWR(0xC1, struct z1_drm_syncobj_handle)
#define Z1_DRM_IOCTL_SYNCOBJ_FD_TO_HANDLE \
    DRM_IOWR(0xC2, struct z1_drm_syncobj_handle)
#define Z1_DRM_IOCTL_SYNCOBJ_WAIT \
    DRM_IOWR(0xC3, struct z1_drm_syncobj_wait)

static inline int z1_drmSyncobjCreate(int fd, uint32_t flags, uint32_t* handle)
{
    struct z1_drm_syncobj_create args = {0};
    args.flags = flags;
    int ret = drmIoctl(fd, Z1_DRM_IOCTL_SYNCOBJ_CREATE, &args);
    if (!ret)
        *handle = args.handle;
    return ret;
}

static inline int z1_drmSyncobjDestroy(int fd, uint32_t handle)
{
    struct z1_drm_syncobj_destroy args = {0};
    args.handle = handle;
    return drmIoctl(fd, Z1_DRM_IOCTL_SYNCOBJ_DESTROY, &args);
}

static inline int z1_drmSyncobjImportSyncFile(int fd, uint32_t handle,
                                               int sync_file_fd)
{
    struct z1_drm_syncobj_handle args = {0};
    args.handle = handle;
    args.flags = Z1_DRM_SYNCOBJ_FD_TO_HANDLE_IMPORT_SYNC_FILE;
    args.fd = sync_file_fd;
    return drmIoctl(fd, Z1_DRM_IOCTL_SYNCOBJ_FD_TO_HANDLE, &args);
}

static inline int z1_drmSyncobjExportSyncFile(int fd, uint32_t handle,
                                               int* sync_file_fd)
{
    struct z1_drm_syncobj_handle args = {0};
    args.handle = handle;
    args.flags = Z1_DRM_SYNCOBJ_HANDLE_TO_FD_EXPORT_SYNC_FILE;
    args.fd = -1;
    int ret = drmIoctl(fd, Z1_DRM_IOCTL_SYNCOBJ_HANDLE_TO_FD, &args);
    if (!ret)
        *sync_file_fd = args.fd;
    return ret;
}

static inline int z1_drmSyncobjWait(int fd, uint32_t* handles,
                                    unsigned int count_handles,
                                    int64_t timeout_nsec, unsigned int flags,
                                    uint32_t* first_signaled)
{
    struct z1_drm_syncobj_wait args = {0};
    args.handles = (uint64_t)(uintptr_t)handles;
    args.timeout_nsec = timeout_nsec;
    args.count_handles = count_handles;
    args.flags = flags;
    int ret = drmIoctl(fd, Z1_DRM_IOCTL_SYNCOBJ_WAIT, &args);
    if (!ret && first_signaled)
        *first_signaled = args.first_signaled;
    return ret;
}

#endif
