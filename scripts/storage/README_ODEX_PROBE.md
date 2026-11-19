# Z1 services.odex direct-read probe

The R7 system installs:

- `/system/bin/z1_odex_direct` - 32-bit ARM static helper that reads the five
  known ext4 blocks through `O_DIRECT`;
- `/system/bin/z1_odex_probe` - root wrapper that records the cached hash,
  direct-read hash, process identities and filtered ext4 errors.

Run before dropping caches:

```sh
/system/bin/z1_odex_probe
```

The direct sample is expected to hash to
`60bfb8d0911b1f9a3be899507f4e6cf2e36c44fae4d5413221eacbf77279a49a`.
`DIRECT_MATCH_IMAGE` with a different cached full-file hash demonstrates that
the installed image is sound but the page cache contains corrupted bytes.
A direct mismatch requires re-reading the partition outside Android and does
not, by itself, prove NAND wear or filesystem corruption.

The helper uses the verified R7 block map for file pages 1212, 2201, 2206,
2207 and 2208. Rebuild the helper if those ext4 block assignments change.
