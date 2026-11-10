#ifndef HOST_SHIM_LOG_LOG_H
#define HOST_SHIM_LOG_LOG_H
#include <stdio.h>
#define ALOGE(...) do { fprintf(stderr, "E "); fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); } while (0)
#define ALOGI(...) do { fprintf(stderr, "I "); fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); } while (0)
#define ALOGW(...) do { fprintf(stderr, "W "); fprintf(stderr, __VA_ARGS__); fputc('\n', stderr); } while (0)
#endif
