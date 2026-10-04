# The Z1 device tree is staged into Android 9 through a symlink, which the
# legacy Makefile discovery pass does not descend into. The real parent shim
# includes this file explicitly to register all device-local Make modules.
z1_device_makefile_path := $(call my-dir)

include $(z1_device_makefile_path)/graphics/gralloc/Android.mk
include $(z1_device_makefile_path)/graphics/hwcomposer/Android.mk
include $(z1_device_makefile_path)/health/Android.mk
include $(z1_device_makefile_path)/storage/Android.mk
include $(z1_device_makefile_path)/wifi/Android.mk

ifeq ($(Z1_KERNEL_VARIANT),mainline)
ifeq ($(wildcard $(Z1_MESA_SOURCE_DIR)/Android.mk),)
$(error Missing Mesa Lima source; run scripts/build_z1.sh with Z1_KERNEL_VARIANT=mainline)
endif
endif
