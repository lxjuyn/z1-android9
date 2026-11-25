#include "../capacity_policy.h"

#include <array>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <dirent.h>
#include <linux/fb.h>
#include <linux/input.h>
#include <linux/reboot.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <time.h>

extern char** environ;

namespace {
constexpr char kCapacity[] = "/sys/class/power_supply/battery/capacity";
constexpr char kBatteryVoltage[] = "/sys/class/power_supply/battery/voltage_now";
constexpr char kFramebuffer[] = "/dev/graphics/fb0";
constexpr char kFallbackFramebuffer[] = "/dev/fb0";
constexpr char kBootMode[] = "/sys/firmware/devicetree/base/chosen/mediatek,boot-mode";
constexpr char kUsbOnline[] = "/sys/class/power_supply/usb/online";
constexpr char kPwmModule[] = "/z1_pwm1_backlight.ko";
constexpr char kPwmBlank[] = "/sys/class/leds/lcd-backlight/display_blank";
constexpr int kUnknown = -1;
constexpr unsigned kFrameUs = 200000;
constexpr unsigned kScreenMs = 6000;
volatile std::sig_atomic_t stopping;
volatile std::sig_atomic_t wake_screen = 1;

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

constexpr unsigned kGlyphSize = 3;
constexpr unsigned kGlyphWidth = 5 * kGlyphSize;
constexpr unsigned kSpacing = kGlyphSize;
constexpr unsigned kArtWidth = 240;
constexpr unsigned kArtHeight = 320;
constexpr unsigned kAnimationFrames = 10;

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

void wake(int) {
    wake_screen = 1;
}

int64_t monotonic_ms() {
    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return static_cast<int64_t>(ts.tv_sec) * 1000 + ts.tv_nsec / 1000000;
}

void set_screen_blank(bool blank) {
    const int fd = open(kPwmBlank, O_WRONLY | O_CLOEXEC);
    if (fd < 0)
        return;
    if (write(fd, blank ? "1\n" : "0\n", 2) != 2)
        std::perror("[z1-charge] set PWM blank");
    close(fd);
}

void load_backlight() {
    const int fd = open(kPwmModule, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        std::perror("[z1-charge] open PWM module");
        return;
    }
    if (syscall(SYS_finit_module, fd, "", 0) < 0 && errno != EEXIST)
        std::perror("[z1-charge] load PWM module");
    close(fd);
}

struct Surface {
    uint8_t* pixels;
    const fb_var_screeninfo& view;
    const fb_fix_screeninfo& info;

    uint32_t color(uint32_t rgb) const {
        const auto channel = [](uint32_t value, const fb_bitfield& field) {
            return (value >> (8 - field.length)) << field.offset;
        };
        const uint32_t opacity = view.transp.length ?
                ((1U << view.transp.length) - 1) << view.transp.offset : 0;
        return opacity | channel(rgb >> 16, view.red) |
               channel((rgb >> 8) & 0xff, view.green) | channel(rgb & 0xff, view.blue);
    }

    void rectangle(unsigned x, unsigned y, unsigned width, unsigned height, uint32_t rgb) const {
        const unsigned left = uint64_t{x} * view.xres / kArtWidth;
        const unsigned right = uint64_t{x + width} * view.xres / kArtWidth;
        const unsigned top = uint64_t{y} * view.yres / kArtHeight;
        const unsigned bottom = uint64_t{y + height} * view.yres / kArtHeight;
        const unsigned bytes = view.bits_per_pixel / 8;
        const uint32_t pixel = color(rgb);
        for (unsigned row = top; row < bottom; ++row) {
            const auto offset = static_cast<size_t>(row + view.yoffset) * info.line_length +
                                static_cast<size_t>(left + view.xoffset) * bytes;
            auto* destination = pixels + offset;
            if (pixel == 0) {
                std::memset(destination, 0, (right - left) * bytes);
                continue;
            }
            for (unsigned column = left; column < right; ++column) {
                std::memcpy(destination, &pixel, bytes);
                destination += bytes;
            }
        }
    }

    void clear() const {
        rectangle(0, 0, kArtWidth, kArtHeight, 0);
    }

    void draw(int capacity, unsigned frame = 0) const {
        clear();
        // Upright battery and centered percentage in the stock panel layout.
        rectangle(105, 106, 30, 8, 0x747c89);
        rectangle(106, 106, 12, 5, 0xbcc3cb);
        rectangle(81, 114, 78, 150, 0x9ba5b2);
        rectangle(76, 120, 88, 138, 0x59616c);
        rectangle(81, 122, 78, 134, 0x151a22);
        rectangle(85, 122, 5, 134, 0x303844);
        rectangle(154, 122, 5, 134, 0x252c36);
        rectangle(82, 258, 76, 5, 0x656d7a);
        rectangle(88, 268, 64, 3, 0x171d24);

        constexpr unsigned fillBottom = 255;
        constexpr unsigned fillHeight = 132;
        if (capacity >= 0) {
            const bool low = capacity < 10;
            const unsigned phase = frame % kAnimationFrames;
            const unsigned pulse = phase < 5 ? phase : kAnimationFrames - 1 - phase;
            const unsigned base = fillHeight * static_cast<unsigned>(capacity) / 100;
            const unsigned level = low ? 4 + pulse * 2 : base;
            const uint32_t fill = low ? 0xa31822 : 0x185c99;
            const uint32_t shine = low ? 0xf27278 : 0x5fb7ec;
            const unsigned top = fillBottom - level;
            rectangle(86, top, 68, level, fill);
            rectangle(89, top, 5, level, shine);
            if (capacity <= 90)
                rectangle(86, top - 1 - pulse, 68, 1 + pulse, shine);
        }

        char label[5];
        format_capacity(capacity, label);
        const unsigned width = std::strlen(label) * (kGlyphWidth + kSpacing) - kSpacing;
        unsigned x = (kArtWidth - width) / 2;
        constexpr unsigned y = 53;
        for (const char* character = label; *character; ++character) {
            for (const Glyph& glyph : kGlyphs) {
                if (glyph.character != *character) continue;
                for (unsigned row = 0; row < glyph.rows.size(); ++row) {
                    for (unsigned column = 0; column < 5; ++column) {
                        if (glyph.rows[row] & (1 << (4 - column))) {
                            rectangle(x + column * kGlyphSize, y + row * kGlyphSize,
                                      kGlyphSize, kGlyphSize, 0xffffff);
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
        view.xres < kArtWidth / kGlyphSize || view.yres < kArtHeight / kGlyphSize) {
        return false;
    }
    uint32_t used_bits = 0;
    for (const fb_bitfield* field : {&view.red, &view.green, &view.blue, &view.transp}) {
        if (field->length == 0 && field == &view.transp)
            continue;
        if (field->length < 1 || field->length > 8 || field->msb_right ||
            field->offset > view.bits_per_pixel - field->length)
            return false;
        const uint32_t bits = ((1U << field->length) - 1) << field->offset;
        if (used_bits & bits)
            return false;
        used_bits |= bits;
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
int run_display(bool charger = false) {
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
    int capacity = kUnknown;
    unsigned sample = 0;
    unsigned frame = 0;
    int64_t blank_at = 0;
    while (!stopping) {
        const bool reveal = charger && wake_screen;
        const int64_t now = monotonic_ms();
        if (reveal) {
            wake_screen = 0;
            blank_at = now + kScreenMs;
            previous = kUnknown - 1;
            sample = 0;
        }
        if (charger && now >= blank_at) {
            if (blank_at) {
                set_screen_blank(true);
                surface.clear();
                blank_at = 0;
            }
            usleep(kFrameUs);
            continue;
        }
        if (sample == 0)
            capacity = read_capacity();
        if (capacity != previous || (capacity >= 0 && capacity <= 90)) {
            surface.draw(capacity, frame);
            previous = capacity;
        }
        if (reveal)
            set_screen_blank(false);
        frame = (frame + 1) % kAnimationFrames;
        sample = (sample + 1) % kAnimationFrames;
        usleep(kFrameUs);
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

int read_kpoc_mode(const char* path) {
    // Read an extra byte so that a malformed property with a valid prefix fails.
    std::array<uint8_t, 5> bytes{};
    const size_t length = read_attribute(path, bytes.data(), bytes.size());
    if (length == 4 && bytes[0] == 0 && bytes[1] == 0 && bytes[2] == 0 &&
        (bytes[3] == 8 || bytes[3] == 9))
        return bytes[3];
    return kUnknown;
}

bool can_boot_on_battery(const char* path = kBatteryVoltage) {
    uint8_t bytes[9];
    if (read_attribute(path, bytes, sizeof(bytes)) != 8 || bytes[7] != '\n')
        return false;
    int uv = 0;
    for (unsigned i = 0; i < 7; ++i) {
        if (bytes[i] < '0' || bytes[i] > '9')
            return false;
        uv = uv * 10 + bytes[i] - '0';
    }
    return uv > 3450000 && uv <= 5000000;
}

int open_power_key() {
    DIR* devices = opendir("/dev/input");
    if (!devices)
        return -1;
    int result = -1;
    while (const dirent* de = readdir(devices)) {
        if (std::strncmp(de->d_name, "event", 5) != 0)
            continue;
        const int fd = openat(dirfd(devices), de->d_name, O_RDONLY | O_CLOEXEC | O_NONBLOCK);
        if (fd < 0)
            continue;
        char name[64]{};
        int clock = CLOCK_MONOTONIC;
        if (ioctl(fd, EVIOCGNAME(sizeof(name)), name) > 0 &&
            std::strncmp(name, "mtk-pmic-keys", sizeof(name)) == 0 &&
            ioctl(fd, EVIOCSCLOCKID, &clock) == 0) {
            result = fd;
            break;
        }
        close(fd);
    }
    closedir(devices);
    return result;
}

enum class PowerKeyAction { none, wake, boot };

class PowerKey {
  public:
    PowerKeyAction update(const input_event& event) {
        if (event.type == EV_SYN && event.code == SYN_DROPPED) {
            down_us_ = -1;
            return PowerKeyAction::none;
        }
        if (event.type != EV_KEY || event.code != KEY_POWER)
            return PowerKeyAction::none;
        const int64_t now = static_cast<int64_t>(event.input_event_sec) * 1000000 +
                            event.input_event_usec;
        if (event.value == 1) {
            down_us_ = now;
            return PowerKeyAction::wake;
        }
        if (event.value != 0 || down_us_ < 0)
            return PowerKeyAction::none;
        const int64_t held = now - down_us_;
        down_us_ = -1;
        return held >= 500000 ? PowerKeyAction::boot : PowerKeyAction::wake;
    }

  private:
    int64_t down_us_ = -1;
};

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
    load_backlight();
    std::signal(SIGUSR1, wake);

    pid_t display = -1;
    bool attempted_display = false;
    int input = -1;
    PowerKey power_key;
    UsbRemoval removal;
    int online = -2;
    int64_t sample_usb_at = 0;
    for (;;) {
        const int64_t now = monotonic_ms();
        if (now >= sample_usb_at) {
            sample_usb_at = now + 1000;
            const int sample = read_usb_online(kUsbOnline);
            if (sample != online) {
                std::fprintf(stderr, "[z1-charge] usb/online=%d\n", sample);
                online = sample;
            }
            if (removal.update(online)) {
                std::fprintf(stderr, "[z1-charge] stable USB removal, requesting power off\n");
                reboot(RB_POWER_OFF);
                std::perror("[z1-charge] power off");
            }
        }

        if (online == 1 && !attempted_display &&
            (access(kFramebuffer, F_OK) == 0 || access(kFallbackFramebuffer, F_OK) == 0)) {
            display = fork();
            if (display == 0) {
                std::fprintf(stderr, "[z1-charge] starting framebuffer preview\n");
                _exit(run_display(true));
            }
            if (display < 0)
                std::perror("[z1-charge] fork framebuffer preview");
            attempted_display = true;
        }
        if (display > 0 && waitpid(display, nullptr, WNOHANG) == display)
            display = -1;

        if (input < 0)
            input = open_power_key();
        if (input >= 0) {
            input_event event{};
            while (read(input, &event, sizeof(event)) == sizeof(event)) {
                const auto action = power_key.update(event);
                if (action == PowerKeyAction::none)
                    continue;
                if (display > 0)
                    kill(display, SIGUSR1);
                if (action == PowerKeyAction::boot && online == 1) {
                    if (can_boot_on_battery()) {
                        std::fprintf(stderr, "[z1-charge] power key held, restarting into normal boot\n");
                        syscall(SYS_reboot, LINUX_REBOOT_MAGIC1, LINUX_REBOOT_MAGIC2,
                                LINUX_REBOOT_CMD_RESTART2, "z1-charger-normal");
                        std::perror("[z1-charge] restart");
                    } else {
                        std::fprintf(stderr, "[z1-charge] startup battery voltage unavailable or low\n");
                    }
                }
            }
        }
        usleep(kFrameUs);
    }
}

int dispatch_boot() {
    const bool mounted_sysfs =
            mount("sysfs", "/sys", "sysfs", MS_NOSUID | MS_NODEV | MS_NOEXEC, nullptr) == 0;
    const int mode = read_kpoc_mode(kBootMode);
    if (mode != kUnknown) {
        std::fprintf(stderr, "[z1-charge] LK boot-mode=%d, starting charger PID 1\n", mode);
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
