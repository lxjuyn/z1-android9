# ART/OAT host path patches

`manifest.json` records the exact Android 9 source-file hashes and the owned patch series. Run
`python3 scripts/apply_art_patches.py --source /path/to/lineage16` to apply it, or add `--check` to
verify that every result hash is present without changing the source checkout. The patch applier is
also invoked by `scripts/build_z1.sh` before the Android build.

The ART change preserves the multi-image `bootclasspath` key and strips the host directory from
each sibling image name. It suppresses dex2oat invocation strings and, when
`--avoid-storing-host-paths` is used, omits the app-ODEX `image-location` diagnostic key. Z1's
product config passes both options to boot-image dexpreopt; the matching make rule passes them to
ordinary ODEX dexpreopt. These options do not verify runtime boot behavior.
