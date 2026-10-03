#ifndef Z1_PIXEL_COPY_H
#define Z1_PIXEL_COPY_H

#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>

// Keep layout arithmetic identical in allocator and the host regression test.
static inline int z1_buffer_layout(int width, int height, unsigned bpp,
        size_t* stride, size_t* size)
{
    if (!stride || !size || width <= 0 || height <= 0 || !bpp)
        return -EINVAL;
    const uint64_t columns = (uint64_t(width) + 1) & ~uint64_t(1);
    const uint64_t rows = (uint64_t(height) + 1) & ~uint64_t(1);
    const uint64_t limit = uint64_t(INT_MAX) - 4096;
    if (columns > limit / bpp || rows > (limit - 4) / (columns * bpp))
        return -EINVAL;
    *stride = size_t(columns);
    *size = size_t(columns * rows * bpp + 4);
    return 0;
}

// Android HAL pixel format wire values (system/graphics.h).
// FP16 and RAW16 allocations remain possible, but are not display formats.
static inline int z1_copy_rgb565(void* destination, size_t dst_size,
        size_t dst_stride, const void* source, size_t src_size,
        int src_width, int src_height, int src_stride_pixels, int format,
        unsigned width, unsigned height)
{
    unsigned bpp;
    switch (format) {
        case 1: case 2: case 5: bpp = 4; break; // RGBA, RGBX, BGRA
        case 3: bpp = 3; break; // RGB888
        case 4: bpp = 2; break; // RGB565
        default: return -EINVAL;
    }
    if (!destination || !source || !width || !height ||
            src_width <= 0 || src_height <= 0 ||
            unsigned(src_width) < width || unsigned(src_height) < height ||
            src_stride_pixels < src_width)
        return -EINVAL;
    const uint64_t src_stride = uint64_t(src_stride_pixels) * bpp;
    const uint64_t row_bytes = uint64_t(width) * 2;
    // Validate complete last visible row before writing any destination byte.
    if (dst_stride < row_bytes ||
            uint64_t(dst_stride) > UINT64_MAX / height ||
            uint64_t(dst_stride) * (height - 1) + row_bytes > dst_size ||
            src_stride * (height - 1) + uint64_t(width) * bpp > src_size)
        return -EINVAL;
    const uint8_t* src = static_cast<const uint8_t*>(source);
    uint8_t* dst = static_cast<uint8_t*>(destination);
    for (unsigned y = 0; y < height; ++y) {
        const uint8_t* in = src + size_t(y * src_stride);
        uint8_t* out = dst + y * dst_stride;
        if (format == 4) {
            memcpy(out, in, size_t(row_bytes));
            continue;
        }
        for (unsigned x = 0; x < width; ++x) {
            const unsigned r = in[format == 5 ? 2 : 0];
            const unsigned g = in[1];
            const unsigned b = in[format == 5 ? 0 : 2];
            const uint16_t pixel = uint16_t(((r >> 3) << 11) |
                                          ((g >> 2) << 5) | (b >> 3));
            // Z1 is little endian; byte writes support unaligned imports.
            out[2 * x] = uint8_t(pixel);
            out[2 * x + 1] = uint8_t(pixel >> 8);
            in += bpp;
        }
    }
    return 0;
}
// Copy into the verified native framebuffer format, using byte accesses on ARM.
static inline int z1_copy_pixels(void* destination, size_t dst_size,
        size_t dst_stride, int dst_format, const void* source, size_t src_size,
        int src_width, int src_height, int src_stride_pixels, int src_format,
        unsigned width, unsigned height)
{
    if (dst_format == 4)
        return z1_copy_rgb565(destination, dst_size, dst_stride, source, src_size,
                src_width, src_height, src_stride_pixels, src_format, width, height);
    if (dst_format != 1 && dst_format != 2 && dst_format != 5) return -EINVAL;
    unsigned bpp;
    switch (src_format) {
        case 1: case 2: case 5: bpp = 4; break;
        case 3: bpp = 3; break;
        case 4: bpp = 2; break;
        default: return -EINVAL;
    }
    if (!destination || !source || !width || !height ||
            src_width <= 0 || src_height <= 0 ||
            unsigned(src_width) < width || unsigned(src_height) < height ||
            src_stride_pixels < src_width) return -EINVAL;
    const uint64_t src_stride = uint64_t(src_stride_pixels) * bpp;
    const uint64_t row_bytes = uint64_t(width) * 4;
    if (dst_stride < row_bytes || uint64_t(dst_stride) > UINT64_MAX / height ||
            uint64_t(dst_stride) * (height - 1) + row_bytes > dst_size ||
            src_stride * (height - 1) + uint64_t(width) * bpp > src_size)
        return -EINVAL;
    const uint8_t* src = static_cast<const uint8_t*>(source);
    uint8_t* dst = static_cast<uint8_t*>(destination);
    for (unsigned y = 0; y < height; ++y) {
        const uint8_t* in = src + size_t(y * src_stride);
        uint8_t* out = dst + y * dst_stride;
        for (unsigned x = 0; x < width; ++x, in += bpp, out += 4) {
            unsigned r, g, b, a = 255;
            if (src_format == 4) {
                const unsigned pixel = unsigned(in[0]) | (unsigned(in[1]) << 8);
                r = (pixel >> 11) & 31; r = (r << 3) | (r >> 2);
                g = (pixel >> 5) & 63; g = (g << 2) | (g >> 4);
                b = pixel & 31; b = (b << 3) | (b >> 2);
            } else {
                r = in[src_format == 5 ? 2 : 0]; g = in[1];
                b = in[src_format == 5 ? 0 : 2];
                if (src_format == 1 || src_format == 5) a = in[3];
            }
            out[dst_format == 5 ? 2 : 0] = uint8_t(r);
            out[1] = uint8_t(g);
            out[dst_format == 5 ? 0 : 2] = uint8_t(b);
            out[3] = uint8_t(dst_format == 2 ? 255 : a);
        }
    }
    return 0;
}
#endif
