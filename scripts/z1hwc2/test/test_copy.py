#!/usr/bin/env python3
"""Fault-inject the production private scanout copy; no device access."""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess

from test_fence import function


MOCK = r'''
#include <algorithm>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>
#include <sys/mman.h>
#include <linux/dma-buf.h>
#include <sys/ioctl.h>
using buffer_handle_t = const void *;
struct z1_hwc2 {};
/* TYPES */
#define CHECK(x) do { if (!(x)) throw std::runtime_error(std::string(__func__) + ": " #x); } while (0)
static std::vector<uint8_t> source, target;
static std::vector<int> sync_errors;
static std::vector<uint64_t> sync_flags;
static unsigned sync_index;
static int allocations, maps, unmaps, allocation_error, map_error, bad_destination;
static int hal_bpp(int fmt) { return fmt == 1 ? 4 : 0; }
static uint32_t hal_to_drm(int fmt) { return fmt == 1 ? 1 : 0; }
static int dumb_ensure(z1_hwc2 *, z1_dumb *d, uint32_t w, uint32_t h,
                       uint32_t fourcc, int) {
    ++allocations;
    if (allocation_error) return -ENOMEM;
    d->pitch = w * 4; d->w = w; d->h = h; d->fourcc = fourcc;
    d->size = size_t(d->pitch) * h; d->fb_id = 71;
    target.assign(d->size + 32, 0xcc); d->map = target.data() + 16;
    if (bad_destination == 1) d->pitch = 1;
    if (bad_destination == 2) d->size = 1;
    if (bad_destination == 3) d->map = nullptr;
    return 0;
}
static void *mock_mmap(void *, size_t length, int protection, int flags, int, off_t offset) {
    ++maps; CHECK(protection == PROT_READ); CHECK(flags == MAP_SHARED); CHECK(offset == 0);
    if (map_error) { errno = map_error; return MAP_FAILED; }
    CHECK(length <= source.size()); return source.data();
}
static int mock_munmap(void *address, size_t length) {
    ++unmaps; CHECK(address == source.data()); CHECK(length <= source.size()); return 0;
}
static int mock_ioctl(int, unsigned long command, dma_buf_sync *sync) {
    CHECK(command == DMA_BUF_IOCTL_SYNC); sync_flags.push_back(sync->flags);
    int error = sync_index < sync_errors.size() ? sync_errors[sync_index++] : 0;
    if (error) { errno = error; return -1; } return 0;
}
#define mmap mock_mmap
#define munmap mock_munmap
#define ioctl mock_ioctl
/* FUNCTION */
#undef mmap
#undef munmap
#undef ioctl
static z1_handle valid() {
    z1_handle h{}; h.version = 12; h.numFds = 1; h.numInts = 12;
    h.fd = 81; h.magic = Z1_HANDLE_MAGIC; h.width = 16; h.height = 2;
    h.stride = 32; h.format = 1; h.offset = 16; h.size = 208;
    return h;
}
static void reset() {
    source.resize(65536); for (size_t i = 0; i < source.size(); ++i) source[i] = (i * 17 + 3) & 255;
    target.clear(); sync_errors.clear(); sync_flags.clear(); sync_index = 0;
    allocations = maps = unmaps = allocation_error = map_error = bad_destination = 0;
}
static void check_pixels(const z1_handle &h, const z1_dumb &d) {
    for (size_t y = 0; y < d.h; ++y)
        for (size_t x = 0; x < d.pitch; ++x) {
            uint8_t expected = y < unsigned(h.height) && x < unsigned(h.width * 4) ?
                source[h.offset + y * h.stride * 4 + x] : 0xcc;
            CHECK(target[16 + y * d.pitch + x] == expected);
        }
    for (size_t i = 0; i < 16; ++i) CHECK(target[i] == 0xcc && target[target.size() - 1 - i] == 0xcc);
}
static void invalid_handles() {
    z1_hwc2 hw; z1_dumb d{};
    CHECK(copy_into_dumb(&hw, &d, nullptr) == -EINVAL);
    for (int field = 0; field < 19; ++field) {
        auto h = valid();
        switch (field) {
        case 0: h.numFds = 0; break; case 1: h.numFds = 2; break;
        case 2: h.numInts = 11; break; case 3: h.magic = 0; break;
        case 4: h.fd = -1; break; case 5: h.width = 0; break;
        case 6: h.width = -1; break; case 7: h.width = 4097; break;
        case 8: h.height = 0; break; case 9: h.height = -1; break;
        case 10: h.height = 4097; break; case 11: h.stride = 15; break;
        case 12: h.stride = 8193; break; case 13: h.offset = -1; break;
        case 14: h.size = 0; break; case 15: h.size = 207; break;
        case 16: h.format = 99; break; case 17: h.offset = 2147483647; break;
        case 18: h.stride = 2147483647; break;
        }
        CHECK(copy_into_dumb(&hw, &d, &h) == -EINVAL);
    }
    CHECK(allocations == 0); CHECK(maps == 0); CHECK(sync_flags.empty());
}
static void exact_last_row_and_padding() {
    z1_hwc2 hw; z1_dumb d{}; auto h = valid();
    // The file contains only actual pixels in the final row, not final stride padding.
    CHECK(copy_into_dumb(&hw, &d, &h) == 0); CHECK(d.w == 64 && d.h == 64);
    CHECK(sync_flags == (std::vector<uint64_t>{DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ,
                                             DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ}));
    CHECK(unmaps == 1); check_pixels(h, d);
}
static void tight_pitch_with_offset() {
    z1_hwc2 hw; z1_dumb d{}; auto h = valid();
    h.width = h.stride = 64;
    h.height = 3;
    h.size = h.offset + h.width * h.height * 4;
    CHECK(copy_into_dumb(&hw, &d, &h) == 0);
    CHECK(d.pitch == unsigned(h.width * 4));
    CHECK(sync_flags == (std::vector<uint64_t>{DMA_BUF_SYNC_START | DMA_BUF_SYNC_READ,
                                             DMA_BUF_SYNC_END | DMA_BUF_SYNC_READ}));
    CHECK(unmaps == 1); check_pixels(h, d);
}
static void ashmem_without_dma_ioctl() {
    z1_hwc2 hw; z1_dumb d{}; auto h = valid(); sync_errors = {ENOTTY};
    CHECK(copy_into_dumb(&hw, &d, &h) == 0); CHECK(sync_flags.size() == 1); check_pixels(h, d);
}
static void dma_start_failure() {
    z1_hwc2 hw; z1_dumb d{}; auto h = valid(); sync_errors = {EIO};
    CHECK(copy_into_dumb(&hw, &d, &h) == -EIO); CHECK(unmaps == 1);
    CHECK(std::all_of(target.begin(), target.end(), [](uint8_t b){ return b == 0xcc; }));
}
static void dma_end_failure() {
    z1_hwc2 hw; z1_dumb d{}; auto h = valid(); sync_errors = {0, EIO};
    CHECK(copy_into_dumb(&hw, &d, &h) == -EIO); CHECK(unmaps == 1);
}
static void dma_interrupts_retry() {
    z1_hwc2 hw; z1_dumb d{}; auto h = valid(); sync_errors = {EINTR, 0, EINTR, 0};
    CHECK(copy_into_dumb(&hw, &d, &h) == 0); CHECK(sync_flags.size() == 4); check_pixels(h, d);
}
static void allocate_failure() {
    z1_hwc2 hw; z1_dumb d{}; auto h = valid(); allocation_error = 1;
    CHECK(copy_into_dumb(&hw, &d, &h) == -ENOMEM); CHECK(maps == 0);
}
static void mapping_failure() {
    z1_hwc2 hw; z1_dumb d{}; auto h = valid(); map_error = EACCES;
    CHECK(copy_into_dumb(&hw, &d, &h) == -EACCES); CHECK(unmaps == 0); CHECK(sync_flags.empty());
}
static void invalid_destination() {
    z1_hwc2 hw; auto h = valid();
    for (bad_destination = 1; bad_destination <= 3; ++bad_destination) {
        z1_dumb d{}; CHECK(copy_into_dumb(&hw, &d, &h) == -EINVAL);
    }
    CHECK(maps == 0);
}
int main() {
    struct Test { const char *name; void (*fn)(); };
#define CASE(x) {#x, x}
    Test tests[] = {CASE(invalid_handles), CASE(exact_last_row_and_padding), CASE(tight_pitch_with_offset),
        CASE(ashmem_without_dma_ioctl), CASE(dma_start_failure), CASE(dma_end_failure),
        CASE(dma_interrupts_retry), CASE(allocate_failure), CASE(mapping_failure), CASE(invalid_destination)};
    int failures = 0;
    for (auto &test : tests) { reset(); try { test.fn(); printf("PASS %s\n", test.name); }
        catch (const std::exception &e) { ++failures; printf("FAIL %s: %s\n", test.name, e.what()); } }
    printf("%zu scenarios, %d failed\n", sizeof(tests)/sizeof(tests[0]), failures); return !!failures;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1] / 'z1hwc2.cpp')
    parser.add_argument('--out-dir', required=True, type=Path)
    args = parser.parse_args()
    src = args.source.read_text()
    types = [re.search(r'^#define Z1_HANDLE_MAGIC\s+[^\n]+', src, re.M).group(0)]
    for name in ('z1_handle', 'z1_dumb'):
        types.append(re.search(r'^struct ' + name + r'\b[^{}]*\{.*?^\};', src, re.M | re.S).group(0))
    generated = MOCK.replace('/* TYPES */', '\n'.join(types)).replace('/* FUNCTION */', function(src, 'copy_into_dumb'))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    cpp, binary = args.out_dir / 'copy.cpp', args.out_dir / 'copy'
    cpp.write_text(generated)
    (args.out_dir / 'source.sha256').write_text(hashlib.sha256(src.encode()).hexdigest() + '\n')
    command = ['g++', '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
               '-fsanitize=address,undefined', '-fno-omit-frame-pointer', str(cpp), '-o', str(binary)]
    built = subprocess.run(command, capture_output=True, text=True)
    (args.out_dir / 'compile.log').write_text(built.stdout + built.stderr)
    if built.returncode:
        print(built.stdout + built.stderr)
        return built.returncode
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
    (args.out_dir / 'test.log').write_text(result.stdout + result.stderr)
    print(result.stdout + result.stderr, end='')
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
