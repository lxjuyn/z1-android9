/* Read selected MT6323/MT6572 regmap debugfs lines without any write path. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

struct map_spec {
    const char *label, *dir, *name, *range;
    unsigned int stride, reg_width, value_width, line_len;
    const unsigned int *regs;
    size_t nregs;
};

static const unsigned int pmic_regs[] = {
    0x52,0x54,0x56,0x58,0x5a,0x5c,0x5e,0x60,0x62,0x64,0x66,0x68,0x6a,
    0x700,0x702,0x704,0x706,0x708,0x70a,0x70c,0x70e,0x710,0x712,
    0x802,0x804, 0x102,0x108,0x126,
    0x4000,0x4002,0x4004,0x4006,0x4008,0x400a,0x400c,0x400e,
    0x4010,0x4012,0x4014,0x4016,0x401e,0x4024,0x4026,0x4028,0x402a
};
static const unsigned int afe_regs[] = {
    0x0,0x4,0x8,0x10,0x14,0x24,0x28,0x34,0x40,0x44,0x48,
    0x108,0x10c,0x124,0x260,0x264,0x4c4
};

static int read_exact(int fd, void *buf, size_t len, off_t off)
{
    ssize_t n = pread(fd, buf, len, off);
    if (n < 0) {
        fprintf(stderr, "pread offset %lld: %s\n", (long long)off, strerror(errno));
        return -1;
    }
    if ((size_t)n != len) {
        fprintf(stderr, "short read offset %lld: %zd/%zu\n", (long long)off, n, len);
        return -1;
    }
    return 0;
}

static int read_text(const char *path, char *buf, size_t cap)
{
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    ssize_t n;
    if (fd < 0) {
        fprintf(stderr, "open %s: %s\n", path, strerror(errno));
        return -1;
    }
    n = read(fd, buf, cap - 1);
    close(fd);
    if (n < 0 || (size_t)n >= cap - 1) {
        fprintf(stderr, "invalid metadata %s\n", path);
        return -1;
    }
    buf[n] = '\0';
    return 0;
}

static int metadata_ok(const struct map_spec *m)
{
    char path[256], text[64], expected[64];
    snprintf(path, sizeof(path), "%s/name", m->dir);
    snprintf(expected, sizeof(expected), "%s\n", m->name);
    if (read_text(path, text, sizeof(text)) < 0 || strcmp(text, expected)) {
        fprintf(stderr, "%s: name metadata mismatch\n", m->label);
        return -1;
    }
    snprintf(path, sizeof(path), "%s/range", m->dir);
    snprintf(expected, sizeof(expected), "0-%s\n", m->range);
    if (read_text(path, text, sizeof(text)) < 0 || strcmp(text, expected)) {
        fprintf(stderr, "%s: range metadata mismatch\n", m->label);
        return -1;
    }
    return 0;
}

static int hex_digit(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static int parse_fixed_hex(const char *s, unsigned int width, unsigned int *out)
{
    unsigned int v = 0;
    for (unsigned int i = 0; i < width; ++i) {
        int d = hex_digit(s[i]);
        if (d < 0) return -1;
        v = (v << 4) | (unsigned int)d;
    }
    *out = v;
    return 0;
}

static int read_map(const struct map_spec *m)
{
    char path[256], *line;
    int fd, rc = -1;
    snprintf(path, sizeof(path), "%s/registers", m->dir);
    fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        fprintf(stderr, "open %s: %s\n", path, strerror(errno));
        return -1;
    }
    line = malloc(m->line_len);
    if (!line) { close(fd); return -1; }
    for (size_t i = 0; i < m->nregs; ++i) {
        unsigned int got_addr, value;
        off_t off = (off_t)(m->regs[i] / m->stride) * (off_t)m->line_len;
        if (read_exact(fd, line, m->line_len, off) < 0) goto out;
        if (line[m->reg_width] != ':' || line[m->reg_width + 1] != ' ' ||
            line[m->line_len - 1] != '\n' ||
            parse_fixed_hex(line, m->reg_width, &got_addr) < 0 ||
            parse_fixed_hex(line + m->reg_width + 2, m->value_width, &value) < 0 ||
            got_addr != m->regs[i]) {
            fprintf(stderr, "%s: malformed/mismatched line at 0x%x\n", m->label, m->regs[i]);
            goto out;
        }
        printf("%s 0x%0*x: 0x%0*x\n", m->label, m->reg_width, got_addr,
               m->value_width, value);
    }
    rc = 0;
out:
    free(line);
    close(fd);
    return rc;
}

int main(void)
{
    static const struct map_spec maps[] = {
        { "PMIC", "/sys/kernel/debug/regmap/1000f000.pwrap", "mt-pmic-pwrap", "fffe", 2, 4, 4, 11,
          pmic_regs, sizeof(pmic_regs) / sizeof(pmic_regs[0]) },
        { "AFE", "/sys/kernel/debug/regmap/11140000.audio", "mt6572-afe-pcm", "ffc", 4, 3, 8, 14,
          afe_regs, sizeof(afe_regs) / sizeof(afe_regs[0]) },
    };
    for (size_t i = 0; i < sizeof(maps) / sizeof(maps[0]); ++i)
        if (metadata_ok(&maps[i]) < 0) return 1;
    for (size_t i = 0; i < sizeof(maps) / sizeof(maps[0]); ++i)
        if (read_map(&maps[i]) < 0) return 1;
    puts("REGREAD_STATUS=OK READ_ONLY=1");
    return 0;
}
