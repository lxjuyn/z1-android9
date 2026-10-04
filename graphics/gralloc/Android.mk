# Copyright (C) 2008 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


LOCAL_PATH := $(call my-dir)

# HAL module implemenation stored in
# hw/<OVERLAY_HARDWARE_MODULE_ID>.<ro.product.board>.so
include $(CLEAR_VARS)

LOCAL_MODULE_PATH := $(TARGET_OUT_SHARED_LIBRARIES)/hw
LOCAL_SHARED_LIBRARIES := liblog libcutils
LOCAL_CFLAGS := -DLOG_TAG=\"gralloc\" -Wno-missing-field-initializers

LOCAL_SRC_FILES := 	\
	gralloc.cpp 	\
	framebuffer.cpp \
	mapper.cpp

ifeq ($(Z1_KERNEL_VARIANT),mainline)
LOCAL_SRC_FILES += lima_allocator.cpp
LOCAL_CFLAGS += -DZ1_USE_LIMA=1
LOCAL_C_INCLUDES += $(TOP)/external/libdrm/include
endif

LOCAL_HEADER_LIBRARIES := libhardware_headers

LOCAL_MODULE := gralloc.z1
ifeq ($(TARGET_USE_PAN_DISPLAY),true)
LOCAL_CFLAGS += -DUSE_PAN_DISPLAY=1
endif

include $(BUILD_SHARED_LIBRARY)
