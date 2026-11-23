// SPDX-License-Identifier: GPL-2.0-only
/* Z1 bootloader-owned FIFO timing, AP PWM channel 1. */
#include <linux/bitfield.h>
#include <linux/clk.h>
#include <linux/io.h>
#include <linux/leds.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>

#define ENABLE       0x00
#define CHANNEL      BIT(1)
#define CONTROL      0x50
#define DATA0        0x70
#define DATA1        0x74
#define STOP_BIT     GENMASK(14, 9)
#define OLD_MODE     BIT(15)
#define MEMORY_MODE  BIT(5)
#define RANDOM_MODE  BIT(6)
#define LEVELS       6 /* R7 lights HAL uses the existing six-step LED range. */

struct z1_bl {
	struct led_classdev led;
	void __iomem *base;
	struct mutex lock;
	u64 factory_data;
	unsigned int factory_high;
	unsigned int requested;
	bool blank;
};

static void enable_channel(struct z1_bl *bl, bool enable)
{
	u32 value = readl(bl->base + ENABLE);

	if (!!(value & CHANNEL) != enable)
		writel(enable ? value | CHANNEL : value & ~CHANNEL, bl->base + ENABLE);
}

/* Keep the factory high-bit ordering and maximum duty. */
static u64 fifo_data(struct z1_bl *bl, unsigned int level)
{
	u64 data = bl->factory_data;
	unsigned int keep = DIV_ROUND_UP(bl->factory_high * level, LEVELS);

	while (keep < bl->factory_high) {
		data &= data - 1;
		keep++;
	}
	return data;
}

static int apply_locked(struct z1_bl *bl)
{
	bool on = !bl->blank && bl->requested;
	u64 data;

	if (on) {
		data = fifo_data(bl, bl->requested);
		writel(lower_32_bits(data), bl->base + DATA0);
		writel(upper_32_bits(data), bl->base + DATA1);
	}
	enable_channel(bl, on);
	return !!(readl(bl->base + ENABLE) & CHANNEL) == on ? 0 : -EIO;
}

static int set_brightness(struct led_classdev *led, enum led_brightness level)
{
	struct z1_bl *bl = container_of(led, struct z1_bl, led);
	int ret;

	mutex_lock(&bl->lock);
	bl->requested = level;
	ret = apply_locked(bl);
	mutex_unlock(&bl->lock);
	return ret;
}

static struct z1_bl *from_led_dev(struct device *dev)
{
	struct led_classdev *led = dev_get_drvdata(dev);

	return container_of(led, struct z1_bl, led);
}

static ssize_t display_blank_show(struct device *dev,
				  struct device_attribute *attr, char *buf)
{
	struct z1_bl *bl = from_led_dev(dev);

	return sysfs_emit(buf, "%u\n", READ_ONCE(bl->blank));
}

static ssize_t display_blank_store(struct device *dev, struct device_attribute *attr,
				   const char *buf, size_t count)
{
	struct z1_bl *bl = from_led_dev(dev);
	bool blank;
	int ret = kstrtobool(buf, &blank);

	if (ret)
		return ret;
	mutex_lock(&bl->lock);
	bl->blank = blank;
	ret = apply_locked(bl);
	mutex_unlock(&bl->lock);
	return ret ? ret : count;
}
static DEVICE_ATTR_RW(display_blank);

static ssize_t pwm1_enabled_show(struct device *dev, struct device_attribute *attr,
				 char *buf)
{
	struct z1_bl *bl = from_led_dev(dev);

	return sysfs_emit(buf, "%u\n", !!(readl(bl->base + ENABLE) & CHANNEL));
}
static DEVICE_ATTR_RO(pwm1_enabled);

static struct attribute *z1_attrs[] = {
	&dev_attr_display_blank.attr,
	&dev_attr_pwm1_enabled.attr,
	NULL,
};
ATTRIBUTE_GROUPS(z1);

static void restore_factory(void *arg)
{
	struct z1_bl *bl = arg;

	writel(lower_32_bits(bl->factory_data), bl->base + DATA0);
	writel(upper_32_bits(bl->factory_data), bl->base + DATA1);
	enable_channel(bl, true);
}

static int z1_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct z1_bl *bl;
	struct clk *clk;
	u32 control, last;
	u64 active;
	int ret;

	bl = devm_kzalloc(dev, sizeof(*bl), GFP_KERNEL);
	if (!bl)
		return -ENOMEM;
	bl->base = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(bl->base))
		return PTR_ERR(bl->base);
	/* The seven clock specifiers in this board DT all refer to CLK_TOP_PWM. */
	clk = devm_clk_get_enabled(dev, "top");
	if (IS_ERR(clk))
		return dev_err_probe(dev, PTR_ERR(clk), "AP PWM clock\n");
	control = readl(bl->base + CONTROL);
	last = FIELD_GET(STOP_BIT, control);
	bl->factory_data = readl(bl->base + DATA0);
	bl->factory_data |= (u64)readl(bl->base + DATA1) << 32;
	active = bl->factory_data & GENMASK_ULL(last, 0);
	dev_info(dev, "LK enable=%#x PWM1 control=%#x data=%016llx\n",
		 readl(bl->base + ENABLE), control, bl->factory_data);
	if (!(readl(bl->base + ENABLE) & CHANNEL) ||
	    (control & (OLD_MODE | MEMORY_MODE | RANDOM_MODE)) ||
	    !active)
		return dev_err_probe(dev, -ENODEV, "unexpected LK PWM1 FIFO state\n");
	/* Bits beyond the STOP position are never emitted. */
	bl->factory_high = hweight64(active);
	mutex_init(&bl->lock);
	bl->requested = LEVELS;
	bl->led.name = "lcd-backlight";
	bl->led.max_brightness = LEVELS;
	bl->led.brightness = LEVELS;
	bl->led.brightness_set_blocking = set_brightness;
	bl->led.groups = z1_groups;
	ret = devm_add_action_or_reset(dev, restore_factory, bl);
	if (ret)
		return ret;
	return devm_led_classdev_register(dev, &bl->led);
}

static const struct of_device_id z1_match[] = {
	{ .compatible = "z1,mt6572-lk-pwm1-backlight" },
	{}
};
MODULE_DEVICE_TABLE(of, z1_match);
static struct platform_driver z1_driver = {
	.probe = z1_probe,
	.driver = { .name = "z1-pwm1-backlight", .of_match_table = z1_match },
};
module_platform_driver(z1_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Z1 LK FIFO PWM1 panel backlight");
