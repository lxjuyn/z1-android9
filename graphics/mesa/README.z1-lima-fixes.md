# Z1 Mesa 19.1.8 corrections

These patches target the verified upstream 19.1.8 archive, not the unrelated
LOS Mesa 17.0.4 tree. `prepare_mesa_lima.sh` applies the existing libdrm wrappers
and then invokes `apply_lima_patches.py`. The helper reconstructs six expected
files from the checksum-verified archive and applies both patches with zero
fuzz. It accepts only exact prepared-original or patched bytes. A rerun does
not rewrite patched files; unknown local changes are rejected before any write.

## PP stream cache miss

M8's actual gallium library has Build ID `bb34954c80dac3ede726f7dafad87433`.
PC `0x3b384` maps to `lima_draw.c:633`, inlined `lima_update_full_pp_stream`:
the hash lookup returns NULL and `entry->data` dereferences it. This establishes
the failure location; it does not establish why the cache entry is missing.

Patch 0001 preserves the surface-owned cache and its refcounts. On miss it
generates a full-framebuffer transient stream using the existing uploader path:

- `u_upload_alloc` returns a referenced resource, mapped CPU storage, and an
  aligned suballocation offset. Allocation/mapping failures are checked.
- `ctx->pp_stream.bo_offset` is the returned offset, not zero. Stream generation
  uses `bo->map + bo_offset + per-PP offset`; submission addresses use the same
  offset. A valid BO mapping is required before writing.
- `lima_submit_add_bo` takes a BO reference before the local resource reference
  is released. The uploader/resource may release its own ownership; the submit
  keeps the BO alive until `lima_submit_start` releases the recorded references
  after its ioctl, or `lima_submit_free` releases them during context destruction.
- No synthetic cache entry/refcount is inserted. Hit behavior is preserved.
- Failure marks the context errored, logs it, and refuses further draw/clear
  submission and fence publication. The existing Gallium reset-status API and
  capability expose `PIPE_UNKNOWN_CONTEXT_RESET`. This reports software errors;
  it does not add hardware reset detection or recovery. Recreate the context.

Upstream commit `8021daeb1fb58415af5d0a779368dc6617af947e` (Mesa 20.1,
MR 3834, "lima: implement PLB PP stream cache") handles misses by generating
streams and replaces surface ownership with an LRU cache. Its six-file redesign
is not directly transplanted here; a lazy insertion without its lifetime changes
would violate 19.1.8's surface destruction contract.

## Empty flush fences

Patch 0002 exports the current PP out-sync even when there is no new rendering.
The syncobj starts signaled for the genuinely empty context; every real GP/PP
ioctl now receives its actual `out_sync` handle, replacing the completion fence.
The original code omitted that assignment, so simply removing the empty-flush
return would not be sufficient.

Pending external server-sync input is merged into an empty flush's exported
fence without consuming the input needed by the next GP submit. Kernel implicit
BO synchronization and existing GP/PP dependency handling remain enabled; no
new explicit-fence flag or kernel API is added. GP/PP submission failure stops
the flush and marks the context errored. Export/merge/allocation failure closes
owned FDs and never publishes a prior or fabricated completed fence. Server-sync
merge failure also marks an error rather than losing the dependency silently.

## Reproduce static checks

```sh
python3 graphics/mesa/test_lima_patches.py \
  --archive "$LOS/.cache/z1-mesa-lima/mesa-19.1.8.tar.xz" \
  --stage /tmp/z1-lima-stage-new
python3 graphics/mesa/compile_lima_fixture.py \
  --los "$LOS" --stage /tmp/z1-lima-stage-new
bash -n scripts/prepare_mesa_lima.sh
```

The host fixture extracts the patched production functions and runs with
ASan/UBSan. It checks cache hit/miss, uploader/map errors, suballocation offset,
BO submit ownership, empty/dirty flush, pending sync preservation, failed fence
publication, FD cleanup, reset status, exact application, idempotence and unknown
edit rejection. Android compilation uses the existing ARM build rule flags with
source/object/depfile redirected to the stage. It builds four affected C files,
not an image. Neither check proves GPU completion, suspend or actual native-fence
ioctl behavior; validate SystemUI and fence signaling on the device.

## Disk budget

The selected-file fixture/stage needs under 10 MiB. The complete existing source
tree is about 109 MiB and archive about 11 MiB. A separate clean source expansion
therefore needs approximately 110 MiB beyond the archive. Existing gallium
link/debug intermediates occupy about 174 MiB; reserve at least 512 MiB free for
the subsequent incremental library rebuild. This estimate excludes a new system
image (about 900 MiB raw) and full Android build outputs.
