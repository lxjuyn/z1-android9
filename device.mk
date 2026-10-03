DEVICE_PATH := device/jiabo/z1

# Factory Android 4.4 launch level. Pie build's legacy path keeps vendor
# content under /system/vendor and does not impose Treble launch requirements.
PRODUCT_SHIPPING_API_LEVEL := 19

# Android 9 uses a classic root ramdisk on this device. The kernel's DT
# bootargs must provide androidboot.hardware=z1 so init imports init.z1.rc.
ifeq ($(Z1_KERNEL_VARIANT),stock)
PRODUCT_COPY_FILES += \
    $(DEVICE_PATH)/stock/init.z1.rc:root/init.z1.rc \
    $(DEVICE_PATH)/stock/fstab.z1:root/fstab.z1 \
    $(DEVICE_PATH)/stock/ueventd.z1.rc:root/ueventd.z1.rc
PRODUCT_PROPERTY_OVERRIDES += ro.z1.stock_kernel=true ro.logd.kernel=false ro.hardware.gralloc=z1 ro.hardware.hwcomposer=z1
PRODUCT_PACKAGES += \
    android.hardware.configstore@1.0-impl.z1stock \
    android.hardware.configstore@1.1-impl.z1stock
else
PRODUCT_COPY_FILES += \
    $(DEVICE_PATH)/rootdir/init.z1.rc:root/init.z1.rc \
    $(DEVICE_PATH)/rootdir/fstab.z1:root/fstab.z1 \
    $(DEVICE_PATH)/rootdir/init.z1.network.rc:root/init.z1.network.rc \
    $(DEVICE_PATH)/rootdir/z1_usb_setup.sh:system/bin/z1_usb_setup.sh
endif

# Rebuilt module files and dependency order are produced together by
# tools/z1_stage_network_modules.py. Load them before netd starts.
ifneq ($(Z1_KERNEL_VARIANT),stock)
PRODUCT_COPY_FILES += $(foreach module,$(wildcard $(DEVICE_PATH)/network/modules/*.ko),\
    $(module):system/lib/modules/z1-network/$(notdir $(module)))
endif

PRODUCT_PROPERTY_OVERRIDES += \
    ro.config.low_ram=true \
    ro.sf.lcd_density=160 \
    ro.hardware.egl=swiftshader \
        ro.vold.z1_fuse=true \
    ro.sys.sdcardfs=false

# FB_SIMPLE exposes Z1's 240x320 RGB565 display through /dev/fb0. The
# board gralloc allocates ashmem-backed buffers; HWC1 asks SurfaceFlinger to
# compose in software and posts completed scan lines into the padded fbdev.
# Pie's default HIDL graphics services bridge to these legacy HAL modules.
PRODUCT_PACKAGES += \
    gralloc.z1 \
    hwcomposer.z1 \
    libEGL_swiftshader \
    libGLESv1_CM_swiftshader \
    libGLESv2_swiftshader \
    android.hardware.graphics.composer@2.1-impl \
    android.hardware.graphics.composer@2.1-service \
    android.hardware.graphics.allocator@2.0-service \
    android.hardware.graphics.allocator@2.0-impl \
    android.hardware.graphics.mapper@2.0-impl

# Pie clients need discoverable HIDL factories even while the physical MT6572
# audio driver is being ported. The primary HAL here is the existing silent
# software implementation; Keymaster is software and does not provide a TEE.
PRODUCT_PACKAGES += \
    audio.primary.default \
    audio.r_submix.default \
    android.hardware.audio@2.0-service \
    android.hardware.audio@2.0-impl \
    android.hardware.audio.effect@2.0-impl \
    android.hardware.keymaster@3.0-service \
    android.hardware.keymaster@3.0-impl \
    android.hardware.health@2.0-service.z1

PRODUCT_COPY_FILES += \
    frameworks/av/services/audiopolicy/config/audio_policy_configuration_generic.xml:$(TARGET_COPY_OUT_VENDOR)/etc/audio_policy_configuration.xml \
    frameworks/av/services/audiopolicy/config/primary_audio_policy_configuration.xml:$(TARGET_COPY_OUT_VENDOR)/etc/primary_audio_policy_configuration.xml \
    frameworks/av/services/audiopolicy/config/r_submix_audio_policy_configuration.xml:$(TARGET_COPY_OUT_VENDOR)/etc/r_submix_audio_policy_configuration.xml \
    frameworks/av/services/audiopolicy/config/audio_policy_volumes.xml:$(TARGET_COPY_OUT_VENDOR)/etc/audio_policy_volumes.xml \
    frameworks/av/services/audiopolicy/config/default_volume_tables.xml:$(TARGET_COPY_OUT_VENDOR)/etc/default_volume_tables.xml

PRODUCT_CHARACTERISTICS := nosdcard

# TODO: Gate software graphics on board fbdev refresh and full BufferQueue test.
# TODO: Verify FunctionFS ADB enumeration and secure host authorization on board.
# TODO: Add Wi-Fi firmware and HALs after per-binary ABI and kernel API audit.

# Mainline has FUSE, not Android sdcardfs/esdfs. The replacement daemon retains
# three permission views and per-package/multi-user access checks.
PRODUCT_PACKAGES += sdcard.z1_fuse

ifeq ($(Z1_KERNEL_VARIANT),stock)
PRODUCT_PROPERTY_OVERRIDES += persist.sys.usb.config=acm
else
PRODUCT_PROPERTY_OVERRIDES += persist.sys.usb.config=adb
endif
