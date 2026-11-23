# Z1 R7 candidate with the Settings crash fix

The November 21 Settings tombstone records `ConnectedDeviceDashboardActivity` throwing in `UsbBackend.updatePorts()` because this port can start without `UsbService`. The Settings fix in `c19a954` tolerates the absent service. The combined system readback confirms Settings APK `6e9aa0dba297e8eb840b565b625716a7939e32a91bdc531187437721663e4b7a`. It also carries Bluetooth stack binary `86f6c3f0de68d417273d7fc869712f2d01b993b7aa2e43e76336a9daee034833`, matching the archived workaround for the controller's rejected feature pages.

The radio-enabled R7 boot keeps the ART startup repair and carries HWC r25 with wake retry (`42b992f`), the Bluetooth vendor transport comparison option (`450e4e8`), and the PCM reopen audio HAL. The three audio slots in the paired system read back as the same newer binary. The default Bluetooth path remains factory-first when NVRAM is ready.

The flash files are on the project HDD in `z1-archive/r7-current-settings-20261123_155141/package/`. The boot SHA-256 is `4a6693405da19df798fdd3423b1b209cf0a05df667a2a6129fa6c5e6772e0ba0`. The system SHA-256 is `d6f08965fe3fa22f0f8f1dd97675a4f06b0b333ba49fd7db9b13a34258ac80b6`. Boot readback verified all three replacement members, and the repeat build was byte-identical. System `e2fsck -fn` and the package checksums passed. The scatter enables `bootimg` and `android` in Download Only.

The November 23 flash completed successfully: all 5,992,448 bytes of boot and all 19 system blocks (943,718,400 bytes) read back byte-identical. The flash log is `flash-reconnect-20261123_160803.log` in the package archive. The board results below come from `mainline_recon/z1_boot_mainline_20261123_161422.log`.

| Check | Live result |
|---|---|
| Boot and graphics | Android reached `sys.boot_completed=1` with activity, Bluetooth manager, and power services registered (lines 2260–2294). SurfaceFlinger reported `Mesa, Mali400, OpenGL ES 2.0 Mesa 26.1.1`. No scrolling or frame pacing pass yet. |
| Lock and wake | UART-injected sleep and wake reported Asleep/OFF then Awake/ON. `lcd-backlight/brightness` went from 0 to 2 with the same boot ID (lines 4526–4573). Physical power key and first visible frame still need verification. |
| Settings and stability | First reset followed the Bluetooth Settings intent at about 275.6 s (lines 2298–2354). Second reset followed `wm dismiss-keyguard` at about 363.7 s, before the Settings launch in that command sequence could run (lines 4583–4653). Neither captured boundary shows a Java Settings exception, a kernel panic, or a confirmed hardware reset cause. |
| Audio | ALSA card, playback and capture nodes registered. A full AudioFlinger diagnostic dump during startup tripped the Binder dump (`0x5f444d50`) TimeCheck and restarted audioserver and the audio HAL (lines 6625–6683). This does not establish whether media playback or the speaker works. |
| Bluetooth | `com.android.bluetooth` was disabled for user 0; enabling the package succeeded. The serial shell's direct enable command was denied `BLUETOOTH_ADMIN` (lines 6940–7021). There is no confirmed privileged enable, HCI result or pairing result on this package. |

The third run had reached an uptime of 926.93 s when the kernel logged `RGS_CHRDET` falling to zero; about 62 ms later the Linux output ended, followed by Preloader reporting `RGU STA=0`, then `Unknown boot` and `pl_power_off` (lines 7165–7267). The UART root request had no queue or execution receipt. This is a charger-detection correlation on the third run; it does not identify the cause of the first two resets or establish that the Bluetooth request ran.

Keep the same payload for the next controlled boot. Record whether the board starts on battery with the download cable absent, then observe idle, sleep/wake, standalone unlock and Settings in separate marked steps. Check power source and boot ID at each boundary. For Bluetooth, wait for a stable boot and verify root request completion before diagnosing the controller. For audio, avoid full service dumps during the probe; test PCM and audible output separately. Treat this candidate as failed for stability until it survives those checks.
