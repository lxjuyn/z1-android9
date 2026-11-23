/* Test-only GNU ld wrappers: exercise real dlopen/dlsym on isolated fixtures.
 * The production source is compiled unchanged. The ARM build never uses this.
 */
#include <dlfcn.h>
#include <stdlib.h>
#include <string.h>
#include <sys/system_properties.h>

void *__real_dlopen(const char *, int);
int __real_dlclose(void *);
static int attempts, acquired, released, wrong_name;

void *__wrap_dlopen(const char *name, int flags) {
  const char *path = getenv("Z1_TEST_DRIVER");
  attempts++;
  if (!name || strcmp(name, "libbluetoothdrv.so") || !path || path[0] != '/') {
    wrong_name++;
    abort();
  }
  void *handle = __real_dlopen(path, flags);
  if (handle) acquired++;
  return handle;
}
int __wrap_dlclose(void *handle) {
  int result = __real_dlclose(handle);
  if (!result) released++;
  return result;
}
void test_loader_stats(int *tries, int *opens, int *closes, int *bad) {
  *tries = attempts; *opens = acquired; *closes = released; *bad = wrong_name;
}

int __system_property_get(const char *name, char *value) {
  const char *setting;
  if (strcmp(name, "sys.z1.radio.bt_nvram_ready") == 0)
    setting = getenv("Z1_TEST_NVRAM_READY");
  else if (strcmp(name, "debug.z1.bt.transport") == 0)
    setting = getenv("Z1_TEST_BT_TRANSPORT");
  else
    return 0;
  if (!setting) return 0;
  size_t size = strlen(setting);
  if (size >= PROP_VALUE_MAX) return 0;
  memcpy(value, setting, size + 1);
  return (int)size;
}
