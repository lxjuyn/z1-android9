/*
 * Android 9 compatibility HAL for the Z1 factory MTK GPS library.
 *
 * The factory gps.default.so was built against Android 4.4. Its callback
 * table orders request_utc_time_cb and create_thread_cb differently from the
 * Android 9 libhardware table. It also contains MTK-private fields after the
 * public callbacks. Passing an Android 9 GpsCallbacks pointer directly makes
 * the factory HAL execute requestUtcTimeCb as its thread creator.
 *
 * This wrapper is installed as gps.default.so by hw_get_module("gps"). The
 * untouched factory library is installed beside it as gps.z1stock.so. The
 * wrapper forwards the normal GpsInterface operations and translates only the
 * callback-table layout. It does not synthesize a fix or bypass mnld/stpgps.
 */
#include <android/log.h>
#include <dlfcn.h>
#include <errno.h>
#include <stdint.h>
#include <string.h>

#include <hardware/gps.h>
#include <hardware/hardware.h>

#ifndef Z1_STOCK_GPS_HAL_PATH
#define Z1_STOCK_GPS_HAL_PATH "/vendor/lib/hw/gps.z1stock.so"
#endif

#define LOG_TAG "z1-gps-adapter"

#define ALOG(level, ...) __android_log_print(level, LOG_TAG, __VA_ARGS__)

/*
 * Factory callback layout recovered from gps.default.so.pre_xlog.
 *
 * 0x406e copies 44 bytes from the caller's callback table. 0x41d2 loads the
 * function at byte offset 0x24 and calls it as
 * (const char *name, void (*start)(void *), void *arg). In the Android 9
 * libhardware table, offset 0x20 is create_thread_cb and offset 0x24 is
 * request_utc_time_cb. The factory binary expects those two slots swapped.
 */
typedef struct {
    size_t size;
    gps_location_callback location_cb;
    gps_status_callback status_cb;
    gps_sv_status_callback sv_status_cb;
    gps_nmea_callback nmea_cb;
    gps_set_capabilities set_capabilities_cb;
    gps_acquire_wakelock acquire_wakelock_cb;
    gps_release_wakelock release_wakelock_cb;
    gps_request_utc_time request_utc_time_cb;
    gps_create_thread create_thread_cb;
    void *mtk_private_cb;
} z1_factory_gps_callbacks;

_Static_assert(sizeof(z1_factory_gps_callbacks) == 44,
               "factory callback table must remain 44 bytes");
_Static_assert(offsetof(z1_factory_gps_callbacks, request_utc_time_cb) == 0x20,
               "factory request UTC callback must be at 0x20");
_Static_assert(offsetof(z1_factory_gps_callbacks, create_thread_cb) == 0x24,
               "factory thread callback must be at 0x24");

typedef const GpsInterface *(*z1_get_interface_fn)(struct gps_device_t *device);

static void *stock_handle;
static const GpsInterface *stock_interface;

static int load_stock_interface(void) {
    if (stock_interface != NULL) {
        return 0;
    }

    stock_handle = dlopen(Z1_STOCK_GPS_HAL_PATH, RTLD_NOW | RTLD_LOCAL);
    if (stock_handle == NULL) {
        ALOG(ANDROID_LOG_ERROR, "cannot load factory HAL %s: %s",
             Z1_STOCK_GPS_HAL_PATH, dlerror());
        return -ENOENT;
    }

    dlerror();
    z1_get_interface_fn get_interface =
            (z1_get_interface_fn)dlsym(stock_handle, "gps__get_gps_interface");
    const char *error = dlerror();
    if (error != NULL || get_interface == NULL) {
        ALOG(ANDROID_LOG_ERROR, "cannot resolve factory interface: %s",
             error != NULL ? error : "symbol is null");
        dlclose(stock_handle);
        stock_handle = NULL;
        return -ENOENT;
    }

    stock_interface = get_interface(NULL);
    if (stock_interface == NULL || stock_interface->init == NULL ||
        stock_interface->start == NULL || stock_interface->stop == NULL ||
        stock_interface->cleanup == NULL ||
        stock_interface->set_position_mode == NULL ||
        stock_interface->get_extension == NULL) {
        ALOG(ANDROID_LOG_ERROR, "factory HAL returned an incomplete interface");
        dlclose(stock_handle);
        stock_handle = NULL;
        stock_interface = NULL;
        return -ENOSYS;
    }

    ALOG(ANDROID_LOG_INFO, "loaded factory HAL %s", Z1_STOCK_GPS_HAL_PATH);
    return 0;
}

static int adapter_init(GpsCallbacks *callbacks) {
    if (callbacks == NULL ||
        callbacks->size <
                offsetof(GpsCallbacks, request_utc_time_cb) +
                        sizeof(callbacks->request_utc_time_cb) ||
        callbacks->create_thread_cb == NULL) {
        return -EINVAL;
    }
    if (load_stock_interface() != 0) {
        return -ENOENT;
    }

    z1_factory_gps_callbacks factory_callbacks;
    memset(&factory_callbacks, 0, sizeof(factory_callbacks));
    factory_callbacks.size = sizeof(factory_callbacks);
    factory_callbacks.location_cb = callbacks->location_cb;
    factory_callbacks.status_cb = callbacks->status_cb;
    factory_callbacks.sv_status_cb = callbacks->sv_status_cb;
    factory_callbacks.nmea_cb = callbacks->nmea_cb;
    factory_callbacks.set_capabilities_cb = callbacks->set_capabilities_cb;
    factory_callbacks.acquire_wakelock_cb = callbacks->acquire_wakelock_cb;
    factory_callbacks.release_wakelock_cb = callbacks->release_wakelock_cb;
    factory_callbacks.request_utc_time_cb = callbacks->request_utc_time_cb;
    factory_callbacks.create_thread_cb = callbacks->create_thread_cb;
    int status = stock_interface->init((GpsCallbacks *)&factory_callbacks);
    if (status != 0) {
        ALOG(ANDROID_LOG_ERROR, "factory init failed: %d", status);
    }
    return status;
}

static int adapter_start(void) {
    return stock_interface != NULL ? stock_interface->start() : -ENODEV;
}

static int adapter_stop(void) {
    return stock_interface != NULL ? stock_interface->stop() : -ENODEV;
}

static void adapter_cleanup(void) {
    if (stock_interface != NULL) {
        stock_interface->cleanup();
    }
}

static int adapter_inject_time(GpsUtcTime time, int64_t time_reference,
                               int uncertainty) {
    if (stock_interface == NULL || stock_interface->inject_time == NULL) {
        return -ENOSYS;
    }
    return stock_interface->inject_time(time, time_reference, uncertainty);
}

static int adapter_inject_location(double latitude, double longitude,
                                   float accuracy) {
    if (stock_interface == NULL || stock_interface->inject_location == NULL) {
        return -ENOSYS;
    }
    return stock_interface->inject_location(latitude, longitude, accuracy);
}

static void adapter_delete_aiding_data(GpsAidingData flags) {
    if (stock_interface != NULL &&
        stock_interface->delete_aiding_data != NULL) {
        stock_interface->delete_aiding_data(flags);
    }
}

static int adapter_set_position_mode(GpsPositionMode mode,
                                     GpsPositionRecurrence recurrence,
                                     uint32_t min_interval,
                                     uint32_t preferred_accuracy,
                                     uint32_t preferred_time) {
    if (stock_interface == NULL) {
        return -ENODEV;
    }
    return stock_interface->set_position_mode(
            mode, recurrence, min_interval, preferred_accuracy, preferred_time);
}

static const void *adapter_get_extension(const char *name) {
    if (stock_interface == NULL || stock_interface->get_extension == NULL) {
        return NULL;
    }
    return stock_interface->get_extension(name);
}

static const GpsInterface adapter_interface = {
    .size = sizeof(GpsInterface),
    .init = adapter_init,
    .start = adapter_start,
    .stop = adapter_stop,
    .cleanup = adapter_cleanup,
    .inject_time = adapter_inject_time,
    .inject_location = adapter_inject_location,
    .delete_aiding_data = adapter_delete_aiding_data,
    .set_position_mode = adapter_set_position_mode,
    .get_extension = adapter_get_extension,
};

static const GpsInterface *adapter_get_gps_interface(
        struct gps_device_t *device) {
    (void)device;
    if (load_stock_interface() != 0) {
        return NULL;
    }
    return &adapter_interface;
}

static int adapter_close(struct hw_device_t *device) {
    (void)device;
    return 0;
}

static int adapter_open(const struct hw_module_t *module, const char *id,
                        struct hw_device_t **device) {
    static struct gps_device_t gps_device;

    if (module == NULL || id == NULL || device == NULL ||
        strcmp(id, GPS_HARDWARE_MODULE_ID) != 0) {
        return -EINVAL;
    }
    if (load_stock_interface() != 0) {
        return -ENOENT;
    }

    memset(&gps_device, 0, sizeof(gps_device));
    gps_device.common.tag = HARDWARE_DEVICE_TAG;
    gps_device.common.version = 1;
    gps_device.common.module = (struct hw_module_t *)module;
    gps_device.common.close = adapter_close;
    gps_device.get_gps_interface = adapter_get_gps_interface;
    *device = &gps_device.common;
    return 0;
}

static struct hw_module_methods_t adapter_methods = {
    .open = adapter_open,
};

__attribute__((visibility("default")))
struct hw_module_t HAL_MODULE_INFO_SYM = {
    .tag = HARDWARE_MODULE_TAG,
    .module_api_version = 1,
    .hal_api_version = 0,
    .id = GPS_HARDWARE_MODULE_ID,
    .name = "Z1 factory GPS ABI adapter",
    .author = "z1-mainline",
    .methods = &adapter_methods,
};
