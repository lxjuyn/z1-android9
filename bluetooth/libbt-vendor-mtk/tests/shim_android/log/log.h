/* Offline stand-in for AOSP system/core/liblog/include/log/log.h.
 * Same expansion as the real header: tag + priority to __android_log_print. */
#ifndef OFFLINE_AOSP_LOG_LOG_H
#define OFFLINE_AOSP_LOG_LOG_H
#include <android/log.h>
#ifndef LOG_TAG
#define LOG_TAG NULL
#endif
#define ALOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)
#define ALOGW(...) __android_log_print(ANDROID_LOG_WARN, LOG_TAG, __VA_ARGS__)
#define ALOGI(...) __android_log_print(ANDROID_LOG_INFO, LOG_TAG, __VA_ARGS__)
#define ALOGD(...) __android_log_print(ANDROID_LOG_DEBUG, LOG_TAG, __VA_ARGS__)
#endif
