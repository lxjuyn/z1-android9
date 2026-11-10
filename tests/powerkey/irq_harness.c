/* Host-only regression for the extracted production PMIC IRQ handler.
 * Adapted from bridge_backups/pwrkey_audit_20261110_205314/pwrkey_fault_test.c.
 * The handler is never cloned here. run_regression.py supplies its exact text.
 * These mocks test handler control flow, not kernel ABI or physical hardware.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t u32;
typedef int irqreturn_t;
#define IRQ_HANDLED 1
struct mtk_pmic_keys_regs { u32 deb_reg; u32 deb_mask; };
struct mtk_pmic_keys { void *regmap; void *input_dev; void *dev; };
struct mtk_pmic_keys_info {
    struct mtk_pmic_keys *keys;
    const struct mtk_pmic_keys_regs *regs;
    unsigned int keycode;
};
static int read_ret, reports, syncs, errors, bad_access, reads, event_count;
static int events[16], poison_on_error;
static u32 sample;
static int map_token, input_token, dev_token;
static int regmap_read(void *map, u32 reg, u32 *out) {
    reads++;
    if (map != &map_token || reg != 0x142) bad_access++;
    if (read_ret) {
        /* Negative controls always initialize output, avoiding undefined
         * behavior when deliberately testing the old unchecked handler. */
#ifdef NEGATIVE_CONTROL
        *out = sample;
#else
        if (poison_on_error) *out = sample;
#endif
        return read_ret;
    }
    *out = sample;
    return 0;
}
static void input_report_key(void *input, unsigned int code, int value) {
    if (input != &input_token || code != 116) bad_access++;
    reports++;
    if (event_count < 16) events[event_count++] = value;
    else bad_access++;
}
static void input_sync(void *input) {
    if (input != &input_token) bad_access++;
    syncs++;
}
#define dev_dbg(...) ((void)0)
#define dev_err_ratelimited(...) (++errors)
#include "handler.inc"
static void reset(void) {
    reports = syncs = errors = bad_access = reads = event_count = 0;
    memset(events, -1, sizeof(events));
}
int main(void) {
    struct mtk_pmic_keys keys = {&map_token, &input_token, &dev_token};
    const struct mtk_pmic_keys_regs regs = {0x142, 2};
    struct mtk_pmic_keys_info info = {&keys, &regs, 116};
    const struct {
        const char *name; int ret; u32 val; int poison; int expected;
    } cases[] = {
        {"pressed", 0, 0, 0, 1},
        {"released", 0, 2, 0, 0},
        {"pressed-other-bits", 0, 0xfffd, 0, 1},
        {"released-other-bits", 0, 0xffff, 0, 0},
        {"timeout-output-untouched", -110, 0, 0, -1},
        {"io-error-output-untouched", -5, 0, 0, -1},
        {"retry-output-untouched", -11, 0, 0, -1},
        {"io-error-poison-pressed", -5, 0, 1, -1},
        {"io-error-poison-released", -5, 2, 1, -1},
        {"timeout-poison-other-bits", -110, 0xffff, 1, -1},
    };
    int failed = 0;
    for (unsigned i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
        reset(); read_ret = cases[i].ret; sample = cases[i].val;
        poison_on_error = cases[i].poison;
        int result = mtk_pmic_keys_irq_handler_thread(46, &info);
        int wanted = cases[i].expected >= 0;
        int good = result == IRQ_HANDLED && reads == 1 && !bad_access
            && reports == wanted && syncs == wanted && event_count == wanted
            && errors == (read_ret ? 1 : 0);
        if (wanted) good = good && events[0] == cases[i].expected;
        printf("%s: %s reads=%d reports=%d syncs=%d errors=%d\n",
               cases[i].name, good ? "PASS" : "FAIL", reads, reports, syncs, errors);
        failed += !good;
    }
    reset(); poison_on_error = 0;
    const int rc[] = {0, -110, 0, -5, 0};
    const u32 values[] = {2, 0, 0, 0, 2};
    int returns_ok = 1;
    for (unsigned i = 0; i < 5; ++i) {
        read_ret = rc[i]; sample = values[i];
        returns_ok &= mtk_pmic_keys_irq_handler_thread(46, &info) == IRQ_HANDLED;
    }
    int good = returns_ok && reads == 5 && event_count == 3 && reports == 3
        && events[0] == 0 && events[1] == 1 && events[2] == 0
        && syncs == 3 && errors == 2 && !bad_access;
    printf("release/timeout/press/io-error/release recovery: %s reports=%d syncs=%d errors=%d\n",
           good ? "PASS" : "FAIL", reports, syncs, errors);
    failed += !good;
    printf("failed_cases=%d total_cases=11\n", failed);
    return failed ? 1 : 0;
}
