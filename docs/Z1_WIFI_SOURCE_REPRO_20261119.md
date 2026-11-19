# Z1 WiFi source recovery and exact reproduction

The r9 vendor WLAN module has now been recovered to a reproducible source
state. Building `gabin8/mt6572-connsys-modules` at
`e9961becdc889266c3a8b5a488ff741159b6ad80` against the recorded kernel state
produces the deployed `wlan_gen2.ko` byte-for-byte:

```text
bytes   956276
sha256  2cf27905d382fb8ac7e6bd8e000c6db1417c4415b5718086562be11399580e9b
```

This removes the previous provenance gap. The result is not based on a
vermagic match or a similar source tree; the complete ELF file matches.

## Reproduction inputs

| Input | Identity |
|---|---|
| Radio source | `gabin8/mt6572-connsys-modules@e9961becdc889266c3a8b5a488ff741159b6ad80` |
| Source archive | SHA256 `af8bb14c434e2ef78d7367a28f2cd5a3a662adbece6b5c67d1e56f20e954613f` |
| Kernel source | commit `c6501138edf2ea6458c7c4370d2fc1033d54f50a` |
| Kernel config | SHA256 `8b0e88cd7a301d016a2f144710e044c9adb436a6ea8d97b33bf59dd7df9e7747` |
| Kernel `Module.symvers` | SHA256 `9739f305d49c41c98ffaf007622b05c4e8bc1f8e1b5e4056227d0f1187435b34` |
| Compiler | `arm-linux-gnueabihf-gcc` 13.3.0 |
| Final processing | `arm-linux-gnueabihf-strip --strip-debug` |

All six radio modules reproduce exactly:

| Module | Bytes | SHA256 |
|---|---:|---|
| `cfg80211.ko` | 493468 | `42b5acca971d720a28bd76a9345a7c6b2b85390a84b10ca0fbba91b262a352e6` |
| `mtk_btif_drv.ko` | 113540 | `8ddf37f1869df0ad4066b37f224f041500c0dee434d7f28684f4f3528b98a70a` |
| `mtk_stp_wmt_soc.ko` | 368556 | `786e9ab4d9ba0905be2b98f9d908ba9aeb196e69ebf58ec79a529fd1fc14ae30` |
| `mtk_stp_bt_soc.ko` | 12292 | `01fbd5706e9c74dc9759d3da60da5e3a5443c5c71c12c0dc1a1464495e45f63e` |
| `mtk_wmt_wifi_soc.ko` | 14016 | `d62db30a274fc472d0e819a444c1c91f57be73c60f8ce355071468a3a980a0c1` |
| `wlan_gen2.ko` | 956276 | `2cf27905d382fb8ac7e6bd8e000c6db1417c4415b5718086562be11399580e9b` |

## Recovered build details

The out-of-tree modules must be built from the radio repository root so their
relative `__FILE__` paths match the release, with the kernel source mapped to
`kernel`:

```sh
make -C "$KERNEL_SRC" O="$KERNEL_BUILD" \
  ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- \
  M="$RADIO_SRC" KBUILD_MODPOST_WARN=1 \
  KCFLAGS="-ffile-prefix-map=$KERNEL_SRC=kernel" \
  -j2 modules
```

`cfg80211` was built in-tree from `net/wireless`; its target paths use the
release's `./include/...` form:

```sh
make -C "$KERNEL_SRC" O="$KERNEL_BUILD" \
  ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- \
  KCFLAGS="-fmacro-prefix-map=$KERNEL_SRC/include=./include" \
  KBUILD_MODPOST_WARN=1 net/wireless/cfg80211.ko
```

The complete compatibility delta is archived as
`wifi_source_all_changes.patch`. It includes the historical wakeup-source
pointer conversion and the later MAC API repair.

## MAC repair

The original r9 module writes the netdev address directly in three locations.
The repaired source uses `eth_hw_addr_set()` in the probe, runtime update, and
BOW setup paths. The repaired artifact is:

```text
bytes   956328
sha256  143c5611267eb4c5e20135fffdd39db491753147ef295278c3a69330c1fbf80f
```

`readelf` shows three `dev_addr_mod` relocations. The original module has none.
This is the requested invariant-preserving API path used by current mainline.

The repaired module has no hardware verification yet. Board acceptance still
requires booting the packaged module and checking that interface up/down emits
no `Incorrect netdev->dev_addr` warning, while `wlan0` retains a valid address
and scan results remain available.

The compatibility patch applies cleanly to a fresh copy of the pinned source
archive. The existing 18 identity-tool tests pass, and all six original
rebuilt modules match their release hashes byte-for-byte.

Artifacts and manifests are under
`bridge_backups/products/products_wifi_source_repro_20261119_215457/`.
