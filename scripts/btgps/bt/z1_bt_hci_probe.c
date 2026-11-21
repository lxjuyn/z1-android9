/* SPDX-License-Identifier: GPL-2.0-only */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

static int64_t monotonic_ms(void)
{
    struct timespec now;

    if (clock_gettime(CLOCK_MONOTONIC, &now) < 0)
        return -1;
    return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static int read_exact(int fd, uint8_t *buf, size_t len, int64_t deadline)
{
    while (len) {
        struct pollfd pfd = { .fd = fd, .events = POLLIN };
        int64_t now = monotonic_ms();
        if (now < 0)
            return -1;
        if (now >= deadline) {
            errno = ETIMEDOUT;
            return -1;
        }
        int ready = poll(&pfd, 1, (int)(deadline - now));
        if (ready < 0 && errno == EINTR)
            continue;
        if (ready <= 0 || !(pfd.revents & POLLIN)) {
            if (ready >= 0)
                errno = ready ? EIO : ETIMEDOUT;
            return -1;
        }
        ssize_t count = read(fd, buf, len);
        if (count < 0 && (errno == EAGAIN || errno == EINTR))
            continue;
        if (count <= 0) {
            if (!count)
                errno = EIO;
            return -1;
        }
        buf += count;
        len -= (size_t)count;
    }
    return 0;
}

static int command(int fd, uint16_t opcode, uint8_t expected_len)
{
    uint8_t tx[] = { 0x01, opcode & 0xff, opcode >> 8, 0 };
    uint8_t header[3], event[255];
    int64_t start = monotonic_ms();
    if (start < 0)
        return -1;
    /* The factory library writes each entire H4 command as one STP payload. */
    ssize_t written = write(fd, tx, sizeof(tx));
    if (written != (ssize_t)sizeof(tx)) {
        if (written >= 0)
            errno = EIO;
        return -1;
    }
    for (;;) {
        if (read_exact(fd, header, sizeof(header), start + 3000) < 0)
            return -1;
        if (header[0] != 0x04) {
            errno = EPROTO;
            return -1;
        }
        if (read_exact(fd, event, header[2], start + 3000) < 0)
            return -1;
        if (header[1] == 0x0f && header[2] == 4 &&
            event[2] == tx[1] && event[3] == tx[2] && event[0]) {
            fprintf(stderr, "HCI opcode 0x%04x command status 0x%02x\n", opcode, event[0]);
            errno = EIO;
            return -1;
        }
        if (header[1] != 0x0e || header[2] < 3 ||
            event[1] != tx[1] || event[2] != tx[2])
            continue;
        if (header[2] != expected_len) {
            errno = EPROTO;
            return -1;
        }
        if (event[3]) {
            fprintf(stderr, "HCI opcode 0x%04x completion status 0x%02x\n", opcode, event[3]);
            errno = EIO;
            return -1;
        }
        printf("PASS opcode=0x%04x\n", opcode);
        if (opcode == 0x1001)
            printf("hci_version=0x%02x manufacturer=0x%04x\n", event[4],
                   (unsigned)(event[8] | (event[9] << 8)));
        return 0;
    }
}

static int read_extended_features(int fd, uint8_t requested_page)
{
    uint8_t tx[] = { 0x01, 0x04, 0x10, 0x01, requested_page };
    uint8_t header[3], event[255];
    int64_t start = monotonic_ms();
    ssize_t written;

    if (start < 0)
        return -1;
    written = write(fd, tx, sizeof(tx));
    if (written != (ssize_t)sizeof(tx)) {
        if (written >= 0)
            errno = EIO;
        return -1;
    }
    for (;;) {
        if (read_exact(fd, header, sizeof(header), start + 3000) < 0)
            return -1;
        if (header[0] != 0x04) {
            errno = EPROTO;
            return -1;
        }
        if (read_exact(fd, event, header[2], start + 3000) < 0)
            return -1;
        if (header[1] == 0x0f && header[2] == 4 &&
            event[2] == tx[1] && event[3] == tx[2]) {
            printf("FEATURE requested=%u command_status=0x%02x\n",
                   requested_page, event[0]);
            return 0;
        }
        if (header[1] != 0x0e || header[2] < 3 ||
            event[1] != tx[1] || event[2] != tx[2])
            continue;
        if (header[2] < 4) {
            errno = EPROTO;
            return -1;
        }
        printf("FEATURE requested=%u status=0x%02x", requested_page, event[3]);
        if (header[2] >= 6)
            printf(" page=%u max_page=%u", event[4], event[5]);
        if (header[2] >= 14) {
            printf(" features=");
            for (size_t i = 6; i < 14; ++i)
                printf("%02x", event[i]);
        }
        putchar('\n');
        return 0;
    }
}

int main(int argc, char **argv)
{
    const char *path = "/dev/stpbt";
    struct stat st;
    int features = 0;

    if (argc < 2 || strcmp(argv[1], "--exclusive-controller") != 0 ||
        (argc == 3 && strcmp(argv[2], "--features") != 0) || argc > 3) {
        fprintf(stderr, "Stop Android Bluetooth and confirm there is no other STP BT opener.\n"
                        "Usage: %s --exclusive-controller [--features]\n", argv[0]);
        return 2;
    }
    if (argc == 3)
        features = 1;
    setvbuf(stdout, NULL, _IOLBF, 0);
    if (lstat(path, &st) < 0 || !S_ISCHR(st.st_mode)) {
        fprintf(stderr, "%s is not available as a character device\n", path);
        return 1;
    }
    puts("OPEN /dev/stpbt (WMT BT power-on)");
    int fd = open(path, O_RDWR | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) {
        perror(path);
        return 1;
    }
    int result = 1;
    if (command(fd, 0x0c03, 4) < 0)
        perror("HCI Reset");
    else if (command(fd, 0x1001, 12) < 0)
        perror("HCI Read Local Version Information");
    else if (features) {
        result = 0;
        for (uint8_t page = 0; page < 4; ++page) {
            if (read_extended_features(fd, page) < 0) {
                perror("HCI Read Local Extended Features");
                result = 1;
                break;
            }
        }
    } else
        result = 0;
    if (close(fd) < 0) {
        perror("close /dev/stpbt");
        result = 1;
    }
    return result;
}
