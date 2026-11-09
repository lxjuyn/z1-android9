/*
 * z1drmtest — minimal DRM/KMS probe for the Z1 (MT6572 mediatek-drm).
 *
 * Purpose: answer the one question that gates drm_hwcomposer on this board:
 * can a user-space KMS client open /dev/dri/card0, enable universal planes +
 * atomic, and enumerate CRTCs / connectors / planes?  Everything is written to
 * /dev/kmsg because the device has no shell or adb.
 *
 * Build: NDK clang + static libdrm (mesa_android/libdrm-static-build/libdrm.a)
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <xf86drm.h>
#include <xf86drmMode.h>

static int kmsg_fd = -1;

static void klog(const char *fmt, ...)
{
	char buf[1024];
	va_list ap;
	int n;

	va_start(ap, fmt);
	n = vsnprintf(buf, sizeof(buf), fmt, ap);
	va_end(ap);
	if (n < 0)
		return;
	if (kmsg_fd >= 0)
		(void)write(kmsg_fd, buf, (size_t)n);
	(void)write(STDOUT_FILENO, buf, (size_t)n);
}

#define KLOG(...) klog("z1drm: " __VA_ARGS__)

static const char *conn_type_name(int t)
{
	switch (t) {
	case DRM_MODE_CONNECTOR_Unknown: return "Unknown";
	case DRM_MODE_CONNECTOR_VGA: return "VGA";
	case DRM_MODE_CONNECTOR_DVII: return "DVI-I";
	case DRM_MODE_CONNECTOR_DVID: return "DVI-D";
	case DRM_MODE_CONNECTOR_DVIA: return "DVI-A";
	case DRM_MODE_CONNECTOR_Composite: return "Composite";
	case DRM_MODE_CONNECTOR_LVDS: return "LVDS";
	case DRM_MODE_CONNECTOR_Component: return "Component";
	case DRM_MODE_CONNECTOR_9PinDIN: return "DIN";
	case DRM_MODE_CONNECTOR_DisplayPort: return "DP";
	case DRM_MODE_CONNECTOR_HDMIA: return "HDMI-A";
	case DRM_MODE_CONNECTOR_HDMIB: return "HDMI-B";
	case DRM_MODE_CONNECTOR_TV: return "TV";
	case DRM_MODE_CONNECTOR_eDP: return "eDP";
	case DRM_MODE_CONNECTOR_VIRTUAL: return "Virtual";
	case DRM_MODE_CONNECTOR_DSI: return "DSI";
	default: return "?";
	}
}

static const char *conn_status_name(drmModeConnection s)
{
	switch (s) {
	case DRM_MODE_CONNECTED: return "connected";
	case DRM_MODE_DISCONNECTED: return "disconnected";
	default: return "unknown";
	}
}

static void dump_props(int fd, uint32_t id, uint32_t type, const char *what)
{
	drmModeObjectProperties *props;
	uint32_t i, j;

	props = drmModeObjectGetProperties(fd, id, type);
	if (!props) {
		KLOG("%s id=%u: no properties (%s)\n", what, id, strerror(errno));
		return;
	}
	for (i = 0; i < props->count_props; i++) {
		drmModePropertyRes *p = drmModeGetProperty(fd, props->props[i]);
		if (!p)
			continue;
		if (p->flags & DRM_MODE_PROP_ENUM) {
			const char *name = "?";
			for (j = 0; j < (uint32_t)p->count_enums; j++)
				if (p->enums[j].value == props->prop_values[i]) {
					name = p->enums[j].name;
					break;
				}
			KLOG("  %s id=%u prop %s = %llu (%s)\n", what, id, p->name,
			     (unsigned long long)props->prop_values[i], name);
		} else {
			KLOG("  %s id=%u prop %s = %llu\n", what, id, p->name,
			     (unsigned long long)props->prop_values[i]);
		}
		drmModeFreeProperty(p);
	}
	drmModeFreeObjectProperties(props);
}

static int probe(const char *path)
{
	int fd, ret;
	drmVersion *ver;
	drmModeRes *res;
	drmModePlaneRes *pres;
	int i;

	fd = open(path, O_RDWR | O_CLOEXEC);
	if (fd < 0) {
		KLOG("open %s failed: %s\n", path, strerror(errno));
		return -1;
	}

	ver = drmGetVersion(fd);
	KLOG("---- %s fd=%d driver=%s ----\n", path, fd,
	     ver ? ver->name : "?");
	if (ver)
		drmFreeVersion(ver);

	ret = drmSetClientCap(fd, DRM_CLIENT_CAP_UNIVERSAL_PLANES, 1);
	KLOG("  DRM_CLIENT_CAP_UNIVERSAL_PLANES -> %d (%s)\n", ret,
	     ret ? strerror(errno) : "ok");

	ret = drmSetClientCap(fd, DRM_CLIENT_CAP_ATOMIC, 1);
	KLOG("  DRM_CLIENT_CAP_ATOMIC -> %d (%s)\n", ret,
	     ret ? strerror(errno) : "ok");
	if (ret) {
		KLOG("  node %s: no atomic modeset (not a KMS device)\n", path);
		close(fd);
		return -1;
	}

	res = drmModeGetResources(fd);
	if (!res) {
		KLOG("  drmModeGetResources failed: %s\n", strerror(errno));
		close(fd);
		return -1;
	}
	KLOG("resources: crtcs=%d encoders=%d connectors=%d fbs=%d\n",
	     res->count_crtcs, res->count_encoders, res->count_connectors,
	     res->count_fbs);

	for (i = 0; i < res->count_crtcs; i++)
		KLOG("crtc[%d] id=%u\n", i, res->crtcs[i]);
	for (i = 0; i < res->count_encoders; i++)
		KLOG("encoder[%d] id=%u type=%u\n", i, res->encoders[i], 0);
	for (i = 0; i < res->count_connectors; i++) {
		drmModeConnector *c = drmModeGetConnector(fd, res->connectors[i]);
		if (!c)
			continue;
		KLOG("connector[%d] id=%u type=%s status=%s encoder_id=%u modes=%d\n",
		     i, c->connector_id, conn_type_name(c->connector_type),
		     conn_status_name(c->connection), c->encoder_id, c->count_modes);
		if (c->count_modes > 0)
			KLOG("  preferred mode: %s %ux%u@%u\n",
			     c->modes[0].name, c->modes[0].hdisplay,
			     c->modes[0].vdisplay, c->modes[0].vrefresh);
		drmModeFreeConnector(c);
	}

	pres = drmModeGetPlaneResources(fd);
	if (!pres) {
		KLOG("drmModeGetPlaneResources failed: %s\n", strerror(errno));
	} else {
		KLOG("planes: count=%u\n", pres->count_planes);
		for (i = 0; i < (int)pres->count_planes; i++) {
			drmModePlane *p = drmModeGetPlane(fd, pres->planes[i]);
			if (!p)
				continue;
			KLOG("plane[%d] id=%u crtc_id=%u fb_id=%u possible_crtcs=0x%x formats=%u\n",
			     i, p->plane_id, p->crtc_id, p->fb_id,
			     p->possible_crtcs, p->count_formats);
			{
				uint32_t k;
				char fmtbuf[512];
				int off = 0;
				fmtbuf[0] = 0;
				for (k = 0; k < p->count_formats && off < 460; k++)
					off += snprintf(fmtbuf + off, sizeof(fmtbuf) - off,
							"%c%c%c%c ",
							(p->formats[k] >> 0) & 0xff,
							(p->formats[k] >> 8) & 0xff,
							(p->formats[k] >> 16) & 0xff,
							(p->formats[k] >> 24) & 0xff);
				KLOG("  formats: %s\n", fmtbuf);
			}
			dump_props(fd, p->plane_id, DRM_MODE_OBJECT_PLANE, "plane");
			drmModeFreePlane(p);
		}
		drmModeFreePlaneResources(pres);
	}

	KLOG("  KMS OK on %s\n", path);
	close(fd);
	return 0;
}

int main(void)
{
	static const char *paths[] = { "/dev/dri/card0", "/dev/dri/card1",
				       "/dev/dri/card2", "/dev/dri/card3" };
	int best = -1;
	unsigned int i;

	kmsg_fd = open("/dev/kmsg", O_WRONLY | O_CLOEXEC);
	KLOG("==== z1drmtest start ====\n");

	/*
	 * mediatek-drm is a component master: it only registers once OVL/RDMA/
	 * DSI have all probed, which happens at ~33 s on this board -- long
	 * after post-fs-data.  Wait for a node whose driver is not lima
	 * before testing, otherwise /dev/dri/card1 does not exist yet.
	 */
	for (i = 0; i < 90; i++) {
		unsigned int k;

		for (k = 0; k < sizeof(paths) / sizeof(paths[0]); k++) {
			int fd = open(paths[k], O_RDWR | O_CLOEXEC);
			drmVersion *v;

			if (fd < 0)
				continue;
			v = drmGetVersion(fd);
			if (v && strcmp(v->name, "lima") != 0) {
				if (v)
					drmFreeVersion(v);
				close(fd);
				goto ready;
			}
			if (v)
				drmFreeVersion(v);
			close(fd);
		}
		if (i && !(i % 10))
			KLOG("still waiting for the KMS node (%us)\n", i * 2);
		sleep(2);
	}
	KLOG("timeout waiting for a non-lima DRM node\n");
ready:
	KLOG("waited %us for the KMS node\n", i * 2);

	for (i = 0; i < sizeof(paths) / sizeof(paths[0]); i++)
		if (probe(paths[i]) == 0 && best < 0)
			best = (int)i;

	KLOG("RESULT: %s\n", best >= 0 ? "PASS (a KMS node supports atomic)"
					 : "FAIL (no node supports atomic)");
	KLOG("==== z1drmtest done ====\n");
	if (kmsg_fd >= 0)
		close(kmsg_fd);
	return best >= 0 ? 0 : 1;
}
