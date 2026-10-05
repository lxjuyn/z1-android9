#define Z1_MOUNT_GUARD_TEST
#include "z1_stock_mount_guard.c"
#include <assert.h>
int main(void) {
  const char *good="23 1 179:4 / /system ro - ext4 /dev/block/z1-stock-system ro\n";
  assert(mount_row(good,"/system",179,4));
  assert(!mount_row(good,"/data",179,4));
  assert(!mount_row(good,"/system",179,5));
  assert(!mount_row("23 1 179:4 /subdir /system ro - ext4 /dev/block/x ro\n","/system",179,4));
  assert(!mount_row("23 1 179:4 / /system rw - tmpfs tmpfs rw\n","/system",179,4));
  assert(!mount_row("bad input","/system",179,4));
  assert(verify() == -1); /* host lacks real stock aliases */
  char path[]="/tmp/z1-guard-file-XXXXXX"; int fd=mkstemp(path); struct stat st;
  unsigned char header[20]={0x7f,'E','L','F',1,1}; header[18]=40;
  assert(fd>=0); assert(write(fd,header,20)==20); assert(fchmod(fd,0700)==0); assert(fstat(fd,&st)==0); close(fd);
  assert(required_file(path,1,st.st_dev)==0);
  assert(required_file(path,1,st.st_dev+1)==-1);
  assert(chmod(path,0600)==0); assert(required_file(path,1,st.st_dev)==-1);
  assert(required_file(path,0,st.st_dev)==0);
  assert(truncate(path,0)==0); assert(required_file(path,0,st.st_dev)==-1); unlink(path);
  (void)publish;
  puts("PASS: 12 production-function assertions; no readiness property published"); return 0;
}
