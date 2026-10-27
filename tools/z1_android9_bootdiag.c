/* Read-only Android early-boot evidence; no system binaries required. */
#define _GNU_SOURCE
#define _FILE_OFFSET_BITS 64
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <poll.h>
#include <stdint.h>
#include <signal.h>
#include <sys/wait.h>
#include <sys/ioctl.h>
#include <sys/sysmacros.h>
#include <sys/utsname.h>
#include <linux/kd.h>
#include <stdlib.h>
#include <sys/types.h>
#include <sys/vfs.h>
#include <termios.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

static int logfd = -1;
static unsigned char *stock_history;
static uint64_t *stock_history_end;

/* At most 89 bytes before newline, including the visible prefix. */
static void logmsg(const char *fmt, ...)
{
    char msg[1024], line[96];
    va_list ap;
    size_t off, len;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof(msg), fmt, ap);
    va_end(ap);
    len = strlen(msg);
    for (off = 0; off < len; ) {
        size_t count = len - off;
        if (count > 80) count = 80;
        int n = snprintf(line, sizeof(line), "[z1diag] %.*s\n", (int)count, msg + off);
        ssize_t written = write(logfd >= 0 ? logfd : STDERR_FILENO, line, (size_t)n);
        if (written < 0) return;
        off += count;
    }
}

static void dumptext(const char *path)
{
    char buf[256], row[512];
    size_t used = 0;
    ssize_t n;
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    logmsg("text %s", path);
    if (fd < 0) { logmsg("open errno=%d", errno); return; }
    while ((n = read(fd, buf, sizeof(buf))) > 0) {
        ssize_t i;
        for (i = 0; i < n; ++i) {
            if (buf[i] == '\n' || used == sizeof(row) - 1) {
                row[used] = 0;
                logmsg("%s", row);
                used = 0;
            }
            if (buf[i] != '\n') row[used++] = buf[i];
        }
    }
    if (n < 0) logmsg("read errno=%d", errno);
    if (used) { row[used] = 0; logmsg("%s", row); }
    close(fd);
}

static void pathstat(const char *path)
{
    struct stat s;
    char target[256];
    ssize_t n;
    logmsg("path %s", path);
    if (lstat(path, &s) < 0) logmsg("lstat errno=%d", errno);
    else {
        logmsg("lstat mode=%#o size=%lld", (unsigned)s.st_mode, (long long)s.st_size);
        if (S_ISLNK(s.st_mode)) {
            n = readlink(path, target, sizeof(target) - 1);
            if (n < 0) logmsg("readlink errno=%d", errno);
            else { target[n] = 0; logmsg("link=%s", target); }
        }
    }
    if (stat(path, &s) < 0) logmsg("stat errno=%d", errno);
    else logmsg("stat mode=%#o size=%lld", (unsigned)s.st_mode, (long long)s.st_size);
}

static void ext4magic(const char *path)
{
    unsigned char magic[2];
    struct stat s;
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NONBLOCK);
    ssize_t n;
    logmsg("ext4 %s offset=1080", path);
    if (fd < 0) { logmsg("open errno=%d", errno); return; }
    if (fstat(fd, &s) < 0) logmsg("fstat errno=%d", errno);
    else if (!S_ISBLK(s.st_mode)) logmsg("not a block device; skip read");
    else {
        n = pread(fd, magic, sizeof(magic), 1080);
        if (n < 0) logmsg("pread errno=%d", errno);
        else if (n != 2) logmsg("short read=%ld", (long)n);
        else logmsg("magic=%02x%02x expected=53ef match=%d", magic[0], magic[1],
                    magic[0] == 0x53 && magic[1] == 0xef);
    }
    close(fd);
}


/* The ACM path deliberately depends only on ramdisk and kernel interfaces. */
#define GADGET_ROOT "/config/usb_gadget"
#define DIAG_GADGET GADGET_ROOT "/z1diag"
#define ADB_EP0 "/dev/usb-ffs/adb/ep0"
#define HISTORY_SIZE (2U * 1024U * 1024U)
#ifndef DIAG_FIFO
#define DIAG_FIFO "/dev/z1_diag_stream"
#endif
#ifndef TOMBSTONE_DIR
#define TOMBSTONE_DIR "/data/tombstones"
#endif
#define TEXT_CHUNK 3000U
#define TOMBSTONE_LIMIT (4U * 1024U * 1024U)
#define TOMBSTONE_TOTAL_LIMIT (16U * 1024U * 1024U)
#ifndef GETPROP_PATH
#define GETPROP_PATH "/system/bin/getprop"
#endif
#ifndef PROC_MOUNTS_PATH
#define PROC_MOUNTS_PATH "/proc/mounts"
#endif
#ifndef POWER_SUPPLY_PATH
#define POWER_SUPPLY_PATH "/sys/class/power_supply"
#endif
#define STATUS_TIMEOUT_MS 15000U
#define PROPERTY_TIMEOUT_MS 1000U

static uint64_t monotonic_ms(void)
{
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) return 0;
    return (uint64_t)t.tv_sec * 1000U + (unsigned)t.tv_nsec / 1000000U;
}

/* FIFO records fit PIPE_BUF (4096), so concurrent writers cannot interleave.
 * This transport never writes logcat into kmsg: printk_devkmsg ratelimiting
 * silently discarded the old relay's crash records. A full FIFO backpressures
 * writers instead. The ACM reader still works without /system or logd. */
static int stream_open(void)
{
    struct stat st;
    if (lstat(DIAG_FIFO, &st) < 0 || !S_ISFIFO(st.st_mode)) {
        errno = ENODEV;
        return -1;
    }
    return open(DIAG_FIFO, O_WRONLY | O_CLOEXEC | O_NOFOLLOW);
}

static int stream_write(int fd, const char *data, size_t size)
{
    ssize_t n;
    do n = write(fd, data, size); while (n < 0 && errno == EINTR);
    if (n == (ssize_t)size) return 0;
    if (n >= 0) errno = EIO;
    return -1;
}

/* '+' means another fragment of the same original line follows; '=' ends
 * that line. Strip prefix/tag and concatenate fragments to reconstruct it.
 * Byte content is preserved, including UTF-8 and tabs; no 145-byte cropping. */
static int stream_line(int fd, const char *label, const char *line,
                       size_t count, int continues)
{
    char msg[TEXT_CHUNK + 80];
    int prefix = snprintf(msg, sizeof(msg), "[%s]%c ", label, continues ? '+' : '=');
    if (prefix < 0 || prefix > 70 || count > TEXT_CHUNK) { errno = EINVAL; return -1; }
    memcpy(msg + prefix, line, count);
    msg[prefix + count] = '\n';
    return stream_write(fd, msg, (size_t)prefix + count + 1);
}

static int stream_text(int fd, const char *label, int input, uint64_t limit,
                       uint64_t *total)
{
    char chunk[4096], row[TEXT_CHUNK];
    size_t used = 0;
    uint64_t read_bytes = 0;
    for (;;) {
        ssize_t n, i;
        size_t want = sizeof(chunk);
        if (limit && read_bytes >= limit) break;
        if (limit && want > limit - read_bytes) want = (size_t)(limit - read_bytes);
        do n = read(input, chunk, want); while (n < 0 && errno == EINTR);
        if (n < 0) return -1;
        if (!n) break;
        read_bytes += (uint64_t)n;
        for (i = 0; i < n; ++i) {
            if (chunk[i] == '\n') {
                if (stream_line(fd, label, row, used, 0)) return -1;
                used = 0;
            } else {
                if (used == sizeof(row)) {
                    if (stream_line(fd, label, row, used, 1)) return -1;
                    used = 0;
                }
                row[used++] = chunk[i];
            }
        }
    }
    if (used && stream_line(fd, label, row, used, 0)) return -1;
    if (total) *total = read_bytes;
    return 0;
}

/* T is a fixed, read-only request, never a shell. Snapshot file sizes before
 * reading, refuse symlinks/nonregular files and report the explicit size cap.
 * A child keeps filesystem reads/backpressure out of the early ACM loop. */
static int tombstone_dump(void)
{
    int stream, dirfd, index;
    unsigned found = 0;
    uint64_t exported = 0;
    alarm(120);
    stream = stream_open();
    if (stream < 0) return 1;
    dirfd = open(TOMBSTONE_DIR, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (dirfd < 0) {
        char msg[128];
        snprintf(msg, sizeof(msg), "directory unavailable errno=%d", errno);
        stream_line(stream, "z1tombstone", msg, strlen(msg), 0);
        close(stream);
        return 1;
    }
    for (index = 0; index < 32; ++index) {
        char name[32], msg[192];
        struct stat st;
        uint64_t limit, bytes = 0;
        int fd, result;
        snprintf(name, sizeof(name), "tombstone_%02d", index);
        fd = openat(dirfd, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
        if (fd < 0) continue;
        if (fstat(fd, &st) || !S_ISREG(st.st_mode) || st.st_size < 0) { close(fd); continue; }
        ++found;
        limit = (uint64_t)st.st_size;
        if (limit > TOMBSTONE_LIMIT) limit = TOMBSTONE_LIMIT;
        if (limit > TOMBSTONE_TOTAL_LIMIT - exported)
            limit = TOMBSTONE_TOTAL_LIMIT - exported;
        snprintf(msg, sizeof(msg), "BEGIN %s size=%lld cap=%u", name,
                 (long long)st.st_size, TOMBSTONE_LIMIT);
        if (stream_line(stream, "z1tombstone", msg, strlen(msg), 0)) { close(fd); break; }
        result = limit ? stream_text(stream, "z1tombstone", fd, limit, &bytes) : 0;
        exported += bytes;
        close(fd);
        snprintf(msg, sizeof(msg), "END %s read=%llu status=%s capped=%d", name,
                 (unsigned long long)bytes, result ? "ERROR" : "OK",
                 (uint64_t)st.st_size > limit);
        if (stream_line(stream, "z1tombstone", msg, strlen(msg), 0)) break;
        if (exported >= TOMBSTONE_TOTAL_LIMIT) {
            static const char capped[] = "TOTAL LIMIT reached (16 MiB); remaining files not exported";
            stream_line(stream, "z1tombstone", capped, sizeof(capped) - 1, 0);
            break;
        }
    }
    {
        char msg[80];
        snprintf(msg, sizeof(msg), "DONE files=%u", found);
        stream_line(stream, "z1tombstone", msg, strlen(msg), 0);
    }
    close(dirfd);
    close(stream);
    return 0;
}

/* S has fixed property keys and paths, no shell, no user-controlled argv.
 * At most 6 properties x 256 bytes, 16 KiB mounts and 8 supplies x 4 x
 * 256 bytes plus metadata (<32 KiB total). ACM supervises this child for
 * 15 seconds and each getprop child has its own 1-second read deadline. */
static int status_emit(int stream, const char *fmt, ...)
{
    char row[768];
    va_list ap;
    size_t i, size;
    va_start(ap, fmt);
    vsnprintf(row, sizeof(row), fmt, ap);
    va_end(ap);
    size = strlen(row);
    for (i = 0; i < size; ++i)
        if ((unsigned char)row[i] < ' ' || row[i] == 127) row[i] = ' ';
    return stream_line(stream, "z1status", row, size, 0);
}

static int status_property(int stream, const char *key)
{
    int channels[2], status = 0, error = 0, finished = 0, limited, reaped = 0;
    pid_t child;
    char value[257];
    size_t used = 0;
    uint64_t deadline = monotonic_ms() + PROPERTY_TIMEOUT_MS, reap_deadline;
    if (pipe2(channels, O_CLOEXEC))
        return status_emit(stream, "PROP key=%s result=PIPE_ERROR errno=%d", key, errno);
    child = fork();
    if (child < 0) {
        error = errno;
        close(channels[0]); close(channels[1]);
        return status_emit(stream, "PROP key=%s result=FORK_ERROR errno=%d", key, error);
    }
    if (!child) {
        close(channels[0]);
        if (dup2(channels[1], STDOUT_FILENO) < 0 || dup2(channels[1], STDERR_FILENO) < 0)
            _exit(126);
        close(channels[1]);
        execl(GETPROP_PATH, "getprop", key, (char *)NULL);
        _exit(127);
    }
    close(channels[1]);
    (void)fcntl(channels[0], F_SETFL, O_NONBLOCK);
    while (monotonic_ms() < deadline && used < sizeof(value) - 1) {
        ssize_t n = read(channels[0], value + used, sizeof(value) - 1 - used);
        if (n > 0) { used += (size_t)n; continue; }
        if (!n) { finished = 1; break; }
        if (errno != EAGAIN && errno != EINTR) { error = errno; break; }
        {
            struct pollfd p = { .fd = channels[0], .events = POLLIN | POLLHUP };
            (void)poll(&p, 1, 20);
        }
    }
    close(channels[0]);
    limited = used == sizeof(value) - 1;
    if (!finished) (void)kill(child, SIGKILL);
    reap_deadline = monotonic_ms() + 100;
    /* Never block ACM or even the snapshot child indefinitely on waitpid. */
    for (;;) {
        pid_t result = waitpid(child, &status, WNOHANG);
        if (result == child || (result < 0 && errno == ECHILD)) { reaped = 1; break; }
        if (monotonic_ms() >= (finished ? deadline : reap_deadline)) {
            (void)kill(child, SIGKILL);
            if (finished) {
                finished = 0;
                reap_deadline = monotonic_ms() + 100;
                continue;
            }
            break;
        }
        (void)poll(NULL, 0, 10);
    }
    value[used] = 0;
    while (used && (value[used - 1] == '\n' || value[used - 1] == '\r')) value[--used] = 0;
    return status_emit(stream, "PROP key=%s result=%s wait_status=%d errno=%d value=%s", key,
                       error ? "READ_ERROR" : limited ? "LIMIT" :
                       (finished && reaped ? (status ? "EXIT_ERROR" : "OK") : "TIMEOUT"), status, error, value);
}

static int status_supply_attribute(int stream, const char *name, const char *attribute)
{
    char path[384], value[257];
    struct stat st;
    ssize_t n;
    int fd, error, checked;
    snprintf(path, sizeof(path), POWER_SUPPLY_PATH "/%s/%s", name, attribute);
    fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return status_emit(stream, "POWER name=%s attribute=%s valid=0 errno=%d", name, attribute, errno);
    checked = fstat(fd, &st);
    if (checked || !S_ISREG(st.st_mode)) {
        error = checked ? errno : EINVAL;
        close(fd);
        return status_emit(stream, "POWER name=%s attribute=%s valid=0 errno=%d", name, attribute, error);
    }
    n = read(fd, value, sizeof(value) - 1);
    error = n < 0 ? errno : 0;
    close(fd);
    value[n >= 0 ? n : 0] = 0;
    return status_emit(stream, "POWER name=%s attribute=%s valid=%d errno=%d limit_reached=%d value=%s",
                       name, attribute, n >= 0, error, n == sizeof(value) - 1, value);
}

static int status_dump(void)
{
    static const char *const keys[] = { "sys.boot_completed", "init.svc.surfaceflinger",
        "init.svc.zygote", "init.svc.bootanim", "init.svc.netd", "init.svc.health-hal-z1" };
    static const char *const attributes[] = { "temp", "capacity", "present", "status" };
    int stream, fd;
    DIR *directory;
    struct dirent *entry;
    unsigned supplies = 0;
    size_t i;
    alarm(15);
    stream = stream_open();
    if (stream < 0) return 1;
    if (status_emit(stream, "BEGIN schema=1 property_cap=256 mounts_cap=16384 supplies_cap=8 timeout_ms=%u",
                    STATUS_TIMEOUT_MS)) { close(stream); return 1; }
    for (i = 0; i < sizeof(keys) / sizeof(keys[0]); ++i)
        if (status_property(stream, keys[i])) { close(stream); return 1; }
    fd = open(PROC_MOUNTS_PATH, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) status_emit(stream, "MOUNTS valid=0 errno=%d", errno);
    else {
        uint64_t bytes = 0;
        int result;
        status_emit(stream, "MOUNTS BEGIN cap=16384");
        result = stream_text(stream, "z1status", fd, 16384, &bytes);
        close(fd);
        if (result) { close(stream); return 1; }
        status_emit(stream, "MOUNTS END bytes=%llu limit_reached=%d",
                    (unsigned long long)bytes, bytes == 16384);
    }
    directory = opendir(POWER_SUPPLY_PATH);
    if (!directory) status_emit(stream, "POWER_DIR valid=0 errno=%d", errno);
    else {
        status_emit(stream, "POWER_DIR valid=1");
        while (supplies < 8 && (entry = readdir(directory))) {
            size_t length = strlen(entry->d_name);
            if (!length || length > 63 || strspn(entry->d_name,
                "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-") != length) continue;
            ++supplies;
            for (i = 0; i < sizeof(attributes) / sizeof(attributes[0]); ++i)
                if (status_supply_attribute(stream, entry->d_name, attributes[i])) {
                    closedir(directory); close(stream); return 1;
                }
        }
        closedir(directory);
    }
    status_emit(stream, "DONE supplies=%u", supplies);
    close(stream);
    return 0;
}

static void acmlog(const char *what, int error)
{
    char line[256];
    int n = snprintf(line, sizeof(line), "[z1acm] %s errno=%d\n", what, error);
    if (n > 0) {
        size_t i, count = (size_t)n;
        if (count >= sizeof(line)) count = sizeof(line) - 1;
        if (stock_history && stock_history_end)
            for (i = 0; i < count; ++i)
                stock_history[((*stock_history_end)++) % HISTORY_SIZE] = (unsigned char)line[i];
        ssize_t ignored = write(logfd >= 0 ? logfd : STDERR_FILENO, line, count);
        (void)ignored;
    }
}

static int folder(const char *path)
{
    struct stat st;
    if (!mkdir(path, 0755)) return 0;
    if (errno == EEXIST && !stat(path, &st) && S_ISDIR(st.st_mode)) return 0;
    return -1;
}

/* R3 root cause (audit A7-1/A7-2): the old composite branch demanded a
 * character-device ep0 (mainline functionfs ep0 is S_IFREG, so the check
 * always failed with ENODEV) and its fallback counter required ep0 to be
 * absent, so it never counted and was dead code. R5 (A7-3) replaces the
 * flat flag with the three-state upgrade machine below: bind ACM-only
 * first (the R1R2-proven configuration), watch the kernel ffs ready
 * probe, then upgrade the live gadget in place; any failed step unwinds
 * to ACM-only, three failures stop the machine for good. */

static int attribute(const char *name, const char *value)
{
    char path[512];
    ssize_t n;
    int fd, saved;
    snprintf(path, sizeof(path), DIAG_GADGET "/%s", name);
    fd = open(path, O_WRONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    n = write(fd, value, strlen(value));
    saved = errno;
    if (close(fd) < 0 && n == (ssize_t)strlen(value)) return -1;
    if (n != (ssize_t)strlen(value)) { errno = n < 0 ? saved : EIO; return -1; }
    return 0;
}

/* A different configured gadget must never be unbound or overwritten. */
static int bound_gadget(char *ours, size_t size)
{
    struct dirent *entry;
    DIR *dir = opendir(GADGET_ROOT);
    int result = 0;
    if (!dir) return -1;
    ours[0] = 0;
    while ((entry = readdir(dir))) {
        char path[512], value[256];
        ssize_t n;
        int fd;
        if (entry->d_name[0] == '.') continue;
        snprintf(path, sizeof(path), GADGET_ROOT "/%s/UDC", entry->d_name);
        fd = open(path, O_RDONLY | O_CLOEXEC);
        if (fd < 0) continue;
        n = read(fd, value, sizeof(value) - 1);
        close(fd);
        if (n <= 0) continue;
        value[n] = 0;
        value[strcspn(value, "\r\n")] = 0;
        if (!value[0]) continue;
        if (!strcmp(entry->d_name, "z1diag")) snprintf(ours, size, "%s", value);
        else result = 1;
    }
    closedir(dir);
    return result;
}

/* composite=0 binds the R1R2-proven ACM-only gadget (state 1); composite=1
 * additionally assembles ffs.adb and binds ACM+ADB in one shot — it is
 * only ever called from the upgrade machine after the ready probe read
 * '1', never at startup (A7-3). */
static int setup_acm(int composite)
{
    static const char *const dirs[] = {
        DIAG_GADGET, DIAG_GADGET "/strings/0x409", DIAG_GADGET "/configs/c.1",
        DIAG_GADGET "/configs/c.1/strings/0x409", DIAG_GADGET "/functions/acm.usb0",
        DIAG_GADGET "/functions/ffs.adb"
    };
    char udc[256] = "", ours[256], target[512];
    DIR *dir;
    struct dirent *entry;
    size_t i;
    struct statfs fs;
    int busy;
    if (statfs(GADGET_ROOT, &fs) < 0) return -1;
    if ((unsigned long)fs.f_type != 0x62656570UL) { errno = ENODEV; return -1; }
    busy = bound_gadget(ours, sizeof(ours));
    if (busy < 0) return -1;
    if (busy) { errno = EBUSY; return -1; }
    if (ours[0] && !composite) return 0;
    if (composite) {
        struct stat adb_ep;
        /* A7-1: mainline functionfs exposes ep0 as a plain S_IFREG file,
         * not a chardev; the old S_ISCHR check was always false and made
         * every composite bind fail with ENODEV. S_ISREG only proves the
         * functionfs mount exists; "descriptors are complete" was already
         * confirmed by the ready probe before this path runs. */
        if (stat(ADB_EP0, &adb_ep) < 0) { errno = ENODEV; return -1; }
        if (!S_ISREG(adb_ep.st_mode)) { errno = ENODEV; return -1; }
    }
    dir = opendir("/sys/class/udc");
    if (!dir) return -1;
    while ((entry = readdir(dir))) {
        if (strstr(entry->d_name, "musb") && !strchr(entry->d_name, '/')) {
            snprintf(udc, sizeof(udc), "%s", entry->d_name);
            break;
        }
    }
    closedir(dir);
    if (!udc[0]) { errno = ENODEV; return -1; }
    for (i = 0; i < sizeof(dirs) / sizeof(dirs[0]); ++i)
        if (!composite && strstr(dirs[i], "ffs.adb")) continue;
        else if (folder(dirs[i]) < 0) return -1;
    if (attribute("idVendor", "0x0e8d") || attribute("idProduct", "0x2006") ||
        attribute("bcdUSB", "0x0200") || attribute("bcdDevice", "0x0100") ||
        attribute("strings/0x409/serialnumber", "Z1DIAG20261001") ||
        attribute("strings/0x409/manufacturer", "Z1") ||
        attribute("strings/0x409/product", composite ? "Z1 Android9 ACM and ADB" : "Z1 Android9 diagnostic ACM") ||
        attribute("configs/c.1/strings/0x409/configuration", composite ? "ACM and ADB" : "Kernel log") ||
        attribute("configs/c.1/MaxPower", "100")) return -1;
    if (symlink(DIAG_GADGET "/functions/acm.usb0", DIAG_GADGET "/configs/c.1/acm.usb0") < 0) {
        ssize_t n;
        if (errno != EEXIST) return -1;
        n = readlink(DIAG_GADGET "/configs/c.1/acm.usb0", target, sizeof(target) - 1);
        if (n < 0) return -1;
        target[n] = 0;
        if (strcmp(target, DIAG_GADGET "/functions/acm.usb0")) { errno = EEXIST; return -1; }
    }
    if (composite) {
        struct stat adb_ep;
        /* A7-1, second site: same S_ISCHR→S_ISREG correction as above. */
        if (stat(ADB_EP0, &adb_ep) < 0) { errno = ENODEV; return -1; }
        if (!S_ISREG(adb_ep.st_mode)) { errno = ENODEV; return -1; }
        if (symlink(DIAG_GADGET "/functions/ffs.adb", DIAG_GADGET "/configs/c.1/ffs.adb") < 0 && errno != EEXIST) return -1;
    }
    /* Recheck ownership just before binding; kernel also refuses a busy UDC. */
    busy = bound_gadget(ours, sizeof(ours));
    if (busy || (ours[0] && !composite)) { errno = EBUSY; return -1; }
    if (attribute("UDC", udc) < 0) return -1;
    acmlog(composite ? "ACM and ADB bound; connect device USB after adbd opens FunctionFS" : "ACM bound; connect device USB after boot", 0);
    return 0;
}

/* ---- A7-3 (R5): composite upgrade state machine ----
 * Kernel semantics below were fixed by reading the local fork_linux tree
 * (Linux 7.0-rc7) directly:
 *  K1 the ffs configfs "ready" attribute reads ffs_dev->desc_ready
 *     (f_fs.c:3974-3985 f_fs_opts_ready_show);
 *  K2 ffs_do_functionfs_bind returns -ENODEV unless desc_ready is set
 *     (f_fs.c:3509) — ready='1' therefore implies a bindable ffs;
 *  K3 desc_ready becomes true when adbd finishes writing its descriptors
 *     (ffs_ready, f_fs.c:4266, reached from the ep0 write path f_fs.c:418);
 *  K4 desc_ready becomes false only when adbd closes its ep files
 *     (ffs_data_closed → ffs_closed, f_fs.c:2156/2216, callers :666/:1376);
 *  K5 UDC unbind (ffs_func_unbind) does NOT clear desc_ready, so the
 *     unbind→rebind window cannot fail with f_fs.c:3509's -ENODEV;
 *  K6 writing "\n" to UDC unbinds (configfs.c:271-312 strips the newline,
 *     empty name → unregister_gadget); unbinding an unbound gadget gives
 *     -ENODEV (configfs.c:260-261) and unwinds into the recovery state;
 *  K7 writing "" to UDC risks strlen(page)<len → EOVERFLOW
 *     (configfs.c:278-279), so unbind always writes "\n";
 *  K8 mkdir functions/ffs.adb does not require functionfs to be mounted
 *     (ffs_alloc_inst/_ffs_alloc_dev, f_fs.c:4029-4053);
 *  K9 mounting functionfs requires a pre-existing same-named ffs_dev or
 *     the mount fails with -ENOENT (ffs_acquire_dev) — polling state 2
 *     creates ffs.adb early, which is a precondition of the rc-side
 *     mount, not just of reading ready. */
#define DIAG_READY_POLL_MS 5000U    /* A7-3: state 2 polls ready every 5s */
#define DIAG_READY_WINDOW_MS 10000U /* A7-3: one upgrade attempt times out at 10s */
#define DIAG_BACKOFF_MS 30000U      /* A7-3: back off 30s after a failed attempt */
#define DIAG_MAX_FAILURES 3U        /* A7-3: permanent fallback after 3 failures */

enum diag_upgrade_state {
    DIAG_ACM_ONLY = 0, /* 状态 1：纯 ACM 在役（setup_acm(0) 成功后即转状态 2） */
    DIAG_WAIT_READY,   /* 状态 2：每 5s 读 ffs.adb/ready，10s 窗口 */
    DIAG_UPGRADE,      /* 状态 2 执行：软断开 tty → 解绑 → 重绑复合 */
    DIAG_RECOVER,      /* 状态 3：拆 ffs 链接 + 回绑纯 ACM，退避后重试 */
    DIAG_COMPOSITE,    /* 终态：ACM+ADB 复合在役（adbd 死亡只影响 ADB，
                          f_acm 独立工作，无需再动 UDC） */
    DIAG_DISABLED      /* 状态 3 终态：累计 3 次失败，永久回退纯 ACM */
};

static int diag_state = DIAG_ACM_ONLY;
static unsigned diag_upgrade_failures;
static uint64_t diag_poll_deadline;
static uint64_t diag_window_deadline;

static void diag_fail_backoff(void)
{
    ++diag_upgrade_failures;
    if (diag_upgrade_failures >= DIAG_MAX_FAILURES) {
        /* A7-3 定案文案；acmlog 自动补 [z1acm] 前缀进 kmsg。 */
        acmlog("composite upgrade failed x3; staying ACM-only", 0);
        diag_state = DIAG_DISABLED;
        return;
    }
    acmlog("composite upgrade failed; backoff 30s before retry", 0);
    diag_poll_deadline = monotonic_ms() + DIAG_BACKOFF_MS;
    diag_window_deadline = diag_poll_deadline + DIAG_READY_WINDOW_MS;
    diag_state = DIAG_WAIT_READY;
}

/* A7-d：解绑前把 tty fd 状态记好并软断开；UDC 解绑会 hangup ttyGS0，
 * 重绑后由 acm_main 既有 retry=5 节奏重开（重开时重放历史窗口，
 * kmsg/FIFO 采集与环形缓冲全程不停——ACM 中继不断流）。 */
static void diag_softclose_tty(int *tty, int *streaming, unsigned *retry)
{
    if (*tty >= 0) {
        close(*tty);
        *tty = -1;
        *streaming = 0;
        *retry = 5;
    }
}

/* 非阻塞步进：acm_main 每 tick（约 200ms）调用一次，任何路径都不睡眠。 */
static void diag_upgrade_step(int *tty, int *streaming, unsigned *retry)
{
    switch (diag_state) {
    case DIAG_ACM_ONLY:
        diag_state = DIAG_WAIT_READY;
        diag_poll_deadline = monotonic_ms();
        diag_window_deadline = diag_poll_deadline + DIAG_READY_WINDOW_MS;
        return;
    case DIAG_WAIT_READY: {
        char value[16];
        int fd;
        ssize_t n;
        int ready = 0;
        if (monotonic_ms() < diag_poll_deadline) return;
        diag_poll_deadline = monotonic_ms() + DIAG_READY_POLL_MS;
        /* K8/K9：先建 ffs.adb 实例目录（幂等）；adbd 的 functionfs
         * mount 依赖它先存在。mkdir 失败按"未就绪"处理，计入窗口。 */
        if (folder(DIAG_GADGET "/functions/ffs.adb") >= 0) {
            fd = open(DIAG_GADGET "/functions/ffs.adb/ready", O_RDONLY | O_CLOEXEC);
            if (fd >= 0) { /* K1：ready 探针读的就是 desc_ready */
                n = read(fd, value, sizeof(value) - 1);
                close(fd);
                if (n > 0) { value[n] = 0; ready = value[0] == '1'; }
            }
        }
        if (ready) {
            diag_state = DIAG_UPGRADE;
            return;
        }
        /* A7-3：10s 窗口耗尽仍未读出 '1'（含 mkdir/open/read 全部失败
         * 路径）= 一次升级失败 → 计数、退避或永久回退。 */
        if (monotonic_ms() >= diag_window_deadline) diag_fail_backoff();
        return;
        }
    case DIAG_UPGRADE:
        diag_softclose_tty(tty, streaming, retry); /* A7-d：先记好 tty 状态 */
        /* K6/K7：写 "\n" 解绑；未绑时 -ENODEV 同样落入恢复路径自愈。 */
        if (attribute("UDC", "\n") < 0) {
            acmlog("composite UDC unbind failed", errno);
            diag_poll_deadline = 0;
            diag_state = DIAG_RECOVER;
            return;
        }
        /* K2/K3/K5：ready 刚确认为 '1' 且解绑不清 desc_ready，
         * setup_acm(1) 的一次性重绑（含 A7-1 修正的 S_ISREG ep0 校验与
         * ffs symlink 装配）不会撞 f_fs.c:3509 的 -ENODEV。 */
        if (setup_acm(1) < 0) {
            acmlog("composite bind failed", errno);
            diag_poll_deadline = 0;
            diag_state = DIAG_RECOVER;
            return;
        }
        acmlog("composite upgraded; ACM and ADB bound (idProduct 0x2006)", 0);
        diag_state = DIAG_COMPOSITE;
        return;
    case DIAG_RECOVER:
        /* 状态 3：ACM 恢复优先于计数——UDC 回绑失败每秒重试（节流），
         * 不计入升级失败；回绑成功才算一次完整升级失败（A7-3）。 */
        if (monotonic_ms() < diag_poll_deadline) return;
        diag_poll_deadline = monotonic_ms() + 1000U;
        (void)unlink(DIAG_GADGET "/configs/c.1/ffs.adb");
        if (setup_acm(0) < 0) return;
        diag_fail_backoff();
        return;
    case DIAG_COMPOSITE:
    case DIAG_DISABLED:
        return;
    }
    /* 不可达；所有 case 均以 return 结束（A7-3 状态机穷尽转移）。 */
}

/* Stock 3.4 android_usb predates configfs. Keep this path independent. */
#ifndef STOCK_USB
#define STOCK_USB "/sys/class/android_usb/android0"
#endif

static int stock_attribute(const char *name, const char *value)
{
    char path[256];
    ssize_t n;
    int fd, saved;
    if (snprintf(path, sizeof(path), STOCK_USB "/%s", name) >= (int)sizeof(path)) {
        errno = ENAMETOOLONG; return -1;
    }
    fd = open(path, O_WRONLY | O_CLOEXEC | O_NONBLOCK);
    if (fd < 0) return -1;
    n = write(fd, value, strlen(value));
    saved = errno;
    if (close(fd) < 0 && n == (ssize_t)strlen(value)) return -1;
    if (n != (ssize_t)strlen(value)) { errno = n < 0 ? saved : EIO; return -1; }
    return 0;
}

/* Use the kernel-advertised device number, never a guessed ACM major. */
static int ensure_tty_node(const char *name)
{
    char path[256], device[128], node[128], extra;
    unsigned major_number, minor_number;
    struct stat st;
    int fd, saved;
    ssize_t n;
    dev_t number;
    if (strcmp(name, "ttyGS0") && strcmp(name, "tty0")) { errno = EINVAL; return -1; }
    snprintf(path, sizeof(path), "/sys/class/tty/%s/dev", name);
    fd = open(path, O_RDONLY | O_CLOEXEC | O_NONBLOCK);
    if (fd < 0) return -1;
    n = read(fd, device, sizeof(device) - 1);
    saved = errno;
    close(fd);
    if (n <= 0) { errno = n < 0 ? saved : EIO; return -1; }
    device[n] = 0;
    device[strcspn(device, "\r\n")] = 0;
    if (sscanf(device, "%u:%u%c", &major_number, &minor_number, &extra) != 2) {
        errno = EINVAL; return -1;
    }
    number = makedev(major_number, minor_number);
    if (major(number) != major_number || minor(number) != minor_number) {
        errno = ERANGE; return -1;
    }
    snprintf(node, sizeof(node), "/dev/%s", name);
    if (!lstat(node, &st)) {
        if (S_ISCHR(st.st_mode) && st.st_rdev == number) return 0;
        errno = EEXIST; return -1;
    }
    if (errno != ENOENT) return -1;
    if (!mknod(node, S_IFCHR | 0600, number)) return 0;
    /* ueventd may win the creation race. Accept only the same real device. */
    if (errno == EEXIST && !lstat(node, &st) && S_ISCHR(st.st_mode) && st.st_rdev == number)
        return 0;
    return -1;
}

static int setup_stock_acm(void)
{
    if (stock_attribute("enable", "0") ||
        stock_attribute("idVendor", "17EF") ||
        stock_attribute("idProduct", "7439") ||
        stock_attribute("f_acm/instances", "1") ||
        stock_attribute("functions", "acm") ||
        stock_attribute("bDeviceClass", "02") ||
        stock_attribute("iSerial", "Z1STOCK20261003") ||
        stock_attribute("enable", "1")) return -1;
    acmlog("stock android_usb ACM enabled VID=17EF PID=7439 serial=Z1STOCK20261003", 0);
    return 0;
}

static void history_identity(unsigned char *history, uint64_t *end)
{
    struct utsname identity;
    char line[4096], cmdline[2048];
    int fd, length;
    ssize_t n;
    size_t i, count;
    if (!uname(&identity)) {
        length = snprintf(line, sizeof(line), "[z1identity] uname=%s %s %s %s\n",
                          identity.sysname, identity.release, identity.version, identity.machine);
        if (length > 0) {
            count = (size_t)length < sizeof(line) ? (size_t)length : sizeof(line) - 1;
            for (i = 0; i < count; ++i) history[((*end)++) % HISTORY_SIZE] = (unsigned char)line[i];
        }
    }
    fd = open("/proc/cmdline", O_RDONLY | O_CLOEXEC | O_NONBLOCK);
    if (fd < 0) return;
    n = read(fd, cmdline, sizeof(cmdline) - 1);
    close(fd);
    if (n <= 0) return;
    cmdline[n] = 0;
    cmdline[strcspn(cmdline, "\r\n")] = 0;
    length = snprintf(line, sizeof(line), "[z1identity] cmdline=%s\n", cmdline);
    if (length <= 0) return;
    count = (size_t)length < sizeof(line) ? (size_t)length : sizeof(line) - 1;
    for (i = 0; i < count; ++i) history[((*end)++) % HISTORY_SIZE] = (unsigned char)line[i];
}

/* Discover stock partitions from the live kernel, never from guessed pN. */
#ifndef STOCK_DUMCHAR_INFO
#define STOCK_DUMCHAR_INFO "/proc/dumchar_info"
#endif
#ifndef STOCK_BLOCK_SYSFS
#define STOCK_BLOCK_SYSFS "/sys/class/block"
#endif
#ifndef STOCK_BLOCK_DEV
#define STOCK_BLOCK_DEV "/dev/block"
#endif
#define STOCK_PART_LIMIT 65536U
struct stock_partition {
    const char *name, *alias;
    char node[48];
    uint64_t size, start;
    int found;
};

static int bounded_text(const char *path, char *text, size_t capacity)
{
    size_t used = 0;
    int fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return -1;
    for (;;) {
        char extra;
        ssize_t n;
        if (used == capacity - 1) {
            n = read(fd, &extra, 1);
            if (n > 0) { close(fd); errno = EFBIG; return -1; }
        } else n = read(fd, text + used, capacity - 1 - used);
        if (n < 0) { int saved = errno; close(fd); errno = saved; return -1; }
        if (!n) break;
        used += (size_t)n;
    }
    close(fd);
    text[used] = 0;
    return 0;
}

static int stock_number(const char *text, int base, uint64_t *value)
{
    const char *digits = text;
    char *end;
    unsigned long long number;
    if (base == 16) {
        if (strncmp(text, "0x", 2)) { errno = EINVAL; return -1; }
        digits += 2;
    }
    if (!*digits || strspn(digits, base == 16 ? "0123456789abcdefABCDEF" : "0123456789") != strlen(digits)) {
        errno = EINVAL; return -1;
    }
    errno = 0;
    number = strtoull(text, &end, base);
    if (errno || *end) { if (!errno) errno = EINVAL; return -1; }
    *value = (uint64_t)number;
    return 0;
}

static int stock_parse_partitions(char *text, struct stock_partition part[3])
{
    char *save = NULL, *line;
    int header = 0;
    while ((line = strtok_r(text, "\n", &save))) {
        char name[32], size[32], start[32], type[16], map[128], extra[2];
        int fields;
        size_t i;
        text = NULL;
        if (strspn(line, " \t\r") == strlen(line)) continue;
        fields = sscanf(line, "%31s %31s %31s %15s %127s %1s", name, size, start, type, map, extra);
        if (!header) {
            if (fields != 5 || strcmp(name, "Part_Name") || strcmp(size, "Size") ||
                strcmp(start, "StartAddr") || strcmp(type, "Type") || strcmp(map, "MapTo")) {
                errno = EINVAL; return -1;
            }
            header = 1;
            continue;
        }
        if (fields < 1) continue;
        for (i = 0; i < 3; ++i) if (!strcmp(name, part[i].name)) {
            const char *suffix;
            if (fields != 5 || part[i].found || strcmp(type, "2") ||
                strncmp(map, "/dev/block/mmcblk0p", 19)) { errno = EINVAL; return -1; }
            suffix = map + 19;
            if (*suffix < '1' || *suffix > '9' || strspn(suffix, "0123456789") != strlen(suffix) ||
                strlen(suffix) > 10 || strlen(map + 11) >= sizeof(part[i].node)) {
                errno = EINVAL; return -1;
            }
            if (stock_number(size, 16, &part[i].size) || stock_number(start, 16, &part[i].start) ||
                !part[i].size || part[i].size % 512 || part[i].start % 512) {
                errno = EINVAL; return -1;
            }
            snprintf(part[i].node, sizeof(part[i].node), "%s", map + 11);
            part[i].found = 1;
        }
    }
    if (!header || !part[0].found || !part[1].found || !part[2].found) { errno = EINVAL; return -1; }
    if (!strcmp(part[0].node, part[1].node) || !strcmp(part[0].node, part[2].node) ||
        !strcmp(part[1].node, part[2].node)) { errno = EINVAL; return -1; }
    return 0;
}

static int stock_sys_number(const char *node, const char *attribute_name, uint64_t *value)
{
    char path[256], text[128];
    snprintf(path, sizeof(path), STOCK_BLOCK_SYSFS "/%s/%s", node, attribute_name);
    if (bounded_text(path, text, sizeof(text))) return -1;
    text[strcspn(text, "\r\n")] = 0;
    return stock_number(text, 10, value);
}

static uint32_t stock_le32(const unsigned char *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static int stock_validate_partition(const struct stock_partition *part)
{
    char path[256], text[128], extra;
    uint64_t sectors, start, bytes, blocks;
    unsigned major_number, minor_number;
    unsigned char superblock[2048];
    uint32_t block_shift, incompat;
    struct stat st;
    dev_t device;
    int fd, saved;
    ssize_t n;
    if (stock_sys_number(part->node, "size", &sectors) || stock_sys_number(part->node, "start", &start)) return -1;
    if (sectors > UINT64_MAX / 512 || start > UINT64_MAX / 512) { errno = ERANGE; return -1; }
    bytes = sectors * 512;
    logmsg("stock partition %s MapTo=%s advertised_size=%llu actual_size=%llu advertised_start=%llu actual_start=%llu",
           part->name, part->node, (unsigned long long)part->size, (unsigned long long)bytes,
           (unsigned long long)part->start, (unsigned long long)(start * 512));
    if (bytes < 1024U * 1024U || start * 512 != part->start ||
        (strcmp(part->name, "usrdata") ? bytes != part->size : bytes > part->size)) {
        errno = EINVAL; return -1;
    }
    if (bytes != part->size)
        logmsg("usrdata EOD truncation advertised_minus_actual=%llu bytes; verify actual ext4 fits",
               (unsigned long long)(part->size - bytes));
    snprintf(path, sizeof(path), STOCK_BLOCK_SYSFS "/%s/dev", part->node);
    if (bounded_text(path, text, sizeof(text))) return -1;
    text[strcspn(text, "\r\n")] = 0;
    if (sscanf(text, "%u:%u%c", &major_number, &minor_number, &extra) != 2) { errno = EINVAL; return -1; }
    device = makedev(major_number, minor_number);
    if (major(device) != major_number || minor(device) != minor_number) { errno = ERANGE; return -1; }
    snprintf(path, sizeof(path), STOCK_BLOCK_DEV "/%s", part->node);
    if (lstat(path, &st) < 0) {
        if (errno != ENOENT) return -1;
        if (mknod(path, S_IFBLK | 0600, device) && errno != EEXIST) return -1;
        if (lstat(path, &st)) return -1;
    }
    if (!S_ISBLK(st.st_mode) || st.st_rdev != device) { errno = EINVAL; return -1; }
    fd = open(path, O_RDONLY | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) return -1;
    n = pread(fd, superblock, sizeof(superblock), 0);
    saved = errno;
    close(fd);
    if (n != (ssize_t)sizeof(superblock)) { errno = n < 0 ? saved : EIO; return -1; }
    if (superblock[1080] != 0x53 || superblock[1081] != 0xef) { errno = EINVAL; return -1; }
    block_shift = stock_le32(superblock + 1048);
    incompat = stock_le32(superblock + 1120);
    blocks = stock_le32(superblock + 1028);
    if (incompat & 0x80U) blocks |= (uint64_t)stock_le32(superblock + 1360) << 32;
    if (block_shift > 2 || !blocks || blocks > bytes / (1024U << block_shift)) { errno = EINVAL; return -1; }
    logmsg("stock %s dev=%u:%u ext4_blocks=%llu blocksize=%u fits=true",
           part->name, major_number, minor_number, (unsigned long long)blocks, 1024U << block_shift);
    return 0;
}

static int stock_partitions_worker(void)
{
    static char table[STOCK_PART_LIMIT + 1];
    struct stock_partition part[3] = {
        { .name = "android", .alias = "z1-stock-system" },
        { .name = "cache", .alias = "z1-stock-cache" },
        { .name = "usrdata", .alias = "z1-stock-data" }
    };
    int created[3] = {0, 0, 0};
    size_t i;
    if (folder(STOCK_BLOCK_DEV) || bounded_text(STOCK_DUMCHAR_INFO, table, sizeof(table)) ||
        stock_parse_partitions(table, part)) {
        int saved = errno;
        logmsg("stock partition schema/read rejected errno=%d; no fallback", saved);
        errno = saved; return 1;
    }
    for (i = 0; i < 3; ++i) if (stock_validate_partition(&part[i])) {
        int saved = errno;
        logmsg("stock partition %s rejected errno=%d; no aliases mounted", part[i].name, saved);
        errno = saved; return 1;
    }
    for (i = 0; i < 3; ++i) {
        char alias[256], target[256], current[256];
        struct stat st;
        ssize_t n;
        snprintf(alias, sizeof(alias), STOCK_BLOCK_DEV "/%s", part[i].alias);
        snprintf(target, sizeof(target), STOCK_BLOCK_DEV "/%s", part[i].node);
        if (!lstat(alias, &st)) {
            n = S_ISLNK(st.st_mode) ? readlink(alias, current, sizeof(current) - 1) : -1;
            if (n >= 0) current[n] = 0;
            if (n < 0 || strcmp(current, target)) { errno = EEXIST; break; }
        } else if (errno != ENOENT || symlink(target, alias)) break;
        else created[i] = 1;
    }
    if (i != 3) {
        int saved = errno;
        for (i = 0; i < 3; ++i) if (created[i]) {
            char alias[256]; snprintf(alias, sizeof(alias), STOCK_BLOCK_DEV "/%s", part[i].alias);
            (void)unlink(alias);
        }
        logmsg("stock alias creation rejected errno=%d; removed new aliases", saved);
        errno = saved; return 1;
    }
    logmsg("stock partition aliases validated: system/cache/data; no formatting or guessed pN");
    return 0;
}

/* MMC rescan may register sysfs after fs actions start. Retry registration
 * errors only; malformed tables, geometry and filesystem mismatches fail fast. */
static int stock_partitions_retry(void)
{
    uint64_t deadline = monotonic_ms() + 4000U;
    unsigned attempts = 0;
    for (;;) {
        int saved;
        ++attempts;
        if (!stock_partitions_worker()) return 0;
        saved = errno;
        if (saved != ENOENT && saved != ENODEV && saved != ENXIO && saved != EAGAIN) {
            errno = saved; return 1;
        }
        if (monotonic_ms() >= deadline) {
            logmsg("stock MMC registration deadline reached attempts=%u errno=%d", attempts, saved);
            errno = saved; return 1;
        }
        logmsg("stock MMC not registered yet attempt=%u errno=%d; bounded retry", attempts, saved);
        (void)poll(NULL, 0, 100);
        if (monotonic_ms() >= deadline) { errno = saved; return 1; }
    }
}

static int stock_partitions(void)
{
    uint64_t deadline = monotonic_ms() + 5000U;
    int status = 0;
    pid_t child = fork();
    if (child < 0) { logmsg("stock partition fork errno=%d", errno); return 1; }
    if (!child) _exit(stock_partitions_retry());
    for (;;) {
        pid_t result = waitpid(child, &status, WNOHANG);
        if (result == child) return WIFEXITED(status) && !WEXITSTATUS(status) ? 0 : 1;
        if (result < 0 && errno != EINTR) { (void)kill(child, SIGKILL); return 1; }
        if (monotonic_ms() >= deadline) {
            (void)kill(child, SIGKILL);
            (void)waitpid(child, &status, WNOHANG);
            logmsg("stock partition deadline exceeded; no fallback"); return 1;
        }
        (void)poll(NULL, 0, 20);
    }
}

/* No framebuffer reinitialization and no KD_TEXT restoration on exit. */
static int graphics_mode(void)
{
    uint64_t deadline = monotonic_ms() + 5000U;
    int fd = -1, mode = -1, error = ENODEV;
    do {
        if (!ensure_tty_node("tty0")) {
            fd = open("/dev/tty0", O_RDWR | O_NONBLOCK | O_NOCTTY | O_CLOEXEC);
            if (fd >= 0) break;
        }
        error = errno;
        (void)poll(NULL, 0, 100);
    } while (monotonic_ms() < deadline);
    if (fd < 0) { acmlog("KD_GRAPHICS unavailable: no usable VT tty0", error); return 1; }
    /* Isolate even a wedged console-driver ioctl from Android init's caller. */
    {
        pid_t child = fork();
        int status = 0;
        if (child < 0) { error = errno; close(fd); acmlog("KD_GRAPHICS fork failed", error); return 1; }
        if (!child) {
            if (ioctl(fd, KDSETMODE, KD_GRAPHICS) < 0 || ioctl(fd, KDGETMODE, &mode) < 0) {
                error = errno;
                close(fd);
                acmlog("KD_GRAPHICS ioctl failed", error);
                _exit(1);
            }
            close(fd);
            if (mode != KD_GRAPHICS) { acmlog("KD_GRAPHICS verification failed", EIO); _exit(1); }
            acmlog("KD_GRAPHICS verified; fbcon text suppressed, framebuffer retained", 0);
            _exit(0);
        }
        close(fd);
        deadline = monotonic_ms() + 2000U;
        for (;;) {
            pid_t result = waitpid(child, &status, WNOHANG);
            if (result == child) return WIFEXITED(status) && !WEXITSTATUS(status) ? 0 : 1;
            if (result < 0 && errno != EINTR) {
                error = errno; (void)kill(child, SIGKILL);
                acmlog("KD_GRAPHICS waitpid failed", error); return 1;
            }
            if (monotonic_ms() >= deadline) {
                (void)kill(child, SIGKILL);
                /* Never block on a driver stuck in uninterruptible sleep. */
                (void)waitpid(child, &status, WNOHANG);
                acmlog("KD_GRAPHICS ioctl child timed out", ETIMEDOUT);
                return 1;
            }
            (void)poll(NULL, 0, 20);
        }
    }
}

static int acm_main(int stock)
{
    static unsigned char history[HISTORY_SIZE];
    char record[8192];
    uint64_t end = 0, sent = 0;
    int kfd = -1, tty = -1, configured = 0, previous_error = -1, streaming = 0;
    int stream = -1;
    pid_t tombstone_child = -1;
    pid_t status_child = -1;
    uint64_t status_deadline = 0;
    char warning[128];
    size_t warning_size = 0, warning_sent = 0;
    uint64_t warning_target = 0;
    unsigned retry = 0;
    uint64_t identity_deadline = 0;
    if (stock) { stock_history = history; stock_history_end = &end; }
    acmlog("starting independent ramdisk ACM log relay; send R to replay", 0);
    for (;;) {
        unsigned reads;
        struct pollfd waitfd;
        if (monotonic_ms() >= identity_deadline) {
            history_identity(history, &end);
            identity_deadline = monotonic_ms() + 60000U;
        }
        if (status_child > 0) {
            int status;
            if (waitpid(status_child, &status, WNOHANG) == status_child) {
                if (status) {
                    (void)kill(-status_child, SIGKILL);
                    if (stream >= 0)
                        status_emit(stream, "END result=FAILED_OR_TIMEOUT wait_status=%d retry=S", status);
                }
                status_child = -1;
            } else if (monotonic_ms() >= status_deadline) {
                /* Includes getprop descendants if snapshot blocked on FIFO/sysfs. */
                (void)kill(-status_child, SIGKILL);
                (void)kill(status_child, SIGKILL);
            }
        }
        if (tombstone_child > 0) {
            int status;
            if (waitpid(tombstone_child, &status, WNOHANG) == tombstone_child) {
                if (status) acmlog("tombstone export failed/timed out; request T to retry", status);
                tombstone_child = -1;
            }
        }
        if (stream < 0) {
            struct stat st;
            if ((!mkfifo(DIAG_FIFO, 0600) || errno == EEXIST) &&
                !lstat(DIAG_FIFO, &st) && S_ISFIFO(st.st_mode))
                stream = open(DIAG_FIFO, O_RDWR | O_NONBLOCK | O_NOFOLLOW | O_CLOEXEC);
        }
        if (kfd < 0) {
            kfd = open(stock ? "/proc/kmsg" : "/dev/kmsg", O_RDONLY | O_NONBLOCK | O_CLOEXEC);
            if (!stock && kfd >= 0 && lseek(kfd, 0, SEEK_SET) < 0) acmlog("kmsg replay seek", errno);
        }
        if (kfd >= 0) for (reads = 0; reads < 256; ++reads) {
            ssize_t n = read(kfd, record, sizeof(record));
            size_t i;
            if (n < 0 && errno == EPIPE) {
                acmlog("kernel ring overrun; earlier records lost", EPIPE);
                continue;
            }
            if (n <= 0) break;
            for (i = 0; i < (size_t)n; ++i) history[(end++) % HISTORY_SIZE] = (unsigned char)record[i];
        }
        if (stream >= 0) for (reads = 0; reads < 256; ++reads) {
            ssize_t n = read(stream, record, sizeof(record));
            size_t i;
            if (n <= 0) break;
            for (i = 0; i < (size_t)n; ++i) history[(end++) % HISTORY_SIZE] = (unsigned char)record[i];
        }
        if (!configured && !retry) {
            if (!(stock ? setup_stock_acm() : setup_acm(0))) { configured = 1; previous_error = -1; }
            else {
                int error = errno;
                if (error != previous_error) acmlog(stock ? "waiting for stock android_usb ACM" : "waiting for configfs/free MUSB UDC", error);
                previous_error = error;
                retry = 25; /* 5 seconds, no retry spin. */
            }
        }
        /* A7-3：复合升级状态机步进（stock 走 android_usb 老路径，不参与；
         * 旧的 adb_missing_cycles/ep0 存在性回退已按审计 A7-2/A7-c 删除，
         * 回退判定由状态机在 ready/UDC 层面完成，不再依赖 ep0）。 */
        if (configured && !stock) diag_upgrade_step(&tty, &streaming, &retry);
        if (retry) --retry;
        if (configured && tty < 0 && !retry) {
            if (stock && ensure_tty_node("ttyGS0") < 0) {
                acmlog("waiting for stock ttyGS0 sysfs device number", errno);
                retry = 5;
            } else tty = open("/dev/ttyGS0", O_RDWR | O_NONBLOCK | O_NOCTTY | O_CLOEXEC);
            if (tty >= 0) {
                struct termios term;
                if (!tcgetattr(tty, &term)) {
                    cfmakeraw(&term);
                    (void)tcsetattr(tty, TCSANOW, &term);
                }
                streaming = 0;
                warning_size = warning_sent = 0;
                sent = end > HISTORY_SIZE ? end - HISTORY_SIZE : 0;
            }
            else retry = 5;
        }
        if (tty >= 0) {
            unsigned attempts;
            for (attempts = 0; attempts < 4; ++attempts) {
                unsigned char command[64];
                ssize_t n = read(tty, command, sizeof(command));
                ssize_t i;
                if (n > 0) {
                    for (i = 0; i < n; ++i) if (command[i] == 'R') {
                        sent = end > HISTORY_SIZE ? end - HISTORY_SIZE : 0;
                        streaming = 1;
                        warning_size = warning_sent = 0;
                    } else if (command[i] == 'T' && streaming && tombstone_child < 0) {
                        tombstone_child = fork();
                        if (tombstone_child == 0) {
                            close(tty);
                            if (stream >= 0) close(stream);
                            if (kfd >= 0) close(kfd);
                            _exit(tombstone_dump());
                        }
                    } else if (command[i] == 'S' && streaming && status_child < 0) {
                        status_child = fork();
                        if (status_child == 0) {
                            (void)setpgid(0, 0);
                            close(tty);
                            if (stream >= 0) close(stream);
                            if (kfd >= 0) close(kfd);
                            _exit(status_dump());
                        }
                        if (status_child > 0) {
                            (void)setpgid(status_child, status_child);
                            status_deadline = monotonic_ms() + STATUS_TIMEOUT_MS;
                        } else if (stream >= 0) {
                            status_emit(stream, "END result=FORK_ERROR errno=%d retry=S", errno);
                        }
                    }
                } else {
                    if (n < 0 && errno != EAGAIN && errno != EINTR) {
                        close(tty);
                        tty = -1;
                        streaming = 0;
                        retry = 5;
                    }
                    break;
                }
            }
        }
        if (sent < end && end - sent > HISTORY_SIZE) {
            if (tty >= 0 && streaming) {
                ssize_t n;
                if (!warning_size) {
                    warning_target = end - HISTORY_SIZE;
                    warning_size = (size_t)snprintf(warning, sizeof(warning),
                        "[z1acm] history overrun skipped_bytes=%llu; replay window=%u\n",
                        (unsigned long long)(warning_target - sent), HISTORY_SIZE);
                    warning_sent = 0;
                }
                n = write(tty, warning + warning_sent, warning_size - warning_sent);
                if (n > 0) warning_sent += (size_t)n;
                else if (n < 0 && errno != EAGAIN && errno != EINTR) {
                    close(tty);
                    tty = -1;
                    streaming = 0;
                    retry = 5;
                    warning_size = warning_sent = 0;
                }
                if (!streaming || warning_sent != warning_size) {
                    /* Do not silently discard the warning on a blocked USB. */
                    (void)poll(NULL, 0, 200);
                    continue;
                }
                sent = warning_target;
                warning_size = warning_sent = 0;
                if (end - sent > HISTORY_SIZE) continue;
            } else {
                sent = end - HISTORY_SIZE;
            }
        }
        if (tty >= 0 && streaming && sent < end) {
            size_t start = (size_t)(sent % HISTORY_SIZE), count = (size_t)(end - sent);
            ssize_t n;
            if (count > HISTORY_SIZE - start) count = HISTORY_SIZE - start;
            n = write(tty, history + start, count);
            if (n > 0) sent += (uint64_t)n;
            else if (n < 0 && errno != EAGAIN && errno != EINTR) {
                close(tty);
                tty = -1;
                streaming = 0;
                retry = 5;
            }
        }
        /* Always yield; stock alone consumes /proc/kmsg, modern uses /dev/kmsg. */
        waitfd.fd = -1;
        waitfd.events = 0;
        (void)poll(&waitfd, 0, 200);
    }
    return 0;
}


/* Only userspace log buffers: kernel is excluded to prevent relay feedback. */
static int logcat_main(void)
{
    for (;;) {
        int channels[2], status, stream;
        stream = stream_open();
        if (stream < 0) { sleep(1); continue; }
        pid_t child;
        if (pipe2(channels, O_CLOEXEC) < 0) {
            acmlog("logcat pipe failed", errno);
            close(stream);
            sleep(5);
            continue;
        }
        child = fork();
        if (child < 0) {
            acmlog("logcat fork failed", errno);
            close(channels[0]);
            close(channels[1]);
            close(stream);
            sleep(5);
            continue;
        }
        if (!child) {
            close(channels[0]);
            if (dup2(channels[1], STDOUT_FILENO) < 0 ||
                dup2(channels[1], STDERR_FILENO) < 0) _exit(126);
            close(channels[1]);
            execl("/system/bin/logcat", "logcat", "-b", "main", "-b", "system",
                  "-b", "crash", "-v", "threadtime", (char *)NULL);
            _exit(127);
        }
        close(channels[1]);
        {
            int result = stream_text(stream, "z1logcat", channels[0], 0, NULL);
            close(channels[0]);
            close(stream);
            if (result) (void)kill(child, SIGTERM);
        }
        while (waitpid(child, &status, 0) < 0) {
            if (errno != EINTR) { status = -1; break; }
        }
        acmlog("logcat exited; retry in 5s", status);
        sleep(5);
    }
    return 0;
}

int main(int argc, char **argv)
{
    static const char *const paths[] = {
        "/system/bin/mkswap", "/system/bin/toybox", "/system/bin/linker",
        "/dev/block/mmcblk0p4", "/dev/mmcblk0p4"
    };
    size_t i;
    logfd = open("/dev/kmsg", O_WRONLY | O_CLOEXEC);
    if (argc == 2 && !strcmp(argv[1], "--acm")) return acm_main(0);
    if (argc == 2 && !strcmp(argv[1], "--acm-stock")) return acm_main(1);
    if (argc == 2 && !strcmp(argv[1], "--stock-partitions")) return stock_partitions();
    if (argc == 2 && !strcmp(argv[1], "--graphics-mode")) return graphics_mode();
    if (argc == 2 && !strcmp(argv[1], "--logcat")) return logcat_main();
    if (argc != 1) return 2;
    logmsg("BEGIN read-only boot evidence pid=%ld", (long)getpid());
    dumptext("/proc/cmdline");
    dumptext("/proc/mounts");
    dumptext("/sys/class/block/mmcblk0p4/start");
    dumptext("/sys/class/block/mmcblk0p4/size");
    for (i = 0; i < sizeof(paths) / sizeof(paths[0]); ++i) pathstat(paths[i]);
    ext4magic("/dev/block/mmcblk0p4");
    ext4magic("/dev/mmcblk0p4");
    logmsg("END read-only boot evidence");
    if (logfd >= 0) close(logfd);
    return 0;
}
