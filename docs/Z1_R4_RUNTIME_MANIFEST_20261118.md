# Z1 R4 runtime manifest (2026-11-18)

The executable, read-only manifest gate is
`scripts/z1deploy/z1_r4_runtime_manifest.sh`; its expected identities are in
`scripts/z1deploy/r4_runtime_manifest.json`.

It checks the flashed R4 candidate by exact file hash and metadata, stage
provenance, loaded-module names, touch registration, the MT6323 sound card, and
the relevant build/layout policies. The file identities include the active Mesa
EGL and Gallium libraries, so a live read that returns either page-cache-corrupt
identity fails the gate:

```text
/vendor/lib/egl/libGLES_mesa.so   bb0ac77c03e79648db5dc8a480ec478be566b78c6105e9f781fd22100b46f445
/vendor/lib/libgallium_dri.so     492f0b5e871868f55023351722a609da6b65e186fbf31366a28d7faf981232b9
```

The gate also samples `libart.so`, `boot.oat`, `services.odex`, and
`SystemUI.apk`. This catches the observed `services.odex` read mismatch even
after the Gallium hash recovers. Run it before any cache eviction during a
failure. The gate writes no property, file, service, or device state.

The shipped R4 ext4 image is 943,718,400 bytes with SHA256
`7806f9153389fe681e7aada3cd8737114b1854132af792dae83ad63f3b66dc75`.
Read-only `debugfs` extraction of its thirteen system-resident manifest files
matched every expected hash. The active vendor HWC library is not baked into
the system image; it is the boot-ramdisk replacement whose source hash
`35176357…` is recorded in `boot_inputs.json` and becomes active after the
runtime staging step.

Run it after a clean post-flash boot:

```sh
sh /data/local/tmp/z1_r4_runtime_manifest.sh
```

The command exits 0 only when every identity and readiness check passes. Each
line is machine-readable:

```text
[r4manifest] BEGIN schema=z1-r4-runtime/v1 candidate=r4-20261115 read_only=1
[r4manifest] check=/system/lib/modules/z1_cst836u.ko status=PASS expected=03b0...
[r4manifest] summary pass=40 fail=0 status=PASS
[r4manifest] END
```

The 2026-11-18 serial capture is retained as useful bring-up evidence, but it
is not accepted as post-flash runtime proof. It started at 22:46:01 and ended at
22:57:46 while the android partition write/readback was still running until
22:51:08. The capture also ended before `sys.boot_completed=1` was observed.
The package pair itself is readback-verified; a clean boot after the write must
still produce a passing manifest.

Host tests:

```sh
cd z1a9_gpt
python3 -m unittest -v scripts/z1deploy/test_z1_r4_runtime_manifest.py
```
