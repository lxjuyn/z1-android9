# Z1 Android 9 input key layout

The stock-kernel product installs the repository's `keylayout/mtk-kpd.kl`,
adapted from the Z1 factory keypad map. Existing Z1 boot logs register the
keypad as `mtk-kpd` and the touchscreen as `mtk-tpd`, matching the keypad map
name.

The A9 map keeps the factory Back (158) and Home (102) keys, maps the factory
Menu event (139) to Recent Apps for Pie, and retains the direction pad,
center/enter, volume, power and media controls. The factory-only `READ_T1` to
`READ_T6` events (250–255) are exposed as standard Android `F1` to `F6`, so
apps can receive them without depending on the factory framework's private
key labels.

The factory `/system/usr/keylayout/virtual-touch-keys.kl` is empty. The FuQuan
ROM is useful as a reference for keeping per-device key maps separate, but its
virtual-key file and event-code assignments are not included here. The empty
factory file alone does not establish which Linux input device emits the
three bottom-key events.

## Runtime evidence still needed

Kernel registration is present in existing logs, but captured key-down events
are not. On the Z1, run `getevent -lt` while pressing each bottom and physical
key to confirm the actual event codes. Then check Back, Home, Recent Apps,
direction/confirm, volume, playback and F1–F6 behavior with `qemu.hw.mainkeys=1`
(the software navigation bar is hidden). The map has factory-keylayout
provenance but still needs a board-level functional check.
