/*
 * Copyright (C) 2026 Z1 Android 9 bring-up contributors
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef Z1_LIMA_ALLOCATOR_H_
#define Z1_LIMA_ALLOCATOR_H_

#include <stddef.h>

int z1_lima_alloc_dmabuf(size_t size);
int z1_lima_sync_dmabuf(int fd, bool start, int usage);

#endif
