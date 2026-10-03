LOCAL_PATH := $(call my-dir)
include $(CLEAR_VARS)
LOCAL_MODULE := android.hardware.health@2.0-service.z1
LOCAL_VENDOR_MODULE := true
LOCAL_MODULE_RELATIVE_PATH := hw
LOCAL_SRC_FILES := health_service.cpp
LOCAL_INIT_RC := android.hardware.health@2.0-service.z1.rc
LOCAL_CFLAGS := -Wall -Werror
LOCAL_HEADER_LIBRARIES := libhealthd_headers
# The service, Health implementation and battery monitor refer back to each
# other. Make does not compute Soong's transitive static archive ordering.
LOCAL_GROUP_STATIC_LIBRARIES := true
LOCAL_STATIC_LIBRARIES := \
    libhealthservice \
    android.hardware.health@2.0-impl \
    android.hardware.health@1.0-convert \
    libhealthstoragedefault \
    libbatterymonitor
LOCAL_SHARED_LIBRARIES := \
    libbase \
    libcutils \
    libhidlbase \
    libhidltransport \
    libhwbinder \
    liblog \
    libutils \
    android.hardware.health@2.0
LOCAL_OVERRIDES_MODULES := healthd android.hardware.health@2.0-service android.hardware.health@2.0-service.override
include $(BUILD_EXECUTABLE)
