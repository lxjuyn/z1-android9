# Z1 R5 offline candidate, November 19, 2026

The R5 images passed host checks. The existing R4 pair remains the device baseline. Its boot and system were read back after flashing. The next storage experiment uses the earlier R4 HSQ pair and the installed R4 system, as described in [the framework readback notes](Z1_R4_FRAMEWORK_READBACK_20261119.md). The R5 package is for subsequent validation of the recovery, audio and backlight changes.

The local package is in `out/r5_offline_candidate_20261119/`. A copy with the build log, kernel config and zImage is in `bridge_backups/products/products_r5_candidate_20261119_205334/`.

| Artifact | Bytes | SHA256 |
|---|---:|---|
| `boot_hsq_control_r5.img` | 5,984,256 | `b175077f8ce3b2322335f4b10f64f0186281242a00cb17dd267cf16d432168b1` |
| `boot_no_hsq_r5.img` | 5,984,256 | `33cab90475df68eb1c3f4d1a87911f50b417712e8c625fe7c3816c47e0017419` |
| `system.img` | 943,718,400 | `a7c3a16ab5a3a4a8c519e5bd5f97a37be26f55f52d5e4baabf922ee898e98a59` |

`boot_pkg/boot.img` has the same hash as the R5 HSQ control. The two R5 boot images use the same kernel hash `b6b5174e8f394a70e8bde22bf2ff472a60aeb500d31ed1088090a0abb654c63e`. The decompiled device trees differ only by `mtk_sd.z1_disable_emmc_hsq=1` in `chosen.bootargs`. The boot package checksum list passes. The system build record shows a clean offline ext4 check before and after replacing `/lib/modules/snd-soc-mt6323.ko`.

The recorded kernel input matches the current local zImage from kernel `main` at `0fade62816`. That source includes `b09fdd195` for HSQ recovery ordering, `5409f3365` for codec bias, and `2cc707b66` for the AFE window. The packaged HWC hash matches the local HWC build associated with `46dfc30`. These associations use the local build outputs and package manifests. The build logs do not embed a source commit, so the artifact hashes remain the identity checks.

The R5 boot ramdisk and the R5 system carry the same new codec module, hash `3fa88d5aa0734612aefec8f45de1b4cabc24a86f67a383fb39f85481663f03cc`. A boot-only R5 trial with the R4 system therefore cannot establish which codec is active. For audio validation, confirm the loaded module identity and test speaker output and recording. For display validation, confirm the active composer hash, then check physical backlight off and on over lock and wake. Verify the R4 runtime manifest against R4 artifacts and the R5 identities against the R5 manifests. R5 device behavior remains unverified.
