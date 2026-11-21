/*
 * Bluetooth vendor adapter for the Z1 MT6572 factory controller library.
 *
 * Copyright (C) 2016 Z1 Android device contributors
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#define LOG_TAG "z1-bt-vendor"

#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <log/log.h>
#include <bt_vendor_lib.h>

#ifndef Z1_BT_DEVICE_PATH
#define Z1_BT_DEVICE_PATH "/dev/stpbt"
#endif

#ifndef Z1_BT_DIRECT_OPEN_DELAY_MS
#define Z1_BT_DIRECT_OPEN_DELAY_MS 2000
#endif

/* Z1 factory libbluetoothdrv.so mtk_bt_enable (0x778) takes no inputs. */
typedef int (*mtk_bt_enable_fn)(void);
typedef int (*mtk_bt_disable_fn)(int bt_fd);

static void *driver_handle;
static mtk_bt_enable_fn mtk_bt_enable;
static mtk_bt_disable_fn mtk_bt_disable;
static const bt_vendor_callbacks_t *vendor_callbacks;
static int hci_fd = -1;

enum hci_fd_owner {
  HCI_FD_NONE,
  HCI_FD_FACTORY,
  HCI_FD_DIRECT,
};

static enum hci_fd_owner hci_fd_owner;

static void wait_for_wmt_reset_to_settle(void) {
  struct timespec delay = {
      .tv_sec = Z1_BT_DIRECT_OPEN_DELAY_MS / 1000,
      .tv_nsec = (Z1_BT_DIRECT_OPEN_DELAY_MS % 1000) * 1000000L,
  };

  /*
   * A failed stpbt open can be followed by a WMT whole-chip reset. The reset
   * completed in under one second on the board; two seconds keeps the fallback
   * clear of that callback without delaying a direct path that opens normally.
   */
  ALOGW("waiting %d ms for WMT reset before direct H4",
        Z1_BT_DIRECT_OPEN_DELAY_MS);
  while (nanosleep(&delay, &delay) < 0 && errno == EINTR) {
  }
}

static void clear_driver(void) {
  mtk_bt_enable = NULL;
  mtk_bt_disable = NULL;
  hci_fd_owner = HCI_FD_NONE;
  if (driver_handle != NULL) {
    dlclose(driver_handle);
    driver_handle = NULL;
  }
}

static int load_driver_symbol(const char *name, void *function, size_t size) {
  void *symbol;
  const char *error;

  dlerror();
  symbol = dlsym(driver_handle, name);
  error = dlerror();
  if (error != NULL || symbol == NULL || size != sizeof(symbol)) {
    ALOGE("cannot load %s from libbluetoothdrv.so: %s", name,
          error != NULL ? error : "invalid function pointer size");
    return -1;
  }
  memcpy(function, &symbol, size);
  return 0;
}

static int z1_bt_init(const bt_vendor_callbacks_t *callbacks,
                      unsigned char *local_bdaddr) {
  (void)local_bdaddr;
  if (callbacks == NULL || callbacks->size < sizeof(*callbacks)) {
    ALOGE("Bluetooth callbacks are missing or truncated");
    return -EINVAL;
  }
  if (driver_handle != NULL || hci_fd >= 0) {
    ALOGE("Bluetooth vendor library was initialized twice");
    return -EALREADY;
  }

  vendor_callbacks = callbacks;
  dlerror();
  driver_handle = dlopen("libbluetoothdrv.so", RTLD_NOW);
  if (driver_handle == NULL) {
    ALOGE("cannot open libbluetoothdrv.so: %s", dlerror());
    vendor_callbacks = NULL;
    return -ENOENT;
  }

  if (load_driver_symbol("mtk_bt_enable", &mtk_bt_enable,
                         sizeof(mtk_bt_enable)) != 0 ||
      load_driver_symbol("mtk_bt_disable", &mtk_bt_disable,
                         sizeof(mtk_bt_disable)) != 0) {
    clear_driver();
    vendor_callbacks = NULL;
    return -ENOSYS;
  }

  ALOGI("loaded the Z1 MT6572 Bluetooth controller adapter");
  return 0;
}

static int open_direct_h4(void) {
  int fd;

  /*
   * stpbt's open callback starts WMT Bluetooth and registers its STP event
   * callback. Android's H4Protocol can then perform controller setup directly.
   * Keep this as a fallback: the factory library remains authoritative when its
   * legacy NVRAM/GORM initialization succeeds.
   */
  fd = open(Z1_BT_DEVICE_PATH, O_RDWR | O_NONBLOCK | O_CLOEXEC | O_NOFOLLOW);
  if (fd < 0) {
    ALOGE("cannot open %s for direct H4: %s", Z1_BT_DEVICE_PATH,
          strerror(errno));
    return -1;
  }

  ALOGI("using direct H4 on %s after factory initialization did not return an HCI descriptor",
        Z1_BT_DEVICE_PATH);
  return fd;
}

static int z1_bt_userial_open(void *param) {
  int *fd_array = (int *)param;
  int fd;
  int channel;

  if (fd_array == NULL) return 0;
  for (channel = 0; channel < CH_MAX; ++channel) fd_array[channel] = -1;
  if (vendor_callbacks == NULL || mtk_bt_enable == NULL || hci_fd >= 0) {
    ALOGE("invalid Bluetooth USERIAL_OPEN state");
    return 0;
  }

  /* mtk() opens stpbt (WMT func-on), then runs NVRAM/GORM HCI init. */
  fd = mtk_bt_enable();
  if (fd < 0) {
    ALOGW("factory Bluetooth initialization failed (fd=%d); trying direct H4",
          fd);
    wait_for_wmt_reset_to_settle();
    fd = open_direct_h4();
    if (fd < 0) return 0;
    hci_fd_owner = HCI_FD_DIRECT;
  } else {
    ALOGI("factory Bluetooth HCI initialization completed");
    hci_fd_owner = HCI_FD_FACTORY;
  }

  hci_fd = fd;
  for (channel = 0; channel < CH_MAX; ++channel) fd_array[channel] = fd;
  return 1;
}

static int z1_bt_userial_close(void) {
  int result;

  if (hci_fd < 0) {
    hci_fd_owner = HCI_FD_NONE;
    return 0;
  }

  if (hci_fd_owner == HCI_FD_DIRECT) {
    result = close(hci_fd);
    if (result < 0)
      ALOGE("direct H4 close failed for fd %d: %s", hci_fd, strerror(errno));
    /* On Linux close() consumes the descriptor even when it reports EINTR. */
    hci_fd = -1;
    hci_fd_owner = HCI_FD_NONE;
    return result;
  }

  if (hci_fd_owner != HCI_FD_FACTORY || mtk_bt_disable == NULL) {
    ALOGE("invalid Bluetooth HCI descriptor owner %d", hci_fd_owner);
    return -EINVAL;
  }

  result = mtk_bt_disable(hci_fd);
  if (result >= 0) {
    hci_fd = -1;
    hci_fd_owner = HCI_FD_NONE;
  } else {
    ALOGE("factory Bluetooth driver failed to close HCI: %d", result);
  }
  return result;
}

static int z1_bt_op(bt_vendor_opcode_t opcode, void *param) {
  switch (opcode) {
    case BT_VND_OP_POWER_CTRL:
      if (param == NULL) return -EINVAL;
      /* Opening stpbt powers on BT; closing it powers BT off. */
      if (*(int *)param == BT_VND_PWR_OFF) return z1_bt_userial_close();
      if (*(int *)param == BT_VND_PWR_ON) return 0;
      return -EINVAL;
    case BT_VND_OP_USERIAL_OPEN:
      return z1_bt_userial_open(param);
    case BT_VND_OP_USERIAL_CLOSE:
      return z1_bt_userial_close();
    case BT_VND_OP_FW_CFG:
      /* Only a successful synchronous factory init may complete FW_CFG. */
      if (vendor_callbacks != NULL && vendor_callbacks->fwcfg_cb != NULL) {
        vendor_callbacks->fwcfg_cb(hci_fd >= 0 ? BT_VND_OP_RESULT_SUCCESS
                                             : BT_VND_OP_RESULT_FAIL);
        return hci_fd >= 0 ? 0 : -ENODEV;
      }
      return -EINVAL;
    case BT_VND_OP_SCO_CFG:
      /* No verified MTK SCO/PCM configuration path is implemented here. */
      if (vendor_callbacks != NULL && vendor_callbacks->scocfg_cb != NULL)
        vendor_callbacks->scocfg_cb(BT_VND_OP_RESULT_FAIL);
      return -EOPNOTSUPP;
    case BT_VND_OP_GET_LPM_IDLE_TIMEOUT:
      if (param == NULL) return -EINVAL;
      *(uint32_t *)param = 200;
      return 0;
    case BT_VND_OP_LPM_SET_MODE:
      /* STP/WMT owns transport sleep/wake; there is no UART GPIO here. */
      if (vendor_callbacks != NULL && vendor_callbacks->lpm_cb != NULL)
        vendor_callbacks->lpm_cb(BT_VND_OP_RESULT_SUCCESS);
      return 0;
    case BT_VND_OP_LPM_WAKE_SET_STATE:
      return 0;
    case BT_VND_OP_SET_AUDIO_STATE:
      if (vendor_callbacks != NULL && vendor_callbacks->audio_state_cb != NULL)
        vendor_callbacks->audio_state_cb(BT_VND_OP_RESULT_FAIL);
      return -EOPNOTSUPP;
    case BT_VND_OP_EPILOG:
      if (vendor_callbacks != NULL && vendor_callbacks->epilog_cb != NULL)
        vendor_callbacks->epilog_cb(BT_VND_OP_RESULT_SUCCESS);
      return 0;
    default:
      ALOGW("unsupported Bluetooth vendor operation %d", opcode);
      return -EINVAL;
  }
}

static void z1_bt_cleanup(void) {
  vendor_callbacks = NULL;
  /* Z1 factory disable returns negative before calling bt_restore(fd).
   * Keep both the fd and its only release path on failure; a later cleanup
   * may retry. Do not reopen/reinit or claim the fd was consumed meanwhile.
   * The restore path owns close(fd), so a raw close here could double-close.
   */
  if (hci_fd >= 0 && z1_bt_userial_close() < 0) {
    ALOGE("Bluetooth cleanup deferred; driver and HCI fd retained for retry");
    return;
  }
  clear_driver();
}

const bt_vendor_interface_t BLUETOOTH_VENDOR_LIB_INTERFACE = {
    .size = sizeof(bt_vendor_interface_t),
    .init = z1_bt_init,
    .op = z1_bt_op,
    .cleanup = z1_bt_cleanup,
};
