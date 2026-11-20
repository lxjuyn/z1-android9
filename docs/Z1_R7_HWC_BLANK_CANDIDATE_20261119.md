# Z1 R7 lock screen candidate

Use `out/r7_lockscreen_restart_20261120/boot.img` with the R7
`out/r7_combined_candidate_20261119/system.img`. The boot image is 5,984,256
bytes and its SHA256 is
`a7e5b0bbc33f13a35c676f84f1a103a9a7d3b5e0e77c65e8f6f25108305115e7`.
The paired system SHA256 is
`8060c851075bb053e931376e7965c3c80850a2f5e9d73cdd62e54add83400bf4`.
The previous `out/r7_hwc_blank_candidate_20261119/boot.img` remains the
immediate rollback boot.

Run the preflight from the workspace root:

```sh
bash z1a9_gpt/scripts/z1deploy/z1_flash_r7.sh --hwc-blank
```

`--execute` selects the write operation. Preserve the R4 storage evidence
before replacing its system partition. The preflight verifies the selected
boot, the paired system and the previous lock-screen rollback boot.

The HWC uses the MT6323 `display_blank` gate before falling back to
brightness. It now clears that kernel gate on every `POWER_MODE_ON`, not only
when the current HWC instance observed lock. A SurfaceFlinger/HWC restart
while the display is blanked therefore cannot leave the panel permanently
dark. All 17 host power cases pass, including this restart case and lock/wake
with unreadable brightness files. The ARM module passed the build ABI checks.
Boot readback found a single ramdisk payload change, `hwcomposer.z1.so` r19,
with SHA256
`5e1294dea240ebb31a9623424ccdb27c78617dc6b929dd4b2c85096422e27ad5`.
The kernel matches R7 byte for byte and the DT diff consists of the ramdisk
end address (`0x84290d5f`). The system passed the R7 offline filesystem check.

On the board, compare `getprop z1stage.hwcomposer.sha` and
`sha256sum /vendor/lib/hw/hwcomposer.z1.so` to the new HWC hash, and require
`r19` in the `z1hwc2` version log. The original R7 runtime manifest pins the
original HWC hash, so its HWC checks will report that difference on this
candidate. During repeated lock and wake cycles,
confirm the panel physically goes dark, the current frame appears after wake,
and `display_blank` reads `0`, `1`, `0` across the transition.

R7 audio has passed ARM linker and HAL negotiation tests. The speaker and
microphone remain board tests. Bluetooth still needs controller verification
with `scripts/btgps/bt/HCI_PROBE.md`.
