# Z1 offline audio factory-contract repair

Date: 2026-11-19

Status: **host verified / physical output pending**

## Finding

The Z1 audio stack was no longer blocked at the Android HAL or PCM layers.
R3 reached `RUNNING`, advanced the hardware pointer, powered the speaker DAPM
widgets, and still produced no physical sound. The later R4 rate and top-trigger
work was already compiled into module `snd-soc-mt6323.ko`, but R4 did not test
playback.

Comparison with the factory kernel exposed two remaining PMIC setup differences:

- The factory writes `AUDTOP_CON5` (`0x070a`) as `0x1114`. The fork wrote
  `0x0014`, clearing bit 12 on both DAC power-up and teardown.
- The factory stages `AUDTOP_CON6` (`0x070c`) through `0x37a2`, then `0x37e2`.
  The fork wrote only the final value.

The factory also mapped the AFE block as `0x10000` bytes, while the Z1 DTB
advertised only `0x600` bytes. The driver's regmap describes registers through
`0xffc`.

## Change

Kernel commits:

- `5409f3365` `ASoC: mt6323: restore factory playback bias sequencing`
- `2cc707b66` `ARM: dts: mt6572-z1: map the full AFE register window`

Android regression commit:

- `314fe18` `audio: preserve factory MT6323 and AFE contracts`

The codec rate table, `NEWIF` request/ack trigger, top-trigger sequencing, and
capture-clock lifetime work are unchanged.

## Evidence

The new regression failed against the old source with:

- `AUDTOP_CON5` equal to `0x0014`;
- no `AUDTOP_CON6=0x37a2` stage;
- AFE mapping size `0x600` below the regmap maximum.

After the change:

- factory contract: 3/3 pass;
- production codec rate/trigger/error harness: pass;
- DAPM capture-clock suite: 6/6 pass;
- ARM module vermagic: `7.0.0-rc7-z1+ SMP mod_unload ARMv7 p2v8`;
- generated codec object contains `0x70a <- 0x1114`, `0x70c <- 0x37a2`,
  then `0x70c <- 0x37e2`;
- rebuilt DTB decompiles to `<0x11140000 0x10000>`.

## Candidate

`out/z1_offline_audio_candidate_20261119/`

| Item | SHA256 |
|---|---|
| `boot.img` | `d0edc17e3603161de4e58a98fe0cbf3ed0bab858c9a6842863d74698517440a8` |
| new codec module | `3fa88d5aa0734612aefec8f45de1b4cabc24a86f67a383fb39f85481663f03cc` |
| appended DTB | `192ab02f27015591336e60f8a243524ce69984831b80315c9e1f9bfef9a7b7d0` |

The boot is 5,984,256 bytes, below the 6 MiB partition limit. It preserves the
R4 zImage and rootfs except for `z1_snd-soc-mt6323.ko`; `linux,initrd-end` is
`0x84290751`.

Rollback boot:
`out/adaptation_candidate_20261115_flash/boot_pkg/boot.img`
(`632d790d7fe1cf8014f4b84fe71ed8c44399f84d9c480790b5d6e8b42395410b`).

## Board acceptance

Flash the boot candidate while retaining the rollback image. After boot:

1. Confirm `/system/lib/modules/snd-soc-mt6323.ko` reports the candidate hash.
2. Run `runtime/z1_audio_factory_test.sh` as root with
   `runtime/regread-arm` and the bundled 48 kHz tone alongside it.
3. Require `AUDTOP_CON5=0x1114`, `SPK_CON0=0x3001`, AFE bits 0/1/10,
   `Speaker` and `Speaker Driver` both powered, and PCM state `RUNNING`.
4. Listen for the 440 Hz/660 Hz stereo tone.

A passing script with silence is still a physical failure. Record the register
and DAPM snapshots; the next differential should alter one analog stage at a
time. Do not mark audio complete from module load, mixer state, PCM progress, or
DAPM alone.
