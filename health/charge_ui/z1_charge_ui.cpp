#include "../capacity_policy.h"

#include <array>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <linux/fb.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#include <sys/wait.h>

extern char** environ;

namespace {
constexpr char kCapacity[] = "/sys/class/power_supply/battery/capacity";
constexpr char kFramebuffer[] = "/dev/graphics/fb0";
constexpr char kFallbackFramebuffer[] = "/dev/fb0";
constexpr char kBootMode[] = "/sys/firmware/devicetree/base/chosen/mediatek,boot-mode";
constexpr char kUsbOnline[] = "/sys/class/power_supply/usb/online";
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
int run_display() {
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

size_t read_attribute(const char* path, uint8_t* bytes, size_t capacity) {
    const int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        return 0;

    size_t length = 0;
    while (length < capacity) {
        const ssize_t count = read(fd, bytes + length, capacity - length);
        if (count < 0 && errno == EINTR)
            continue;
        if (count <= 0) {
            if (count < 0)
                length = 0;
            break;
        }
        length += static_cast<size_t>(count);
    }
    close(fd);
    return length;
}

bool read_kpoc_mode(const char* path) {
    // Read an extra byte so that a malformed property with a valid prefix fails.
    std::array<uint8_t, 5> bytes{};
    const size_t length = read_attribute(path, bytes.data(), bytes.size());
    return length == 4 && bytes == std::array<uint8_t, 5>{0, 0, 0, 8, 0};
}

int read_usb_online(const char* path) {
    uint8_t bytes[3];
    if (read_attribute(path, bytes, sizeof(bytes)) != 2 || bytes[1] != '\n' ||
        (bytes[0] != '0' && bytes[0] != '1'))
        return kUnknown;
    return bytes[0] - '0';
}

class UsbRemoval {
  public:
    bool update(int online) {
        // Require evidence of attachment before acting on a disconnect. A PMIC
        // that has yet to report USB must not make us power off during startup.
        if (online == 1)
            seen_online_ = true;
        if (online != 0 || !seen_online_) {
            offline_count_ = 0;
            return false;
        }
        if (offline_count_ == 3)
            return false;
        return ++offline_count_ == 3;
    }

  private:
    bool seen_online_ = false;
    unsigned offline_count_ = 0;
};

void run_charger() {
    if (mount("proc", "/proc", "proc", MS_NOSUID | MS_NODEV | MS_NOEXEC, nullptr) < 0 &&
        errno != EBUSY)
        std::perror("[z1-charge] mount proc");
    if (mount("devtmpfs", "/dev", "devtmpfs", MS_NOSUID, nullptr) < 0 &&
        errno != EBUSY)
        std::perror("[z1-charge] mount devtmpfs");

    pid_t display = -1;
    bool attempted_display = false;
    UsbRemoval removal;
    int last_online = -2;
    for (;;) {
        if (!attempted_display &&
            (access(kFramebuffer, F_OK) == 0 || access(kFallbackFramebuffer, F_OK) == 0)) {
            display = fork();
            if (display == 0) {
                std::fprintf(stderr, "[z1-charge] starting framebuffer preview\n");
                _exit(run_display());
            }
            if (display < 0)
                std::perror("[z1-charge] fork framebuffer preview");
            attempted_display = true;
        }
        if (display > 0 && waitpid(display, nullptr, WNOHANG) == display)
            display = -1;

        const int online = read_usb_online(kUsbOnline);
        if (online != last_online) {
            std::fprintf(stderr, "[z1-charge] usb/online=%d\n", online);
            last_online = online;
        }
        if (removal.update(online)) {
            std::fprintf(stderr, "[z1-charge] stable USB removal, requesting power off\n");
            reboot(RB_POWER_OFF);
            std::perror("[z1-charge] power off");
        }
        sleep(1);
    }
}

int dispatch_boot() {
    const bool mounted_sysfs =
            mount("sysfs", "/sys", "sysfs", MS_NOSUID | MS_NODEV | MS_NOEXEC, nullptr) == 0;
    if (read_kpoc_mode(kBootMode)) {
        std::fprintf(stderr, "[z1-charge] LK boot-mode=8, starting charger PID 1\n");
        run_charger();
    }

    if (mounted_sysfs && umount("/sys") < 0)
        std::perror("[z1-charge] unmount sysfs");
    std::fprintf(stderr, "[z1-charge] handing normal boot to /init\n");
    char path[] = "/init";
    char* android_args[] = {path, nullptr};
    execve(path, android_args, environ);
    std::perror("[z1-charge] exec /init");
    return 1;
}

}  // namespace

int main(int argc, char** argv) {
    if (getpid() == 1)
        return dispatch_boot();
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
    return run_display();
}
