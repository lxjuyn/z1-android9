// SPDX-License-Identifier: GPL-2.0
/*
 * z1_key_gpio_hunt — 佳伯 Z1（MT6572）"复读/跟读"按键 GPIO 猎人（只读被动监视版）
 *
 * 背景：Z1 键盘矩阵经 mainline mt6779-keypad 工作的是 12 键（音量+/-、
 * F 键 250/251/252/254、方向键、确定、BACK）。"复读""跟读"两个专用按键
 * 不上报——怀疑不在 KPD 矩阵上，而是独立 GPIO（低有效接 GND、内部/外部
 * 上拉）或独立 EINT。本模块用最保守的手段找到它们：
 *
 *  设计红线（与 drivers/z1_gpio_hunt 触摸猎人相反）：
 *    - 全程【只读】：不 gpio_request、不设方向、不写电平、不改任何
 *      pinctrl 配置。对任何脚（含 eMMC/DSI/pwrap/串口）读 DI 寄存器都
 *      无副作用，因此默认扫满全部 154 脚。
 *    - 通过 chip->get（mtk_gpio_get → mtk_hw_get_value(PINCTRL_PIN_REG_DI)）
 *      读物理电平，不问方向不看占用——pinctrl_gpio_can_use_line 闸门
 *      只属于"要 request 抢脚"的模块，本模块不抢，无需该闸门。
 *    - 矩阵 KROW 脚（110/111/112/142/143）是 keypad 驱动在扫的输出，
 *      KCOL 脚（107/108/109）带拉起输入，读到扫描活动属正常噪声，
 *      判读见 README。
 *
 * 工作方式：kthread 每 poll_ms（默认 10ms）对 [start, start+count) 的每
 * 个脚 gpiod_get_raw_value_cansleep 一次；连续 2 次采样不同于稳定态才算
 * 边沿（约 20ms 去抖），边沿打印到 kmsg：
 *      [gpiotest] GPIO74 下降沿 1->0（t=+12.345s）  ← 按键候选（低有效）
 * 释放时附按住时长。加载时打一次全脚初始电平/方向 dump，此后每
 * summary_sec（默认 30s，0=关）打一次变化汇总。rmmod 时打最终计数。
 *
 * API 依据（fork_linux 7.0-rc7 本树逐条核实，与 R3d 触摸猎人共用结论）：
 *  - gpio_to_desc：include/linux/gpio/consumer.h:173 声明，
 *    drivers/gpio/gpiolib.c:187 EXPORT_SYMBOL_GPL。
 *  - gpiod_get_raw_value_cansleep：drivers/gpio/gpiolib.c 导出，
 *    走 gpiod_get_raw_value_commit → gc->get（pinctrl-paris.c
 *    mtk_gpio_get，纯 DI 寄存器读）。gpiolib 的 VALIDATE_DESC 只校验
 *    desc 有效性，不做 request 检查 → 未 request 的脚可读。
 *  - gpiod_get_direction：gpiolib.c:445，EXPORT_SYMBOL_GPL:486
 *    （1=入 0=出，负值=读失败）。纯读 DIR 寄存器，无副作用。
 *  - gpio_device_find/gpio_device_put：gpio/driver.h:628/633，
 *    gpiolib.c:1368 导出。按 compatible "mediatek,mt6572-pinctrl"
 *    （pinctrl-mt6572.c:385）锁定主 gpiochip，避开 MT6397(PMIC)。
 *    chip->base = -1 动态分配（pinctrl-paris.c:964），全局号 =
 *    base + 硬件脚号，运行时解析。
 *  - chip->ngpio = soc->npins = ARRAY_SIZE(mtk_pins_mt6572) = 154
 *    （0..153 连续无洞；dt-bindings pinfunc 宏缺 125 但 pinctrl 表
 *    实有 GPIO125，只读扫描不受影响）。
 *  - desc 归全局号空间所有，绝不 gpiod_put（R3d 触摸猎人同结论）。
 *    MTK pinctrl 内建(=y)永不卸载，module 生命周期内 desc 有效。
 *
 * vermagic 目标：7.0.0-rc7-z1+（fork_linux 编译树，CONFIG_GPIOLIB_LEGACY=y
 * 已核实，但本模块直接用 gpiod_* 导出 API，不依赖 legacy 内联）。
 */

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/delay.h>
#include <linux/kthread.h>
#include <linux/string.h>
#include <linux/errno.h>
#include <linux/time.h>
#include <linux/gpio.h>
#include <linux/gpio/consumer.h>
#include <linux/gpio/driver.h>
#include <linux/of.h>

#define TAG "[gpiotest] "

/* 扫描范围（硬件脚号 0..153）。默认全扫——只读无风险，复读/跟读脚
 * 理论上也可能落在 BPI/BSI 射频复用区（135-153），不预设禁区。
 */
static int start = 0;
module_param(start, int, 0444);
MODULE_PARM_DESC(start, "起始硬件脚号（默认 0）");

static int count = 154;
module_param(count, int, 0444);
MODULE_PARM_DESC(count, "扫描脚数（默认 154=全脚）");

static int poll_ms = 10;
module_param(poll_ms, int, 0444);
MODULE_PARM_DESC(poll_ms, "轮询间隔 ms（默认 10）");

static int summary_sec = 30;
module_param(summary_sec, int, 0444);
MODULE_PARM_DESC(summary_sec, "变化汇总间隔秒（默认 30，0=关）");

static bool dump_direction = true;
module_param(dump_direction, bool, 0444);
MODULE_PARM_DESC(dump_direction, "加载时 dump 各脚方向（默认 1）");

/* 每脚运行时状态 */
struct pin_state {
	struct gpio_desc *desc;
	u8		stable;		/* 当前稳定电平 */
	u8		raw_prev;	/* 上一次采样 */
	bool		valid;		/* desc 有效且可读 */
	bool		read_err;	/* 读错误只报一次 */
	unsigned int	falls;		/* 下降沿（1->0）计数 */
	unsigned int	rises;		/* 上升沿（0->1）计数 */
	s64		press_ms;	/* 下降沿时刻（boot ms），-1=未按下 */
	u8		init_level;	/* 加载时电平 */
};

static struct pin_state *pins;		/* count 个 */
static struct gpio_chip *hunt_gc;	/* 仅作 ngpio 读取的 chip 句柄；
					 * MTK pinctrl 内建(=y)永不卸载，安全 */
static int hunt_base = -1;		/* gpiochip 全局 base */
static struct task_struct *hunt_task;
static s64 t0_ms;			/* 模块加载时刻（boot ms） */

static s64 boot_ms(void)
{
	return ktime_get_boottime_ns() / 1000000;
}

static s64 rel_ms(void)
{
	return boot_ms() - t0_ms;
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

/* 加载时全脚初始电平/方向 dump */
static void initial_dump(void)
{
	int i, global;
	char line[80];
	int len;
	int lows_first = 1;

	pr_info(TAG "初始电平 dump（%d..%d，1=高 0=低，'?'=不可读）：\n",
		start, start + count - 1);
	for (i = 0; i < count; i += 32) {
		int j, hi = min(i + 32, count) - i;

		len = scnprintf(line, sizeof(line), "  %3d-%3d: ", start + i,
				start + i + hi - 1);
		for (j = 0; j < hi && len < (int)sizeof(line) - 2; j++)
			line[len++] = pins[i + j].valid ?
				('0' + pins[i + j].init_level) : '?';
		line[len] = '\0';
		pr_info(TAG "%s\n", line);
	}

	for (i = 0; i < count; i++) {
		if (pins[i].valid && pins[i].init_level == 0) {
			if (lows_first) {
				pr_cont(TAG "低电平脚:");
				lows_first = 0;
			}
			pr_cont(" %d", start + i);
		}
	}
	if (!lows_first)
		pr_cont("\n");

	if (dump_direction) {
		int ins_first = 1, outs_first = 1;

		for (i = 0; i < count; i++) {
			int d;

			if (!pins[i].valid)
				continue;
			global = hunt_base + start + i;
			d = gpiod_get_direction(pins[i].desc);
			if (d < 0)
				continue;
			if (d == GPIO_LINE_DIRECTION_IN) {
				if (ins_first) {
					pr_cont(TAG "输入脚:");
					ins_first = 0;
				}
				pr_cont(" %d", start + i);
			} else {
				if (outs_first) {
					pr_cont(TAG "输出脚:");
					outs_first = 0;
				}
				pr_cont(" %d", start + i);
			}
		}
		if (!ins_first || !outs_first)
			pr_cont("\n");
		pr_info(TAG "（输出脚=被某驱动驱动中，KROW 即属此类；"
			"边沿判读见 README）\n");
	}
}

/* 边沿上报 */
static void report_edge(int pin_no, struct pin_state *p, int to_level)
{
	if (to_level == 0) {
		p->falls++;
		p->press_ms = rel_ms();
		pr_info(TAG "GPIO%d 下降沿 1->0（t=+%.3fs）← 按键候选（低有效）\n",
			pin_no, p->press_ms / 1000.0);
	} else {
		p->rises++;
		if (p->press_ms >= 0)
			pr_info(TAG "GPIO%d 上升沿 0->1（t=+%.3fs，按住 %.0fms）\n",
				pin_no, rel_ms() / 1000.0,
				(rel_ms() - p->press_ms) * 1.0);
		else
			pr_info(TAG "GPIO%d 上升沿 0->1（t=+%.3fs）\n",
				pin_no, rel_ms() / 1000.0);
		p->press_ms = -1;
	}
}

/* 周期汇总 */
static void summary_print(void)
{
	int i, any = 0;

	for (i = 0; i < count; i++) {
		if (!pins[i].valid)
			continue;
		if (pins[i].falls || pins[i].rises ||
		    pins[i].stable != pins[i].init_level) {
			if (!any) {
				pr_info(TAG "汇总（t=+%.0fs）：",
					rel_ms() / 1000.0);
				any = 1;
			}
			pr_cont(" GPIO%d[↓%u ↑%u 现在=%d 初始=%d]",
				start + i, pins[i].falls, pins[i].rises,
				pins[i].stable, pins[i].init_level);
		}
	}
	if (any)
		pr_cont("\n");
	else
		pr_info(TAG "汇总（t=+%.0fs）：尚无任何脚变化\n",
			rel_ms() / 1000.0);
}

static int hunt_thread_fn(void *unused)
{
	int i;
	s64 next_summary = summary_sec > 0 ? boot_ms() + summary_sec * 1000 : 0;

	while (!kthread_should_stop()) {
		for (i = 0; i < count; i++) {
			struct pin_state *p = &pins[i];
			int v;

			if (!p->valid)
				continue;

			v = gpiod_get_raw_value_cansleep(p->desc);
			if (v < 0) {
				if (!p->read_err) {
					pr_info(TAG "GPIO%d: 读失败 %d（后续不再报）\n",
						start + i, v);
					p->read_err = true;
				}
				continue;
			}

			/* 连续 2 次采样相同且不同于稳定态 → 提交边沿（去抖） */
			if (v == p->raw_prev && v != p->stable) {
				p->stable = (u8)v;
				report_edge(start + i, p, v);
			}
			p->raw_prev = (u8)v;
		}

		if (summary_sec > 0 && boot_ms() >= next_summary) {
			summary_print();
			next_summary = boot_ms() + summary_sec * 1000;
		}

		msleep_interruptible(poll_ms);
	}
	return 0;
}

static int __init z1_key_gpio_hunt_init(void)
{
	struct gpio_device *gdev;
	int i, n_valid = 0;

	if (start < 0 || count <= 0 || poll_ms <= 0) {
		pr_err(TAG "参数非法：start=%d count=%d poll_ms=%d\n",
			start, count, poll_ms);
		return -EINVAL;
	}

	gdev = gpio_device_find(NULL, hunt_match_chip);
	if (!gdev || hunt_base < 0 || !hunt_gc) {
		pr_err(TAG "找不到 mediatek,mt6572-pinctrl gpiochip"
			"（pinctrl_mt6572 未 probe？），加载中止\n");
		return -ENODEV;
	}
	if (start + count > (int)hunt_gc->ngpio) {
		pr_info(TAG "范围 %d..%d 超出 chip ngpio=%u，截到 %u\n",
			start, start + count - 1, hunt_gc->ngpio,
			hunt_gc->ngpio);
		count = (int)hunt_gc->ngpio - start;
		if (count <= 0) {
			gpio_device_put(gdev);
			return -EINVAL;
		}
	}
	/* base/ngpio/gc 已在 match 回调（srcu 保护期）内取好，立即释放
	 * 引用；chip 本体内建永生，desc（全局号空间持有）生命周期等同
	 */
	gpio_device_put(gdev);

	pins = kzalloc_objs(struct pin_state, count, GFP_KERNEL);
	if (!pins)
		return -ENOMEM;

	t0_ms = boot_ms();

	for (i = 0; i < count; i++) {
		int global = hunt_base + start + i;
		int v;

		pins[i].desc = gpio_to_desc(global);
		if (!pins[i].desc)
			continue;

		v = gpiod_get_raw_value_cansleep(pins[i].desc);
		if (v < 0) {
			pr_info(TAG "GPIO%d: 初始读失败 %d，跳过\n",
				start + i, v);
			pins[i].read_err = true;
			continue;
		}
		pins[i].valid = true;
		pins[i].stable = (u8)v;
		pins[i].raw_prev = (u8)v;
		pins[i].init_level = (u8)v;
		pins[i].press_ms = -1;
		n_valid++;
	}

	pr_info(TAG "Z1 按键 GPIO 猎人启动：只读扫描 GPIO%d..%d"
		"（有效 %d/%d），poll=%dms 去抖≈%dms，summary=%ds，"
		"全程不 request 不驱动不改配置\n",
		start, start + count - 1, n_valid, count,
		poll_ms, poll_ms * 2, summary_sec);

	initial_dump();

	hunt_task = kthread_run(hunt_thread_fn, NULL, "z1_key_gpio_hunt");
	if (IS_ERR(hunt_task)) {
		pr_err(TAG "扫描线程创建失败 %ld\n", PTR_ERR(hunt_task));
		kfree(pins);
		return PTR_ERR(hunt_task);
	}
	return 0;
}

static void __exit z1_key_gpio_hunt_exit(void)
{
	int i;

	if (hunt_task)
		kthread_stop(hunt_task);

	pr_info(TAG "卸载。全期边沿计数：\n");
	for (i = 0; i < count; i++) {
		if (pins[i].valid && (pins[i].falls || pins[i].rises))
			pr_info(TAG "  GPIO%d: 下降 %u 次 / 上升 %u 次\n",
				start + i, pins[i].falls, pins[i].rises);
	}
	kfree(pins);
}

module_init(z1_key_gpio_hunt_init);
module_exit(z1_key_gpio_hunt_exit);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Z1 (MT6572) 复读/跟读按键 GPIO 猎人（只读被动监视：全 154 脚电平轮询，边沿打 kmsg，不 request 不驱动）");
MODULE_AUTHOR("z1-mt6572-mainline bringup");
