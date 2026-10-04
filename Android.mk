# The Z1 device tree is staged into Android 9 through a symlink, which the
# legacy Makefile discovery pass does not descend into. The real parent shim
# includes this file explicitly to register all device-local Make modules.
z1_device_makefile_path := $(call my-dir)

include $(z1_device_makefile_path)/graphics/gralloc/Android.mk
include $(z1_device_makefile_path)/graphics/hwcomposer/Android.mk
include $(z1_device_makefile_path)/health/Android.mk
include $(z1_device_makefile_path)/storage/Android.mk
include $(z1_device_makefile_path)/wifi/Android.mk
