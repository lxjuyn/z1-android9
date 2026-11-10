#!/usr/bin/env python3
"""Run the actual driver read/report/poll functions with mocked kernel I/O.

No device access, guessed hardware registers, or generated calibration. All
frames below are synthetic event-contract tests, not measured button positions.
Usage: python3 test_cst836u.py [--source OLD.c] [--case TEST_NAME]
"""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

SHIM = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <linux/input-event-codes.h>
typedef uint8_t u8;
struct device { int unused; };
struct i2c_client { struct device dev; };
struct input_dev { int unused; };
struct work_struct { int unused; };
struct delayed_work { struct work_struct work; };
#define container_of(ptr, type, member) ((type *)((char *)(ptr) - offsetof(type, member)))
#define to_delayed_work(w) container_of(w, struct delayed_work, work)
#define min(a, b) ((a) < (b) ? (a) : (b))
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, v) ((x) = (v))
#define module_param(...)
#define MODULE_PARM_DESC(...)
struct event { int type, code, value; };
static struct event events[128];
static unsigned nevents, schedules, frame_logs, raw_logs, error_logs;
static unsigned reads;
static u8 frame[13];
static int read_result = 13;
static void event(int type, int code, int value) {
    assert(nevents < 128);
    events[nevents++] = (struct event){type, code, value};
}
static void input_report_key(struct input_dev *d, int code, int value) {
    (void)d; event(EV_KEY, code, value);
}
static void input_report_abs(struct input_dev *d, int code, int value) {
    (void)d; event(EV_ABS, code, value);
}
static void input_sync(struct input_dev *d) {
    (void)d; event(EV_SYN, SYN_REPORT, 0);
}
static int i2c_smbus_read_i2c_block_data(struct i2c_client *c, u8 reg, size_t len, u8 *buf) {
    (void)c; assert(reg == 0x02 && len == sizeof(frame));
    reads++; memcpy(buf, frame, len); return read_result;
}
static unsigned msecs_to_jiffies(unsigned n) { return n; }
static void schedule_delayed_work(struct delayed_work *w, unsigned n) {
    (void)w; (void)n; schedules++;
}
static void dev_info(struct device *d, const char *fmt, ...) {
    (void)d;
    if (!strncmp(fmt, "report 0x02", 11)) frame_logs++;
    else if (!strncmp(fmt, "raw sample", 10)) raw_logs++;
}
static void dev_err(struct device *d, const char *fmt, ...) {
    (void)d; (void)fmt; error_logs++;
}
'''
TESTS = r'''
static struct cst836u ts;
static struct i2c_client client;
static struct input_dev input;
static void reset(void) {
    memset(&ts, 0, sizeof(ts)); memset(frame, 0, sizeof(frame));
    ts.client = &client; ts.input = &input;
    nevents = schedules = reads = frame_logs = raw_logs = error_logs = 0;
    read_result = sizeof(frame); log_raw = 64; log_frames = 0;
    max_x = 240; max_y = 320; key_y_min = 300;
    key_x_split1 = 80; key_x_split2 = 160;
    flip_x = flip_y = swap_xy = false;
}
static void expect(unsigned i, int t, int c, int v) {
    assert(i < nevents);
    assert(events[i].type == t && events[i].code == c && events[i].value == v);
}
static void poll_frame(unsigned count, unsigned flag, unsigned x, unsigned y) {
    memset(frame, 0, sizeof(frame));
    frame[0] = count; frame[1] = flag | ((x >> 8) & 15);
    frame[2] = x; frame[3] = (y >> 8) & 15; frame[4] = y;
    nevents = 0; cst836u_poll(&ts.work.work);
}
static void test_press_release(void) {
    reset(); poll_frame(1, 0x00, 100, 200);
    assert(ts.touching && !ts.last_key && nevents == 4);
    expect(0, EV_KEY, BTN_TOUCH, 1); expect(1, EV_ABS, ABS_X, 100);
    expect(2, EV_ABS, ABS_Y, 200); expect(3, EV_SYN, SYN_REPORT, 0);
    poll_frame(1, 0x80, 101, 201);
    assert(nevents == 3); expect(0, EV_ABS, ABS_X, 101);
    /* Real logs also contain UP event with count still equal to one. */
    poll_frame(1, 0x40, 101, 201);
    assert(!ts.touching && nevents == 2); expect(0, EV_KEY, BTN_TOUCH, 0);
    poll_frame(0, 0x00, 0, 0); assert(nevents == 1 && !ts.touching);
}
static void test_three_keys(void) {
    const unsigned x[] = {40, 120, 200};
    const int keys[] = {KEY_BACK, KEY_HOME, KEY_MENU};
    for (unsigned n=0; n<3; n++) {
        reset(); poll_frame(1, 0x00, x[n], 320);
        assert(!ts.touching && ts.last_key == keys[n] && nevents == 2);
        expect(0, EV_KEY, keys[n], 1);
        poll_frame(1, 0x80, x[n], 320); assert(nevents == 1);
        poll_frame(0, 0x40, x[n], 320);
        assert(!ts.last_key && nevents == 2); expect(0, EV_KEY, keys[n], 0);
    }
}
static void test_display_to_key(void) {
    reset(); poll_frame(1, 0x80, 10, 100);
    poll_frame(1, 0x80, 40, 320);
    assert(!ts.touching && ts.last_key == KEY_BACK && nevents == 3);
    expect(0, EV_KEY, BTN_TOUCH, 0); expect(1, EV_KEY, KEY_BACK, 1);
}
static void test_key_to_display(void) {
    reset(); poll_frame(1, 0x80, 40, 320);
    poll_frame(1, 0x80, 10, 100);
    assert(ts.touching && !ts.last_key && nevents == 5);
    expect(0, EV_KEY, BTN_TOUCH, 1); expect(1, EV_KEY, KEY_BACK, 0);
    expect(2, EV_ABS, ABS_X, 10); expect(3, EV_ABS, ABS_Y, 100);
}
static void test_key_change(void) {
    reset(); poll_frame(1, 0x80, 40, 320);
    poll_frame(1, 0x80, 120, 320);
    assert(ts.last_key == KEY_HOME && nevents == 3);
    expect(0, EV_KEY, KEY_BACK, 0); expect(1, EV_KEY, KEY_HOME, 1);
}
static void test_invalid_frames(void) {
    const unsigned counts[] = {0, 3, 15, 1};
    const unsigned flags[] = {0x80, 0x80, 0xc0, 0xc0};
    for (unsigned n=0; n<4; n++) {
        reset(); poll_frame(1, 0x80, 120, 320);
        poll_frame(counts[n], flags[n], 120, 320);
        assert(!ts.last_key && !ts.touching); expect(0, EV_KEY, KEY_HOME, 0);
    }
}
static void test_bus_failure(void) {
    for (unsigned key=0; key<2; key++) {
        for (unsigned short_read=0; short_read<2; short_read++) {
            reset(); poll_frame(1, 0x80, 40, key ? 320 : 100);
            read_result = short_read ? 12 : -EIO;
            nevents = 0;
            for (unsigned n=0; n<49; n++) cst836u_poll(&ts.work.work);
            assert(nevents == 0 && ts.bus_errors == 49);
            cst836u_poll(&ts.work.work);
            assert(!ts.touching && !ts.last_key && nevents == 2 && error_logs == 1);
            expect(0, EV_KEY, key ? KEY_BACK : BTN_TOUCH, 0);
            nevents=0; cst836u_poll(&ts.work.work); assert(nevents == 0);
            read_result = 13; poll_frame(1, 0x80, 40, key ? 320 : 100);
            assert(ts.bus_errors == 0);
            expect(0, EV_KEY, key ? KEY_BACK : BTN_TOUCH, 1);
        }
    }
}
static void test_capture(void) {
    reset(); log_frames = 3;
    poll_frame(0, 0, 0, 0); poll_frame(1, 0x80, 40, 320);
    poll_frame(0, 0x40, 40, 320);
    assert(log_frames == 0 && frame_logs == 3 && reads == 3);
    assert(raw_logs == 2); /* held-key release must not vanish from old log */
    poll_frame(0, 0, 0, 0); assert(frame_logs == 3);
    log_frames = 1; read_result = -EIO;
    poll_frame(0, 0, 0, 0); assert(log_frames == 1 && frame_logs == 3);
    read_result = 13; poll_frame(0, 0, 0, 0);
    assert(log_frames == 0 && frame_logs == 4);
}
static void test_orientation(void) {
    reset(); flip_x = flip_y = true;
    poll_frame(1, 0x80, 10, 30);
    expect(1, EV_ABS, ABS_X, 229); expect(2, EV_ABS, ABS_Y, 289);
    reset(); swap_xy = true; poll_frame(1, 0x80, 10, 30);
    expect(1, EV_ABS, ABS_X, 30); expect(2, EV_ABS, ABS_Y, 10);
}
static void test_boundaries(void) {
    /* Tests existing policy, not evidence that these are hardware bounds. */
    const unsigned x[] = {79, 80, 159, 160};
    const int keys[] = {KEY_BACK, KEY_HOME, KEY_HOME, KEY_MENU};
    for (unsigned n=0; n<4; n++) {
        reset(); poll_frame(1, 0x80, x[n], 299); assert(ts.touching);
        poll_frame(1, 0x80, x[n], 300); assert(ts.last_key == keys[n]);
    }
}
struct testcase { const char *name; void (*fn)(void); };
static const struct testcase cases[] = {
    {"press_release", test_press_release}, {"three_keys", test_three_keys},
    {"display_to_key", test_display_to_key}, {"key_to_display", test_key_to_display},
    {"key_change", test_key_change}, {"invalid_frames", test_invalid_frames},
    {"bus_failure", test_bus_failure}, {"capture", test_capture},
    {"orientation", test_orientation}, {"boundaries", test_boundaries},
};
int main(int argc, char **argv) {
    unsigned ran = 0;
    for (unsigned i=0; i<sizeof(cases)/sizeof(cases[0]); i++) {
        if (argc > 1 && strcmp(argv[1], cases[i].name)) continue;
        cases[i].fn(); printf("PASS %s\n", cases[i].name); ran++;
    }
    assert(ran); return 0;
}
'''

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', type=Path, default=Path(__file__).with_name('z1_cst836u.c'))
    ap.add_argument('--case')
    args = ap.parse_args()
    source = args.source.read_text()
    # Compile the exact production functions, not a Python copy of their logic.
    chunk = source[source.index('#define CST836U_ADDR'):source.index('static int cst836u_probe')]
    fallback = '' if re.search(r'static unsigned int log_frames\s*;', chunk) else 'static unsigned int log_frames;\n'
    with tempfile.TemporaryDirectory(prefix='cst836u-host-') as td:
        c = Path(td) / 'test.c'; binary = Path(td) / 'test'
        c.write_text(SHIM + fallback + chunk + TESTS)
        subprocess.run(['cc', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=undefined', '-fno-sanitize-recover=all', '-o', str(binary), str(c)], check=True)
        result = subprocess.run([str(binary)] + ([args.case] if args.case else []))
        return result.returncode

if __name__ == '__main__':
    raise SystemExit(main())
