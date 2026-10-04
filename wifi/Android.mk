LOCAL_PATH := $(call my-dir)

# Android 9's legacy wpa_supplicant and hostapd makefiles consume private
# driver libraries through LOCAL_STATIC_LIBRARIES. Keep this module in the
# Make graph so BOARD_*_PRIVATE_LIB resolves for both binaries.
include $(CLEAR_VARS)
LOCAL_MODULE := lib_driver_cmd_mt66xx
LOCAL_MODULE_TAGS := optional
LOCAL_PROPRIETARY_MODULE := true
LOCAL_SRC_FILES := mtk_wpa_supplicant/mediatek_driver_cmd_nl80211.c
LOCAL_C_INCLUDES := \
    external/libnl/include \
    external/wpa_supplicant_8/src \
    external/wpa_supplicant_8/src/common \
    external/wpa_supplicant_8/src/drivers \
    external/wpa_supplicant_8/src/l2_packet \
    external/wpa_supplicant_8/src/utils \
    external/wpa_supplicant_8/src/wps \
    external/wpa_supplicant_8/wpa_supplicant
LOCAL_CFLAGS := -mabi=aapcs-linux
include $(BUILD_STATIC_LIBRARY)
