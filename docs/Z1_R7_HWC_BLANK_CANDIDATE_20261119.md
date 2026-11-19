# Z1 R7 lock screen candidate

Use `out/r7_hwc_blank_candidate_20261119/boot.img` with the R7
`out/r7_combined_candidate_20261119/system.img`. The boot image is 5,984,256
bytes and its SHA256 is
`bb6b1b6711036146607018dec27d04538bbe54e0d5696f72b0f110ba31cbebed`.
The paired system SHA256 is
`8060c851075bb053e931376e7965c3c80850a2f5e9d73cdd62e54add83400bf4`.

Run the preflight from the workspace root:

```sh
bash z1a9_gpt/scripts/z1deploy/z1_flash_r7.sh --hwc-blank
```

`--execute` selects the write operation. Preserve the R4 storage evidence
before replacing its system partition. The preflight verifies the selected
boot, the paired system and the original R7 rollback boot.

The HWC now tries the MT6323 `display_blank` gate before reading brightness.
The brightness fallback is still available when that gate is absent. All 16
host power cases pass, including lock and wake with unreadable brightness
files. The ARM module passed the build ABI checks. Boot readback found a
single ramdisk payload change, `hwcomposer.z1.so`, with SHA256
`5f53153a9971d35fb8037ae4b245942058f372fa0fa055caeaff195e5d7bca9c`.
The kernel matches R7 byte for byte and the DT diff consists of the ramdisk
end address. The system passed the R7 offline filesystem check.

On the board, compare `getprop z1stage.hwcomposer.sha` and `sha256sum
/vendor/lib/hw/hwcomposer.z1.so` to the new HWC hash. The original R7
runtime manifest pins the original HWC hash, so its HWC checks will report
that difference on this candidate. During repeated lock and wake cycles,
confirm the panel physically goes dark, the current frame appears after wake,
and `display_blank` reads `0`, `1`, `0` across the transition.

R7 audio has passed ARM linker and HAL negotiation tests. The speaker and
microphone remain board tests. Bluetooth still needs controller verification
with `scripts/btgps/bt/HCI_PROBE.md`.
