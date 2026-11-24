# Android 9 Wi-Fi power on Z1 R7

The core-wake/livegain system's `/vendor/lib/libwifi-hal.so` has SHA-256
`bc7e05250c6849e883d66856470e185e5cb61e71cd56675c01fd441cd9493088`.
Its `wifi_load_driver()` at 0xdef0 only sets `wlan.driver.status=ok`.
The corresponding unload function only sets `unloaded`.
Its `DriverTool::IsDriverLoaded()` at 0x5538 calls `wifi_unload_driver()`.
A property-trigger retrofit would give that query the side effect of
powering Wi-Fi off.

Pie `WifiNative.setupInterfaceForClientMode()` starts the HAL, starts
supplicant, then creates the STA interface and attaches it to supplicant.
`IWifi.start()` already invokes `WifiModeController::initialize()` and
`DriverTool::LoadDriver()`. `WifiLegacyHal::start()` checks for the interface
later, during chip configuration. The earlier synchronous load return is
the point where a WMT write failure or a missing interface can be reported
before Android asks supplicant to start. [Framework path](https://android.googlesource.com/platform/frameworks/opt/net/wifi/+/android-9.0.0_r61/service/java/com/android/server/wifi/WifiNative.java) [HAL path](https://android.googlesource.com/platform/hardware/interfaces/+/android-9.0.0_r61/wifi/1.2/default/wifi.cpp)

The R7 radio loader publishes `sys.z1.radio.wifi_modules_ready` after
inserting drivers and creating character device 155:0. Init reports
`z1_6620` running after starting the WMT helper. Its running state is a
prerequisite. The return from the driver write and the presence of
`wlan0` determine whether the start actually completed.
The SoC module serializes power writes and combo reset with `wr_mtx`,
accepts byte `1` for on and `0` for off, and returns a negative result on
WMT failure. Both requests can be repeated. [SoC Wi-Fi character driver](https://github.com/gabin8/mt6572-connsys-modules/blob/e9961becdc889266c3a8b5a488ff741159b6ad80/conn_soc/linux/pub/wmt_chrdev_wifi.c)

## Source candidate

`pie-r61.patch` adds the synchronous WMT backend behind the
`Z1_WMT_WIFI` build flag. It validates the opened device, waits for
`wlan0` before publishing success, and rolls back if the interface or
driver status fails to come up. On stop it waits for the legacy HAL's
normal cleanup before removing Wi-Fi power, checks interface removal and
retains enough state to retry a failed power-off. It also adds error
cleanup before iface allocation/creation and changes `IsDriverLoaded()`
to a read-only query.

R7 currently creates the node with mode 0660 and owner/group
`system:system`. The source-candidate Wi-Fi service adds supplementary
group `system` for that node. This follows the existing R7 permissive
policy. An enforcing release needs a dedicated device label and policy,
as well as a review of using a dedicated device group.

Use a checkout with the matching `android-9.0.0_r61` versions of
`frameworks/opt/net/wifi` and `hardware/interfaces`. Verify and apply
the patch from the root of that checkout. Set `Z1_WMT_WIFI := true`
in the candidate Z1 BoardConfig. Rebuild the source Wi-Fi HAL, Wi-Fi
service and Wi-Fi framework with the existing compatible vendor Wi-Fi
backend selected in the product. Preserve the mainline STA firmware
contract by keeping the STA firmware-path variable unset in this
source build. The flag alone does not select a Wi-Fi backend. The R7
prebuilt Broadcom donor does not consume this patch and cannot be
substituted for the rebuilt library.

```sh
patch --batch --fuzz=0 --dry-run -p1 -i /path/to/pie-r61.patch
patch --batch --fuzz=0 -p1 -i /path/to/pie-r61.patch
source build/envsetup.sh
lunch lineage_z1-userdebug
m libwifi-hal android.hardware.wifi@1.0-service wifi-service
```

## Verification

`python3 test_candidate.py /path/to/unmodified-pie-source` applies the
patch with zero fuzz and compiles the actual patched power-control
functions against test doubles. An optional
`--android-cxx /path/to/armv7a-linux-androideabi28-clang++` also compiles
the exercised code for Android ARMv7 API 28.

Both checks passed against the pinned files. The host tests cover module
and launcher readiness, wrong node, open/write failure, interface
appearance, read-only query, rollback, interface removal and failed off
retry. The patch also verifies source ordering for load-before-supplicant
and legacy cleanup-before-off. A complete AOSP product build and vendor
backend integration were unavailable in this checkout. The existing
comparator artifacts and installed software were left intact.

On a separate board candidate, boot with the automatic Wi-Fi diagnostic
removed. Confirm Wi-Fi off causes no WMT function-on and can be followed
by a cold Bluetooth enable. Then enable Wi-Fi through Settings and
measure WMT on, `wlan0` registration, supplicant startup, association
and traffic. Disable and verify supplicant release, function-off, no
`wlan0`, and Bluetooth continuity. Repeat off/on and test with Bluetooth
already connected. The old manual probe also forced WMT PSM off after
Wi-Fi startup, so coexistence must be checked with the new path's normal
PSM behavior.
