# r4 BROM DRAM input recovery (2026-11-18)

The r4 pair is unchanged: boot SHA256 `632d790d7fe1cf8014f4b84fe71ed8c44399f84d9c480790b5d6e8b42395410b`, system SHA256 `7806f9153389fe681e7aada3cd8737114b1854132af792dae83ad63f3b66dc75`.

Cold BROM initialization failed before partition I/O. The November 18 21:57 attempt could not dump a preloader from RAM, received an all-zero DRAM identifier, and automatically selected `preloader_lava6737m_op60_z60g.bin`. Subsequent attempts returned `Please disconnect, start mtkclient and reconnect.` No successful write/readback was recorded. These failures must not be reported as completed flashing.

Use the retained Z1 factory file `1/preloader_j72_z1.bin` as the explicit DRAM configuration input. It is 99,844 bytes, SHA256 `c917f1d2efd3d573d10c28777c24aab33de21b2b925e9367f757a616db578e92`. Independent offline calls to this installation's `DAconfig.m_extract_emi` return version 11, 1,008 bytes, EMI SHA256 `dac6c53dfd55f895d4a86c32bf820a7028dffe6d7d4e3f8e01bc3fe9c7188eca`; the EMI contains the board's observed flash identifier. No header conversion is needed.

`z1_chunked_flash.py` now optionally accepts these paired environment variables:

```sh
export Z1_MTK_PRELOADER=/home/lxj/claude/6572/1/preloader_j72_z1.bin
export Z1_MTK_PRELOADER_SHA256=c917f1d2efd3d573d10c28777c24aab33de21b2b925e9367f757a616db578e92
```

The backend validates the supplied file, snapshots those same bytes into its log directory, records provenance in `manifest.json`, and appends `--preloader <snapshot>` after the positional arguments of every `wo` and `ro` invocation. Missing pins, bad hashes, and changed snapshots fail before the next MTK process starts. The option supplies EMI to the download agent; the partition and byte-range arguments remain bootimg/android. Do not run any preloader write command.

Validation: all 21 chunk-transfer tests pass, including both new tests for pinned input propagation through write/readback and rejection before device access. Both legacy CLI parsers accept the option in this position. A primary and independent reviewer reproduced the EMI extraction without accessing the device. Runtime DRAM initialization and the full boot/system byte readback remain pending.

Successful recovery still requires accepted DRAM initialization, a working DA connection, and byte-identical readback of boot and all 19 system chunks. `OK (0x0)` in the previous failed log was an unexpected response where the code required `0xBC4`; it was not proof of success.
