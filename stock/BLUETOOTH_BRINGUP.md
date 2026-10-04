# Z1 MT6572 Bluetooth bring-up

The stock-kernel profile uses Android 9's Bluetooth 1.0 HIDL passthrough
implementation. `libbt-vendor.so` adapts the Pie vendor interface to the
factory `libbluetoothdrv.so`; the latter starts the MTK controller and reads
its own address and radio calibration from the Z1's existing NVRAM.

Extract only the generic driver library from the local factory system image:

```sh
python3 scripts/extract_z1_bt_driver.py \
  --system-image /path/to/local/factory/system.img
```

The extractor checks the fixed `/system/lib/libbluetoothdrv.so` path, its ARM32
ABI, and the two required exported functions. It never reads the NVRAM
partition or extracts address/calibration data. The proprietary output is
ignored by Git; the product copies only this one library into `/vendor/lib/`.

The vendor driver obtains the BD_ADDR from its own on-device NVRAM path, while
Android 9's generic passthrough client requires an address before it calls the
vendor adapter. The stock-only `ro.bt.vendor_owns_bdaddr=true` property allows
that adapter to proceed with a zeroed unused input address; the controller
still reports its address from the device-local NVRAM.

Static ELF/init-closure checks can prove that the HIDL factory, adapter,
factory library, and their declared dependencies are present. They cannot
prove that the `/dev/stpbt` transport opens, the controller initializes, or
pairing/SCO audio works. Those checks need a live Z1 and serial/logcat evidence.
