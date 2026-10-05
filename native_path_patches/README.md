# Native `__FILE__` path cleanup

The pinned Android tree uses Clang 6.0.2, which does not accept
`-fmacro-prefix-map`. These source-generator patches instead give generated C++
files stable logical names through `#line` directives:

- Protobuf C++ output uses the repository-relative `.proto` name.
- libchrome's wrapped headers use a relative `external/libchrome/...` name.

That keeps `__FILE__` diagnostics useful while preventing the compiler's
absolute checkout path from entering native ELF `.rodata` for these two
generators. Other build inputs may still embed local paths and must be scanned
in the finished system image before release. The patch applier checks the exact
input and result SHA-256 for each source file before applying or accepting the
patches.

Run `scripts/apply_native_path_patches.py --source "$LOS_ROOT"` before building.
Run it with `--check` to verify the source tree has the expected patched state.
