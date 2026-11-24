#include <android/log.h>
#include <errno.h>
#include <fcntl.h>
#include <net/if.h>
#include <pthread.h>
#include <stdbool.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>

// Exported by the existing Pie libcutils.so.
extern int property_get(const char *, char *, const char *);
extern int property_set(const char *, const char *);

static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static bool needs_power_off;

static bool wmt_set_power(char state) {
    int fd = TEMP_FAILURE_RETRY(open("/dev/wmtWifi", O_WRONLY | O_NOFOLLOW | O_CLOEXEC));
    if (fd < 0) {
        __android_log_print(ANDROID_LOG_ERROR, "Z1WmtWifi", "open failed: %d", errno);
        return false;
    }

    struct stat st;
    bool success = fstat(fd, &st) == 0 && S_ISCHR(st.st_mode) &&
                   major(st.st_rdev) == 155 && minor(st.st_rdev) == 0 &&
                   TEMP_FAILURE_RETRY(write(fd, &state, 1)) == 1;
    if (!success)
        __android_log_print(ANDROID_LOG_ERROR, "Z1WmtWifi", "request %c failed", state);
    close(fd);
    return success;
}

static bool radio_ready(void) {
    char value[92];
    return property_get("sys.z1.radio.wifi_modules_ready", value, "") > 0 &&
           strcmp(value, "1") == 0 &&
           property_get("init.svc.z1_6620", value, "") > 0 && strcmp(value, "running") == 0;
}

static bool has_interface(void) {
    return if_nametoindex("wlan0") != 0;
}

static bool interface_removed(void) {
    return !has_interface();
}

static bool wait_until(bool (*ready)(void)) {
    for (int retry = 0; retry < 50 && !ready(); ++retry)
        usleep(100000);
    return ready();
}

static bool power_off(void) {
    if (needs_power_off || has_interface()) {
        if (!wmt_set_power('0') || !wait_until(interface_removed))
            return false;
    }
    needs_power_off = false;
    return property_set("wlan.driver.status", "unloaded") == 0;
}

bool load_driver(void *) __asm__("_ZN7android8wifi_hal10DriverTool10LoadDriverEv");
bool unload_driver(void *) __asm__("_ZN7android8wifi_hal10DriverTool12UnloadDriverEv");
bool is_driver_loaded(void *) __asm__("_ZN7android8wifi_hal10DriverTool14IsDriverLoadedEv");

bool load_driver(void *driver_tool) {
    (void)driver_tool;
    pthread_mutex_lock(&lock);
    bool success = false;
    if (wait_until(radio_ready)) {
        // Even a failed write can leave the controller partly powered.
        needs_power_off = true;
        success = wmt_set_power('1') && wait_until(has_interface) &&
                  property_set("wlan.driver.status", "ok") == 0;
        if (!success && !power_off())
            __android_log_print(ANDROID_LOG_ERROR, "Z1WmtWifi", "rollback failed");
    }
    pthread_mutex_unlock(&lock);
    return success;
}

bool unload_driver(void *driver_tool) {
    (void)driver_tool;
    pthread_mutex_lock(&lock);
    bool success = power_off();
    pthread_mutex_unlock(&lock);
    return success;
}

bool is_driver_loaded(void *driver_tool) {
    (void)driver_tool;
    pthread_mutex_lock(&lock);
    bool loaded = has_interface();
    pthread_mutex_unlock(&lock);
    return loaded;
}
