/* Z1 GPS HAL stub: makes the GNSS HIDL service register so that
 * system_server's GnssLocationProvider.isSupported() does not block in
 * hwservicemanager getService(), while never touching the real hardware.
 * The stock gps.default.so blocks in open()/init() because mnld is dead. */
#include <hardware/hardware.h>
#include <hardware/gps.h>
#include <string.h>
#include <errno.h>

static int stub_init(GpsCallbacks* cbs) { (void)cbs; return 0; }
static int stub_start(void) { return 0; }
static int stub_stop(void) { return 0; }
static void stub_cleanup(void) { }
static int stub_set_position_mode(GpsPositionMode m, GpsPositionRecurrence r,
                                  uint32_t mi, uint32_t di, uint32_t s) {
    (void)m; (void)r; (void)mi; (void)di; (void)s; return 0;
}
static const void* stub_get_extension(const char* name) { (void)name; return NULL; }

static const GpsInterface stub_iface = {
    .size = sizeof(GpsInterface),
    .init = stub_init,
    .start = stub_start,
    .stop = stub_stop,
    .cleanup = stub_cleanup,
    .set_position_mode = stub_set_position_mode,
    .get_extension = stub_get_extension,
};

static const GpsInterface* stub_get_gps_interface(struct gps_device_t* dev) {
    (void)dev; return &stub_iface;
}

static int stub_close(struct hw_device_t* dev) { (void)dev; return 0; }

static int stub_open(const struct hw_module_t* module, const char* id,
                     struct hw_device_t** device) {
    static struct gps_device_t dev;
    if (id == NULL || strcmp(id, GPS_HARDWARE_MODULE_ID) != 0) return -EINVAL;
    memset(&dev, 0, sizeof(dev));
    dev.common.tag = HARDWARE_DEVICE_TAG;
    dev.common.version = 1;
    dev.common.module = (struct hw_module_t*)module;
    dev.common.close = stub_close;
    dev.get_gps_interface = stub_get_gps_interface;
    *device = &dev.common;
    return 0;
}

static struct hw_module_methods_t stub_module_methods = { .open = stub_open };

struct hw_module_t HAL_MODULE_INFO_SYM = {
    .tag = HARDWARE_MODULE_TAG,
    .module_api_version = 1,
    .hal_api_version = 0,
    .id = GPS_HARDWARE_MODULE_ID,
    .name = "Z1 GPS stub",
    .author = "z1-mainline",
    .methods = &stub_module_methods,
};
