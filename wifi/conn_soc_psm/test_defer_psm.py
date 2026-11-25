#!/usr/bin/env python3
"""Exercise the patched SoC PSM policy and verify its WMT call sites."""
import argparse
from pathlib import Path
import subprocess
import tempfile


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("source", type=Path, help="patched radio source root")
    root = args.parse_args().source / "conn_soc"
    core = (root / "core/wmt_core.c").read_text()
    soc = (root / "core/wmt_ic_soc.c").read_text()
    api = (root / "linux/pri/wmt_exp.c").read_text()
    init_start = soc.index("bool defer_psm)\n{")
    init_end = soc.index("static INT32 mtk_wcn_soc_sw_deinit(", init_start + 1)
    assertion = soc.rindex("osal_assert(NULL != gp_soc_info);", init_start, init_end)
    policy = soc[assertion:soc.index("#endif", assertion)]
    assert assertion < init_end, "expected PSM policy in SoC software init"

    assert "wmt_core_stp_init(defer_psm);" in core
    assert "(&pctx->wmtHifConf, defer_psm);" in core
    assert "return wmt_core_pwr_on(pWmtOp, false);" in core
    assert "iRet = wmt_core_pwr_on(pWmtOp, true);" in core
    assert "iRet = wmt_core_stp_init(false);" in core
    start = api.index("if (DISABLE_PSM_MONITOR())")
    submit = api.index("bRet = wmt_lib_put_act_op(pOp);", start)
    resume = api.index("ENABLE_PSM_MONITOR();", submit)
    assert start < submit < resume

    source = """
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#define osal_assert assert
#define MTK_WCN_BOOL_FALSE 0
static struct { bool bPsmSupport; } chip, *gp_soc_info = &chip;
static int enables, disables;
static void wmt_lib_ps_enable(void) { ++enables; }
static void wmt_lib_ps_disable(void) { ++disables; }
static void soc_init(bool defer_psm) {
""" + policy + """
}
int main(void) {
    chip.bPsmSupport = true;
    soc_init(true);
    assert(enables == 0 && disables == 0);
    wmt_lib_ps_enable();
    assert(enables == 1 && disables == 0);
    soc_init(false);
    assert(enables == 2 && disables == 0);
    chip.bPsmSupport = false;
    soc_init(true);
    soc_init(false);
    assert(enables == 2 && disables == 2);
    return 0;
}
"""
    with tempfile.TemporaryDirectory(prefix="z1-wmt-psm-test-") as build:
        test = Path(build) / "test"
        subprocess.run(
            ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-x", "c",
             "-", "-o", str(test)],
            input=source, text=True, check=True,
        )
        subprocess.run([test], check=True)
    print("WMT nested power-on, standalone startup, reset and no-PSM policy checks passed.")


if __name__ == "__main__":
    main()
