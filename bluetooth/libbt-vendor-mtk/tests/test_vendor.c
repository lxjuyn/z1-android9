/* Offline regressions for the CURRENT production adapter, inspired by the
 * archived 2026-11-10 test_vendor.c. Only test-built shared objects are loaded.
 */
#define _GNU_SOURCE
#include <bt_vendor_lib.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const bt_vendor_interface_t *vi;
static void (*set_enable)(int), (*set_disable)(int);
static void (*get_stats)(int *, int *, int *);
static void (*use_real_fds)(void);
static void (*get_fd_stats)(int *, int *);
static void (*loader_stats)(int *, int *, int *, int *);
static int assertions, failures;
static int fw_ok, fw_bad, sco_ok, sco_bad, lpm_ok, lpm_bad;
static int audio_ok, audio_bad, epilog_ok, epilog_bad, xmit_calls;
static unsigned char address[6];
#define CHECK(c) do { assertions++; if (!(c)) { \
  fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); failures++; \
} } while (0)
static void firmware(bt_vendor_op_result_t r) { r ? fw_bad++ : fw_ok++; }
static void sco(bt_vendor_op_result_t r) { r ? sco_bad++ : sco_ok++; }
static void lpm(bt_vendor_op_result_t r) { r ? lpm_bad++ : lpm_ok++; }
static void audio(bt_vendor_op_result_t r) { r ? audio_bad++ : audio_ok++; }
static void epilog(bt_vendor_op_result_t r) { r ? epilog_bad++ : epilog_ok++; }
static void *allocate(int n) { return malloc((size_t)n); }
static uint8_t transmit(uint16_t op, void *buffer, tINT_CMD_CBACK cb) {
  (void)op; (void)buffer; (void)cb; xmit_calls++; return 0;
}
static void a2dp(bt_vendor_op_result_t r, bt_vendor_opcode_t op, uint8_t h) {
  (void)r; (void)op; (void)h;
}
static bt_vendor_callbacks_t callbacks = {
  .size = sizeof(bt_vendor_callbacks_t), .fwcfg_cb = firmware, .scocfg_cb = sco,
  .lpm_cb = lpm, .audio_state_cb = audio, .alloc = allocate, .dealloc = free,
  .xmit_cb = transmit, .epilog_cb = epilog, .a2dp_offload_cb = a2dp
};
static void *symbol(void *handle, const char *name) {
  dlerror();
  void *result = dlsym(handle, name);
  const char *error = dlerror();
  if (!result || error) { fprintf(stderr, "missing test symbol %s: %s\n", name,
                                 error ? error : "null"); exit(2); }
  return result;
}
static void channels(const int *fds, int expected) {
  for (int i = 0; i < CH_MAX; i++) CHECK(fds[i] == expected);
}
static void driver_counts(int en, int dis, int fd) {
  int actual_en, actual_dis, actual_fd;
  get_stats(&actual_en, &actual_dis, &actual_fd);
  CHECK(actual_en == en); CHECK(actual_dis == dis); CHECK(actual_fd == fd);
}
static void loader_counts(int tries, int opens, int closes) {
  int actual_tries, actual_opens, actual_closes, bad;
  loader_stats(&actual_tries, &actual_opens, &actual_closes, &bad);
  CHECK(actual_tries == tries); CHECK(actual_opens == opens);
  CHECK(actual_closes == closes); CHECK(bad == 0);
}
static void *mock_hooks(const char *path) {
  void *mock = dlopen(path, RTLD_NOW | RTLD_LOCAL);
  if (!mock) { fprintf(stderr, "mock fixture: %s\n", dlerror()); exit(2); }
  set_enable = symbol(mock, "mock_set_enable_result");
  set_disable = symbol(mock, "mock_set_disable_result");
  get_stats = symbol(mock, "mock_get_stats");
  use_real_fds = symbol(mock, "mock_use_real_fds");
  get_fd_stats = symbol(mock, "mock_get_fd_stats");
  return mock;
}
static void unavailable_ops(void) {
  int fds[CH_MAX] = {0};
  CHECK(vi->op(BT_VND_OP_USERIAL_OPEN, fds) == 0); channels(fds, -1);
  int before = fw_ok + fw_bad;
  CHECK(vi->op(BT_VND_OP_FW_CFG, NULL) == -EINVAL);
  CHECK(fw_ok + fw_bad == before);
  CHECK(vi->op(BT_VND_OP_USERIAL_CLOSE, NULL) == 0);
  CHECK(vi->op((bt_vendor_opcode_t)999, NULL) == -EINVAL);
}
static void lifecycle(void) {
  unavailable_ops();
  CHECK(vi->init(NULL, address) == -EINVAL);
  bt_vendor_callbacks_t short_cb = callbacks;
  short_cb.size--;
  CHECK(vi->init(&short_cb, address) == -EINVAL);
  loader_counts(0, 0, 0);
  CHECK(vi->init(&callbacks, address) == 0);
  CHECK(vi->init(&callbacks, address) == -EALREADY);
  loader_counts(1, 1, 0);
  CHECK(vi->op(BT_VND_OP_FW_CFG, NULL) == -ENODEV);
  CHECK(fw_ok == 0 && fw_bad == 1);
  CHECK(vi->op(BT_VND_OP_USERIAL_OPEN, NULL) == 0);
  driver_counts(0, 0, -12345);
  int fds[CH_MAX] = {0};
  CHECK(vi->op(BT_VND_OP_USERIAL_OPEN, fds) == 1); channels(fds, 7);
  driver_counts(1, 0, -12345);
  CHECK(vi->op(BT_VND_OP_USERIAL_OPEN, fds) == 0); channels(fds, -1);
  driver_counts(1, 0, -12345);
  CHECK(vi->op(BT_VND_OP_FW_CFG, NULL) == 0);
  CHECK(fw_ok == 1 && fw_bad == 1 && xmit_calls == 0);
  int on = BT_VND_PWR_ON, off = BT_VND_PWR_OFF, invalid = 99;
  CHECK(vi->op(BT_VND_OP_POWER_CTRL, NULL) == -EINVAL);
  CHECK(vi->op(BT_VND_OP_POWER_CTRL, &invalid) == -EINVAL);
  CHECK(vi->op(BT_VND_OP_POWER_CTRL, &on) == 0);
  driver_counts(1, 0, -12345);
  uint8_t mode = BT_VND_LPM_DISABLE;
  CHECK(vi->op(BT_VND_OP_LPM_SET_MODE, &mode) == 0);
  CHECK(lpm_ok == 1 && lpm_bad == 0);
  CHECK(vi->op(BT_VND_OP_USERIAL_CLOSE, NULL) == 0);
  driver_counts(1, 1, 7);
  CHECK(vi->op(BT_VND_OP_POWER_CTRL, &off) == 0);
  CHECK(vi->op(BT_VND_OP_FW_CFG, NULL) == -ENODEV);
  CHECK(fw_ok == 1 && fw_bad == 2);
  vi->cleanup(); vi->cleanup();
  driver_counts(1, 1, 7); loader_counts(1, 1, 1);
  unavailable_ops();
  CHECK(vi->init(&callbacks, address) == 0);
  CHECK(vi->op(BT_VND_OP_USERIAL_OPEN, fds) == 1); channels(fds, 7);
  vi->cleanup(); driver_counts(2, 2, 7);
  CHECK(vi->init(&callbacks, address) == 0);
  set_enable(-5);
  CHECK(vi->op(BT_VND_OP_USERIAL_OPEN, fds) == 0); channels(fds, -1);
  CHECK(vi->op(BT_VND_OP_FW_CFG, NULL) == -ENODEV);
  CHECK(fw_ok == 1 && fw_bad == 3);
  driver_counts(3, 2, 7);
  set_enable(8);
  CHECK(vi->op(BT_VND_OP_USERIAL_OPEN, fds) == 1); channels(fds, 8);
  CHECK(vi->op(BT_VND_OP_POWER_CTRL, &off) == 0);
  CHECK(vi->op(BT_VND_OP_USERIAL_CLOSE, NULL) == 0);
  vi->cleanup(); driver_counts(4, 3, 8); loader_counts(3, 3, 3);
  CHECK(vi->init(&callbacks, address) == 0);
  CHECK(vi->op(BT_VND_OP_SCO_CFG, NULL) == -EOPNOTSUPP);
  CHECK(sco_ok == 0 && sco_bad == 1);
  bt_vendor_op_audio_state_t state = {0};
  CHECK(vi->op(BT_VND_OP_SET_AUDIO_STATE, &state) == -EOPNOTSUPP);
  CHECK(audio_ok == 0 && audio_bad == 1);
  uint32_t timeout = 0;
  CHECK(vi->op(BT_VND_OP_GET_LPM_IDLE_TIMEOUT, &timeout) == 0);
  CHECK(timeout == 200);
  CHECK(vi->op(BT_VND_OP_GET_LPM_IDLE_TIMEOUT, NULL) == -EINVAL);
  CHECK(vi->op(BT_VND_OP_LPM_WAKE_SET_STATE, &mode) == 0);
  CHECK(vi->op(BT_VND_OP_EPILOG, NULL) == 0);
  CHECK(epilog_ok == 1 && epilog_bad == 0);
  vi->cleanup(); loader_counts(4, 4, 4);
  /* Full-size callbacks may contain null optional members. */
  bt_vendor_callbacks_t empty = {.size = sizeof(empty)};
  CHECK(vi->init(&empty, NULL) == 0);
  CHECK(vi->op(BT_VND_OP_FW_CFG, NULL) == -EINVAL);
  CHECK(vi->op(BT_VND_OP_SCO_CFG, NULL) == -EOPNOTSUPP);
  CHECK(vi->op(BT_VND_OP_SET_AUDIO_STATE, NULL) == -EOPNOTSUPP);
  CHECK(vi->op(BT_VND_OP_LPM_SET_MODE, NULL) == 0);
  CHECK(vi->op(BT_VND_OP_EPILOG, NULL) == 0);
  vi->cleanup(); loader_counts(5, 5, 5);
}
static void zero_fd(void) {
  CHECK(vi->init(&callbacks, address) == 0); set_enable(0);
  int fds[CH_MAX] = {-9, -9, -9, -9};
  CHECK(vi->op(BT_VND_OP_USERIAL_OPEN, fds) == 1); channels(fds, 0);
  CHECK(vi->op(BT_VND_OP_FW_CFG, NULL) == 0);
  CHECK(fw_ok == 1 && fw_bad == 0);
  CHECK(vi->op(BT_VND_OP_USERIAL_CLOSE, NULL) == 0);
  int off = BT_VND_PWR_OFF;
  CHECK(vi->op(BT_VND_OP_POWER_CTRL, &off) == 0);
  vi->cleanup(); driver_counts(1, 1, 0); loader_counts(1, 1, 1);
  CHECK(vi->init(&callbacks, address) == 0);
  CHECK(vi->op(BT_VND_OP_USERIAL_OPEN, fds) == 1); channels(fds, 0);
  vi->cleanup(); driver_counts(2, 2, 0); loader_counts(2, 2, 2);
}
static void close_failure(void) {
  CHECK(vi->init(&callbacks, address) == 0);
  int fds[CH_MAX] = {0};
  CHECK(vi->op(BT_VND_OP_USERIAL_OPEN, fds) == 1);
  set_disable(-EIO);
  CHECK(vi->op(BT_VND_OP_USERIAL_CLOSE, NULL) == -EIO);
  driver_counts(1, 1, 7);
  CHECK(vi->op(BT_VND_OP_USERIAL_OPEN, fds) == 0); channels(fds, -1);
  CHECK(vi->op(BT_VND_OP_FW_CFG, NULL) == 0);
  set_disable(0);
  CHECK(vi->op(BT_VND_OP_USERIAL_CLOSE, NULL) == 0);
  vi->cleanup(); driver_counts(1, 2, 7); loader_counts(1, 1, 1);
  CHECK(vi->init(&callbacks, address) == 0);
  vi->cleanup(); loader_counts(2, 2, 2);
}
static void fd_counts(int owned, int closed) {
  int actual_owned, actual_closed;
  get_fd_stats(&actual_owned, &actual_closed);
  CHECK(actual_owned == owned); CHECK(actual_closed == closed);
}
static void cleared_callbacks(void) {
  int before = fw_ok + fw_bad + sco_ok + sco_bad + lpm_ok + lpm_bad +
               audio_ok + audio_bad + epilog_ok + epilog_bad;
  CHECK(vi->op(BT_VND_OP_FW_CFG, NULL) == -EINVAL);
  CHECK(vi->op(BT_VND_OP_SCO_CFG, NULL) == -EOPNOTSUPP);
  CHECK(vi->op(BT_VND_OP_SET_AUDIO_STATE, NULL) == -EOPNOTSUPP);
  CHECK(vi->op(BT_VND_OP_LPM_SET_MODE, NULL) == 0);
  CHECK(vi->op(BT_VND_OP_EPILOG, NULL) == 0);
  CHECK(fw_ok + fw_bad + sco_ok + sco_bad + lpm_ok + lpm_bad +
        audio_ok + audio_bad + epilog_ok + epilog_bad == before);
}
static void cleanup_failure(void) {
  /* All paths run in the default close-retry case as well as the probe.
   * A transient, pre-consumption mock failure can recover; the factory's
   * missing internal handle is NOT claimed to recover by itself.
   */
  use_real_fds();
  for (int retry = 0; retry < 3; retry++) {
    int en, dis, last, tries, opens, closes, bad, owned, closed;
    get_stats(&en, &dis, &last);
    loader_stats(&tries, &opens, &closes, &bad);
    get_fd_stats(&owned, &closed);
    CHECK(owned == -1);
    CHECK(vi->init(&callbacks, address) == 0);
    int fds[CH_MAX] = {-1, -1, -1, -1};
    set_disable(0);
    CHECK(vi->op(BT_VND_OP_USERIAL_OPEN, fds) == 1);
    int fd = fds[0];
    CHECK(fd >= 0);
    if (fd < 0) return;
    channels(fds, fd);
    CHECK(fcntl(fd, F_GETFD) >= 0); fd_counts(fd, closed);
    set_disable(-EIO);
    vi->cleanup();
    driver_counts(en + 1, dis + 1, fd);
    loader_counts(tries + 1, opens + 1, closes);
    CHECK(fcntl(fd, F_GETFD) >= 0); fd_counts(fd, closed);
    cleared_callbacks();
    CHECK(vi->init(&callbacks, address) == -EALREADY);
    CHECK(vi->op(BT_VND_OP_USERIAL_OPEN, fds) == 0); channels(fds, -1);
    driver_counts(en + 1, dis + 1, fd);
    /* Also cover the exact negative result in the archived factory branch. */
    set_disable(-1);
    vi->cleanup();
    driver_counts(en + 1, dis + 2, fd);
    loader_counts(tries + 1, opens + 1, closes);
    CHECK(fcntl(fd, F_GETFD) >= 0); fd_counts(fd, closed);
    cleared_callbacks();
    set_disable(0);
    int off = BT_VND_PWR_OFF;
    if (retry == 0) {
      vi->cleanup();
    } else {
      CHECK(vi->op(retry == 1 ? BT_VND_OP_USERIAL_CLOSE : BT_VND_OP_POWER_CTRL,
                   retry == 1 ? NULL : &off) == 0);
      loader_counts(tries + 1, opens + 1, closes);
      /* Closing a retained fd must not reactivate the cleaned-up session. */
      CHECK(vi->op(BT_VND_OP_USERIAL_OPEN, fds) == 0); channels(fds, -1);
      CHECK(vi->init(&callbacks, address) == -EALREADY);
      cleared_callbacks();
      vi->cleanup();
    }
    driver_counts(en + 1, dis + 3, fd);
    loader_counts(tries + 1, opens + 1, closes + 1);
    fd_counts(-1, closed + 1);
    errno = 0;
    int status = fcntl(fd, F_GETFD);
    CHECK(status == -1 && errno == EBADF);
    if (status != -1) return; /* Never replace a still-owned fd in a failed test. */
    /* Reuse that exact descriptor number for an unrelated pipe. Any stale
     * factory disable or raw close during teardown would close this guard. */
    int guard[2];
    int result = pipe(guard);
    CHECK(result == 0);
    if (result != 0) return;
    if (guard[0] != fd) {
      CHECK(dup2(guard[0], fd) == fd);
      CHECK(close(guard[0]) == 0);
    }
    if (guard[1] != fd) CHECK(close(guard[1]) == 0);
    vi->cleanup(); vi->cleanup();
    CHECK(vi->op(BT_VND_OP_USERIAL_CLOSE, NULL) == 0);
    CHECK(vi->op(BT_VND_OP_POWER_CTRL, &off) == 0);
    CHECK(fcntl(fd, F_GETFD) >= 0);
    driver_counts(en + 1, dis + 3, fd);
    loader_counts(tries + 1, opens + 1, closes + 1);
    fd_counts(-1, closed + 1);
    cleared_callbacks();
    CHECK(vi->init(&callbacks, address) == 0);
    CHECK(vi->op(BT_VND_OP_USERIAL_OPEN, fds) == 1);
    int next_fd = fds[0];
    CHECK(next_fd >= 0 && next_fd != fd); channels(fds, next_fd);
    CHECK(vi->op(BT_VND_OP_FW_CFG, NULL) == 0);
    vi->cleanup();
    driver_counts(en + 2, dis + 4, next_fd);
    loader_counts(tries + 2, opens + 2, closes + 2);
    fd_counts(-1, closed + 2);
    errno = 0; CHECK(fcntl(next_fd, F_GETFD) == -1 && errno == EBADF);
    CHECK(fcntl(fd, F_GETFD) >= 0);
    CHECK(close(fd) == 0);
  }
}
static void load_failure(const char *good, int expected) {
  /* Do not preload failed mock: verify real unload, then retry without cleanup. */
  const char *bad = getenv("Z1_TEST_DRIVER");
  CHECK(vi->init(&callbacks, address) == expected);
  loader_counts(1, expected == -ENOENT ? 0 : 1, expected == -ENOENT ? 0 : 1);
  void *leftover = dlopen(bad, RTLD_NOW | RTLD_NOLOAD);
  CHECK(leftover == NULL);
  if (leftover) dlclose(leftover);
  unavailable_ops();
  CHECK(setenv("Z1_TEST_DRIVER", good, 1) == 0);
  CHECK(vi->init(&callbacks, address) == 0);
  void *mock = mock_hooks(good);
  int fds[CH_MAX] = {0};
  CHECK(vi->op(BT_VND_OP_USERIAL_OPEN, fds) == 1); channels(fds, 7);
  CHECK(vi->op(BT_VND_OP_FW_CFG, NULL) == 0);
  CHECK(fw_ok == 1 && fw_bad == 0);
  vi->cleanup(); driver_counts(1, 1, 7);
  int count = expected == -ENOENT ? 1 : 2;
  loader_counts(2, count, count);
  CHECK(dlclose(mock) == 0);
}
int main(int argc, char **argv) {
  if (argc != 4) { fprintf(stderr, "usage: test_vendor VENDOR_SO CASE GOOD_MOCK\n"); return 2; }
  void *vendor = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
  if (!vendor) { fprintf(stderr, "vendor: %s\n", dlerror()); return 2; }
  vi = symbol(vendor, "BLUETOOTH_VENDOR_LIB_INTERFACE");
  loader_stats = symbol(vendor, "test_loader_stats");
  CHECK(vi->size == sizeof(*vi));
  void *mock = NULL;
  const char *test = argv[2];
  if (!strcmp(test, "dlopen-missing") || !strcmp(test, "dlopen-invalid"))
    load_failure(argv[3], -ENOENT);
  else if (!strcmp(test, "missing-enable") || !strcmp(test, "missing-disable"))
    load_failure(argv[3], -ENOSYS);
  else {
    mock = mock_hooks(argv[3]);
    if (!strcmp(test, "lifecycle")) lifecycle();
    else if (!strcmp(test, "fd-zero")) zero_fd();
    else if (!strcmp(test, "close-retry")) { close_failure(); cleanup_failure(); }
    else if (!strcmp(test, "cleanup-disable-failure")) cleanup_failure();
    else { fprintf(stderr, "unknown case: %s\n", test); return 2; }
  }
  if (mock) CHECK(dlclose(mock) == 0);
  CHECK(dlclose(vendor) == 0);
  printf("CASE %s: assertions=%d failures=%d\n", test, assertions, failures);
  return failures ? 1 : 0;
}
