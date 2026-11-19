#include <android/log.h>
#include <dlfcn.h>
#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>

#include <hardware/gps.h>
#include <hardware/hardware.h>

static int create_thread_calls;
static int request_utc_calls;

static pthread_t test_create_thread(const char *name, void (*start)(void *),
                                    void *arg) {
    (void)name;
    create_thread_calls++;
    start(arg);
    return pthread_self();
}

static void test_request_utc_time(void) {
    request_utc_calls++;
}

int main(void) {
    void *handle = dlopen("/vendor/lib/hw/gps.default.so", RTLD_NOW);
    if (handle == NULL) {
        fprintf(stderr, "adapter dlopen failed: %s\n", dlerror());
        return 10;
    }

    struct hw_module_t *module =
            (struct hw_module_t *)dlsym(handle, "HMI");
    if (module == NULL) {
        fprintf(stderr, "HMI not exported\n");
        return 11;
    }
    if (strcmp(module->id, GPS_HARDWARE_MODULE_ID) != 0 ||
        module->module_api_version != 1 || module->hal_api_version != 0) {
        fprintf(stderr, "HMI contract mismatch\n");
        return 12;
    }

    struct gps_device_t *device = NULL;
    if (module->methods == NULL || module->methods->open == NULL ||
        module->methods->open(module, GPS_HARDWARE_MODULE_ID,
                              (struct hw_device_t **)&device) != 0 ||
        device == NULL || device->get_gps_interface == NULL) {
        fprintf(stderr, "adapter open failed\n");
        return 13;
    }

    const GpsInterface *interface = device->get_gps_interface(device);
    if (interface == NULL || interface->init == NULL ||
        interface->start == NULL || interface->stop == NULL ||
        interface->cleanup == NULL) {
        fprintf(stderr, "adapter interface incomplete\n");
        return 14;
    }

    GpsCallbacks callbacks;
    memset(&callbacks, 0, sizeof(callbacks));
    callbacks.size = sizeof(callbacks);
    callbacks.create_thread_cb = test_create_thread;
    callbacks.request_utc_time_cb = test_request_utc_time;

    callbacks.size = offsetof(GpsCallbacks, request_utc_time_cb) +
                     sizeof(callbacks.request_utc_time_cb) - 1;
    if (interface->init(&callbacks) != -EINVAL) {
        fprintf(stderr, "short callback table was accepted\n");
        return 15;
    }
    callbacks.size = sizeof(callbacks);

    if (interface->init(&callbacks) != 0) {
        fprintf(stderr, "factory init through adapter failed\n");
        return 16;
    }
    if (create_thread_calls != 1 || request_utc_calls != 0) {
        fprintf(stderr,
                "callback order mismatch: create=%d request_utc=%d\n",
                create_thread_calls, request_utc_calls);
        return 17;
    }

    if (interface->start() != 0) {
        fprintf(stderr, "adapter start failed\n");
        return 18;
    }
    void *stock = dlopen("/vendor/lib/hw/gps.z1stock.so",
                         RTLD_NOW | RTLD_NOLOAD);
    if (stock == NULL) {
        fprintf(stderr, "factory library was not loaded\n");
        return 19;
    }
    int (*is_started)(void) = (int (*)(void))dlsym(
            stock, "z1_mock_is_started");
    if (is_started == NULL || is_started() != 1) {
        fprintf(stderr, "factory start was not forwarded\n");
        return 20;
    }

    interface->stop();
    interface->cleanup();
    device->common.close(&device->common);
    dlclose(stock);
    dlclose(handle);
    puts("PASS: Android 9 callback order mapped to factory layout");
    return 0;
}
