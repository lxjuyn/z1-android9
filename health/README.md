# Z1 diagnostic health service

Module: `android.hardware.health@2.0-service.z1`, vendor executable with its
own healthd board hooks. The explicit `los16/device/jiabo/Android.mk` include
makes the device symlink visible to the Make build. Add this module to the
product and declare health HIDL 2.0 / IHealth / default / hwbinder in the
existing device manifest. No BoardConfig backend variable is required.
`LOCAL_OVERRIDES_MODULES` removes the generic healthd and health2 services.

This service addresses a real incomplete telemetry condition: the generic
ADC battery exposes status/voltage/temperature but no state of charge.
BatteryMonitor otherwise supplies level zero with DISCHARGING, which triggers
framework low-battery shutdown after system readiness. The board callback
marks status UNKNOWN only when capacity cannot be read as a valid 0..100
integer. Capacity, voltage, temperature, health, presence and charger flags
remain untouched. A real zero-percent measurement preserves its original
status and normal shutdown behavior. Log transitions distinguish unavailable
capacity from a measured value. This is a diagnostic image; a real fuel gauge
or calibrated state-of-charge implementation is still needed for battery
operation. No charger executable, charging controls, thermal shutdown policy,
framework BatteryService, or global kernel driver is modified.

Do not enable ro.boot.fake_battery: upstream substitutes both 42% and 42.4 C,
which would replace real temperature telemetry. No device property enables
that switch in the inspected source tree.

`tests/run_host_tests.sh` passes under ASan/UBSan, exercising missing, empty,
malformed, overflowing and out-of-range files, valid 0/47/100 capacity, and
preservation of measured fields. This is a host policy test; target build,
service registration, callbacks and on-device thermometry are not proved.
Pre-change parent Makefile: bridge_backups/health_z1_pre_20261002_233627/.

## Temperature telemetry limitation (2026-10-03)

M5 incorrectly exposed BAT_TEMP ADC voltage as power_supply TEMP: 1335 mV
became 1335000 in the 0.1 C field. The Z1 DTS now removes that invalid consumer
connection, while retaining the AUXADC voltage channel for calibration. The
ADC battery driver accepts TEMP only from an IIO_TEMP channel and converts
its milli-Celsius result to power_supply tenths Celsius by dividing by 100.
No Z1-specific NTC divider/curve calibration has been established; other-board
BSP tables are not evidence for this board.

With no valid temperature node, upstream BatteryMonitor supplies its default
zero. That is **unavailable telemetry, not a measured 0 C**. The diagnostic
image currently has no usable battery temperature measurement for Android
overtemperature protection. Framework temperature shutdown thresholds and
charging controls remain intact; real battery thermal protection requires
validated sensing and conversion before battery operation is considered
ready. Do not substitute a fixed temperature or interpret UNKNOWN status as
proof of safe charging. Preserve raw ADC observations for that next step.
