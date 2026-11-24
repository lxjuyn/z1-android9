#define main charge_ui_main
#include "z1_charge_ui.cpp"
#undef main

#include <cassert>
#include <vector>

int main(int argc, char** argv) {
    assert(argc == 2);
    int fd = open(argv[1], O_CREAT | O_EXCL | O_RDWR, 0600);
    assert(fd >= 0);

    const auto check_sample = [&](const char* text, int expected, const char* display) {
        const size_t length = std::strlen(text);
        assert(ftruncate(fd, 0) == 0);
        assert(pwrite(fd, text, length, 0) == static_cast<ssize_t>(length));
        assert(read_capacity(argv[1]) == expected);
        char label[5];
        format_capacity(expected, label);
        assert(std::strcmp(label, display) == 0);
    };
    check_sample("0\n", 0, "0%");
    check_sample("72\n", 72, "72%");
    check_sample("100\n", 100, "100%");
    check_sample("", kUnknown, "--%");
    check_sample("101\n", kUnknown, "--%");
    check_sample("-1\n", kUnknown, "--%");
    check_sample("4mV\n", kUnknown, "--%");
    assert(ftruncate(fd, 0) == 0 && pwrite(fd, "0\0\n", 3, 0) == 3);
    assert(read_capacity(argv[1]) == kUnknown);
    assert(close(fd) == 0 && unlink(argv[1]) == 0);
    assert(read_capacity(argv[1]) == kUnknown);

    fb_var_screeninfo view{};
    view.xres = 240;
    view.yres = 320;
    view.xres_virtual = 242;
    view.yres_virtual = 322;
    view.xoffset = 1;
    view.yoffset = 1;
    view.bits_per_pixel = 16;

    fb_fix_screeninfo info{};
    info.type = FB_TYPE_PACKED_PIXELS;
    info.visual = FB_VISUAL_TRUECOLOR;
    info.line_length = 512;
    info.smem_len = 322 * info.line_length;
    assert(valid_framebuffer(view, info));

    std::vector<uint8_t> buffer(info.smem_len, 0x55);
    const Surface surface{buffer.data(), view, info};
    surface.draw(kUnknown);
    const auto unavailable = buffer;
    surface.draw(0);
    assert(buffer != unavailable);
    surface.draw(100);
    assert(buffer != unavailable);
    surface.draw(kUnknown);
    assert(buffer == unavailable);
    const auto y = (view.yres - kGlyphHeight) / 2 + 3 * kGlyphSize;
    const auto x = (view.xres - (3 * kGlyphWidth + 2 * kSpacing)) / 2;
    const auto glyph = (y + view.yoffset) * info.line_length + (x + view.xoffset) * 2;
    assert(buffer[glyph] == 0xff && buffer[glyph + 1] == 0xff);

    for (unsigned row = 0; row < view.yres_virtual; ++row) {
        for (unsigned byte = 0; byte < info.line_length; ++byte) {
            const bool visible = row >= view.yoffset && row < view.yoffset + view.yres &&
                                 byte >= 2 && byte < 2 * (view.xres + view.xoffset);
            const auto pixel = buffer[row * info.line_length + byte];
            assert(visible ? (pixel == 0 || pixel == 0xff) : pixel == 0x55);
        }
    }
    info.smem_len = 100;
    assert(!valid_framebuffer(view, info));
    info.line_length = 0;
    assert(!valid_framebuffer(view, info));

    std::puts("charge capacity, unavailable vs. valid zero, and framebuffer bounds passed");
}
