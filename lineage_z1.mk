# Minimal 32-bit product for the 1 GiB, 240x320 Z1 bring-up.
$(call inherit-product, $(SRC_TARGET_DIR)/product/aosp_base.mk)
$(call inherit-product, device/jiabo/z1/device.mk)
$(call inherit-product, vendor/lineage/config/common.mk)

# The original Z1 kernel exposes Binder protocol 7, but no hwbinder context or
# scatter/gather transactions. Keep passthrough libraries for clients and omit
# the remote HIDL server processes in this profile. BatteryService polls the
# stock power_supply sysfs directly. Linux7 keeps the standard service set.
ifeq ($(Z1_KERNEL_VARIANT),stock)
Z1_STOCK_REMOTE_HIDL_SERVERS := \
    android.hardware.audio@2.0-service \
    android.hardware.bluetooth@1.0-service \
    android.hardware.cas@1.0-service \
    android.hardware.configstore@1.1-service \
    android.hardware.drm@1.1-service.clearkey \
    android.hardware.graphics.allocator@2.0-service \
    android.hardware.graphics.composer@2.1-service \
    android.hardware.health@2.0-service.z1 \
    android.hardware.keymaster@3.0-service \
    android.hardware.media.omx@1.0-service \
    vndservicemanager
PRODUCT_PACKAGES_REMOVE += $(Z1_STOCK_REMOTE_HIDL_SERVERS)
endif

TARGET_SCREEN_WIDTH := 240
TARGET_SCREEN_HEIGHT := 320

PRODUCT_NAME := lineage_z1
PRODUCT_DEVICE := z1
PRODUCT_BRAND := Jiabo
PRODUCT_MODEL := Z1
PRODUCT_MANUFACTURER := Jiabo
