# R7 WMT PSM during first radio function-on

The public WMT function-control path disables the PSM monitor before
submitting its command to the WMT worker. On the first function-on, the
worker powers up CONNSYS and calls the SoC software initializer. That
initializer used to re-enable PSM inside the transaction, before the
BT function-on command or its reply. The monitor's default idle period
is 30 ms.

The patch passes the existing function-on operation's intent into the
SoC initializer so the outer function-control path resumes monitoring
after it returns from waiting. A direct power-on and the software-reset
path still follow the normal SoC policy. The patch also keeps the
disable operation for chips without PSM support. WMT's existing
caller-timeout and operation lifetime behavior are outside this change.

## Reproduce the module

Use radio source at https://github.com/gabin8/mt6572-connsys-modules/tree/165df1b4a0c6ac767c7e1eaf687494cace78480c.
The GitHub codeload archive SHA-256 is
`c13efc3fa76127c49c8db0335794256e99ec6a93f0bf8a3d62e7f7f5934813af`.
Prepare the R7 kernel build directory with the config, symbol table and
kernel commit recorded in the product archive. Use ARM cross GCC
13.3.0. From the root of the extracted radio sources, with `WIFI_DIR` set
to the absolute path of this repository's `wifi` directory and `KERNEL_DIR`
set to the prepared kernel build:

```sh
set -e
for change in compat_wakeup_osal_c.diff compat_wakeup_osal_h.diff \
    compat_wakeup_wmt_plat_alps.diff emi_base_0xbe080000.diff; do
    diff="$WIFI_DIR/conn_soc_wmt_emi/$change"
    src=$(sed -n '1s/^--- \([^[:space:]]*\).*/\1/p' "$diff")
    patch --batch --fuzz=0 "${src%.orig}" "$diff"
done
make -C "$KERNEL_DIR" M="$PWD/conn_soc" ARCH=arm \
    CROSS_COMPILE=arm-linux-gnueabihf- KBUILD_MODPOST_WARN=1 modules
arm-linux-gnueabihf-strip --strip-debug -o baseline.ko conn_soc/mtk_stp_wmt_soc.ko
patch --batch --fuzz=0 -p1 -i "$WIFI_DIR/conn_soc_psm/0001-defer-nested-psm-enable.patch"
python3 "$WIFI_DIR/conn_soc_psm/test_defer_psm.py" "$PWD"
make -C "$KERNEL_DIR" M="$PWD/conn_soc" ARCH=arm \
    CROSS_COMPILE=arm-linux-gnueabihf- KBUILD_MODPOST_WARN=1 modules
arm-linux-gnueabihf-strip --strip-debug -o candidate.ko conn_soc/mtk_stp_wmt_soc.ko
```

The rebuilt baseline matches the installed livegain module's allocatable
ELF sections byte for byte and all 10,105 semantic relocation records.
Its whole-file hash differs because of non-allocatable ELF symbol and
string metadata. The candidate has the same module imports, exports and
vermagic. The test exercises the SoC PSM policy extracted from the
patched C file and checks the enclosing WMT call sequence.

Historical failures were recorded with Wi-Fi already active. This
patch tests a specific race during the first radio activation, so use
separate cold boots and the same probe-free image pair to compare
baseline and candidate modules. Keep Android Wi-Fi and Bluetooth off
while booting. Run the first-enable test over UART and compare WMT
request/reply, Android ON state and successful BT/Wi-Fi coexistence.
Hardware verification is pending.

The local build archive is
`bridge_backups/products/products_r7_wmt_nested_psm_20261125_163525.vx97bRpm/`.
The archived candidate SHA-256 is
`209a0022a1687fa5c36e913bfab0dc56ba0efbbd4323b86c4d70718278464cc0`.
