# Z1 可刷写镜像清单 (2026-11-04)

两套，按顺序刷。**都未上板验证。**

## 步骤 1 —— 触摸 + 显示（先刷这套）

| 镜像 | 路径 | sha256 | 大小 |
|---|---|---|---|
| boot | `out/z1_fix3_boot/boot.img` | `8859f6611e7b2424e5206e4ae8cb24a66fb0b3c42f5e7ec30203d70b30e0c436` | 6,219,776 |
| system | `out/z1_fix3_system/system_fix3.img` | `51d565935f97cb84eb48e3ec5230ebb780d50c225c3aebfdc4e08dbdef81dcc5` | 943,718,400 |

boot 侧改动（相对 11-03 线上那版）：
1. `init.z1.rc`：5 条 `exec_background` 从 `service z1_sf_dump` 块移回 `on post-fs`。
   原来 init 报 `Invalid keyword 'exec_background'` 整行丢弃，导致 `/z1_touch_load.sh`
   （GPIO145 复位 + CST836U）从 10-30 起就没执行过 → 触摸芯片在 I2C 0x15 一直 NACK。
2. `service z1_sf_dump` 加 `oneshot`：原先每次退出被 init 立刻重启，每 ~4 秒跑一次
   `logcat -d -t 4000` 并往 /data 和 /cache 各写 300KB（440 秒写了 41.7 MB）。
3. 新增 `/z1_provision.sh`，由 `on property:sys.boot_completed=1` 拉起：置
   `device_provisioned=1` + `user_setup_complete=1` 并 `am start ... HOME`。
   不做这一步 HOME 永远停在 `com.android.settings/.FallbackHome`（纯黑兜底页），
   屏幕就是黑的 —— 和显示链路无关。
4. dts bootargs 加 `pd_ignore_unused`：mainline 没有 DISP 驱动，DT 里 DIS 电源域的
   三个消费者（smi_common / iommu / larb0）都没有驱动，`genpd_power_off_unused()`
   会在 late_initcall_sync 把 DIS 域关掉。cmdline 早已有 clk/regulator 的 ignore_unused，
   唯独漏了 pd。

system 侧改动：
1. `/build.prop` 权限 **0664 → 0644**。init 的 `load_properties_from_file()` 见
   `S_IWGRP` 就判 insecure **整份跳过** —— 不只 `ro.build.fingerprint` 是空的，
   `dalvik.vm.heapsize=256m` 等 ART 堆参数也全没生效。
2. `/etc/init/android.hidl.allocator@1.0-service.rc`：原文件 `service hidl_memory` 带
   `disabled` 且**没有 `interface` 行** → 服务永不启动，`interface_start` 必然失败，
   system_server/SystemUI/media 各卡 5 秒（时间点紧贴第一次 Watchdog）。
   改成 `class hal` + `interface android.hidl.allocator@1.0::IAllocator ashmem`
   （实例名是 **ashmem** 不是 default）。

### 验收判据（串口 921600）
- `Invalid keyword` 命中 **0**
- `z1_gpio_hunt insmod OK` → `z1_cst836u insmod OK` → `chip ACKed (byte read) at 0x15 on i2c-0`
  → `CST836U identity: fw=...`；**手指点屏幕有反应**
- `z1_sf_dump` 只出现一次
- `z1prov` 出现 `try N: device_provisioned=1 user_setup_complete=1`
- `dumpsys window | grep mCurrentFocus` 不是 null；屏幕出现桌面（`com.example.android.home`）
- `getprop ro.build.fingerprint` 不再是 `unknown/unknown/unknown:9/...`

## 步骤 2 —— GPU 加速（步骤 1 验完再刷，**boot + system 必须一起刷**）

| 镜像 | 路径 | sha256 | 大小 |
|---|---|---|---|
| boot | `out/z1_gpu_boot/boot.img` | `4fcd899878a841d18fab2c460d125b21849fbbc9e25519256e229f957651e621` | 6,221,824 |
| system | `out/z1_gpu_system/system_gpu.img` | `b1d90bf3ab9a6e3a790f21f2072db64bd38d46ccb43341f94b4be5ccb309f2b9` | 943,718,400 |

boot 侧在步骤 1 基础上再加两处：
- ramdisk `/default.prop`：`ro.hardware.egl=swiftshader` → `mesa`
  （**唯一生效来源**；改 `/system/build.prop` 或 `/etc/prop.default` 都无效）
- ramdisk `/ueventd.z1.rc`：`/dev/dri/renderD128` 0660 system:graphics → 0666 root:graphics；
  新增 `/dev/dma_heap/system 0666 root system`

system 侧在步骤 1 基础上再叠：
| 路径 | 大小 | 说明 |
|---|---|---|
| `/lib/hw/gralloc.z1.so` | 6,352 | 换成 `out/gralloc.z1.so`：alloc 走 `/dev/dma_heap/system` 产出真 dma-buf，失败自动回落 ashmem |
| `/vendor/lib/egl/libGLES_mesa.so` | 94,488 | Mesa 19.1.8 合并库，SONAME 不撞框架库 |
| `/vendor/lib/dri/gallium_dri.so` | 4,662,060 | 同上 |
| `/vendor/lib/dri/lima_dri.so` | symlink | → `gallium_dri.so`（镜像里原本是悬空链接，现在目标补上） |

**为什么必须换 gralloc**：原 `gralloc.default.so` 只用 `ashmem_create_region`，
ashmem fd 不是 dma-buf，mesa 的 `drmPrimeFDToHandle()` 必然 EINVAL
（10-20 已实测 `EGL-MAIN: failed to create DRI image from FD`）。
内核侧不用改（`DMABUF_HEAPS_SYSTEM=y` / `ANDROID_ASHMEM=y` / `DRM_LIMA=y` 都在）。

### 验收判据
- `getprop ro.hardware.egl` = `mesa`
- logcat `libEGL : loaded /vendor/lib/egl/libGLES_mesa.so`
- `SurfaceFlinger: vendor : lima` / `renderer : Mali400` / `version : ... Mesa 19.1.8`
- 没有 `failed to create DRI image from FD`
- 风险：19.1.8 的 lima 在这块 SoC 上可能刷 `gp error irq state=4000 status=3`。
  若渲染错乱，退回步骤 1 即可；长期需重编 Mesa 24+/26+（修 SONAME 与 DRI 路径两个链接参数）。

## 校验记录（生成时逐项跑过）
两套 boot：`ANDROID!` magic / MTK `KERNEL`+`ROOTFS` 双头 / Android 头 size 与 MTK 头一致 /
dtb `d00dfeed` 在 zImage 末尾 / `initrd-end` 与实际 blob 长度逐字节吻合 /
gzip 流完整无残留 / 78 条 cpio 记录 0 条缺类型位 / `z1_provision.sh` 在位 /
rc 里没有残留的 service 内 `exec_background`。
两套 system：`build.prop` 0644 / allocator rc 正确 / GPU 那份四个文件在位 /
`e2fsck -fn` 干净（fix3 2701 inodes，gpu 2703 inodes）。

## 回滚
- 任一步异常：刷回 `out/z1_hafm_boot/boot.img` + `out/z1_r5_system/system_hafm.img`
- 彻底兜底：原厂 `1/boot.img`（sha256 `2dd41702e839ec2547ce0dea2bbd3603a5d019786502d939ebba4a6b578eaab5`）
- 命令：`Z1_VERIFY=0 tools/flash_with_retry.sh bootimg <img> <rollback> 12`
