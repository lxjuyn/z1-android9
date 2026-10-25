// SPDX-License-Identifier: GPL-2.0
/*
 * z1_gpio_hunt — 佳伯 Z1（MT6572）触摸 IC 电源/复位脚猎人
 *
 * 背景：CST836U 触摸挂在 i2c 从机地址 0x15，但对 i2c0/i2c1 的常规探测
 * 全部无 ACK。怀疑触摸板需要某个 GPIO 先拉高（电源使能/复位解除）才会上线。
 * 本模块逐脚做"拉高→探测→拉低→探测→恢复"实验：任何一次 0x15 ACK
 * 即判定该脚为触摸供电/复位脚，立刻锁存电平、停止扫描。
 *
 * 安全设计（三层闸门，缺一不可）：
 *  1. 静态禁扫清单：PMIC pwrap/EINT(7-11)、WATCHDOG(13)、eMMC(44-54，
 *     含 RSTB)、i2c0(105/106)、i2c1 在用(113/114)。
 *  2. pinctrl_gpio_can_use_line() 闸门：脚正被 pinmux 用作非 GPIO 功能
 *     （如 dts 已 mux 的 SCL_1_1/SDA_1_1、eMMC、pwrap）则不碰——否则
 *     gpio_request 会把 mux 切到 GPIO 且 free 后不恢复，等于亲手拆总线。
 *  3. gpio_request 本身对已占用脚返回 -EBUSY（pinmux pin_request 兜底）。
 *
 * 注：89/90（SCL_1_1/SDA_1_1 候选）若已被 dts mux 进 i2c1，本模块经闸门 2
 * 自动让位（该路线由 CST836U 轮询驱动直接验证）；若 dts 未 mux 它们，则
 * 作为普通空闲脚参与扫描。
 *
 * API 依据（Linux 7.0-rc7 本树核实）：
 *  - legacy GPIO 可用：CONFIG_GPIOLIB_LEGACY=y；gpio_request/gpio_free
 *    drivers/gpio/gpiolib-legacy.c:58/18 EXPORT_SYMBOL_GPL；
 *    gpio_direction_input/output/gpio_get_value include/linux/gpio.h:56-81
 *    内联经 gpio_to_desc（drivers/gpio/gpiolib.c:187）。
 *  - 全局号不可假设 0 起：pinctrl-paris.c mtk_build_gpiochip 设
 *    chip->base = -1 动态分配，故运行时用 gpio_device_find() 按
 *    compatible "mediatek,mt6572-pinctrl" 解析（gpio_device_find 声明
 *    include/linux/gpio/driver.h:628）。
 *  - i2c：i2c_get_adapter/i2c_put_adapter（i2c-core-base.c:2620/2631）、
 *    i2c_smbus_xfer（i2c-core-smbus.c:552）均 EXPORT_SYMBOL；
 *    MT6572 i2c 走 mt6577_i2c_quirks（无 I2C_AQ_NO_ZERO_LEN），
 *    I2C_FUNC_SMBUS_QUICK 可用（i2c-mt65xx.c:382/352/1375）。
 *
 * vermagic 目标：7.0.0-rc7-z1+（R9 配方编译树）。
 */

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/delay.h>
#include <linux/kthread.h>
#include <linux/string.h>
#include <linux/gpio.h>
#include <linux/gpio/consumer.h>
#include <linux/gpio/driver.h>
#include <linux/i2c.h>
#include <linux/of.h>
#include <linux/pinctrl/consumer.h>

#define TAG "[gpiohunt] "

/* 扫描上界：pin 0..134。135-153 是 BPI/BSI/VM 射频脚，一律不动。
 *（驱动侧 pinctrl-mtk-mt6572.h 实有 154 脚，dt-bindings 只定义到 134，
 *  以 dt-bindings 为准。）
 */
#define HUNT_MAX_PIN	134

/* 每 phase 电平保持时长与轮间隔 */
#define HUNT_SETTLE_MS	60
#define HUNT_ROUND_MS	500

static int hunt_addr = 0x15;		/* 触摸从机地址（CST836U） */
module_param(hunt_addr, int, 0644);
MODULE_PARM_DESC(hunt_addr, "触摸 I2C 从机地址（默认 0x15）");

/* 按任务命名：scan_bus1=i2c-0，scan_bus2=i2c-1，默认两者都扫 */
static bool scan_bus1 = true;
module_param(scan_bus1, bool, 0644);
MODULE_PARM_DESC(scan_bus1, "是否探测 i2c-0（默认 1）");

static bool scan_bus2 = true;
module_param(scan_bus2, bool, 0644);
MODULE_PARM_DESC(scan_bus2, "是否探测 i2c-1（默认 1）");

static int gpio_base_override = -1;
module_param(gpio_base_override, int, 0444);
MODULE_PARM_DESC(gpio_base_override,
		 "手动指定 MT6572 gpiochip 全局 base（默认 -1=自动解析）");

static bool verbose = true;
module_param(verbose, bool, 0644);
MODULE_PARM_DESC(verbose, "详细日志（默认 1）");

/* 扫描结果（只读导出，板上 cat /sys/module/z1_gpio_hunt/parameters/ 可查） */
static int found_gpio = -1;
module_param(found_gpio, int, 0444);
static int found_bus = -1;
module_param(found_bus, int, 0444);
static int found_level = -1;
module_param(found_level, int, 0444);

static struct gpio_chip *hunt_gc;	/* 仅作 can_use_line 的 chip 句柄；
					 * MTK pinctrl 内建(=y)永不卸载，安全 */
static int hunt_base = -1;		/* MT6572 gpiochip 全局 base */
static int hunt_rounds;
static struct task_struct *hunt_task;

/*
 * 静态禁扫清单（编号 = dt-bindings/pinctrl/mt6572-pinfunc.h 的 pin 号）：
 *  - 7..11  ：PMIC pwrap/EINT（pwrap SPI 在 7/8，动则失联 MT6323）
 *  - 13     ：WATCHDOG 专用脚（pinfunc.h:59 MT6572_PIN_13_WATCHDOG，
 *             翻转可能直接咬死复位）
 *  - 44..54 ：eMMC mmc0 数据/命令/时钟 + RSTB（动则掉 eMMC）
 *  - 105,106：i2c0 SCL_0_0/SDA_0_0（dts 已启用）
 *  - 113,114：i2c1 SCL_1_0/SDA_1_0（在用）
 *  JTAG（JTMS/JTCK/JTDI/JTDO）经 pinfunc.h 查证无专用脚、仅 FUNC 复用
 *  （87/88/91/93/95/96/101/102/109/112 及 89/90 自身），GPIO 模式下扫描
 *  安全，且 89/90 正是本轮候选，故不因 JTAG 复用禁扫。
 */
static const struct {
	int first, last;
	const char *why;
} banned_ranges[] = {
	{  7,  11, "PMIC pwrap/EINT" },
	{ 13,  13, "WATCHDOG 专用脚" },
	{ 44,  54, "eMMC mmc0（含 RSTB）" },
	{ 105, 106, "i2c0 SCL_0_0/SDA_0_0" },
	{ 113, 114, "i2c1 SCL_1_0/SDA_1_0 在用" },
};

static bool is_banned(int pin, const char **why)
{
	int i;

	for (i = 0; i < ARRAY_SIZE(banned_ranges); i++) {
		if (pin >= banned_ranges[i].first &&
		    pin <= banned_ranges[i].last) {
			if (why)
				*why = banned_ranges[i].why;
			return true;
		}
	}
	return false;
}

static bool want_bus(int bus)
{
	return (bus == 0 && scan_bus1) || (bus == 1 && scan_bus2);
}

/* 对指定适配器做一次 QUICK(写方向) 探测。0 = ACK（命中），负值 = 无应答/错误 */
static int probe_bus(int bus)
{
	struct i2c_adapter *adap;
	int ret;

	adap = i2c_get_adapter(bus);
	if (!adap) {
		if (verbose)
			pr_info(TAG "i2c-%d 适配器不存在\n", bus);
		return -ENXIO;
	}

	/* I2C_SMBUS_QUICK + 写方向：与 i2cdetect 的 Quick Write 等价 */
	ret = i2c_smbus_xfer(adap, hunt_addr, 0, I2C_SMBUS_WRITE, 0,
			     I2C_SMBUS_QUICK, NULL);

	i2c_put_adapter(adap);
	return ret;
}

/*
 * 扫单个脚（pin 为 MT6572 硬件脚号 0..134）。
 * 返回 1 = 命中（脚已锁存为输出、未释放、不再动）；
 *       0 = 未命中（已恢复原方向/原值并释放）；
 *       负值 = 跳过（禁扫/pinmux 占用/request 失败），未动电平。
 */
static int scan_one_pin(int pin)
{
	int global = hunt_base + pin;
	struct gpio_desc *desc;
	const char *why = NULL;
	int orig_dir, orig_val = 0;
	int ret, bus, level;

	if (is_banned(pin, &why)) {
		if (verbose)
			pr_info(TAG "GPIO%d: 禁扫（%s），跳过\n", pin, why);
		return -EPERM;
	}

	/* 全局号必须落在本 chip 范围内 */
	if (!gpio_is_valid(global) || !hunt_gc ||
	    pin >= (int)hunt_gc->ngpio) {
		pr_info(TAG "GPIO%d: 超出 MT6572 gpiochip 范围，跳过\n", pin);
		return -ERANGE;
	}

	desc = gpio_to_desc(global);
	if (!desc)
		return -EINVAL;

	/* 闸门 2：脚正被 pinmux 用作非 GPIO 功能（i2c/eMMC/pwrap/JTAG-mux）。
	 * 不先问就 request 会把 mux 切成 GPIO，free 后也不恢复 → 亲手拆总线。
	 */
	if (!pinctrl_gpio_can_use_line(hunt_gc, pin)) {
		if (verbose)
			pr_info(TAG "GPIO%d: pinmux 非 GPIO 功能在用（如已被 dts mux），让位不扫\n",
				pin);
		return -EBUSY;
	}

	/* 闸门 3：request（被占用的脚在这里 -EBUSY 兜底） */
	ret = gpio_request(global, "z1_gpio_hunt");
	if (ret) {
		if (verbose)
			pr_info(TAG "GPIO%d: request 失败 %d（在用），跳过\n",
				pin, ret);
		return ret;
	}

	/* 记录原方向/原值，尽力恢复 */
	orig_dir = gpiod_get_direction(desc);
	if (orig_dir == GPIO_LINE_DIRECTION_OUT)
		orig_val = gpio_get_value(global);

	/* phase 高：输出高 → 60ms → 探测；phase 低：保持输出翻低 → 60ms → 探测 */
	for (level = 1; level >= 0; level--) {
		if (level == 1)
			gpio_direction_output(global, 1);
		else
			gpio_set_value(global, 0);

		msleep_interruptible(HUNT_SETTLE_MS);

		for (bus = 0; bus < 2; bus++) {
			if (!want_bus(bus))
				continue;
			ret = probe_bus(bus);
			if (ret == 0) {
				pr_err(TAG "************************************************\n");
				pr_err(TAG "GPIO%d 输出%s 时 i2c-%d 的 0x%02x ACK！\n",
				       pin, level ? "高" : "低", bus, hunt_addr);
				pr_err(TAG ">>> 触摸上线了！锁存该脚（不恢复不释放），停止扫描\n");
				pr_err(TAG "************************************************\n");
				found_gpio = pin;
				found_bus = bus;
				found_level = level;
				return 1;	/* 保持输出电平，不 free */
			}
		}
	}

	/* 未命中：恢复原方向/原值并释放 */
	if (orig_dir == GPIO_LINE_DIRECTION_IN)
		gpio_direction_input(global);
	else if (orig_dir == GPIO_LINE_DIRECTION_OUT)
		gpio_direction_output(global, orig_val);
	else
		gpio_direction_input(global);	/* 读不到原方向则保守回输入 */

	gpio_free(global);

	if (verbose)
		pr_info(TAG "GPIO%d: 无 ACK（原dir=%d 原val=%d），已恢复释放\n",
			pin, orig_dir, orig_val);
	return 0;
}

static int hunt_thread_fn(void *unused)
{
	int pin, r;

	while (!kthread_should_stop()) {
		if (found_gpio >= 0) {
			/* 命中后挂起等 rmmod，电平保持不动 */
			msleep_interruptible(HUNT_ROUND_MS);
			continue;
		}

		hunt_rounds++;
		for (pin = 0; pin <= HUNT_MAX_PIN; pin++) {
			if (kthread_should_stop())
				break;
			r = scan_one_pin(pin);
			if (r == 1)
				break;	/* 命中，本轮终止 */
		}

		if (found_gpio < 0)
			pr_info(TAG "第 %d 轮扫描完成，无 ACK，%dms 后下一轮\n",
				hunt_rounds, HUNT_ROUND_MS);

		msleep_interruptible(HUNT_ROUND_MS);
	}
	return 0;
}

/* gpio_device_find 的 match 回调：认 MT6572 主 pinctrl，避开 MT6397(PMIC) */
static int hunt_match_chip(struct gpio_chip *gc, const void *data)
{
	struct device_node *np;

	if (!gc->parent)
		return 0;

	np = dev_of_node(gc->parent);
	if (np && of_device_is_compatible(np, "mediatek,mt6572-pinctrl")) {
		hunt_gc = gc;
		hunt_base = gc->base;
		pr_info(TAG "锁定 MT6572 gpiochip：base=%d ngpio=%u label=%s\n",
			gc->base, gc->ngpio, gc->label ? gc->label : "?");
		return 1;
	}
	return 0;
}

static int __init z1_gpio_hunt_init(void)
{
	struct gpio_device *gdev;

	if (!scan_bus1 && !scan_bus2) {
		pr_err(TAG "scan_bus1/scan_bus2 全关，无适配器可探测\n");
		return -EINVAL;
	}

	if (gpio_base_override >= 0) {
		/* 手动 base：兼容基址已知的场景（仍需 gc 做 can_use_line，
		 * 找不到 chip 时闸门 2 退化为仅靠 request 的 EBUSY 兜底） */
		hunt_base = gpio_base_override;
		gpio_device_find(NULL, hunt_match_chip);
	} else {
		gdev = gpio_device_find(NULL, hunt_match_chip);
		if (!gdev || hunt_base < 0) {
			pr_err(TAG "找不到 mediatek,mt6572-pinctrl gpiochip"
				"（内建 pinctrl_mt6572 未 probe？），加载中止\n");
			return -ENODEV;
		}
		/* base/ngpio/gc 已在 match 回调（srcu 保护期）内取好，
		 * 立即释放引用；chip 本体内建永生，指针此后仍有效 */
		gpio_device_put(gdev);
	}

	pr_info(TAG "启动：扫 pin 0..%d（禁扫 7-11/13/44-54/105-106/113-114），"
		"QUICK@0x%02x 于 i2c-%s%s，轮间隔 %dms\n",
		HUNT_MAX_PIN, hunt_addr,
		scan_bus1 ? "0" : "", scan_bus2 ? "+1" : "", HUNT_ROUND_MS);

	hunt_task = kthread_run(hunt_thread_fn, NULL, "z1_gpio_hunt");
	if (IS_ERR(hunt_task)) {
		pr_err(TAG "扫描线程创建失败 %ld\n", PTR_ERR(hunt_task));
		return PTR_ERR(hunt_task);
	}
	return 0;
}

static void __exit z1_gpio_hunt_exit(void)
{
	if (hunt_task)
		kthread_stop(hunt_task);

	if (found_gpio >= 0)
		pr_err(TAG "卸载：GPIO%d 仍锁存为输出%s（未恢复未释放，按设计；"
			"重启或手动 gpio 操作才会改变）\n",
			found_gpio, found_level ? "高" : "低");
	else
		pr_info(TAG "卸载（共 %d 轮，未发现 ACK）\n", hunt_rounds);
}

module_init(z1_gpio_hunt_init);
module_exit(z1_gpio_hunt_exit);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Z1 (MT6572) 触摸电源/复位脚猎人：逐脚拉高拉低 + i2c 0x15 QUICK 探测");
MODULE_AUTHOR("z1-mt6572-mainline bringup");
