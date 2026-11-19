#include <stdint.h>
#include <string.h>

#include <hardware/gps.h>

static GpsCallbacks *factory_callbacks;
static int started;

static void mock_worker(void *arg) {
    int *ran = (int *)arg;
    *ran = 1;
}

static int mock_init(GpsCallbacks *callbacks) {
    if (callbacks == NULL) {
        return -1;
    }

    factory_callbacks = callbacks;
    uintptr_t *slots = (uintptr_t *)callbacks;
    gps_create_thread create_thread = (gps_create_thread)slots[9];
    if (create_thread == NULL) {
        return -2;
    }

    int worker_ran = 0;
    create_thread("z1-gps-abi-test", mock_worker, &worker_ran);
    return worker_ran == 1 ? 0 : -3;
}

static int mock_start(void) {
    started = 1;
    return 0;
}

static int mock_stop(void) {
    started = 0;
    return 0;
}

static void mock_cleanup(void) {
    started = 0;
    factory_callbacks = NULL;
}

static int mock_set_position_mode(GpsPositionMode mode,
                                  GpsPositionRecurrence recurrence,
                                  uint32_t min_interval,
                                  uint32_t preferred_accuracy,
                                  uint32_t preferred_time) {
    (void)mode;
    (void)recurrence;
    (void)min_interval;
    (void)preferred_accuracy;
    (void)preferred_time;
    return 0;
}

static const void *mock_get_extension(const char *name) {
    static const char unadapted_extension = 1;
    return strcmp(name, AGPS_INTERFACE) == 0 ? &unadapted_extension : NULL;
}

static const GpsInterface mock_interface = {
    .size = sizeof(GpsInterface),
    .init = mock_init,
    .start = mock_start,
    .stop = mock_stop,
    .cleanup = mock_cleanup,
    .set_position_mode = mock_set_position_mode,
    .get_extension = mock_get_extension,
};

__attribute__((visibility("default")))
const GpsInterface *gps__get_gps_interface(struct gps_device_t *device) {
    (void)device;
    return &mock_interface;
}

__attribute__((visibility("default")))
int z1_mock_is_started(void) {
    return started;
}
