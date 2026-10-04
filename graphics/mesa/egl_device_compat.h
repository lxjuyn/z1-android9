#ifndef Z1_EGL_DEVICE_COMPAT_H
#define Z1_EGL_DEVICE_COMPAT_H

#include <string.h>
#include <xf86drm.h>

/* libdrm 2.4.91 in this Android tree predates drmDevicesEqual(). */
static inline int
z1_drmDevicesEqual(drmDevicePtr a, drmDevicePtr b)
{
   if (a == b)
      return 0;
   if (!a || !b || a->bustype != b->bustype)
      return 1;

   switch (a->bustype) {
   case DRM_BUS_PCI:
      if (!a->businfo.pci || !b->businfo.pci)
         return 1;
      return a->businfo.pci->domain == b->businfo.pci->domain &&
             a->businfo.pci->bus == b->businfo.pci->bus &&
             a->businfo.pci->dev == b->businfo.pci->dev &&
             a->businfo.pci->func == b->businfo.pci->func ? 0 : 1;
   case DRM_BUS_USB:
      if (!a->businfo.usb || !b->businfo.usb)
         return 1;
      return a->businfo.usb->bus == b->businfo.usb->bus &&
             a->businfo.usb->dev == b->businfo.usb->dev ? 0 : 1;
   case DRM_BUS_PLATFORM:
      if (!a->businfo.platform || !b->businfo.platform)
         return 1;
      return strcmp(a->businfo.platform->fullname,
                    b->businfo.platform->fullname) == 0 ? 0 : 1;
   case DRM_BUS_HOST1X:
      if (!a->businfo.host1x || !b->businfo.host1x)
         return 1;
      return strcmp(a->businfo.host1x->fullname,
                    b->businfo.host1x->fullname) == 0 ? 0 : 1;
   default:
      return 1;
   }
}

#endif
