# Z1 R7 candidate with the Settings crash fix

The November 21 Settings tombstone records `ConnectedDeviceDashboardActivity` throwing in `UsbBackend.updatePorts()` because this port can start without `UsbService`. The Settings fix in `c19a954` tolerates the absent service. The combined system readback confirms Settings APK `6e9aa0dba297e8eb840b565b625716a7939e32a91bdc531187437721663e4b7a`. It also carries Bluetooth stack binary `86f6c3f0de68d417273d7fc869712f2d01b993b7aa2e43e76336a9daee034833`, matching the archived workaround for the controller's rejected feature pages.

The radio-enabled R7 boot keeps the ART startup repair and carries HWC r25 with wake retry (`42b992f`), the Bluetooth vendor transport comparison option (`450e4e8`), and the PCM reopen audio HAL. The three audio slots in the paired system read back as the same newer binary. The default Bluetooth path remains factory-first when NVRAM is ready.

The flash files are on the project HDD in `z1-archive/r7-current-settings-20261123_155141/package/`. The boot SHA-256 is `4a6693405da19df798fdd3423b1b209cf0a05df667a2a6129fa6c5e6772e0ba0`. The system SHA-256 is `d6f08965fe3fa22f0f8f1dd97675a4f06b0b333ba49fd7db9b13a34258ac80b6`. Boot readback verified all three replacement members, and the repeat build was byte-identical. System `e2fsck -fn` and the package checksums passed. The scatter enables `bootimg` and `android` in Download Only.

The latest armed UART capture has no bytes from the device. The Settings fix addresses the recorded Java exception. A fresh reproduction needs the exact foreground action, crash buffer, framework PIDs and UART boot continuity to distinguish a new Settings exception, Bluetooth service abort, or whole-device reset. This candidate is verified on the host and awaits board testing.
