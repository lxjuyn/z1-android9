# Mesa 26 "dlopen failed: cannot find xImage" —— 根因与修复（已用设备自己的 linker 验证）

## 现象

上板后 libEGL 打印：

```
E libEGL : load_driver(/vendor/lib/egl/libGLES_mesa.so): dlopen failed:
  cannot find "xImage" from verneed[0] in DT_NEEDED list for ".../libGLES_mesa.so"
```

⇒ mesa 加载失败 → libEGL 回落到 `libEGL_swiftshader.so` 三件套 → **仍然是软渲染**，所以"还是卡"。

## 定位方法（关键：不用刷机就能验证）

qemu-user 受限（bionic 32 位 libc 要求 pid ≤ 65535），用 PID namespace 绕过：

```
sudo unshare --pid --fork --mount-proc \
  env LD_LIBRARY_PATH=/system/lib:/system/vendor/lib \
  qemu-arm -L /tmp/btroot /tmp/btroot/dltest /system/vendor/lib/egl/x.so
```
`dltest` 是 NDK 编的 6 行程序（dlopen + dlsym eglGetProcAddress）。
rootfs 从 v24 镜像里 debugfs dump 出 linker/libc/libm/… 和 mesa 两个库。

## 根因（二分出来的）

| 测试对象 | 结果 |
|---|---|
| 构建树原始 `libEGL.so`（未 strip） | ✅ 能加载 |
| 构建树 + `patchelf --set-soname` | ✅ 能加载 |
| 构建树 + `strip` | ✅ 能加载 |
| 构建树 + `patchelf` + `strip` | ✅ 能加载 |
| **出货的 `libGLES_mesa.so`（323,696 B）** | ❌ xImage |

所以**不是 patchelf、也不是 strip 本身**，而是出货那个文件在打包链路上被弄坏了（版本表 `.gnu.version_r` 的
`vn_file` 字符串偏移与实际 `.dynstr` 对不上，bionic 读出来是 `xImage` —— 它其实是
`eglBindTexImage` 里的一个子串，说明偏移指进了别的字符串中间）。

## 修复

用构建树原始 `libEGL.so` 重新做一遍，顺序是 **先 patchelf 改 SONAME，再 strip**：

```
patchelf --set-soname libGLES_mesa.so libEGL.so
arm-linux-gnueabihf-strip --strip-unneeded libEGL.so
```
结果 324,832 B，sha256 `bb0ac77c03e79648db5dc8a480ec478be566b78c6105e9f781fd22100b46f445`
（`/tmp/v25lib.so`），SONAME=`libGLES_mesa.so`、导出 **44 个 egl\***、版本表完好。

**验证（设备自己的 linker）**：
```
DLOPEN_OK 0x8f7cd051
eglGetProcAddress=0x41eb3cd0
```

## 产物

`out/z1_v25_gpu26_fixed/system_v25.img` sha256
`3915602cfdb347a69f1003cb6c9d0913c5c6fd6f0cb51a8c329f218ed061ccde`，e2fsck 干净；
与 v24 只差 `libGLES_mesa.so` 一个文件（已从镜像里 dump 回来复验 sha256 与加载）。

## NOT VERIFIED
- 还没上板。预期判据：`libEGL : loaded /vendor/lib/egl/libGLES_mesa.so`（而不是 dlopen failed），
  以及 `gp error irq` 是否消失。
