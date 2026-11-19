#include <dlfcn.h>
#include <stdio.h>
#include <string.h>

#include <hardware/gps.h>
#include <hardware/hardware.h>

int main(void) {
    void *handle = dlopen("/vendor/lib/hw/gps.default.so", RTLD_NOW);
    if (handle == NULL) {
        fprintf(stderr, "adapter load failed: %s\n", dlerror());
        return 10;
    }
    struct hw_module_t *module = (struct hw_module_t *)dlsym(handle, "HMI");
    if (module == NULL || strcmp(module->id, GPS_HARDWARE_MODULE_ID) != 0) {
        fprintf(stderr, "adapter HMI invalid\n");
        return 11;
    }

    struct gps_device_t *device = NULL;
    if (module->methods == NULL || module->methods->open == NULL ||
        module->methods->open(module, GPS_HARDWARE_MODULE_ID,
                              (struct hw_device_t **)&device) != 0 ||
        device == NULL || device->get_gps_interface == NULL) {
        void *direct = dlopen("/vendor/lib/hw/gps.z1stock.so", RTLD_NOW);
        fprintf(stderr, "factory HAL did not load through adapter: %s\n",
                direct == NULL ? dlerror() : "direct load succeeded");
        if (direct != NULL) {
            dlclose(direct);
        }
        return 12;
    }
    const GpsInterface *interface = device->get_gps_interface(device);
    if (interface == NULL || interface->init == NULL ||
        interface->start == NULL || interface->stop == NULL) {
        fprintf(stderr, "factory GpsInterface incomplete\n");
        return 13;
    }
    void *stock = dlopen("/vendor/lib/hw/gps.z1stock.so",
                         RTLD_NOW | RTLD_NOLOAD);
    if (stock == NULL) {
        fprintf(stderr, "factory library was not present after open\n");
        return 14;
    }
    dlclose(stock);
    device->common.close(&device->common);
    dlclose(handle);
    puts("PASS: adapter loaded the real factory HAL without initializing mnld");
    return 0;
}
