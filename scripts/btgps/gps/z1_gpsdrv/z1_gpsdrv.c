// SPDX-License-Identifier: GPL-2.0
/*
 * z1_gpsdrv - recreate the MediaTek "gpsdrv" sysfs class that the stock
 * 3.4.67 mt3326-gps driver provided on the Z1 (MT6572).
 *
 * Why: the MediaTek mnld userspace daemon (Android 9 image, /vendor/bin/mnld)
 * unconditionally opens/writes five attributes under
 * /sys/class/gpsdrv/gps/ during its initialisation:
 *
 *   /sys/class/gpsdrv/gps/status   write 48 bytes: "(%d/%d/%d %d:%d:%d) - %d/%d"
 *   /sys/class/gpsdrv/gps/pwrctl   write 1 byte : '0'..'5'
 *   /sys/class/gpsdrv/gps/suspend  write 1 byte : '0'/'1'
 *   /sys/class/gpsdrv/gps/pwrsave  write 1 byte : '0'/'1'
 *   /sys/class/gpsdrv/gps/state    write 1 byte : '0'..'5'
 *
 * mnl_set_status() aborts mnl_init() when the first open() fails, which is
 * exactly what happens on the mainline kernel (no mt3326 driver), so mnld
 * exits with status 1 ~40 ms after start.
 *
 * The nodes are write-only in practice (mnld never reads them); the real
 * GPS power rail / WMT function-on is handled by mtk_wcn_wmt_func_on().
 * This module only needs to accept those writes.
 *
 * Attribute mode is 0666 because the Android service definition runs mnld
 * as user "system" (uid 1000) and we do not want to depend on an init.rc
 * chown.  (The stock init.rc did: chown gps gps + 0660.)
 */

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/device.h>
#include <linux/fs.h>
#include <linux/mutex.h>
#include <linux/string.h>
#include <linux/slab.h>

#define GPSDRV_ATTR_MAX 64

struct gpsdrv_attr {
	struct device_attribute dev_attr;
	struct mutex lock;
	char buf[GPSDRV_ATTR_MAX];
	size_t len;
};

static ssize_t gpsdrv_show(struct device *dev, struct device_attribute *attr,
			   char *buf)
{
	struct gpsdrv_attr *a = container_of(attr, struct gpsdrv_attr, dev_attr);
	ssize_t ret;

	mutex_lock(&a->lock);
	if (a->len == 0)
		ret = sysfs_emit(buf, "0\n");
	else
		ret = sysfs_emit(buf, "%s\n", a->buf);
	mutex_unlock(&a->lock);

	return ret;
}

static ssize_t gpsdrv_store(struct device *dev, struct device_attribute *attr,
			    const char *buf, size_t count)
{
	struct gpsdrv_attr *a = container_of(attr, struct gpsdrv_attr, dev_attr);
	size_t n = min(count, (size_t)GPSDRV_ATTR_MAX - 1);

	mutex_lock(&a->lock);
	memcpy(a->buf, buf, n);
	a->buf[n] = '\0';
	a->len = n;
	mutex_unlock(&a->lock);

	return count;
}

static struct gpsdrv_attr gpsdrv_status = {
	.dev_attr = __ATTR(status, 0660, gpsdrv_show, gpsdrv_store),
};
static struct gpsdrv_attr gpsdrv_state = {
	.dev_attr = __ATTR(state, 0660, gpsdrv_show, gpsdrv_store),
};
static struct gpsdrv_attr gpsdrv_pwrctl = {
	.dev_attr = __ATTR(pwrctl, 0660, gpsdrv_show, gpsdrv_store),
};
static struct gpsdrv_attr gpsdrv_suspend = {
	.dev_attr = __ATTR(suspend, 0660, gpsdrv_show, gpsdrv_store),
};
static struct gpsdrv_attr gpsdrv_pwrsave = {
	.dev_attr = __ATTR(pwrsave, 0660, gpsdrv_show, gpsdrv_store),
};

static struct gpsdrv_attr *gpsdrv_attrs[] = {
	&gpsdrv_status,
	&gpsdrv_state,
	&gpsdrv_pwrctl,
	&gpsdrv_suspend,
	&gpsdrv_pwrsave,
};

static struct class *gpsdrv_class;
static struct device *gpsdrv_dev;

static int __init z1_gpsdrv_init(void)
{
	int i, ret;

	for (i = 0; i < ARRAY_SIZE(gpsdrv_attrs); i++)
		mutex_init(&gpsdrv_attrs[i]->lock);

	gpsdrv_class = class_create("gpsdrv");
	if (IS_ERR(gpsdrv_class))
		return PTR_ERR(gpsdrv_class);

	/* devt 0 => sysfs-only device, no /dev node is created */
	gpsdrv_dev = device_create(gpsdrv_class, NULL, MKDEV(0, 0), NULL,
				   "gps");
	if (IS_ERR(gpsdrv_dev)) {
		ret = PTR_ERR(gpsdrv_dev);
		goto err_class;
	}

	for (i = 0; i < ARRAY_SIZE(gpsdrv_attrs); i++) {
		ret = device_create_file(gpsdrv_dev, &gpsdrv_attrs[i]->dev_attr);
		if (ret)
			goto err_files;
		/*
		 * __ATTR() refuses 0666 at compile time (kernel policy: no
		 * other-writable sysfs files).  We need mnld (uid 1000,
		 * gid 1000) to write these; widen at runtime.  The stock
		 * driver used 0660 + "chown gps gps" in init.rc.
		 */
		if (sysfs_chmod_file(&gpsdrv_dev->kobj,
				     &gpsdrv_attrs[i]->dev_attr.attr, 0666))
			pr_warn("z1_gpsdrv: could not widen mode of %s\n",
				gpsdrv_attrs[i]->dev_attr.attr.name);
	}

	pr_info("z1_gpsdrv: /sys/class/gpsdrv/gps/{status,state,pwrctl,suspend,pwrsave} created (mode 0666)\n");
	return 0;

err_files:
	while (--i >= 0)
		device_remove_file(gpsdrv_dev, &gpsdrv_attrs[i]->dev_attr);
	device_destroy(gpsdrv_class, MKDEV(0, 0));
err_class:
	class_destroy(gpsdrv_class);
	return ret;
}

static void __exit z1_gpsdrv_exit(void)
{
	int i;

	for (i = 0; i < ARRAY_SIZE(gpsdrv_attrs); i++)
		device_remove_file(gpsdrv_dev, &gpsdrv_attrs[i]->dev_attr);
	device_destroy(gpsdrv_class, MKDEV(0, 0));
	class_destroy(gpsdrv_class);
	pr_info("z1_gpsdrv: removed\n");
}

module_init(z1_gpsdrv_init);
module_exit(z1_gpsdrv_exit);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Z1 MT6572 gpsdrv sysfs class shim for MediaTek mnld");
MODULE_AUTHOR("Z1 mainline port");
