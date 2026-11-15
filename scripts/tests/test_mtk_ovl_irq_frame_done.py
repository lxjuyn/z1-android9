#!/usr/bin/env python3
"""Fault-inject the actual OVL IRQ handler with mocked register I/O.

No board or kernel build is required. The handler and register constants are
extracted from the kernel source, then compiled into a temporary host harness.
Pass --source to check a different kernel source or an archived pre-fix file.
"""

import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


def extract_handler(source):
    signature = "static irqreturn_t mtk_disp_ovl_irq_handler("
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def main():
    workspace = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path,
        default=workspace / "fork_linux/drivers/gpu/drm/mediatek/mtk_disp_ovl.c",
    )
    args = parser.parse_args()
    source = args.source.read_text()
    constants = "\n".join(re.findall(
        r"^#define\s+(?:DISP_REG_OVL_INTSTA|OVL_[A-Z0-9_]+)\s+"
        r"(?:0x[0-9a-fA-F]+|BIT\(\d+\))\s*$", source, re.MULTILINE,
    ))
    harness = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>

typedef uint32_t u32;
typedef int irqreturn_t;
#define IRQ_NONE 0
#define IRQ_HANDLED 1
#define BIT(n) (1u << (n))
CONSTANTS

struct mtk_disp_ovl {
    unsigned char *regs;
    void (*vblank_cb)(void *);
    void *vblank_cb_data;
};

static unsigned char registers[16];
static u32 status;
static unsigned int reads, writes, callbacks, errors;

static u32 readl(void *addr)
{
    assert(addr == registers + DISP_REG_OVL_INTSTA);
    reads++;
    return status;
}

static void writel(u32 value, void *addr)
{
    assert(addr == registers + DISP_REG_OVL_INTSTA);
    assert(value == 0);
    writes++;
}

static void pr_err(const char *format, ...)
{
    assert(format != NULL);
    errors++;
}

static void vblank(void *data)
{
    assert(data == &callbacks);
    assert(writes == 1); /* The status must be acknowledged first. */
    callbacks++;
}

HANDLER

static void check(const char *name, u32 injected, int with_callback,
                  int expected_irq, unsigned int expected_writes,
                  unsigned int expected_callbacks, unsigned int expected_errors)
{
    struct mtk_disp_ovl ovl = {
        registers, with_callback ? vblank : NULL, &callbacks
    };
    status = injected;
    reads = writes = callbacks = errors = 0;
    int ret = mtk_disp_ovl_irq_handler(7, &ovl);
    if (ret != expected_irq || reads != 1 || writes != expected_writes ||
        callbacks != expected_callbacks || errors != expected_errors) {
        fprintf(stderr, "FAIL %s: irq=%d reads=%u writes=%u callbacks=%u errors=%u\n",
                name, ret, reads, writes, callbacks, errors);
        assert(0);
    }
    printf("PASS %s\n", name);
}

int main(void)
{
    const u32 error_bits[] = {
        OVL_FME_UND, OVL_RDMA0_EOF_ABNORMAL, OVL_RDMA1_EOF_ABNORMAL,
        OVL_RDMA0_FIFO_UND, OVL_RDMA1_FIFO_UND
    };
    const u32 all_errors = OVL_FME_UND | OVL_RDMA0_EOF_ABNORMAL |
        OVL_RDMA1_EOF_ABNORMAL | OVL_RDMA0_FIFO_UND | OVL_RDMA1_FIFO_UND;

    check("zero", 0, 1, IRQ_NONE, 0, 0, 0);
    check("frame-only", OVL_FME_CPL_INT, 1, IRQ_HANDLED, 1, 1, 0);
    for (unsigned int i = 0; i < sizeof(error_bits) / sizeof(error_bits[0]); i++) {
        char name[64];
        snprintf(name, sizeof(name), "error-only bit %u", i);
        check(name, error_bits[i], 1, IRQ_HANDLED, 1, 0, 1);
        snprintf(name, sizeof(name), "frame+error bit %u", i);
        check(name, OVL_FME_CPL_INT | error_bits[i], 1, IRQ_HANDLED, 1, 1, 1);
    }
    check("all-errors", all_errors, 1, IRQ_HANDLED, 1, 0, 5);
    check("frame+all-errors", OVL_FME_CPL_INT | all_errors,
          1, IRQ_HANDLED, 1, 1, 5);
    check("zero/no-callback", 0, 0, IRQ_NONE, 0, 0, 0);
    check("frame/no-callback", OVL_FME_CPL_INT, 0, IRQ_HANDLED, 1, 0, 0);
    check("error/no-callback", OVL_FME_UND, 0, IRQ_HANDLED, 1, 0, 1);
    check("unknown-status", BIT(31), 1, IRQ_HANDLED, 1, 0, 0);
    return 0;
}
'''.replace("CONSTANTS", constants).replace("HANDLER", extract_handler(source))
    with tempfile.TemporaryDirectory(prefix="z1_ovl_irq_test_") as temp:
        c_file = Path(temp) / "ovl_irq_test.c"
        executable = Path(temp) / "ovl_irq_test"
        c_file.write_text(harness)
        subprocess.run(
            shlex.split(os.environ.get("HOSTCC", "cc")) + [
                "-std=gnu11", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", str(c_file), "-o", str(executable),
            ], check=True,
        )
        subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    main()
