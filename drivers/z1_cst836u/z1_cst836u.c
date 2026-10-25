// SPDX-License-Identifier: GPL-2.0
/*
 * z1_cst836u - polled driver for the Hynitron CST836U touchscreen on the
 * Jiabo Z1 (MT6572) hearing trainer, I2C address 0x15.
 *
 * The Z1 board has no schematic: the touch IRQ and reset GPIO numbers are
 * unknown, so the interrupt-driven mainline candidates (edt-ft5x06 et al.)
 * cannot probe. This driver polls the FT6206-style register window instead:
 *   0x02 touch count, 0x03.. P1 XH/XL/YH/YL (12-bit), event in XH[7:6],
 *   identity at 0xa6 (FW) 0xa8 (module) 0xa9 (project) 0xaa/0xab (chip).
 * Register layout cross-checked against the independent EdgeTX
 * implementation (radio/src/boards/helloradio-h750/touch_driver.cpp).
 *
 * The module instantiates its own i2c client on the first adapter (i2c-0 or
 * i2c-1) where a single-byte READ at 0x15 gets an ACK, so no DT node is
 * required. R3d note: the probe must NOT use I2C_SMBUS_QUICK — 0-length
 * messages never reach the bus on i2c-mt65xx (see comment in cst836u_scan).
 * Orientation knobs (flip_x,
 * flip_y, swap_xy) are module parameters: recalibrate by rmmod/insmod
 * without reflashing. Raw samples are logged until log_raw quota is used so
 * the first on-board session doubles as orientation calibration evidence.
 */

#include <linux/delay.h>
#include <linux/errno.h>
#include <linux/i2c.h>
#include <linux/input.h>
#include <linux/module.h>
#include <linux/workqueue.h>

#define CST836U_ADDR        0x15
#define CST836U_REG_COUNT   0x02
#define CST836U_REG_ID      0xa6
#define CST836U_READ_LEN    13  /* 0x02..0x0e: count + P1 + P2 windows */

/* Point 1 window: XH 0x03, XL 0x04, YH 0x05, YL 0x06 */
#define CST836U_P1_OFF      1
#define CST836U_COORD_MASK  0x0f
#define CST836U_EV_MASK     0xc0
#define CST836U_EV_PRESS    0x00
#define CST836U_EV_CONTACT  0x80 /* XH[7:6] = 0b10 */
#define CST836U_MAX_TOUCH   2

#define CST836U_SCAN_MS     5000
#define CST836U_SCAN_TRIES  60

static unsigned int poll_ms = 15;
static bool flip_x, flip_y, swap_xy;
static unsigned int max_x = 240, max_y = 320;
static unsigned int log_raw = 64;
module_param(poll_ms, uint, 0644);
module_param(flip_x, bool, 0644);
module_param(flip_y, bool, 0644);
module_param(swap_xy, bool, 0644);
module_param(max_x, uint, 0444);
module_param(max_y, uint, 0444);
module_param(log_raw, uint, 0644);
MODULE_PARM_DESC(poll_ms, "polling interval in milliseconds");
MODULE_PARM_DESC(flip_x, "mirror X coordinates");
MODULE_PARM_DESC(flip_y, "mirror Y coordinates");
MODULE_PARM_DESC(swap_xy, "exchange X and Y axes");
MODULE_PARM_DESC(max_x, "clamped maximum X coordinate");
MODULE_PARM_DESC(max_y, "clamped maximum Y coordinate");
MODULE_PARM_DESC(log_raw, "number of raw samples logged for calibration");

struct cst836u {
    struct i2c_client *client;
    struct input_dev *input;
    struct delayed_work work;
    bool touching;
    unsigned int raw_logged;
    unsigned int bus_errors;
};

static int cst836u_read_block(struct i2c_client *client, u8 reg, u8 *buf, size_t len)
{
    int ret = i2c_smbus_read_i2c_block_data(client, reg, len, buf);
    return ret < 0 ? ret : (size_t)ret == len ? 0 : -EIO;
}

static void cst836u_report(struct cst836u *ts, bool touch, unsigned int x, unsigned int y)
{
    if (touch != ts->touching) {
        input_report_key(ts->input, BTN_TOUCH, touch);
        ts->touching = touch;
    }
    if (touch) {
        input_report_abs(ts->input, ABS_X, x);
        input_report_abs(ts->input, ABS_Y, y);
    }
    input_sync(ts->input);
}

static void cst836u_poll(struct work_struct *work)
{
    struct cst836u *ts = container_of(to_delayed_work(work), struct cst836u, work);
    u8 buf[CST836U_READ_LEN];
    unsigned int count, xh, xl, yh, yl, x, y;
    bool touch;

    if (cst836u_read_block(ts->client, CST836U_REG_COUNT, buf, sizeof(buf))) {
        if (++ts->bus_errors == 50)
            dev_err(&ts->client->dev, "50 consecutive I2C read failures; check power/clock\n");
        goto resched;
    }
    ts->bus_errors = 0;

    count = buf[0] & 0x0f;
    xh = buf[CST836U_P1_OFF];
    xl = buf[CST836U_P1_OFF + 1];
    yh = buf[CST836U_P1_OFF + 2];
    yl = buf[CST836U_P1_OFF + 3];

    /* FT-style event bits live in XH[7:6]: 00 press, 10 contact, others release. */
    switch (xh & CST836U_EV_MASK) {
    case CST836U_EV_PRESS:
    case CST836U_EV_CONTACT:
        touch = count >= 1 && count <= CST836U_MAX_TOUCH;
        break;
    default:
        touch = false;
    }

    if (touch) {
        unsigned int a = ((xh & CST836U_COORD_MASK) << 8) | xl;
        unsigned int b = ((yh & CST836U_COORD_MASK) << 8) | yl;
        if (swap_xy) { unsigned int t = a; a = b; b = t; }
        x = min(flip_x ? max_x - 1 - a : a, max_x - 1);
        y = min(flip_y ? max_y - 1 - b : b, max_y - 1);
    } else {
        x = y = 0;
    }

    if (ts->raw_logged < log_raw && (touch || ts->touching)) {
        dev_info(&ts->client->dev,
                 "raw sample %u: count=%u xh=%02x xl=%02x yh=%02x yl=%02x -> x=%u y=%u\n",
                 ts->raw_logged++, count, xh, xl, yh, yl, x, y);
    }

    cst836u_report(ts, touch, x, y);

resched:
    schedule_delayed_work(&ts->work, msecs_to_jiffies(poll_ms));
}

static int cst836u_probe(struct i2c_client *client)
{
    struct cst836u *ts;
    u8 id[8];
    int error;

    error = cst836u_read_block(client, CST836U_REG_ID, id, sizeof(id));
    if (error)
        dev_warn(&client->dev, "identity read failed (%d); continuing in poll mode\n", error);
    else
        dev_info(&client->dev,
                 "CST836U identity: fw=%02x%02x module=%02x project=%02x chip=%02x%02x\n",
                 id[1], id[0], id[2], id[3], id[5], id[4]);

    ts = devm_kzalloc(&client->dev, sizeof(*ts), GFP_KERNEL);
    if (!ts)
        return -ENOMEM;
    ts->client = client;

    ts->input = devm_input_allocate_device(&client->dev);
    if (!ts->input)
        return -ENOMEM;

    ts->input->name = "z1-cst836u";
    ts->input->id.bustype = BUS_I2C;
    ts->input->phys = devm_kasprintf(&client->dev, GFP_KERNEL,
                                     "i2c-%u/0x%02x", i2c_adapter_id(client->adapter), client->addr);
    __set_bit(EV_KEY, ts->input->evbit);
    __set_bit(EV_ABS, ts->input->evbit);
    __set_bit(BTN_TOUCH, ts->input->keybit);
    input_set_abs_params(ts->input, ABS_X, 0, max_x - 1, 0, 0);
    input_set_abs_params(ts->input, ABS_Y, 0, max_y - 1, 0, 0);

    error = input_register_device(ts->input);
    if (error)
        return error;

    i2c_set_clientdata(client, ts);
    INIT_DELAYED_WORK(&ts->work, cst836u_poll);
    schedule_delayed_work(&ts->work, msecs_to_jiffies(poll_ms));

    dev_info(&client->dev, "polled CST836U input device registered (poll=%ums flip_x=%d flip_y=%d swap_xy=%d)\n",
             poll_ms, flip_x, flip_y, swap_xy);
    return 0;
}

static const struct i2c_device_id cst836u_id[] = {
    { "z1_cst836u", 0 },
    { }
};
MODULE_DEVICE_TABLE(i2c, cst836u_id);

#ifdef CONFIG_OF
static const struct of_device_id cst836u_of_match[] = {
    { .compatible = "hynitron,cst836u" },
    { }
};
MODULE_DEVICE_TABLE(of, cst836u_of_match);
#endif

static struct i2c_driver cst836u_driver = {
    .driver = {
        .name = "z1_cst836u",
        .of_match_table = of_match_ptr(cst836u_of_match),
    },
    .probe = cst836u_probe,
    .id_table = cst836u_id,
};

static struct i2c_client *scan_client;
static struct delayed_work scan_work;
static unsigned int scan_tries;

static void cst836u_scan(struct work_struct *work)
{
    struct i2c_board_info info = {
        .type = "z1_cst836u",
        .addr = CST836U_ADDR,
    };
    struct i2c_client *client;
    struct i2c_adapter *adap;
    union i2c_smbus_data dummy;
    s32 probe;
    int n;

    /*
     * R3d fix: i2c_new_scanned_device() probes with I2C_SMBUS_QUICK
     * (0-length message), which the MT6572 i2c-mt65xx driver rejects in
     * its DMA buffer setup before the transfer ever reaches the bus
     * (drivers/i2c/busses/i2c-mt65xx.c:1140 -> i2c_get_dma_safe_msg_buf,
     * drivers/i2c/i2c-core-base.c:2651 returns NULL for len==0), so every
     * historical quick scan on this platform was void. Probe with a
     * single-byte READ instead: a real bus transaction whose errno is
     * meaningful (0 = ACK, -ENXIO = NACK, -ETIMEDOUT = no completion irq).
     */
    for (n = 0; n < 2; n++) {
        adap = i2c_get_adapter(n);
        if (!adap)
            continue;
        probe = i2c_smbus_xfer(adap, CST836U_ADDR, 0, I2C_SMBUS_READ, 0,
                               I2C_SMBUS_BYTE, &dummy);
        if (probe == 0) {
            /* Explicit instantiation on the adapter that just ACKed.
             * NB: i2c_new_device() was renamed i2c_new_client_device()
             * in this tree (include/linux/i2c.h:459).
             */
            client = i2c_new_client_device(adap, &info);
            i2c_put_adapter(adap);
            if (IS_ERR(client)) {
                pr_warn("z1_cst836u: 0x%02x ACKed on i2c-%d but instantiating client failed: %ld\n",
                        CST836U_ADDR, n, PTR_ERR(client));
            } else {
                scan_client = client;
                pr_info("z1_cst836u: chip ACKed (byte read) at 0x%02x on i2c-%d\n",
                        CST836U_ADDR, n);
                return;
            }
            continue;
        }
        if (probe != -ENXIO)
            pr_info("z1_cst836u: byte-read probe i2c-%d at 0x%02x returned %d\n",
                    n, CST836U_ADDR, probe);
        i2c_put_adapter(adap);
    }

    if (++scan_tries < CST836U_SCAN_TRIES) {
        pr_info("z1_cst836u: no ACK at 0x%02x yet (try %u/%u); retrying in %us\n",
                CST836U_ADDR, scan_tries, CST836U_SCAN_TRIES, CST836U_SCAN_MS / 1000);
        schedule_delayed_work(&scan_work, msecs_to_jiffies(CST836U_SCAN_MS));
    } else {
        pr_err("z1_cst836u: giving up after %u scans; touch chip absent or unpowered\n",
               scan_tries);
    }
}

static int __init cst836u_init(void)
{
    int error = i2c_register_driver(THIS_MODULE, &cst836u_driver);
    if (error)
        return error;
    INIT_DELAYED_WORK(&scan_work, cst836u_scan);
    schedule_delayed_work(&scan_work, 0);
    return 0;
}

static void __exit cst836u_exit(void)
{
    cancel_delayed_work_sync(&scan_work);
    if (scan_client) {
        struct cst836u *ts = i2c_get_clientdata(scan_client);
        if (ts)
            cancel_delayed_work_sync(&ts->work);
        i2c_unregister_device(scan_client);
    }
    i2c_del_driver(&cst836u_driver);
}

module_init(cst836u_init);
module_exit(cst836u_exit);

MODULE_AUTHOR("Z1 mainline bring-up");
MODULE_DESCRIPTION("Polled CST836U (FT6206 protocol) touchscreen for Jiabo Z1");
MODULE_LICENSE("GPL");
