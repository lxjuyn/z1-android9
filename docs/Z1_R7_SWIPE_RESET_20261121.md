# R7 Priority R2 swipe-associated resets

All times below are per-boot kernel uptime. UART source: `mainline_recon/z1_boot_mainline_20261121_003416.log`.

| Run | Evidence |
| --- | --- |
| UART-requested reboot, for comparison | `sys.powerctl=reboot,shell` at line 11025, clean unmount at 11161, restart at 11165; next Preloader RGU STA `40000000` at 11211. |
| Physical swipe 1 | Complete touchscreen samples 13804–13812, ending at 225.212 s. Preloader at 13845, RGU STA `0` at 13860. |
| Physical swipe 2 | Complete samples 16095–16105, ending at 241.520 s. Preloader at 16138, RGU STA `0` at 16153. |
| Later physical swipe | This boot reached `sys.boot_completed=1` at 210.168 s (22200) and remained up until a swipe ended at 1051.178 s (23982). Backlight and app RenderThread activity followed (23986–23989), then Preloader at 24022, RGU STA `0` at 24037. |

These logs reproduce a swipe-associated SoC reset, including after more than 14 minutes in Android. They do not establish whether input handling, display load, a shared driver, board power, or another concurrent activity caused it. In particular, the earlier boot had an abrupt reset without a captured swipe, so the gesture is not established as necessary. No orderly shutdown or panic is visible at these transitions. The pstore listing after the first spontaneous reset was empty (15835–15838). Capture ended on the host at 01:34:16; that final timeout does not establish when the device powered off.

Bluetooth failed separately: WMT waited for the function-on reply and timed out (22314–22319), then `BT_open` failed and combo recovery also tore Wi-Fi down (22322–22338). This run passed device-node ownership and reached the driver. Speaker controls were set successfully (22245–22254), but the log does not identify the reported two beeps or prove normal media playback.

The R4 captured Mesa page at file offset `0xb07000` is exactly the 4096 bytes at offset `28` of the archived `ROMv1_patch_1_hdr.bin` (SHA256 `b445f3264e90b585e1113114b7e687c2d723bc9cf8295a16d591f5dfa8860aa2`). This independently verified match points at structured wrong data and prioritizes the WMT firmware and DMA paths for isolation. It does not establish which component wrote the bad page or link it to every reset.

After preserving `/data`, first compare an untouched idle baseline with a single UART-injected swipe on the same screen. If it reproduces, a physical touchscreen operation is unnecessary; separate content redraw without input from dispatcher activity next. If only physical swipes reproduce, repeat at comparable coordinates and use an evdev grab to separate touch-controller activity from Android processing. Run Bluetooth only after that baseline and test one enable from a clean boot with timestamped WMT/STP/BTIF logs. Keep UI, Bluetooth, and repeatable speaker playback as separate tests.
