#include "../capacity_policy.h"

#include <array>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <linux/fb.h>
#include <sys/ioctl.h>
#include <sys/mman.h>

namespace {
constexpr char kCapacity[] = "/sys/class/power_supply/battery/capacity";
constexpr char kFramebuffer[] = "/dev/graphics/fb0";
constexpr char kFallbackFramebuffer[] = "/dev/fb0";
constexpr int kUnknown = -1;
volatile std::sig_atomic_t stopping;

struct Glyph {
    char character;
    std::array<uint8_t, 7> rows;
};

constexpr Glyph kGlyphs[] = {
    {'0', {14, 17, 17, 17, 17, 17, 14}},
    {'1', {4, 12, 4, 4, 4, 4, 14}},
    {'2', {14, 17, 1, 2, 4, 8, 31}},
    {'3', {30, 1, 1, 14, 1, 1, 30}},
    {'4', {2, 6, 10, 18, 31, 2, 2}},
    {'5', {31, 16, 16, 30, 1, 1, 30}},
    {'6', {14, 16, 16, 30, 17, 17, 14}},
    {'7', {31, 1, 2, 4, 8, 8, 8}},
    {'8', {14, 17, 17, 14, 17, 17, 14}},
    {'9', {14, 17, 17, 15, 1, 1, 14}},
    {'-', {0, 0, 0, 31, 0, 0, 0}},
    {'%', {25, 25, 2, 4, 8, 19, 19}},
};

constexpr unsigned kGlyphSize = 5;
constexpr unsigned kGlyphWidth = 5 * kGlyphSize;
constexpr unsigned kGlyphHeight = 7 * kGlyphSize;
constexpr unsigned kSpacing = kGlyphSize;
constexpr unsigned kMaxTextWidth = 4 * kGlyphWidth + 3 * kSpacing;

void format_capacity(int capacity, char (&label)[5]) {
    if (capacity < 0 || capacity > 100)
        std::memcpy(label, "--%", 4);
    else
        std::snprintf(label, sizeof(label), "%u%%", static_cast<unsigned>(capacity));
}

int read_capacity(const char* path = kCapacity) {
    int value;
    return z1_read_capacity(path, &value) ? value : kUnknown;
}

void stop(int) {
    stopping = 1;
}

struct Surface {
    uint8_t* pixels;
    const fb_var_screeninfo& view;
    const fb_fix_screeninfo& info;

    void rectangle(unsigned x, unsigned y, unsigned width, unsigned height, bool lit) const {
        const unsigned bytes = view.bits_per_pixel / 8;
        const uint8_t value = lit ? 0xff : 0;
        for (unsigned row = y; row < y + height; ++row) {
            const auto offset = static_cast<size_t>(row + view.yoffset) * info.line_length +
                                static_cast<size_t>(x + view.xoffset) * bytes;
            std::memset(pixels + offset, value, width * bytes);
        }
    }

    void draw(int capacity) const {
        char label[5];
        format_capacity(capacity, label);
        const unsigned width = std::strlen(label) * (kGlyphWidth + kSpacing) - kSpacing;
        unsigned x = (view.xres - width) / 2;
        const unsigned y = (view.yres - kGlyphHeight) / 2;
        rectangle(0, 0, view.xres, view.yres, false);
        for (const char* character = label; *character; ++character) {
            for (const Glyph& glyph : kGlyphs) {
                if (glyph.character != *character) continue;
                for (unsigned row = 0; row < glyph.rows.size(); ++row) {
                    for (unsigned column = 0; column < 5; ++column) {
                        if (glyph.rows[row] & (1 << (4 - column))) {
                            rectangle(x + column * kGlyphSize, y + row * kGlyphSize,
                                      kGlyphSize, kGlyphSize, true);
                        }
                    }
                }
                break;
            }
            x += kGlyphWidth + kSpacing;
        }
    }
};

bool valid_framebuffer(const fb_var_screeninfo& view, const fb_fix_screeninfo& info) {
    if (info.type != FB_TYPE_PACKED_PIXELS ||
        (info.visual != FB_VISUAL_TRUECOLOR && info.visual != FB_VISUAL_DIRECTCOLOR) ||
        (view.bits_per_pixel != 16 && view.bits_per_pixel != 24 && view.bits_per_pixel != 32) ||
        view.nonstd != 0 || view.grayscale != 0 ||
        view.xres < kMaxTextWidth || view.yres < kGlyphHeight) {
        return false;
    }
    const uint64_t right = uint64_t{view.xoffset} + view.xres;
    const uint64_t bottom = uint64_t{view.yoffset} + view.yres;
    const uint64_t row = right * (view.bits_per_pixel / 8);
    return right <= view.xres_virtual && bottom <= view.yres_virtual &&
           row <= info.line_length && bottom <= info.smem_len / info.line_length;
}

int open_framebuffer() {
    const int fd = open(kFramebuffer, O_RDWR | O_CLOEXEC);
    return fd >= 0 ? fd : open(kFallbackFramebuffer, O_RDWR | O_CLOEXEC);
}
}  // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::strcmp(argv[1], "--sample") == 0) {
        char label[5];
        format_capacity(read_capacity(), label);
        std::puts(label);
        return 0;
    }
    if (argc != 1) {
        std::fprintf(stderr, "usage: z1_charge_ui [--sample]\n");
        return 2;
    }

    const int fd = open_framebuffer();
    if (fd < 0) {
        std::perror("open framebuffer");
        return 1;
    }

    fb_var_screeninfo view{};
    fb_fix_screeninfo info{};
    if (ioctl(fd, FBIOGET_VSCREENINFO, &view) < 0 ||
        ioctl(fd, FBIOGET_FSCREENINFO, &info) < 0 || !valid_framebuffer(view, info)) {
        std::fprintf(stderr, "unsupported framebuffer geometry or format\n");
        close(fd);
        return 1;
    }

    void* address = mmap(nullptr, info.smem_len, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (address == MAP_FAILED) {
        std::perror("map framebuffer");
        close(fd);
        return 1;
    }

    std::signal(SIGINT, stop);
    std::signal(SIGTERM, stop);
    const Surface surface{static_cast<uint8_t*>(address), view, info};
    int previous = kUnknown - 1;
    while (!stopping) {
        const int capacity = read_capacity();
        if (capacity != previous) {
            surface.draw(capacity);
            previous = capacity;
        }
        sleep(2);
    }
    munmap(address, info.smem_len);
    close(fd);
    return 0;
}
