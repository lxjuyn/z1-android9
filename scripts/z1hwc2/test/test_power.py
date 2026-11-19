#!/usr/bin/env python3
"""Exercise the production Route B power callback against a mock LED sysfs.

No device files are opened. Exact production functions are compiled with
injected open/read/write failures and independently controlled light-HAL writes.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import shlex
import subprocess

from test_fence import function


MOCK = r'''
#include <cerrno>
#include <climits>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <stdexcept>
#include <string>
#include <sys/types.h>
#include <vector>
using hwc2_display_t = uint64_t;
struct hwc2_device_t {};
struct z1_hwc2 : hwc2_device_t {
    bool powered_on = true, backlight_blank_applied = false;
    bool backlight_gate_applied = false;
    int backlight_saved = 0;
};
static z1_hwc2 *H(hwc2_device_t *d) { return static_cast<z1_hwc2 *>(d); }
static void klogf(int, const char *, ...) {}
/* DEFINITIONS */
#define CHECK(x) do { if (!(x)) throw std::runtime_error(std::string(__func__) + ": " #x); } while (0)
static std::string level, maximum, blank = "0\n";
static int read_error, max_error, write_error, short_write, read_eintr, write_eintr;
static int blank_missing, blank_write_error, blank_short_write, blank_write_eintr;
static bool ignore_write;
static int opens, closes;
static std::vector<int> writes;
static int mock_open(const char *path, int flags) {
    ++opens;
    CHECK(flags & O_CLOEXEC);
    if (!strcmp(path, Z1_DISPLAY_BLANK_PATH)) {
        if (blank_missing) { errno = ENOENT; return -1; }
        if (blank_write_error) { errno = blank_write_error; return -1; }
        return 13;
    }
    if (!strcmp(path, Z1_BACKLIGHT_MAX_PATH)) {
        if (max_error) { errno = max_error; return -1; }
        return 12;
    }
    CHECK(!strcmp(path, Z1_BACKLIGHT_PATH));
    if ((flags & O_ACCMODE) == O_WRONLY) {
        if (write_error) { errno = write_error; return -1; }
        return 11;
    }
    if (read_error) { errno = read_error; return -1; }
    return 10;
}
static ssize_t mock_read(int fd, void *buf, size_t size) {
    CHECK(fd == 10 || fd == 12);
    if (read_eintr) { --read_eintr; errno = EINTR; return -1; }
    const auto &s = fd == 12 ? maximum : level;
    size_t n = s.size() < size ? s.size() : size;
    memcpy(buf, s.data(), n); return n;
}
static ssize_t mock_write(int fd, const void *buf, size_t size) {
    CHECK(fd == 11 || fd == 13);
    if (fd == 13) {
        if (blank_write_eintr) { --blank_write_eintr; errno = EINTR; return -1; }
        if (blank_short_write) return size - 1;
        blank = std::string(static_cast<const char *>(buf), size);
        return size;
    }
    if (write_eintr) { --write_eintr; errno = EINTR; return -1; }
    if (short_write) return size - 1;
    std::string s(static_cast<const char *>(buf), size);
    writes.push_back(std::stoi(s));
    if (!ignore_write) level = s;
    return size;
}
static int mock_close(int fd) { CHECK(fd >= 10 && fd <= 13); ++closes; return 0; }
#define open mock_open
#define read mock_read
#define write mock_write
#define close mock_close
/* FUNCTIONS */
#undef open
#undef read
#undef write
#undef close
static int power(z1_hwc2 &h, int mode) { return z1_set_power_mode(&h, 0, mode); }
static void reset() {
    level = "2\n"; maximum = "6\n"; blank = "0\n";
    read_error = max_error = write_error = short_write = read_eintr = write_eintr = 0;
    blank_missing = blank_write_error = blank_short_write = blank_write_eintr = 0;
    opens = closes = 0; ignore_write = false; writes.clear();
}
static void valid_power_cycle() {
    z1_hwc2 h;
    CHECK(power(h, HWC2_POWER_MODE_OFF) == HWC2_ERROR_NONE);
    CHECK(h.backlight_saved == 2 && h.backlight_blank_applied && !h.powered_on);
    CHECK(h.backlight_gate_applied && blank == "1\n" && level == "2\n");
    CHECK(power(h, HWC2_POWER_MODE_ON) == HWC2_ERROR_NONE);
    CHECK(h.powered_on && !h.backlight_blank_applied && level == "2\n");
    CHECK(!h.backlight_gate_applied && blank == "0\n");
    CHECK(writes.empty()); CHECK(opens == closes);
}
static void invalid_display_and_modes() {
    z1_hwc2 h;
    CHECK(z1_set_power_mode(&h, 1, HWC2_POWER_MODE_OFF) == HWC2_ERROR_BAD_DISPLAY);
    CHECK(power(h, 99) == HWC2_ERROR_BAD_PARAMETER);
    CHECK(power(h, HWC2_POWER_MODE_DOZE) == HWC2_ERROR_UNSUPPORTED);
    CHECK(power(h, HWC2_POWER_MODE_DOZE_SUSPEND) == HWC2_ERROR_UNSUPPORTED);
    CHECK(opens == 0 && writes.empty() && h.powered_on);
}
static void repeated_on_preserves_user_level() {
    z1_hwc2 h; level = "1\n";
    CHECK(power(h, HWC2_POWER_MODE_ON) == HWC2_ERROR_NONE);
    CHECK(power(h, HWC2_POWER_MODE_ON) == HWC2_ERROR_NONE);
    CHECK(opens == 0 && writes.empty() && level == "1\n");
}
static void repeated_off_keeps_restore_level() {
    z1_hwc2 h; CHECK(power(h, HWC2_POWER_MODE_OFF) == HWC2_ERROR_NONE);
    blank = "1\n"; // the gate remains latched across repeated OFF
    CHECK(power(h, HWC2_POWER_MODE_OFF) == HWC2_ERROR_NONE);
    CHECK(h.backlight_saved == 2 && blank == "1\n");
    CHECK(power(h, HWC2_POWER_MODE_ON) == HWC2_ERROR_NONE);
    CHECK(blank == "0\n");
}
static void wake_preserves_new_light_hal_level() {
    z1_hwc2 h; CHECK(power(h, HWC2_POWER_MODE_OFF) == HWC2_ERROR_NONE);
    level = "4\n"; // the LED driver keeps this level for wake while blanked
    CHECK(power(h, HWC2_POWER_MODE_ON) == HWC2_ERROR_NONE);
    CHECK(level == "4\n" && blank == "0\n" && h.powered_on);
}
static void light_hal_cleared_before_off() {
    z1_hwc2 h; h.backlight_saved = 2; level = "0\n";
    CHECK(power(h, HWC2_POWER_MODE_OFF) == HWC2_ERROR_NONE);
    CHECK(power(h, HWC2_POWER_MODE_ON) == HWC2_ERROR_NONE);
    CHECK(blank == "0\n" && level == "0\n");
}
static void zero_without_cache_uses_maximum() {
    z1_hwc2 h; level = "0\n";
    CHECK(power(h, HWC2_POWER_MODE_OFF) == HWC2_ERROR_NONE);
    CHECK(h.backlight_saved == 6);
    CHECK(power(h, HWC2_POWER_MODE_ON) == HWC2_ERROR_NONE &&
          blank == "0\n" && level == "0\n");
}
static void off_permission_failure_and_retry() {
    z1_hwc2 h; blank_write_error = EACCES;
    CHECK(power(h, HWC2_POWER_MODE_OFF) == HWC2_ERROR_NO_RESOURCES);
    CHECK(h.powered_on && !h.backlight_blank_applied && level == "2\n");
    blank_write_error = 0; CHECK(power(h, HWC2_POWER_MODE_OFF) == HWC2_ERROR_NONE);
    CHECK(!h.powered_on && h.backlight_saved == 2);
}
static void wake_permission_failure_and_retry() {
    z1_hwc2 h; CHECK(power(h, HWC2_POWER_MODE_OFF) == HWC2_ERROR_NONE);
    blank_write_error = EACCES;
    CHECK(power(h, HWC2_POWER_MODE_ON) == HWC2_ERROR_NO_RESOURCES);
    CHECK(!h.powered_on && h.backlight_blank_applied);
    blank_write_error = 0; CHECK(power(h, HWC2_POWER_MODE_ON) == HWC2_ERROR_NONE);
    CHECK(blank == "0\n" && h.powered_on);
}
static void no_restore_source_refuses_off() {
    z1_hwc2 h; read_error = ENOENT; max_error = ENOENT;
    CHECK(power(h, HWC2_POWER_MODE_OFF) == HWC2_ERROR_NO_RESOURCES);
    CHECK(h.powered_on && !h.backlight_blank_applied && writes.empty());
}
static void short_write_does_not_claim_success() {
    z1_hwc2 h; blank_short_write = 1;
    CHECK(power(h, HWC2_POWER_MODE_OFF) == HWC2_ERROR_NO_RESOURCES);
    CHECK(h.powered_on && !h.backlight_blank_applied && blank == "0\n");
    CHECK(opens == closes);
}
static void hardware_readback_mismatch() {
    z1_hwc2 h; blank_missing = 1; ignore_write = true;
    CHECK(power(h, HWC2_POWER_MODE_OFF) == HWC2_ERROR_NO_RESOURCES);
    CHECK(h.powered_on && !h.backlight_blank_applied && level == "2\n");
}
static void interrupted_io_retries() {
    z1_hwc2 h; read_eintr = write_eintr = blank_write_eintr = 1;
    CHECK(power(h, HWC2_POWER_MODE_OFF) == HWC2_ERROR_NONE);
    CHECK(!h.powered_on && blank == "1\n" && opens == closes);
}
static void old_kernel_brightness_fallback() {
    z1_hwc2 h; blank_missing = 1;
    CHECK(power(h, HWC2_POWER_MODE_OFF) == HWC2_ERROR_NONE);
    CHECK(level == "0\n" && !h.backlight_gate_applied &&
          h.backlight_blank_applied && !h.powered_on);
    CHECK(power(h, HWC2_POWER_MODE_ON) == HWC2_ERROR_NONE);
    CHECK(level == "2\n" && h.powered_on);
}
static void malformed_sysfs_values() {
    for (const char *bad : {"", "-1\n", "abc", "2oops", "99999999999999999999999999999999", "2147483648"}) {
        level = bad; int v = 71;
        CHECK(z1_backlight_read(Z1_BACKLIGHT_PATH, &v) < 0 && v == 71);
    }
    maximum = "0\n"; level = "0\n"; blank_missing = 1; z1_hwc2 h;
    CHECK(power(h, HWC2_POWER_MODE_OFF) == HWC2_ERROR_NO_RESOURCES);
    CHECK(h.powered_on && writes.empty());
}
int main() {
    struct Test { const char *name; void (*fn)(); };
#define CASE(x) {#x, x}
    Test cases[] = {CASE(valid_power_cycle), CASE(invalid_display_and_modes),
        CASE(repeated_on_preserves_user_level), CASE(repeated_off_keeps_restore_level),
        CASE(wake_preserves_new_light_hal_level), CASE(light_hal_cleared_before_off),
        CASE(zero_without_cache_uses_maximum), CASE(off_permission_failure_and_retry),
        CASE(wake_permission_failure_and_retry), CASE(no_restore_source_refuses_off),
        CASE(short_write_does_not_claim_success), CASE(hardware_readback_mismatch),
        CASE(interrupted_io_retries), CASE(old_kernel_brightness_fallback),
        CASE(malformed_sysfs_values)};
    int failures = 0;
    for (auto &test : cases) { reset(); try { test.fn(); printf("PASS %s\n", test.name); }
        catch (const std::exception &e) { ++failures; printf("FAIL %s: %s\n", test.name, e.what()); } }
    printf("%zu scenarios, %d failed\n", sizeof(cases)/sizeof(cases[0]), failures);
    return !!failures;
}
'''


def main():
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=here.parent / 'z1hwc2.cpp')
    parser.add_argument('--out-dir', type=Path, required=True)
    parser.add_argument('--sanitize', action='store_true')
    args = parser.parse_args()
    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    src = args.source.read_text()
    hdr = (here.parent / 'include/hardware/hwcomposer2.h').read_text()
    definitions = []
    for name in ('hwc2_error_t', 'hwc2_power_mode_t'):
        definitions.append(re.search(r'typedef enum \{[^}]*\}\s*' + name + ';', hdr, re.S).group(0))
    for name in ('Z1_BACKLIGHT_PATH', 'Z1_BACKLIGHT_MAX_PATH',
                 'Z1_DISPLAY_BLANK_PATH'):
        definitions.append(re.search(r'^#define ' + name + r'\s+[^\n]+', src, re.M).group(0))
    bodies = '\n\n'.join(function(src, name) for name in
                         ('z1_backlight_read', 'z1_backlight_write',
                          'z1_display_blank_write', 'z1_set_power_mode'))
    generated = MOCK.replace('/* DEFINITIONS */', '\n'.join(definitions))
    generated = generated.replace('/* FUNCTIONS */', bodies)
    cpp = out / 'power.cpp'
    binary = out / 'power'
    cpp.write_text(generated)
    cmd = shlex.split(os.environ.get('CXX_HOST', 'g++')) + [
        '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror']
    if args.sanitize:
        cmd += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    cmd += [str(cpp), '-o', str(binary)]
    print('source_sha256=' + hashlib.sha256(args.source.read_bytes()).hexdigest(), flush=True)
    built = subprocess.run(cmd, capture_output=True, text=True)
    (out / 'compile.log').write_text(built.stdout + built.stderr)
    if built.returncode:
        print(built.stdout + built.stderr)
        return built.returncode
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
    (out / 'test.log').write_text(result.stdout + result.stderr)
    print(result.stdout + result.stderr, end='')
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
