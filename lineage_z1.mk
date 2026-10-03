# Minimal 32-bit product for the 1 GiB, 240x320 Z1 bring-up.
$(call inherit-product, $(SRC_TARGET_DIR)/product/aosp_base.mk)
$(call inherit-product, device/jiabo/z1/device.mk)
$(call inherit-product, vendor/lineage/config/common.mk)

TARGET_SCREEN_WIDTH := 240
TARGET_SCREEN_HEIGHT := 320

PRODUCT_NAME := lineage_z1
PRODUCT_DEVICE := z1
PRODUCT_BRAND := Jiabo
PRODUCT_MODEL := Z1
PRODUCT_MANUFACTURER := Jiabo
