# Change and recovery policy

- Make one focused Android 9/device change per commit.
- Keep source edits as patch files with base/result hashes in `stock_patches/manifest.json`; update validation evidence in the same focused commit.
- Never commit build outputs, raw images, userdata, serial logs, credentials, access tokens, SSH keys, or local environment files.
- Put distributable images in a versioned Release asset with SHA-256 checksums; keep Git history source-only.
- Before a device flash, record the source commit, image hashes, and partition scope.
