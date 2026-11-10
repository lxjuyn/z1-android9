/* Host-only fake factory driver. No device, firmware, or NVRAM access.
 * Optional anonymous pipe fds exercise ownership, not controller behavior.
 */
#include <errno.h>
#include <unistd.h>
static int enable_calls, disable_calls, enable_result = 7;
static int disable_result, last_disabled_fd = -12345;
static int real_fds, owned_fd = -1, closed_fds;
#ifndef MOCK_MISSING_ENABLE
int mtk_bt_enable(void) {
  int fds[2];
  enable_calls++;
  if (!real_fds || enable_result < 0) return enable_result;
  if (owned_fd >= 0) return -EBUSY;
  if (pipe(fds) < 0) return -errno;
  close(fds[1]);
  owned_fd = fds[0];
  return owned_fd;
}
#endif
#ifndef MOCK_MISSING_DISABLE
int mtk_bt_disable(int fd) {
  disable_calls++;
  last_disabled_fd = fd;
  /* Injected failures leave ownership intact, as the audited pre-restore
   * error branch does. Success consumes the fd exactly once.
   */
  if (real_fds && disable_result >= 0) {
    if (fd < 0 || fd != owned_fd) return -EBADF;
    if (close(fd) < 0) return -errno;
    owned_fd = -1;
    closed_fds++;
  }
  return disable_result;
}
#endif
void mock_set_enable_result(int result) { enable_result = result; }
void mock_set_disable_result(int result) { disable_result = result; }
void mock_get_stats(int *en, int *dis, int *fd) {
  *en = enable_calls; *dis = disable_calls; *fd = last_disabled_fd;
}
void mock_use_real_fds(void) { real_fds = 1; }
void mock_get_fd_stats(int *owned, int *closed) {
  *owned = owned_fd; *closed = closed_fds;
}
