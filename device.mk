DEVICE_PATH := device/jiabo/z1
PRODUCT_SOONG_NAMESPACES += $(DEVICE_PATH)

# Factory Android 4.4 launch level. Pie build's legacy path keeps vendor
# content under /system/vendor and does not impose Treble launch requirements.
PRODUCT_SHIPPING_API_LEVEL := 19

# Android 9 uses a classic root ramdisk on this device. The kernel's DT
# bootargs must provide androidboot.hardware=z1 so init imports init.z1.rc.
ifeq ($(Z1_KERNEL_VARIANT),stock)
Z1_MT6572_RADIO := $(DEVICE_PATH)/proprietary/mt6572
Z1_MT6572_AUDIO := $(DEVICE_PATH)/proprietary/mt6572/audio
Z1_MT6572_BLUETOOTH := $(DEVICE_PATH)/proprietary/mt6572/bluetooth
PRODUCT_COPY_FILES += \
    $(DEVICE_PATH)/stock/init.z1.rc:root/init.z1.rc \
    $(DEVICE_PATH)/stock/fstab.z1:root/fstab.z1 \
    $(DEVICE_PATH)/stock/ueventd.z1.rc:root/ueventd.z1.rc \
    $(DEVICE_PATH)/stock/z1_fbcon_takeover.sh:root/z1_fbcon_takeover.sh \
    $(Z1_MT6572_RADIO)/bin/wmt_loader:$(TARGET_COPY_OUT_SYSTEM)/bin/wmt_loader \
    $(Z1_MT6572_RADIO)/bin/6620_launcher:$(TARGET_COPY_OUT_SYSTEM)/bin/6620_launcher \
    $(Z1_MT6572_RADIO)/etc/firmware/WMT_SOC.cfg:$(TARGET_COPY_OUT_SYSTEM)/etc/firmware/WMT_SOC.cfg \
    $(Z1_MT6572_RADIO)/etc/firmware/WIFI_RAM_CODE_SOC:$(TARGET_COPY_OUT_SYSTEM)/etc/firmware/WIFI_RAM_CODE_SOC \
    $(Z1_MT6572_RADIO)/etc/firmware/mt6572_82_patch_e1_0_hdr.bin:$(TARGET_COPY_OUT_SYSTEM)/etc/firmware/mt6572_82_patch_e1_0_hdr.bin \
    $(Z1_MT6572_RADIO)/etc/firmware/mt6572_82_patch_e1_1_hdr.bin:$(TARGET_COPY_OUT_SYSTEM)/etc/firmware/mt6572_82_patch_e1_1_hdr.bin \
    $(Z1_MT6572_AUDIO)/lib/hw/audio.primary.mt6572.so:$(TARGET_COPY_OUT_VENDOR)/lib/hw/audio.primary.mt6572.so \
    $(Z1_MT6572_BLUETOOTH)/libbluetoothdrv.so:$(TARGET_COPY_OUT_VENDOR)/lib/libbluetoothdrv.so \
    frameworks/native/data/etc/android.hardware.bluetooth.xml:$(TARGET_COPY_OUT_SYSTEM)/etc/permissions/android.hardware.bluetooth.xml \
    frameworks/native/data/etc/android.hardware.bluetooth_le.xml:$(TARGET_COPY_OUT_SYSTEM)/etc/permissions/android.hardware.bluetooth_le.xml
PRODUCT_PROPERTY_OVERRIDES += ro.z1.stock_kernel=true ro.logd.kernel=false ro.hardware.gralloc=z1 ro.hardware.hwcomposer=z1 ro.hardware.audio=mt6572 ro.bt.vendor_owns_bdaddr=true
PRODUCT_PACKAGES += \
    android.hardware.configstore@1.0-impl.z1stock \
    android.hardware.configstore@1.1-impl.z1stock \
    android.hardware.bluetooth@1.0-impl \
    libbt-vendor
else
PRODUCT_COPY_FILES += \
    $(DEVICE_PATH)/rootdir/init.z1.rc:root/init.z1.rc \
    $(DEVICE_PATH)/rootdir/fstab.z1:root/fstab.z1 \
    $(DEVICE_PATH)/rootdir/init.z1.network.rc:root/init.z1.network.rc \
    $(DEVICE_PATH)/rootdir/ueventd.z1.rc:root/ueventd.z1.rc \
    $(DEVICE_PATH)/rootdir/z1_usb_setup.sh:system/bin/z1_usb_setup.sh
PRODUCT_PROPERTY_OVERRIDES += ro.hardware.egl=mesa
PRODUCT_PACKAGES += libGLES_mesa gallium_dri
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
    qemu.hw.mainkeys=1 \
    ro.vold.z1_fuse=true \
    ro.sys.sdcardfs=false

# FB_SIMPLE exposes Z1's 240x320 RGB565 display through /dev/fb0. The
# board gralloc allocates ashmem-backed buffers; HWC1 asks SurfaceFlinger to
# compose in software and posts completed scan lines into the padded fbdev.
# Pie's default HIDL graphics services bridge to these legacy HAL modules.
PRODUCT_PACKAGES += \
    gralloc.z1 \
    hwcomposer.z1 \
    android.hardware.graphics.composer@2.1-impl \
    android.hardware.graphics.composer@2.1-service \
    android.hardware.graphics.allocator@2.0-service \
    android.hardware.graphics.allocator@2.0-impl \
    android.hardware.graphics.mapper@2.0-impl

ifeq ($(Z1_KERNEL_VARIANT),stock)
PRODUCT_PROPERTY_OVERRIDES += ro.hardware.egl=swiftshader
PRODUCT_PACKAGES += \
    libEGL_swiftshader \
    libGLESv1_CM_swiftshader \
    libGLESv2_swiftshader
endif

# Pie HIDL clients can load these implementations in-process on the stock
# kernel. The stock profile removes the separate HIDL server processes after
# all product packages have been inherited; Linux7 retains the normal servers.
# The stock profile selects its legacy MT6572 primary audio HAL through
# ro.hardware.audio; the Android 9 HIDL client loads it in-process. Keymaster
# remains software and does not provide a TEE.
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
    $(DEVICE_PATH)/stock/primary_audio_policy_configuration.xml:$(TARGET_COPY_OUT_VENDOR)/etc/primary_audio_policy_configuration.xml \
    frameworks/av/services/audiopolicy/config/r_submix_audio_policy_configuration.xml:$(TARGET_COPY_OUT_VENDOR)/etc/r_submix_audio_policy_configuration.xml \
    frameworks/av/services/audiopolicy/config/audio_policy_volumes.xml:$(TARGET_COPY_OUT_VENDOR)/etc/audio_policy_volumes.xml \
    frameworks/av/services/audiopolicy/config/default_volume_tables.xml:$(TARGET_COPY_OUT_VENDOR)/etc/default_volume_tables.xml

# The stock keypad and touchscreen register as "mtk-kpd" and "mtk-tpd".
# Install a factory-derived map for each, with Pie-compatible navigation keys.
PRODUCT_COPY_FILES += \
    $(DEVICE_PATH)/keylayout/mtk-kpd.kl:$(TARGET_COPY_OUT_SYSTEM)/usr/keylayout/mtk-kpd.kl \
    $(DEVICE_PATH)/keylayout/mtk-tpd.kl:$(TARGET_COPY_OUT_SYSTEM)/usr/keylayout/mtk-tpd.kl

PRODUCT_CHARACTERISTICS := nosdcard

# TODO: Gate software graphics on board fbdev refresh and full BufferQueue test.
# TODO: Verify FunctionFS ADB enumeration and secure host authorization on board.
# WMT firmware and its stock loader are staged from a local ROM by the
# whitelist extractor. Bluetooth uses a 32-bit passthrough HIDL implementation
# and a source-built adapter around the factory driver library. That driver
# reads its Bluetooth address/calibration from this device's own NVRAM.

# Mainline has FUSE, not Android sdcardfs/esdfs. The replacement daemon retains
# three permission views and per-package/multi-user access checks.
PRODUCT_PACKAGES += sdcard.z1_fuse

ifeq ($(Z1_KERNEL_VARIANT),stock)
PRODUCT_PROPERTY_OVERRIDES += persist.sys.usb.config=acm
else
PRODUCT_PROPERTY_OVERRIDES += persist.sys.usb.config=adb
endif
