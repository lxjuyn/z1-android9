#include <assert.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>

static bool modules_ready = true;
static bool interface_up;
static bool reject_open;
static bool reject_power_on;
static bool reject_power_off;
static bool reject_status;
static bool wrong_node;
static bool no_interface;
static unsigned open_count;
static char writes[32];
static unsigned write_count;
static char driver_status[16];

int property_get(const char *name, char *value, const char *fallback) {
    const char *data = fallback;
    if (strcmp(name, "sys.z1.radio.wifi_modules_ready") == 0)
        data = modules_ready ? "1" : "0";
    else if (strcmp(name, "init.svc.z1_6620") == 0)
        data = "running";
    return (int)(stpcpy(value, data) - value);
}

int property_set(const char *name, const char *value) {
    assert(strcmp(name, "wlan.driver.status") == 0 && strlen(value) < sizeof driver_status);
    if (reject_status && strcmp(value, "ok") == 0)
        return -1;
    strcpy(driver_status, value);
    return 0;
}

int open(const char *path, int flags, ...) {
    if (strcmp(path, "/dev/wmtWifi") != 0) {
        static int (*real_open)(const char *, int, ...);
        if (!real_open) real_open = dlsym(RTLD_NEXT, "open");
        if (!(flags & O_CREAT)) return real_open(path, flags);
        va_list args;
        va_start(args, flags);
        mode_t mode = va_arg(args, int);
        va_end(args);
        return real_open(path, flags, mode);
    }
    assert(flags == (O_WRONLY | O_NOFOLLOW | O_CLOEXEC));
    ++open_count;
    if (reject_open) {
        errno = EACCES;
        return -1;
    }
    return 109;
}

int fstat(int fd, struct stat *st) {
    if (fd != 109) {
        static int (*real_fstat)(int, struct stat *);
        if (!real_fstat) real_fstat = dlsym(RTLD_NEXT, "fstat");
        return real_fstat(fd, st);
    }
    st->st_mode = S_IFCHR | 0660;
    st->st_rdev = makedev(155, wrong_node ? 1 : 0);
    return 0;
}

ssize_t write(int fd, const void *buffer, size_t length) {
    if (fd != 109) {
        static ssize_t (*real_write)(int, const void *, size_t);
        if (!real_write) real_write = dlsym(RTLD_NEXT, "write");
        return real_write(fd, buffer, length);
    }
    assert(length == 1 && write_count + 1 < sizeof writes);
    char value = *(const char *)buffer;
    writes[write_count++] = value;
    if ((value == '1' && reject_power_on) || (value == '0' && reject_power_off)) {
        errno = EIO;
        return -1;
    }
    interface_up = value == '1' && !no_interface;
    return 1;
}

int close(int fd) {
    if (fd != 109) {
        static int (*real_close)(int);
        if (!real_close) real_close = dlsym(RTLD_NEXT, "close");
        return real_close(fd);
    }
    return 0;
}

unsigned if_nametoindex(const char *name) {
    assert(strcmp(name, "wlan0") == 0);
    return interface_up ? 1 : 0;
}

int usleep(useconds_t interval) {
    assert(interval == 100000);
    return 0;
}

static void check_provider(const void *symbol, const char *filename) {
    Dl_info info;
    assert(symbol && dladdr(symbol, &info));
    assert(strcmp(strrchr(info.dli_fname, '/') + 1, filename) == 0);
}

int main(void) {
    void *hal = dlopen("libwifi-hal.so", RTLD_NOW | RTLD_LOCAL);
    if (!hal) {
        puts(dlerror());
        return 1;
    }
    void **vtable = dlsym(hal, "_ZTVN7android8wifi_hal10DriverToolE");
    check_provider(vtable, "libwmt-orig.so");
    bool (*load)(void*) = vtable[4];
    bool (*unload)(void*) = vtable[5];
    bool (*query)(void*) = vtable[6];
    check_provider(load, "libwifi-hal.so");
    check_provider(unload, "libwifi-hal.so");
    check_provider(query, "libwifi-hal.so");
    const char *(*firmware_path)(int) = dlsym(hal, "wifi_get_fw_path");
    check_provider(firmware_path, "libwmt-orig.so");
    assert(!firmware_path(0));
    int (*init_table)(void*) = dlsym(hal, "init_wifi_vendor_hal_func_table");
    check_provider(init_table, "libwmt-orig.so");
    void *table[128] = {0};
    assert(init_table(table) == 0 && table[0] != NULL);

    assert(!query(NULL) && open_count == 0);
    assert(unload(NULL) && open_count == 0);
    modules_ready = false;
    assert(!load(NULL) && open_count == 0);
    modules_ready = true;
    reject_open = true;
    assert(!load(NULL) && open_count == 2 && write_count == 0);
    reject_open = false;
    wrong_node = true;
    assert(!load(NULL) && open_count == 4 && write_count == 0);
    wrong_node = false;
    no_interface = true;
    assert(!load(NULL) && !query(NULL) && strcmp(writes, "10") == 0);
    no_interface = false;
    write_count = 0;
    memset(writes, 0, sizeof writes);
    reject_power_on = true;
    assert(!load(NULL) && strcmp(writes, "10") == 0);
    reject_power_on = false;
    reject_status = true;
    assert(!load(NULL) && !query(NULL) && strcmp(writes, "1010") == 0);
    reject_status = false;
    assert(load(NULL) && query(NULL) && strcmp(driver_status, "ok") == 0);
    assert(strcmp(writes, "10101") == 0);
    reject_power_off = true;
    assert(!unload(NULL) && query(NULL) && strcmp(driver_status, "ok") == 0);
    reject_power_off = false;
    assert(unload(NULL) && !query(NULL) && strcmp(driver_status, "unloaded") == 0);
    assert(load(NULL) && unload(NULL) && strcmp(writes, "101010010") == 0);
    puts("R7 Bionic donor symbol binding and Wi-Fi power lifecycle passed");
    return 0;
}
