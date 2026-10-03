LOCAL_PATH := $(call my-dir)

# Z1 only: replace Pie's sdcardfs-only executable with the permission-preserving
# AOSP Oreo FUSE daemon. vold keeps its ordinary path and mount validation.
include $(CLEAR_VARS)
LOCAL_MODULE := sdcard.z1_fuse
LOCAL_MODULE_STEM := sdcard
LOCAL_OVERRIDES_MODULES := sdcard
LOCAL_SRC_FILES := sdcard.cpp fuse.cpp
LOCAL_CFLAGS := -Wall -Wno-unused-parameter -Werror
LOCAL_SHARED_LIBRARIES := libbase libcutils libminijail libpackagelistparser
LOCAL_SANITIZE := integer
include $(BUILD_EXECUTABLE)
