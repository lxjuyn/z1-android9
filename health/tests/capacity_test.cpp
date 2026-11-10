#include "../capacity_policy.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <string>

struct Props {
    int batteryStatus, batteryLevel, batteryVoltage, batteryTemperature;
    int batteryHealth, batteryPresent;
    int chargerAcOnline, chargerUsbOnline, chargerWirelessOnline;
    int batteryCurrent, batteryChargeCounter;
};

static void put_bytes(const char* path, const char* value, size_t size) {
    FILE* f = fopen(path, "wb");
    assert(f);
    assert(fwrite(value, 1, size, f) == size);
    assert(fclose(f) == 0);
}

static void put(const char* path, const char* value) {
    put_bytes(path, value, strlen(value));
}

static void assert_other_fields(const Props& actual, const Props& before) {
    assert(actual.batteryVoltage == before.batteryVoltage);
    assert(actual.batteryTemperature == before.batteryTemperature);
    assert(actual.batteryHealth == before.batteryHealth);
    assert(actual.batteryPresent == before.batteryPresent);
    assert(actual.chargerAcOnline == before.chargerAcOnline);
    assert(actual.chargerUsbOnline == before.chargerUsbOnline);
    assert(actual.chargerWirelessOnline == before.chargerWirelessOnline);
    assert(actual.batteryCurrent == before.batteryCurrent);
    assert(actual.batteryChargeCounter == before.batteryChargeCounter);
}

static void assert_unavailable(const char* path, const Props& before) {
    int sample = -123;
    assert(!z1_read_capacity(path, &sample));
    assert(sample == -123); // A failed read must not output a replacement value.
    Props props = before;
    assert(!z1_update_capacity(&props, path));
    assert(props.batteryStatus == 1);
    assert(props.batteryLevel == before.batteryLevel);
    assert_other_fields(props, before);
}

static void assert_available(const char* path, int expected, const Props& before) {
    int sample = -123;
    assert(z1_read_capacity(path, &sample));
    assert(sample == expected);
    Props props = before;
    assert(z1_update_capacity(&props, path));
    assert(props.batteryLevel == expected);
    assert(props.batteryStatus == before.batteryStatus);
    assert_other_fields(props, before);
}

int main(int argc, char** argv) {
    assert(argc == 2);
    const char* p = argv[1];
    const Props initial = {3, 0, 3876, 451, 2, 1, 0, 0, 0, -123, 456789};
    assert_unavailable(nullptr, initial);
    assert_unavailable("", initial);
    assert_unavailable(p, initial); // Test runner supplies a fresh missing path.
    assert_unavailable(".", initial); // open succeeds but reading a directory fails.

    const char* invalid[] = {
        "", "-1\n", "101\n", "47junk\n", "unknown\n", " \n",
        "999999999999999999999999999999\n",
        "-999999999999999999999999999999\n",
        "47 48\n", "47.0\n", "0x2f\n", "+\n", "-\n", "4\n7\n",
        "47\xff", "\xff" "47"
    };
    for (const char* text : invalid) {
        put(p, text);
        assert_unavailable(p, initial);
    }
    const char nul_junk[] = {'4', '7', '\0', 'j', 'u', 'n', 'k', '\n'};
    const char nul_only[] = {'\0'};
    const char nul_tail[] = {'4', '7', '\0'};
    const char nul_space[] = {'4', '7', ' ', '\0', '\n'};
    put_bytes(p, nul_junk, sizeof(nul_junk));
    assert_unavailable(p, initial);
    put_bytes(p, nul_only, sizeof(nul_only));
    assert_unavailable(p, initial);
    put_bytes(p, nul_tail, sizeof(nul_tail));
    assert_unavailable(p, initial);
    put_bytes(p, nul_space, sizeof(nul_space));
    assert_unavailable(p, initial);

    struct Valid { const char* text; int expected; };
    const Valid valid[] = {
        {"0\n", 0}, {"100\n", 100}, {"47\n", 47}, {" 42 \n", 42},
        {"1", 1}, {"99\r\n", 99}, {"\t\r\n47\v\f ", 47},
        {"+47\n", 47}, {"-0\n", 0}, {"00047\n", 47}
    };
    for (const Valid& item : valid) {
        put(p, item.text);
        assert_available(p, item.expected, initial);
    }
    put(p, "47\n");
    assert(!z1_read_capacity(p, nullptr));

    // Preserve the existing conservative buffer limit: 62 bytes fit; a read
    // filling all 63 available bytes is rejected rather than treated as EOF.
    const std::string at_limit = "47" + std::string(60, ' ');
    put_bytes(p, at_limit.data(), at_limit.size());
    assert_available(p, 47, initial);
    for (size_t length : {size_t(63), size_t(64), size_t(128)}) {
        const std::string too_long = "47" + std::string(length - 2, ' ');
        put_bytes(p, too_long.data(), too_long.size());
        assert_unavailable(p, initial);
    }

    // An earlier failed/default-zero, stale/full or out-of-range framework
    // sample must never be certified by a different valid second read.
    const int earlier_levels[] = {-1, 0, 47, 100, 101};
    const Valid samples[] = {{"100\n", 100}, {"47\n", 47}, {"0\n", 0}, {"9\n", 9}};
    unsigned combinations = 0;
    for (int status = 1; status <= 5; ++status) {
        for (int online = 0; online < 8; ++online) {
            for (int present = 0; present <= 1; ++present) {
                for (int earlier : earlier_levels) {
                    Props before = initial;
                    before.batteryStatus = status;
                    before.batteryLevel = earlier;
                    before.batteryPresent = present;
                    before.chargerAcOnline = !!(online & 1);
                    before.chargerUsbOnline = !!(online & 2);
                    before.chargerWirelessOnline = !!(online & 4);
                    for (const Valid& sample : samples) {
                        put(p, sample.text);
                        assert_available(p, sample.expected, before);
                        ++combinations;
                    }
                    put(p, "unavailable\n");
                    assert_unavailable(p, before);
                }
            }
        }
    }

    // Reuse one object across plug/unplug and unavailable/recovery updates.
    // There is no retained capacity, smoothing, or online-to-100 conversion.
    Props props = initial;
    put(p, "47\n");
    assert(z1_update_capacity(&props, p));
    assert(props.batteryLevel == 47);
    props.chargerUsbOnline = 1;
    props.batteryStatus = 2;
    assert(z1_update_capacity(&props, p));
    assert(props.batteryLevel == 47 && props.batteryStatus == 2);
    put(p, "100\n");
    assert(z1_update_capacity(&props, p));
    assert(props.batteryLevel == 100 && props.batteryStatus == 2);
    props.chargerUsbOnline = 0;
    props.batteryStatus = 3;
    put(p, "9\n");
    assert(z1_update_capacity(&props, p));
    assert(props.batteryLevel == 9 && props.batteryStatus == 3);
    assert(unlink(p) == 0);
    assert(!z1_update_capacity(&props, p));
    assert(props.batteryStatus == 1 && props.batteryLevel == 9);
    put(p, "0\n");
    props.batteryStatus = 3; // BatteryMonitor supplies freshly read status.
    assert(z1_update_capacity(&props, p));
    assert(props.batteryStatus == 3 && props.batteryLevel == 0);
    assert_other_fields(props, initial);
    assert(unlink(p) == 0);
    assert_unavailable(p, props);

    printf("PASS: %u status/charger/presence/prior-level combinations; same validated sample reported; malformed/NUL/length cases rejected; other fields preserved\n", combinations);
}
