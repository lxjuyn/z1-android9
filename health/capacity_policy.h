#ifndef Z1_CAPACITY_POLICY_H
#define Z1_CAPACITY_POLICY_H
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <unistd.h>
#include <ctype.h>

// Distinguish an unavailable state of charge from a measured zero percent.
// No replacement capacity or temperature is invented.
static inline bool z1_capacity_available(const char* path) {
    if (!path || !*path) return false;
    int fd;
    do { fd = open(path, O_RDONLY | O_CLOEXEC); } while(fd < 0 && errno == EINTR);
    if(fd < 0) return false;
    char text[64];
    ssize_t count;
    do { count = read(fd, text, sizeof(text)-1); } while(count < 0 && errno == EINTR);
    close(fd);
    if(count <= 0 || count == ssize_t(sizeof(text)-1)) return false;
    text[count] = '\0';
    errno = 0;
    char* end;
    const long value = strtol(text, &end, 10);
    if(errno || end == text || value < 0 || value > 100) return false;
    while(*end && isspace(static_cast<unsigned char>(*end))) ++end;
    return *end == '\0';
}

template<typename Properties>
static inline void z1_mark_capacity_unknown(Properties* props, bool available) {
    if (!available) props->batteryStatus = 1; // BATTERY_STATUS_UNKNOWN
}
#endif
