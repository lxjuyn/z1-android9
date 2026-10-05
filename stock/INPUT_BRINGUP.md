# Z1 Android 9 input key layout

The stock-kernel product installs separate layouts for the two Z1 input
devices: `keylayout/mtk-kpd.kl` for the keypad and `keylayout/mtk-tpd.kl` for
the touchscreen controller. Existing Z1 boot logs register them as `mtk-kpd`
and `mtk-tpd`.

The keypad map keeps the factory Back (158) and Home (102) keys, maps its
factory Menu event (139) to Recent Apps for Pie, and retains the direction
pad, center/enter, volume, power and media controls. The factory-only
`READ_T1` to `READ_T6` events (250–255) are exposed as standard Android `F1`
to `F6`. The touchscreen map separately maps capacitive navigation codes
158/102/139 to Back/Home/Recent Apps. This prevents Android's generic fallback
map from treating code 102 as `MOVE_HOME` and code 139 as Menu.

The original factory system image contains `mtk-kpd.kl` (158=Back, 102=Home,
139=Menu, 250–255=READ_T1–READ_T6), but contains neither
`virtual-touch-keys.kl` nor `mtk-tpd.kl`. The bottom-touch map is therefore a
candidate mapping for the registered `mtk-tpd` device, not an original
factory mapping. The FuQuan ROM is used only as a reference for separating
per-device maps; its keylayout files and event assignments are not copied.
Android's software navigation bar is hidden with `qemu.hw.mainkeys=1`.

## Runtime evidence still needed

Kernel registration is present in existing logs, but captured key-down events
are not. On the Z1, run `getevent -lt` while pressing each bottom and physical
key to confirm which device emits each code. Then check Back, Home, Recent
Apps, direction/confirm, volume, playback and F1–F6 behavior. Only the
physical keypad codes have factory-layout evidence; both maps still need a
board-level functional check.
