# The Z1 device tree is staged into Android 9 through a symlink, which the
# legacy Makefile discovery pass does not descend into. The real parent shim
# includes this file explicitly to register all device-local Make modules.
LOCAL_PATH := $(call my-dir)

include $(LOCAL_PATH)/graphics/gralloc/Android.mk
include $(LOCAL_PATH)/graphics/hwcomposer/Android.mk
include $(LOCAL_PATH)/health/Android.mk
include $(LOCAL_PATH)/storage/Android.mk
include $(LOCAL_PATH)/wifi/Android.mk
