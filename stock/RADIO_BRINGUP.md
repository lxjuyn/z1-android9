# MT6572 radio bring-up

The stock 3.4.67 kernel contains built-in MTK WMT, WLAN, and BTIF driver code.
The Android 9 product can stage the generic MT6572 loader and firmware from a
local Z1 ROM with:

```sh
python3 scripts/extract_z1_radio_blobs.py --rom /path/to/local-z1-rom.zip
```

The extractor has a fixed whitelist. It writes under the ignored
`proprietary/mt6572/` directory and does not read or copy NVRAM, calibration
databases, MAC addresses, userdata, logs, or other ZIP entries. Keep the radio
calibration already stored on the device's data partition; do not add it to the
repository or release package.

The stock init file starts `wmt_loader` and `6620_launcher`, matching the
factory Z1 startup flow. This initializes the low-level combo-radio transport;
it does not by itself provide Android 9 Wi-Fi or Bluetooth framework controls.
The current stock kernel lacks the separate HIDL Binder transport expected by
those framework HALs, and the vendor Android 7 HAL ABI has not passed a runtime
compatibility check. Those framework bridges remain unfinished until a device
boot confirms the radio interface and a safe Android 9 HAL path.
