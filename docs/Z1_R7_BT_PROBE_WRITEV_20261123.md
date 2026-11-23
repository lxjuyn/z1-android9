# Z1 R7 Bluetooth H4 writev probe

Status: `HOST_VERIFIED_NOT_DEVICE_TESTED`.

Commit `d290519` added `--writev` to the standalone Z1 Bluetooth controller
probe, matching Android 9's two-iovec H4 send path. The build test and all
packaged probes still described the older write-only binary, so the new source
had no reproducible artifact that could be used in the next board comparison.

Two clean builds with the pinned Android NDK r26d produced the same executable:

| File | Bytes | SHA256 |
| --- | ---: | --- |
| `z1_bt_hci_probe` | 9,136 | `5f9ef202b68e8b77924435c17ec54aa0a19399d28b523b4e02e69203591c9f36` |

The source is
`scripts/btgps/bt/z1_bt_hci_probe.c`, SHA256
`69c4b097c35334da05df1605239c7038e08f6b156e2eb514b8552d102cb51a7`.
The materialized build is `out/r7_bt_probe_writev_20261123_r2/`; the archived
copy is
`bridge_backups/products/products_r7_bt_probe_writev_final_20261123_145313/`.
The build's `SHA256SUMS` readback passes.

Existing packages that pin probe SHA `b0f783c0...` contain the old binary and
cannot test the vectored path. Their manifests remain historical package
identities and must not be silently rewritten to the new hash.

## Next board gate

Use a radio-on boot that is otherwise stable, record its boot and system
identities, and verify the mounted `libbt-vendor.so` and STP modules before
opening Bluetooth. Stop Android Bluetooth and confirm no other process owns
`/dev/stpbt`.

Run the two transport modes from equivalent cold boots:

```sh
/data/local/tmp/z1_bt_hci_probe --exclusive-controller
/data/local/tmp/z1_bt_hci_probe --exclusive-controller --writev
```

Capture WMT, STP/BTIF, HCI Reset and Read Local Version results for both.
`--features` is a follow-up diagnostic, not part of this two-mode transport
comparison. A successful reply in either mode proves only that command/event
transport works; framework enable, discovery, pairing, reconnect, Wi-Fi
coexistence, SCO and A2DP remain separate gates.
