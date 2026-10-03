#define LOG_TAG "health-z1"
#include <health2/service.h>
#include <healthd/healthd.h>
#include <log/log.h>
#include "capacity_policy.h"

static healthd_config* gConfig;

void healthd_board_init(healthd_config* config) {
    // BatteryMonitor::init runs after this hook, populating this same config
    // with discovered sysfs paths. Leave charger polling and all paths intact.
    gConfig = config;
}

int healthd_board_battery_update(android::BatteryProperties* props) {
    const char* path = gConfig ? gConfig->batteryCapacityPath.string() : nullptr;
    const bool available = z1_capacity_available(path);
    static int previous = -1;
    if (previous != int(available)) {
        if (!available) {
            ALOGW("Capacity unavailable: reporting status UNKNOWN; level=%d is not a measured state of charge; voltage=%d temperature=%d retained",
                  props->batteryLevel, props->batteryVoltage, props->batteryTemperature);
        } else {
            ALOGI("Real capacity available at %s: preserving measured battery state", path);
        }
        previous = int(available);
    }
    // Framework treats UNKNOWN as unavailable power state. Retain measured
    // voltage/temperature, online state and presence. A valid measured 0%
    // remains 0% with its original status and normal low-battery shutdown.
    z1_mark_capacity_unknown(props, available);
    return 0;
}

int main() { return health_service_main(); }
