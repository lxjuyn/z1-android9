/* Minimal A9 log/log.h shim for the standalone gralloc build. */
#ifndef Z1_LOG_LOG_H
#define Z1_LOG_LOG_H
#include <stdio.h>
#ifdef __cplusplus
extern "C" {
#endif
enum { ANDROID_LOG_UNKNOWN=0, ANDROID_LOG_DEFAULT, ANDROID_LOG_VERBOSE, ANDROID_LOG_DEBUG,
       ANDROID_LOG_INFO, ANDROID_LOG_WARN, ANDROID_LOG_ERROR, ANDROID_LOG_FATAL, ANDROID_LOG_SILENT };
int __android_log_print(int prio, const char* tag, const char* fmt, ...);
#ifdef __cplusplus
}
#endif
#define ALOGV(...) ((void)0)
#define ALOGD(...) ((void)0)
#define ALOGI(...) ((void)0)
#define ALOGW(...) ((void)0)
#define ALOGE(...) ((void)0)
#define ALOGF(...) ((void)0)
#define ALOGD_IF(cond, ...) ((void)0)
#define ALOGI_IF(cond, ...) ((void)0)
#define ALOGW_IF(cond, ...) ((void)0)
#define ALOGE_IF(cond, ...) ((void)0)
#define LOG_ALWAYS_FATAL(...) ((void)0)
#define LOG_ALWAYS_FATAL_IF(cond, ...) ((void)0)
#endif
