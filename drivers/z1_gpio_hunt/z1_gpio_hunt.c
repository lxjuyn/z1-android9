// SPDX-License-Identifier: GPL-2.0
/*
 * z1_gpio_hunt — 佳伯 Z1（MT6572）触摸 IC 电源/复位脚猎人（R3d 版，三段式）
 *
 * 背景：CST836U 触摸挂在 i2c 从机地址 0x15，但对 i2c0/i2c1 的常规探测
 * 全部无 ACK。策略 = DTB 保险改动（VGP1 always-on）+ 本模块运行时扫描。
 *
 * 三段式按序执行，任一段拿到 0x15 ACK 即收手：
 *  【第 0 段·基线探测】模块加载即对适配器 0/1 各做一次 0x15 单字节读探测
 *      并记录 errno——dts 已给 VGP1 加 always-on（上电根因已修），芯片可能
 *      直接就 ACK。ACK 则报告并收手，不动任何 GPIO。
 *  【第 1 段·定向复位】GPIO145（BPI_BUS15）已被十路取证反汇编定案为触摸
 *      复位脚（原厂序列：GPIO 模式+输出→拉低 20ms→拉高→等 50ms，上电
 *      就绪 Tpor≥100ms）。request 成功后按此时序驱动（低 20ms→高→等
 *      150ms 冗余），随后探测适配器 0/1；ACK 则打印"GPIO145 定向复位
 *      成功，触摸应答（适配器 N）"，保持 145 输出高不释放，跳过盲扫。
 *  【第 2 段·盲扫兜底】扫描线程每 500ms 一轮：遍历允许的 GPIO（禁扫清单
 *      见 banned_ranges，145 已由第 1 段处理不重扫），request→记原方向→
 *      输出高→等 60ms→对 i2c 适配器 0/1 各做一次 0x15 单字节读探测→无
 *      ACK 则输出低→60ms→再探→都无 ACK 恢复原方向并 free→下一个脚。
 *      任一 ACK：打印"GPIO N 点亮触摸（极性 X，适配器 Y）"，保持该脚
 *      输出状态不再动，停止扫描。
 *
 * 探测一律用 I2C_SMBUS_BYTE 单字节【读】：
 *     i2c_smbus_xfer(adap, 0x15, 0, I2C_SMBUS_READ, 0, I2C_SMBUS_BYTE, &d)。
 *     I2C_SMBUS_QUICK 已定案禁用——len=0 消息在 i2c-mt65xx 的 DMA 缓冲
 *     分配处一律返回 -ENOMEM（drivers/i2c/busses/i2c-mt65xx.c:1140-1142
 *     → drivers/i2c/i2c-core-base.c:2651-2652 `msg->len == 0 → return NULL`），
 *     事务从未上过总线，历轮 quick 扫描结果全部无效。
 *
 * errno 判读（每轮汇总进 kmsg，命中时 dump 全轮 raw 序列作证据）：
 *    0         = ACK（命中）
 *   -ENXIO     = 总线上地址被 NACK（芯片无电/不在）
 *   -ETIMEDOUT = 等不到完成中断（引脚未复用/总线卡死嫌疑）
 *   -ENODEV    = 该编号适配器不存在（i2c_get_adapter 返回 NULL）
 *   -ENOMEM    = 不应再出现（byte 读 len=1>0）；再出现说明 DMA 又出问题
 *
 * 安全设计（三层闸门，缺一不可）：
 *  1. 静态禁扫清单：PMIC_EINT/pwrap 四线/SRCLKENA/WATCHDOG(7-13)、
 *     eMMC MC0 含 RSTB(44-54)、MIPI DSI 差分对(79-84，屏在活动扫描)、
 *     i2c0(105/106，R3d dts 已启用)、i2c1 在用(113/114)、125(pinfunc
 *     无此编号，124 直接跳 126)。
 *  2. pinctrl_gpio_can_use_line() 闸门：脚正被 pinmux 用作非 GPIO 功能
 *     （如 dts 已 mux 的 SCL/SDA、eMMC、pwrap）则不碰——否则
 *     gpio_request 会把 mux 切到 GPIO 且 free 后不恢复，等于亲手拆总线。
 *  3. gpio_request 本身对已占用脚返回 -EBUSY（pinmux pin_request 兜底）。
 *
 * 注：89/90（SCL_1_1/SDA_1_1 候选）当前 dts 未 mux（i2c1_pins 用 113/114），
 * 作为普通空闲脚参与扫描；若后续 dts 改 mux 它们，本模块经闸门 2 自动让位。
 *
 * API 依据（Linux 7.0-rc7 本树 /tmp/z1_r3d_mod 逐条核实）：
 *  - legacy GPIO 可用：CONFIG_GPIOLIB_LEGACY=y（.config）；gpio_request/
 *    gpio_free 为真函数（drivers/gpio/gpiolib-legacy.c:58/18，
 *    EXPORT_SYMBOL_GPL:69/22）；gpio_direction_input/output/gpio_get_value/
 *    gpio_set_value 为 include/linux/gpio.h:56-81 内联包装（走
 *    gpiod_direction_input/gpiod_direction_output_raw/gpiod_get_raw_value/
 *    gpiod_set_raw_value，gpiolib.c:2850/2997/3501/3854 全部导出）。
 *  - 方向读取：gpio_get_direction 已删，走 gpiod_get_direction(desc)
 *    （gpiolib.c:445，EXPORT_SYMBOL_GPL:486；返回 1=入 0=出）。
 *    对 gpio_to_desc 所得 desc 绝不能 gpiod_put——desc 归全局号空间所有，
 *    put 会打穿引用计数，本模块全程不持有额外引用。
 *  - "GPIO 模式+输出" = gpio_request + gpio_direction_output：MTK
 *    pinctrl-paris 的 gpiochip 设 chip->request = gpiochip_generic_request
 *    （pinctrl-paris.c:948），request 即把 pinmux 切到 GPIO；direction_
 *    output 先写 DO 再设 DIR 出（pinctrl-paris.c:892-901）。
 *  - 全局号不可假设 0 起：pinctrl-paris mtk_build_gpiochip 设
 *    chip->base = -1 动态分配（pinctrl-paris.c:964），故运行时用
 *    gpio_device_find()（gpio/driver.h:628，gpiolib.c:1368 导出）按
 *    compatible "mediatek,mt6572-pinctrl"（pinctrl-mt6572.c:385）解析。
 *  - chip->ngpio = soc->npins = 154（pinctrl-mtk-mt6572.h 到 153 结束），
 *    GPIO145（pinfunc.h:800 MT6572_PIN_145_BPI_BUS15__FUNC_GPIO145）在
 *    范围内。
 *  - i2c：i2c_get_adapter/i2c_put_adapter（i2c-core-base.c:2620/2631）、
 *    i2c_smbus_xfer（i2c-core-smbus.c:552，签名 include/linux/i2c.h:139）
 *    均导出。
 *
 * vermagic 目标：7.0.0-rc7-z1+（/tmp/z1_r3d_mod R9 配方编译树）。
 */

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/delay.h>
#include <linux/kthread.h>
#include <linux/string.h>
#include <linux/errno.h>
#include <linux/gpio.h>
#include <linux/gpio/consumer.h>
#include <linux/gpio/driver.h>
#include <linux/i2c.h>
#include <linux/of.h>
#include <linux/pinctrl/consumer.h>

#define TAG "[gpiohunt] "

/* 盲扫上界：pin 0..134（任务定案"其余 0-134 全扫"）。135-153 是 BPI/BSI/VM
 * 射频脚，盲扫一律不动；GPIO145 是其中唯一例外——第 1 段按铁证定案单独
 * 定向驱动，其余射频脚不碰。
 *（驱动侧 pinctrl-mtk-mt6572.h 实有 154 脚（0..153），dt-bindings pinfunc
 *  宏只定义到 134+，以 dt-bindings 为准做盲扫上界。）
 */
#define HUNT_MAX_PIN	134

/* 第 1 段定向复位参数（GPIO145 = BPI_BUS15，触摸复位脚） */
#define RST_PIN			145	/* MT6572_PIN_145_BPI_BUS15__FUNC_GPIO145 */
#define RST_LOW_MS		20	/* 原厂序列：拉低 20ms */
#define RST_HIGH_WAIT_MS	150	/* 拉高后等 150ms（Tpor>=100ms 冗余） */

/* 每 phase 电平保持时长与轮间隔 */
#define HUNT_SETTLE_MS	60
#define HUNT_ROUND_MS	500

/* 命中来源 */
#define HUNT_NONE	0	/* 未命中 */
#define HUNT_BASELINE	1	/* 第 0 段：基线探测直接 ACK */
#define HUNT_RESET145	2	/* 第 1 段：GPIO145 定向复位后 ACK */
#define HUNT_BLIND	3	/* 第 2 段：盲扫某脚点亮 */

/* errno 证据缓冲：一轮最多 106 允扫脚 × 2 相位 × 2 总线 = 424 条，512 封顶 */
#define ERRNO_LOG_SZ	512

struct probe_rec {
	u8	pin;	/* 硬件脚号 0..134（145 为第 1 段定向复位） */
	u8	level;	/* 探测时该脚输出电平：1=高 0=低 */
	u8	bus;	/* i2c 适配器编号 */
	s32	err;	/* i2c_smbus_xfer 原始返回值 */
};

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
static int found_gpio = -1;	/* 命中脚号（基线命中保持 -1） */
module_param(found_gpio, int, 0444);
static int found_bus = -1;
module_param(found_bus, int, 0444);
static int found_level = -1;	/* 命中时锁存电平（基线命中 -1=未动 GPIO） */
module_param(found_level, int, 0444);
static int found_how = HUNT_NONE;	/* 1=基线 2=145复位 3=盲扫 */
module_param(found_how, int, 0444);

static struct gpio_chip *hunt_gc;	/* 仅作 can_use_line 的 chip 句柄；
					 * MTK pinctrl 内建(=y)永不卸载，安全 */
static int hunt_base = -1;		/* MT6572 gpiochip 全局 base */
static int hunt_rounds;
static struct task_struct *hunt_task;

/* 本轮 errno 证据环形缓冲（每轮开始清零；第 1 段记录也走这里） */
static struct probe_rec errno_log[ERRNO_LOG_SZ];
static unsigned int errno_log_n;

static void errno_log_add(u8 pin, u8 level, u8 bus, s32 err)
{
	if (errno_log_n >= ERRNO_LOG_SZ)
		return;		/* 封顶丢弃（正常一轮到不了 512） */
	errno_log[errno_log_n].pin = pin;
	errno_log[errno_log_n].level = level;
	errno_log[errno_log_n].bus = bus;
	errno_log[errno_log_n].err = err;
	errno_log_n++;
}

/* 其他类 errno 聚合（ACK/ENXIO/ETIMEDOUT 之外的，最多记 8 种） */
struct other_err {
	s32 err;
	unsigned int count;
};

static void round_summary(void)
{
	unsigned int n, i, ack = 0, enxio = 0, etim = 0, enodev = 0;
	struct other_err others[8];
	unsigned int n_others = 0;

	for (n = 0; n < errno_log_n; n++) {
		s32 e = errno_log[n].err;

		if (e == 0) {
			ack++;
		} else if (e == -ENXIO) {
			enxio++;
		} else if (e == -ETIMEDOUT) {
			etim++;
		} else if (e == -ENODEV) {
			enodev++;
		} else {
			for (i = 0; i < n_others; i++) {
				if (others[i].err == e) {
					others[i].count++;
					break;
				}
			}
			if (i == n_others && n_others < ARRAY_SIZE(others)) {
				others[n_others].err = e;
				others[n_others].count = 1;
				n_others++;
			}
		}
	}

	pr_info(TAG "第 %d 轮 errno 汇总（共 %u 次探测）："
		"ACK=%u -ENXIO(NACK)=%u -ETIMEDOUT(无完成中断)=%u -ENODEV(无适配器)=%u",
		hunt_rounds, errno_log_n, ack, enxio, etim, enodev);
	for (i = 0; i < n_others; i++)
		pr_cont(" 其他 errno %d ×%u", others[i].err, others[i].count);
	pr_cont("\n");
}

/* 命中时 dump 全部 raw errno 序列（证据） */
static void dump_errno_log(const char *stage)
{
	char line[160];
	int len, n;

	pr_err(TAG "%s raw errno 序列（pin/相位L-H/总线=errno，%u 条）：\n",
	       stage, errno_log_n);
	for (n = 0, len = 0; n < (int)errno_log_n; n++) {
		len += scnprintf(line + len, sizeof(line) - len, "%u/%s/b%d=%d ",
				 errno_log[n].pin,
				 errno_log[n].level ? "H" : "L",
				 errno_log[n].bus, errno_log[n].err);
		if (len >= (int)sizeof(line) - 32 || n == (int)errno_log_n - 1) {
			pr_err(TAG "  %s\n", line);
			len = 0;
		}
	}
}

/*
 * 静态禁扫清单（编号 = dt-bindings/pinctrl/mt6572-pinfunc.h 的 pin 号，
 * 本树逐条核实）：
 *  - 7      ：PMIC_EINT（pinfunc.h:41）
 *  - 8..11  ：PMIC_SPI CSN/SCK/MOSI/MISO = pwrap 四线（pinfunc.h:44-54），
 *             拉错最坏误写 PMIC 核心轨立即掉电
 *  - 12     ：SRCLKENA（pinfunc.h:56）
 *  - 13     ：WATCHDOG（pinfunc.h:59），翻转可能直接咬死复位
 *  - 44..54 ：eMMC mmc0 数据/命令/时钟 + 54=RSTB 拉低=硬复位 eMMC
 *  - 79..84 ：MIPI DSI 差分对 TDP0/TDN0/TDP1/TDN1/TCP/TCN
 *             （pinfunc.h:432-455），屏在活动扫描，禁碰
 *  - 105,106：i2c0 SCL_0_0/SDA_0_0（R3d dts 已启用，pinfunc.h:593-597）
 *  - 113,114：i2c1 SCL_1_0/SDA_1_0 在用（pinfunc.h:637-641）
 *  - 125    ：pinfunc 无此编号（124 CONN_RSTB 直接跳 126），防御性禁扫
 *  其余 0-134 全扫；GPIO145 由第 1 段定向复位处理，不进盲扫。
 *  JTAG（JTMS/JTCK/JTDI/JTDO）经 pinfunc.h 查证无专用脚、仅 FUNC 复用
 *  （87/88/91/93/95/96/101/102/109/112 及 89/90 自身），GPIO 模式下扫描
 *  安全，且 89/90 正是本轮候选，故不因 JTAG 复用禁扫。
 */
static const struct {
	int first, last;
	const char *why;
} banned_ranges[] = {
	{   7,  13, "PMIC_EINT/pwrap 四线(8-11)/SRCLKENA(12)/WATCHDOG(13)" },
	{  44,  54, "eMMC MC0 数据/命令/时钟/RSTB(54)" },
	{  79,  84, "MIPI DSI 差分对（屏在活动扫描）" },
	{ 105, 106, "i2c0 SCL_0_0/SDA_0_0（R3d 已启用）" },
	{ 113, 114, "i2c1 SCL_1_0/SDA_1_0 在用" },
	{ 125, 125, "pinfunc 无此编号（124 直接跳 126）" },
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

/*
 * 对指定适配器做一次 0x15 单字节【读】探测（QUICK 已定案禁用，见头注释）。
 * 返回值即 i2c_smbus_xfer 原始 errno：0 = ACK（命中），负值 = 无应答/错误。
 */
static int probe_bus(int bus)
{
	union i2c_smbus_data dummy;
	struct i2c_adapter *adap;
	int ret;

	adap = i2c_get_adapter(bus);
	if (!adap)
		return -ENODEV;		/* 适配器不存在，区别于总线 NACK */

	ret = i2c_smbus_xfer(adap, hunt_addr, 0, I2C_SMBUS_READ, 0,
			     I2C_SMBUS_BYTE, &dummy);

	i2c_put_adapter(adap);
	return ret;
}

/* 基础合法性/闸门检查，返回 0=可碰；负值=跳过原因（已打印） */
static int pin_gate(int pin, int global, struct gpio_desc **desc_out)
{
	struct gpio_desc *desc;

	/* 全局号必须落在本 chip 范围内 */
	if (!gpio_is_valid(global) || !hunt_gc ||
	    pin >= (int)hunt_gc->ngpio) {
		pr_info(TAG "GPIO%d: 超出 MT6572 gpiochip 范围（base=%d ngpio=%u），跳过\n",
			pin, hunt_base, hunt_gc ? hunt_gc->ngpio : 0);
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

	*desc_out = desc;
	return 0;
}

/* 尽力恢复某脚到 request 前的方向/电平（desc 由 gpio_to_desc 所得，不 put） */
static void pin_restore(int global, struct gpio_desc *desc,
			int orig_dir, int orig_val)
{
	if (orig_dir == GPIO_LINE_DIRECTION_IN)
		gpio_direction_input(global);
	else if (orig_dir == GPIO_LINE_DIRECTION_OUT)
		gpio_direction_output(global, orig_val);
	else
		gpio_direction_input(global);	/* 读不到原方向则保守回输入 */
}

/* 记录某脚 request 前的方向/电平（供恢复） */
static void pin_snapshot(int global, struct gpio_desc *desc,
			 int *orig_dir, int *orig_val)
{
	*orig_dir = gpiod_get_direction(desc);
	*orig_val = 0;
	if (*orig_dir == GPIO_LINE_DIRECTION_OUT)
		*orig_val = gpio_get_value(global);
}

/*
 * 【第 0 段·基线探测】加载即做：对适配器 0/1 各一次 0x15 单字节读，
 * errno 原样进 kmsg。dts 已给 VGP1 加 always-on（上电根因已修），芯片
 * 可能直接就 ACK——ACK 则报告命中来源并收手，不动任何 GPIO。
 * 返回 0 = 基线即 ACK（后续段全部跳过）；-1 = 无 ACK（继续第 1 段）。
 */
static int baseline_probe(void)
{
	int bus, ret;

	pr_info(TAG "第 0 段·基线探测：dts VGP1 已 always-on，先直接试 0x%02x\n",
		hunt_addr);

	for (bus = 0; bus < 2; bus++) {
		if (!want_bus(bus))
			continue;
		ret = probe_bus(bus);
		pr_info(TAG "基线探测 i2c-%d @0x%02x: errno=%d"
			"（0=ACK / -ENXIO=总线上NACK / -ETIMEDOUT=等不到完成中断）\n",
			bus, hunt_addr, ret);

		if (ret == 0) {
			pr_err(TAG "************************************************\n");
			pr_err(TAG "基线探测即命中：触摸 0x%02x 在 i2c-%d ACK"
				"（VGP1 always-on 修复生效），无需复位/盲扫\n",
				hunt_addr, bus);
			pr_err(TAG "************************************************\n");
			found_how = HUNT_BASELINE;
			found_bus = bus;
			found_level = -1;	/* 未动任何 GPIO */
			return 0;
		}
	}
	return -1;
}

/*
 * 【第 1 段·定向复位】GPIO145（BPI_BUS15）铁证触摸复位脚：
 * request（即切 GPIO 模式）→ 输出低 20ms → 拉高 → 等 150ms（Tpor≥100ms
 * 冗余）→ 探测适配器 0/1。ACK 则打印"GPIO145 定向复位成功，触摸应答
 * （适配器 N）"，保持 145 输出高不释放，跳过盲扫；无 ACK/不可 request
 * 则恢复原方向并 free，转第 2 段盲扫。
 * 返回 0 = 命中（已锁存）；-1 = 未命中或跳过（转盲扫）。
 */
static int targeted_reset_145(void)
{
	int global = hunt_base + RST_PIN;
	struct gpio_desc *desc = NULL;
	int orig_dir, orig_val = 0;
	int ret, bus;

	pr_info(TAG "第 1 段·定向复位：GPIO%d（BPI_BUS15，十路取证定案触摸"
		"复位脚），序列=低%dms→高→等%dms\n",
		RST_PIN, RST_LOW_MS, RST_HIGH_WAIT_MS);

	ret = pin_gate(RST_PIN, global, &desc);
	if (ret) {
		pr_info(TAG "GPIO%d: 定向复位前置检查不过（%d），跳过复位转盲扫\n",
			RST_PIN, ret);
		return -1;
	}

	/* 闸门 3：request（GPIO 模式切换在此发生；被占则 -EBUSY 兜底） */
	ret = gpio_request(global, "z1_gpio_hunt_rst145");
	if (ret) {
		pr_info(TAG "GPIO%d: request 失败 %d（在用），定向复位跳过，转盲扫\n",
			RST_PIN, ret);
		return -1;
	}

	pin_snapshot(global, desc, &orig_dir, &orig_val);

	/* 原厂序列：GPIO 模式+输出（request+direction_output）→拉低 20ms
	 * →拉高→等 150ms 冗余（规格 Tpor≥100ms）
	 */
	gpio_direction_output(global, 0);
	msleep_interruptible(RST_LOW_MS);
	gpio_set_value(global, 1);
	msleep_interruptible(RST_HIGH_WAIT_MS);

	for (bus = 0; bus < 2; bus++) {
		if (!want_bus(bus))
			continue;
		ret = probe_bus(bus);
		errno_log_add(RST_PIN, 1, bus, ret);	/* 证据进缓冲 */
		if (ret == 0) {
			pr_err(TAG "************************************************\n");
			pr_err(TAG "GPIO145 定向复位成功，触摸应答（适配器 %d）\n",
			       bus);
			pr_err(TAG ">>> 触摸 0x%02x ACK，保持 GPIO%d 输出高"
				"（不恢复不释放），跳过盲扫\n",
				hunt_addr, RST_PIN);
			dump_errno_log("第 1 段（GPIO145 复位后）");
			pr_err(TAG "************************************************\n");
			found_how = HUNT_RESET145;
			found_gpio = RST_PIN;
			found_bus = bus;
			found_level = 1;
			return 0;		/* 保持输出高，不 free */
		}
	}

	/* 未命中：恢复原方向/原值并释放，转盲扫 */
	pin_restore(global, desc, orig_dir, orig_val);
	gpio_free(global);
	pr_info(TAG "GPIO%d: 定向复位后仍无 ACK（errno 见第 1 段记录，"
		"原dir=%d 原val=%d），已恢复释放，转第 2 段盲扫\n",
		RST_PIN, orig_dir, orig_val);
	return -1;
}

/*
 * 【第 2 段·盲扫】扫单个脚（pin 为 MT6572 硬件脚号 0..134）。
 * 流程（任务定案）：request→记原方向→输出高→60ms→对 i2c0/i2c1 各探一次
 * →无 ACK 则输出低→60ms→再探→都无 ACK 恢复原方向并 free→下一个脚。
 * 返回 1 = 命中（脚已锁存为输出、未释放、不再动）；
 *       0 = 未命中（已恢复原方向/原值并释放）；
 *       负值 = 跳过（禁扫/pinmux 占用/request 失败），未动电平。
 */
static int scan_one_pin(int pin)
{
	int global = hunt_base + pin;
	struct gpio_desc *desc = NULL;
	const char *why = NULL;
	int orig_dir, orig_val = 0;
	int ret, bus, level;

	if (is_banned(pin, &why)) {
		if (verbose)
			pr_info(TAG "GPIO%d: 禁扫（%s），跳过\n", pin, why);
		return -EPERM;
	}

	if (pin_gate(pin, global, &desc))
		return -EINVAL;

	/* 闸门 3：request（被占用的脚在这里 -EBUSY 兜底） */
	ret = gpio_request(global, "z1_gpio_hunt");
	if (ret) {
		if (verbose)
			pr_info(TAG "GPIO%d: request 失败 %d（在用），跳过\n",
				pin, ret);
		return ret;
	}

	/* 记录原方向/原值，尽力恢复。
	 * gpiod_get_direction 返回 GPIO_LINE_DIRECTION_IN(1)/OUT(0)，
	 * 负值=读失败。此处 desc 来自 gpio_to_desc，全程不可 gpiod_put。
	 */
	pin_snapshot(global, desc, &orig_dir, &orig_val);

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
			errno_log_add(pin, level, bus, ret);
			if (ret == 0) {
				pr_err(TAG "************************************************\n");
				pr_err(TAG "GPIO%d 点亮触摸（极性 %s，适配器 %d）！\n",
				       pin, level ? "高" : "低", bus);
				pr_err(TAG ">>> 触摸 0x%02x 在 i2c-%d ACK，"
					"锁存 GPIO%d 输出%s（不恢复不释放），停止扫描\n",
				       hunt_addr, bus, pin, level ? "高" : "低");
				dump_errno_log("本轮盲扫");	/* raw errno 序列证据 */
				pr_err(TAG "************************************************\n");
				found_gpio = pin;
				found_bus = bus;
				found_level = level;
				found_how = HUNT_BLIND;
				return 1;	/* 保持输出电平，不 free */
			}
		}
	}

	/* 未命中：恢复原方向/原值并释放 */
	pin_restore(global, desc, orig_dir, orig_val);
	gpio_free(global);

	if (verbose)
		pr_info(TAG "GPIO%d: 无 ACK（原dir=%d 原val=%d），已恢复释放\n",
			pin, orig_dir, orig_val);
	return 0;
}

static int hunt_thread_fn(void *unused)
{
	int pin, r;

	/* 第 1 段·定向复位（一次性，优先于盲扫；基线已在 init 段做过） */
	if (found_gpio < 0 && found_how == HUNT_NONE &&
	    targeted_reset_145() == 0)
		goto hit;

	while (!kthread_should_stop()) {
		if (found_gpio >= 0 || found_how != HUNT_NONE) {
			/* 命中后挂起等 rmmod，电平保持不动 */
			msleep_interruptible(HUNT_ROUND_MS);
			continue;
		}

		hunt_rounds++;
		errno_log_n = 0;		/* 新一轮证据缓冲 */
		for (pin = 0; pin <= HUNT_MAX_PIN; pin++) {
			if (kthread_should_stop())
				break;
			r = scan_one_pin(pin);
			if (r == 1)
				break;	/* 命中，本轮终止 */
		}

		if (found_gpio < 0) {
			round_summary();	/* 每轮 errno 记 kmsg */
			pr_info(TAG "第 %d 轮扫描完成，无 ACK，%dms 后下一轮\n",
				hunt_rounds, HUNT_ROUND_MS);
		}

		msleep_interruptible(HUNT_ROUND_MS);
	}
hit:
	while (!kthread_should_stop())
		msleep_interruptible(HUNT_ROUND_MS);
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
		 * 找不到 chip 时闸门 2 退化为仅靠 request 的 EBUSY 兜底）
		 */
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
		 * 立即释放引用；chip 本体内建永生，指针此后仍有效
		 */
		gpio_device_put(gdev);
	}

	/* 第 0 段·基线探测：加载即做（i2c_smbus_xfer 可睡眠，init 上下文允许）。
	 * ACK 则收手——连扫描线程都不起。
	 */
	if (baseline_probe() == 0) {
		pr_info(TAG "基线即 ACK，不启动扫描线程；结果见 /sys/module/z1_gpio_hunt/parameters/found_*\n");
		return 0;
	}

	pr_info(TAG "启动扫描线程：盲扫 pin 0..%d（禁扫 7-13/44-54/79-84/"
		"105-106/113-114/125），先 GPIO%d 定向复位，"
		"BYTE-READ@0x%02x 于 i2c-%s%s，相位保持 %dms，轮间隔 %dms\n",
		HUNT_MAX_PIN, RST_PIN, hunt_addr,
		scan_bus1 ? "0" : "", scan_bus2 ? "+1" : "",
		HUNT_SETTLE_MS, HUNT_ROUND_MS);

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

	if (found_how == HUNT_BASELINE)
		pr_info(TAG "卸载（基线探测即命中，i2c-%d，未动任何 GPIO）\n",
			found_bus);
	else if (found_gpio >= 0)
		pr_err(TAG "卸载：GPIO%d 仍锁存为输出%s（未恢复未释放，按设计；"
			"重启或手动 gpio 操作才会改变）\n",
			found_gpio, found_level ? "高" : "低");
	else
		pr_info(TAG "卸载（共 %d 轮盲扫，未发现 ACK；GPIO145 复位未命中）\n",
			hunt_rounds);
}

module_init(z1_gpio_hunt_init);
module_exit(z1_gpio_hunt_exit);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Z1 (MT6572) 触摸电源/复位脚猎人（三段式：基线探测→GPIO145 定向复位→盲扫；i2c 0x15 单字节读探测，QUICK 已禁用）");
MODULE_AUTHOR("z1-mt6572-mainline bringup");
