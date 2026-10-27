# Mesa lima 部署记录 (2026-10-27)

## 构建配置 (全部验证通过)
- Mesa 24.0.9, NDK r26d armv7a-linux-androideabi28-clang (API 28 = Z1 Android 9)
- meson: -Dgallium-drivers=lima -Degl=enabled -Dplatforms=android -Dandroid-stub=true
  -Dplatform-sdk-version=28 -Dandroid-libbacktrace=disabled -Dllvm=disabled
- 关键修复:
  1. sys/shm.h 探测加固 (meson.build): NDK 有头无实现, 需 cc.has_function('shmget')
  2. __ANDROID_API__ 24->28: libsync.h 的 sync_file_info 等标 INTRODUCED_IN(26)
  3. 宿主 libz 泄漏: NDK sysroot zlib.pc (mesa_android/ndk-pkgconf/)
- libdrm 2.4.120 交叉编 (mesa_android/libdrm-install)

## 产物
- lima_dri.so (13.5MB, NEEDED 全 A9 标准库) → /system/lib/dri/
- libEGL.so → /system/lib/egl/libEGL_mesa.so
- libglapi.so → /system/lib/

## 运行时
- ro.hardware.egl=mesa 切硬件加速 (当前 swiftshader 软渲染先稳定)
- lima 内核驱动已 probe OK (Mali-400 GP+PP0, 见 a9_acm_capture.log)
- dri 设备节点: /dev/dri/renderD128 (需要内核 DRM render node — Z1 无 DRM 显示管线,
  render node 是否创建待上板验证; 若无, renderonly 路径/或 lima 的 drm-shim 不可用)
