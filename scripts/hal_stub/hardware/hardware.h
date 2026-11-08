/* Minimal A9 libhardware ABI for the Z1 GPS stub (avoids the graphics.h chain). */
#ifndef ANDROID_INCLUDE_HARDWARE_HARDWARE_H
#define ANDROID_INCLUDE_HARDWARE_HARDWARE_H
#include <stdint.h>
#include <stddef.h>
#include <sys/cdefs.h>

/* AOSP: the loader dlsym()s the literal name "HMI" (HAL_MODULE_INFO_SYM_AS_STR),
 * so the exported symbol must be spelled HMI, not HAL_MODULE_INFO_SYM. */
#define HAL_MODULE_INFO_SYM         HMI
#define HAL_MODULE_INFO_SYM_AS_STR  "HMI"

#define HARDWARE_MODULE_TAG 0x48574d54u
#define HARDWARE_DEVICE_TAG 0x48574456u

typedef struct hw_module_t {
    uint32_t tag;
    uint16_t module_api_version;
#define version_major module_api_version
    uint16_t hal_api_version;
#define version_minor hal_api_version
    const char *id;
    const char *name;
    const char *author;
    struct hw_module_methods_t* methods;
    void* dso;
    uint32_t reserved[32-7];
} hw_module_t;

typedef struct hw_device_t {
    uint32_t tag;
    uint32_t version;
    struct hw_module_t* module;
    int (*close)(struct hw_device_t* device);
    uint32_t reserved[12];
} hw_device_t;

typedef struct hw_module_methods_t {
    int (*open)(const struct hw_module_t* module, const char* id, struct hw_device_t** device);
} hw_module_methods_t;
#ifdef __cplusplus
#define TO_HW_DEVICE_T_OPEN(x) reinterpret_cast<struct hw_device_t**>(x)
#else
#define TO_HW_DEVICE_T_OPEN(x) (struct hw_device_t**)(x)
#endif
#endif
