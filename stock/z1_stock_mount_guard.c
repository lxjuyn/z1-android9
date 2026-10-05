/* Stock ramdisk mount guard. No /system executable or Android shared library. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/time.h>
#include <sys/un.h>
#include <unistd.h>

#define READY_PROPERTY "sys.z1.stock_mount_ready"
static void logline(const char *text) {
    fprintf(stderr, "%s\n", text);
#ifndef Z1_MOUNT_GUARD_TEST
    int fd = open("/dev/kmsg", O_WRONLY | O_CLOEXEC | O_NONBLOCK);
    if (fd >= 0) { dprintf(fd, "<3>%s\n", text); close(fd); }
#endif
}
static int fail(const char *what) {
    char message[256];
    snprintf(message, sizeof(message), "Z1 stock mount guard: BLOCKED %s errno=%d", what, errno);
    logline(message);
    return -1;
}

/* Check mountinfo's device, filesystem root and type, not directory existence. */
static int mount_row(const char *line, const char *path, unsigned maj, unsigned min) {
    char root[256], point[256], type[32];
    unsigned a, b;
    const char *separator = strstr(line, " - ");
    if (!separator || sscanf(line, "%*u %*u %u:%u %255s %255s", &a, &b, root, point) != 4 ||
        sscanf(separator + 3, "%31s", type) != 1) return 0;
    return a == maj && b == min && !strcmp(root, "/") &&
           !strcmp(point, path) && !strcmp(type, "ext4");
}

static int mounted(const char *path, const char *alias) {
    struct stat device, directory;
    char line[4096];
    int found = 0;
    FILE *mounts;
    if (stat(alias, &device) || !S_ISBLK(device.st_mode) ||
        stat(path, &directory) || !S_ISDIR(directory.st_mode) ||
        directory.st_dev != device.st_rdev) return fail(path);
    mounts = fopen("/proc/self/mountinfo", "re");
    if (!mounts) return fail("mountinfo");
    while (fgets(line, sizeof(line), mounts)) {
        if (!strchr(line, '\n')) { fclose(mounts); errno = EOVERFLOW; return fail("mountinfo row"); }
        if (mount_row(line, path, major(device.st_rdev), minor(device.st_rdev))) found = 1;
    }
    if (ferror(mounts)) { fclose(mounts); return fail("mountinfo read"); }
    fclose(mounts);
    if (!found) { errno = ENODEV; return fail(path); }
    return 0;
}

static int required_file(const char *path, int executable, dev_t filesystem) {
    struct stat st;
    unsigned char elf[20];
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NONBLOCK);
    if (fd < 0) return fail(path);
    if (fstat(fd, &st) || !S_ISREG(st.st_mode) || !st.st_size || st.st_dev != filesystem ||
        (executable && (!(st.st_mode & 0111) || read(fd, elf, sizeof(elf)) != sizeof(elf) ||
         memcmp(elf, "\177ELF\1\1", 6) || elf[18] != 40 || elf[19]))) {
        close(fd); errno = EINVAL; return fail(path);
    }
    close(fd);
    return 0;
}

static int verify(void) {
    static const char *bins[] = {"/system/bin/app_process", "/system/bin/linker", "/system/bin/sh"};
    static const char *framework[] = {"/system/framework/arm/boot.art", "/system/framework/arm/boot.oat",
        "/system/framework/framework.jar", "/system/framework/services.jar"};
    char vendor[PATH_MAX];
    struct stat system, vd;
    size_t i;
    if (mounted("/system", "/dev/block/z1-stock-system") ||
        mounted("/cache", "/dev/block/z1-stock-cache") ||
        mounted("/data", "/dev/block/z1-stock-data")) return -1;
    if (stat("/system", &system) || !realpath("/vendor", vendor) ||
        strcmp(vendor, "/system/vendor") || stat(vendor, &vd) ||
        !S_ISDIR(vd.st_mode) || vd.st_dev != system.st_dev) {
        errno = EINVAL; return fail("/vendor must resolve to mounted /system/vendor");
    }
    for (i = 0; i < sizeof(bins)/sizeof(bins[0]); ++i)
        if (required_file(bins[i], 1, system.st_dev)) return -1;
    for (i = 0; i < sizeof(framework)/sizeof(framework[0]); ++i)
        if (required_file(framework[i], 0, system.st_dev)) return -1;
    return 0;
}

static int transfer(int fd, void *buffer, size_t bytes, int receive) {
    unsigned char *p = buffer;
    while (bytes) {
        ssize_t n = receive ? recv(fd, p, bytes, 0) : send(fd, p, bytes, MSG_NOSIGNAL);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return -1;
        p += n; bytes -= (size_t)n;
    }
    return 0;
}

/* Pie PROP_MSG_SETPROP2 with acknowledgement; timeout/failure never opens gate. */
static int publish(void) {
    struct sockaddr_un address = {.sun_family = AF_UNIX};
    struct timeval timeout = {.tv_sec = 2};
    uint32_t command = 0x00020001, name_length = sizeof(READY_PROPERTY)-1, value_length = 1, reply = ~0U;
    char name[] = READY_PROPERTY, value = '1';
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0), result;
    if (fd < 0) return fail("property socket");
    strcpy(address.sun_path, "/dev/socket/property_service");
    result = setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout)) ||
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) ||
        connect(fd, (struct sockaddr *)&address, sizeof(address)) ||
        transfer(fd, &command, sizeof(command), 0) || transfer(fd, &name_length, sizeof(name_length), 0) ||
        transfer(fd, name, name_length, 0) || transfer(fd, &value_length, sizeof(value_length), 0) ||
        transfer(fd, &value, 1, 0) || transfer(fd, &reply, sizeof(reply), 1) || reply;
    close(fd);
    if (result) { errno = EACCES; return fail("readiness property not acknowledged"); }
    return 0;
}

#ifndef Z1_MOUNT_GUARD_TEST
int main(int argc, char **argv) {
    int verify_only = argc == 2 && !strcmp(argv[1], "--verify-only");
    if (argc != 1 && !verify_only) return 2;
    if (verify()) return 1;
    if (!verify_only && publish()) return 1;
    logline(verify_only ? "Z1 stock mount guard: verified system/cache/data, vendor and boot files (no property written)" :
            "Z1 stock mount guard: verified system/cache/data, vendor and boot files; readiness=1");
    return 0;
}
#endif
