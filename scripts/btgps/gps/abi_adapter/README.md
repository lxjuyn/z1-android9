# Z1 factory GPS ABI adapter

This directory contains an Android 9 compatibility `gps.default.so` for the
factory MTK GPS library. It exists because the factory HAL was built against
Android 4.4 and uses a 44-byte callback table with this ordering:

```text
offset 0x20: request_utc_time_cb
offset 0x24: create_thread_cb
```

Android 9's `libhardware` supplies those two callbacks in the opposite order.
Passing the Android 9 table directly makes the factory HAL call
`requestUtcTimeCb` as its GPS worker thread constructor. The wrapper copies the
common callbacks into the factory layout and forwards all normal
`GpsInterface` operations.

The adapter deliberately does not create fake locations. A successful load
still depends on all of these real components:

```text
gnss@1.0 HIDL
  -> gps.default.so (this adapter)
  -> gps.z1stock.so (factory HAL)
  -> mnld socket
  -> libmnlp_mt6572
  -> /dev/stpgps
  -> WMT GPS
```

Install the untouched factory HAL as
`/vendor/lib/hw/gps.z1stock.so` and the adapter as
`/vendor/lib/hw/gps.default.so`. The stock service must be named `mnld`, because
the factory HAL polls `init.svc.mnld` and issues `ctl.start mnld`.

Build:

```sh
scripts/btgps/gps/abi_adapter/build.sh /tmp/gps-abi-adapter
```

Host verification proves ELF architecture, exported `HMI`, SONAME, and the
factory symbol lookup. It does not prove device loading, STP transport, a
satellite fix, NMEA delivery, or a location callback. Those remain board gates.
