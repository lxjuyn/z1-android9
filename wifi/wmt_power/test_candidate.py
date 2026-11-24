#!/usr/bin/env python3
"""Apply the patch to exact Pie sources and exercise the synchronous HAL path."""
import argparse
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
COMMON = 'frameworks/opt/net/wifi/libwifi_hal/wifi_hal_common.cpp'
TOOL = 'frameworks/opt/net/wifi/libwifi_hal/driver_tool.cpp'
WIFI = 'hardware/interfaces/wifi/1.2/default/wifi.cpp'
NATIVE = 'frameworks/opt/net/wifi/service/java/com/android/server/wifi/WifiNative.java'
PATCH = HERE / 'pie-r61.patch'

HARNESS = r'''
#include <cassert>
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <mutex>
#include <string>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>

static int index_calls;
static int wait_calls;
static int node_type;
static unsigned device_major = 155;
static bool fail_open;
static bool fail_on;
static bool fail_off;
static bool fail_status;
static bool link_stuck;
static bool radio_ready = true;
static bool launcher_ready = true;
static bool link_seen;
static int delay;
static std::string writes;
static std::string prop;

struct Log {
  template <typename T> Log& operator<<(const T&) { return *this; }
};
#define LOG(level) Log{}
#define PLOG(level) Log{}
#define PROPERTY_VALUE_MAX 92
static constexpr char DRIVER_PROP_NAME[] = "wlan.driver.status";

int mock_open(const char* path, int flags) {
  assert(strcmp(path, "/dev/wmtWifi") == 0);
  assert(flags == (O_WRONLY | O_CLOEXEC | O_NOFOLLOW));
  if (fail_open) { errno = EACCES; return -1; }
  return 8;
}
int mock_fstat(int fd, struct stat* st) {
  assert(fd == 8);
  st->st_mode = node_type;
  st->st_rdev = makedev(device_major, 0);
  return 0;
}
ssize_t mock_write(int fd, const char* state, size_t len) {
  assert(fd == 8 && len == 1);
  writes += *state;
  if ((*state == '1' && fail_on) || (*state == '0' && fail_off)) {
    errno = EIO;
    return -1;
  }
  if (*state == '1') link_seen = delay == 0;
  else if (!link_stuck) link_seen = false;
  return 1;
}
int mock_close(int fd) { assert(fd == 8); return 0; }
unsigned int mock_if_nametoindex(const char* name) {
  assert(strcmp(name, "wlan0") == 0);
  ++index_calls;
  if (delay > 0 && index_calls >= delay) link_seen = true;
  return link_seen ? 11 : 0;
}
int mock_usleep(useconds_t interval) {
  assert(interval == 100000);
  ++wait_calls;
  return 0;
}
int property_get(const char* name, char* value, const char*) {
  if (strcmp(name, "sys.z1.radio.wifi_modules_ready") == 0) {
    strcpy(value, radio_ready ? "1" : "0");
  } else {
    assert(strcmp(name, "init.svc.z1_6620") == 0);
    strcpy(value, launcher_ready ? "running" : "stopped");
  }
  return strlen(value);
}
int property_set(const char* name, const char* value) {
  assert(strcmp(name, DRIVER_PROP_NAME) == 0);
  if (fail_status && strcmp(value, "ok") == 0) return -1;
  prop = value;
  return 0;
}

#define open mock_open
#define fstat mock_fstat
#define write mock_write
#define close mock_close
#define if_nametoindex mock_if_nametoindex
#define usleep mock_usleep
'''

CASES = r'''
void reset() {
  wmt_powered = false;
  link_seen = link_stuck = fail_open = fail_on = fail_off = fail_status = false;
  radio_ready = launcher_ready = true;
  node_type = S_IFCHR;
  device_major = 155;
  index_calls = wait_calls = delay = 0;
  prop = "unloaded";
  writes.clear();
}

int main() {
  reset();
  delay = 3;
  assert(wifi_load_driver() == 0 && wait_calls == 2);
  assert(writes == "1" && prop == "ok" && is_wifi_driver_loaded());
  assert(writes == "1");
  delay = 0;
  assert(wifi_unload_driver() == 0 && writes == "10" && prop == "unloaded");
  assert(!is_wifi_driver_loaded());
  assert(wifi_unload_driver() == 0 && writes == "10");

  reset();
  radio_ready = false;
  assert(wifi_load_driver() == -1 && wait_calls == 50 && writes.empty());

  reset();
  launcher_ready = false;
  assert(wifi_load_driver() == -1 && writes.empty());

  reset();
  fail_open = true;
  assert(wifi_load_driver() == -1 && writes.empty());

  reset();
  node_type = S_IFREG;
  assert(wifi_load_driver() == -1 && writes.empty());
  node_type = S_IFCHR;
  device_major = 153;
  assert(wifi_load_driver() == -1 && writes.empty());

  reset();
  fail_on = true;
  assert(wifi_load_driver() == -1 && writes == "1" && prop == "unloaded");

  reset();
  delay = -1;
  assert(wifi_load_driver() == -1 && wait_calls == 50 && writes == "10");

  reset();
  fail_status = true;
  assert(wifi_load_driver() == -1 && writes == "10" && prop == "unloaded");

  reset();
  assert(wifi_load_driver() == 0);
  fail_off = true;
  assert(wifi_unload_driver() == -1 && prop == "ok" && writes == "10");
  fail_off = false;
  assert(wifi_unload_driver() == 0 && prop == "unloaded" && writes == "100");

  reset();
  assert(wifi_load_driver() == 0);
  link_stuck = true;
  assert(wifi_unload_driver() == -1 && prop == "ok" && wait_calls == 50);
  link_stuck = false;
  assert(wifi_unload_driver() == 0 && writes == "100");

  reset();
  delay = -1;
  fail_off = true;
  assert(wifi_load_driver() == -1 && writes == "10");
  fail_off = false;
  assert(wifi_unload_driver() == 0 && writes == "100");

  reset();
  prop = "ok";
  link_seen = true;  // The first HAL process died with Wi-Fi still on.
  assert(wifi_unload_driver() == 0 && writes == "0" && prop == "unloaded");
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='unpatched Pie source root')
    parser.add_argument('--android-cxx', type=Path, help='optional API 28 ARMv7 clang++')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='z1-wmt-wifi-test-') as scratch:
        work = Path(scratch)
        for line in PATCH.read_text().splitlines():
            if line.startswith('--- a/'):
                rel = line[len('--- a/'):]
                path = work / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((args.source / rel).read_bytes())
        subprocess.run(['patch', '--batch', '--fuzz=0', '--forward', '-p1', '-i', PATCH],
                       check=True, cwd=work)

        common = (work / COMMON).read_text()
        begin = common.index('#ifdef Z1_WMT_WIFI\nnamespace {')
        end = common.index('const char *wifi_get_fw_path(', begin)
        code = HARNESS + '\n#define Z1_WMT_WIFI\n' + common[begin:end] + CASES
        (work / 'harness.cpp').write_text(code)
        subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-pthread',
                        'harness.cpp', '-o', 'test'], check=True, cwd=work)
        subprocess.run([work / 'test'], check=True)
        if args.android_cxx:
            subprocess.run([args.android_cxx.resolve(), '-std=c++17', '-Wall', '-Wextra',
                            '-Werror', '-c', 'harness.cpp', '-o', 'harness.arm.o'],
                           check=True, cwd=work)

        wifi = (work / WIFI).read_text()
        init = wifi.split('WifiStatus Wifi::initializeModeControllerAndLegacyHal()', 1)[1]
        assert init.index('mode_controller_->initialize()') < init.index('legacy_hal_->initialize()')
        assert init.index('mode_controller_->deinitialize()') < init.index(
            'return createWifiStatusFromLegacyError(legacy_status)')
        stop = wifi.split('WifiStatus Wifi::stopLegacyHalAndDeinitializeModeController(', 1)[1]
        assert stop.index('legacy_hal_->stop') < stop.index('mode_controller_->deinitialize()')
        tool = (work / TOOL).read_text()
        assert 'bool DriverTool::IsDriverLoaded() {\n  return ::is_wifi_driver_loaded() != 0;' in tool
        native = (work / NATIVE).read_text()
        client = native.split('public String setupInterfaceForClientMode(', 1)[1].split(
            'public String setupInterfaceForSoftApMode(', 1)[0]
        assert client.index('startHal()') < client.index('startSupplicant()') < client.index(
            'createStaIface(')
        assert client.count('stopSupplicantIfNecessary();\n'
                            '                stopHalAndWificondIfNecessary();') == 3
    print('PASS: patched Pie HAL lifecycle and mocked WMT power, readiness, rollback and off')


if __name__ == '__main__':
    main()
