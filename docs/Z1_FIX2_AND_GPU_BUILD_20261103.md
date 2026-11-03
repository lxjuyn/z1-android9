# Z1 fix2 + GPU 两套镜像 (2026-11-03)

两套**互相独立**的产物，按单变量纪律分两次刷。都未上板。

## A. fix2 —— 触摸 + 显示（先刷这个）

| 项 | 值 |
|---|---|
| boot.img | `out/z1_fix2_boot/boot.img` |
| sha256 | `7dcf05beef127bee1bc0b05192e5c5f721dd2c89d6f66afc13d535d72af14c23` |
| 大小 | 6,219,776 B（≤ 6,291,456 分区） |
| 内核 | 沿用 #114 zImage 4,215,296 B，未重编 |
| dtb | 20,742 B，`linux,initrd-end = 0x842e3516`，bootargs 加 `pd_ignore_unused` |
| ramdisk | 512 + gzip 1,979,158 = 1,979,670 B |

改动只有两处：
1. `init.z1.rc`：5 条 `exec_background` 从 `service z1_sf_dump` 块移回 `on post-fs` 动作。
   原来 init 报 `Invalid keyword 'exec_background'` 整行丢弃，导致 `/z1_touch_load.sh`
   （GPIO145 复位 + CST836U）从未执行 → 触摸芯片在 I2C 0x15 一直 NACK。
   10-31 能用的那次五个脚本都在 ~15.5s 由 `on post-fs` 拉起。
2. `service z1_sf_dump` 加 `oneshot`：原先每次退出被 init 立刻重启，每 ~4 秒跑一次
   `logcat -d -t 4000` 并往 /data 和 /cache 各写 300KB，持续烧 CPU 和 eMMC。

加上 dts 的 `pd_ignore_unused`（DIS 电源域在 mainline 下零消费者，会被
`genpd_power_off_unused()` 关掉；mainline 没有 DISP 驱动，显示全靠 LK 留下的状态）。

### 验收判据（串口 921600）
- `grep 'Invalid keyword'` 命中数 **0**
- `z1touch` 出现 `z1_gpio_hunt insmod OK` → `z1_cst836u insmod OK` →
  dmesg 出现 `chip ACKed (byte read) at 0x15 on i2c-0` 和 `CST836U identity: fw=...`
- `z1_sf_dump` 只出现一次，不再每 4 秒重复
- 触摸能点

## A2. system_fix3 —— 两个 P0 单行修复（和 fix2 boot 一起刷）

| 项 | 值 |
|---|---|
| system.img | `out/z1_fix3_system/system_fix3.img` |
| sha256 | `51d565935f97cb84eb48e3ec5230ebb780d50c225c3aebfdc4e08dbdef81dcc5` |

基于 `system_hafm.img` 改两处：

1. **`/build.prop` 权限 0664 → 0644**。init 的 `load_properties_from_file()` 见到
   `S_IWGRP` 就判定 "insecure" 整份跳过 —— 所以 `ro.build.fingerprint` 一直是空的
   （`unknown/unknown/unknown:9/...`），更要紧的是 `dalvik.vm.heapsize=256m` 这类
   **ART 堆参数全部没生效**。开机日志里 `free` 从 950MB 掉到 241MB 的内存锯齿，
   很可能就是它放大的。
2. **`/etc/init/android.hidl.allocator@1.0-service.rc`**：原文件是
   ```
   service hidl_memory /system/bin/hw/android.hidl.allocator@1.0-service
       disabled
       class hal
       user system
       group system
   ```
   `disabled` + **没有 `interface` 行** → 服务永远不启动，init 也不知道它承载
   `android.hidl.allocator@1.0::IAllocator/ashmem`，所以 `interface_start` 永远失败。
   开机日志里 system_server / SystemUI / media 各自 `Waited one second for ...IAllocator/ashmem`
   卡 5 秒，时间点紧贴第一次 Watchdog。改成：
   ```
   service hidl_memory /system/bin/hw/android.hidl.allocator@1.0-service
       class hal
       user system
       group system
       interface android.hidl.allocator@1.0::IAllocator ashmem
   ```
   实例名是 **`ashmem`** 不是 `default` —— 从二进制 strings 里 `registerAsService` 和
   调用方日志的 `/ashmem` 两处对上。

`e2fsck -fn` 干净（2701/57600 inodes）。

## B. GPU —— Mesa/lima 硬件加速（fix2 验完再刷，**要同时刷 boot + system**）

| 项 | 值 |
|---|---|
| boot.img | `out/z1_gpu_boot/boot.img` sha256 `4df61d4f175debd4bec86c3d5ac95d9a36974ff62f75f2eeb1650713300a17cd` |
| system.img | `out/z1_gpu_system/system_gpu.img` sha256 `b1d90bf3ab9a6e3a790f21f2072db64bd38d46ccb43341f94b4be5ccb309f2b9` |

boot 侧（在 fix2 基础上再改两处）：
- ramdisk `/default.prop`：`ro.hardware.egl=swiftshader` → `mesa`
  （**唯一生效来源**；改 `/system/build.prop` 或 `/etc/prop.default` 都无效）
- ramdisk `/ueventd.z1.rc`：
  - `/dev/dri/renderD128 0660 system graphics` → `0666 root graphics`
    （mesa 在调用者进程内 dlopen，app 也要 open 渲染节点）
  - 新增 `/dev/dma_heap/system 0666 root system`

system 侧（基于 **`system_fix3.img`** 再叠，所以上面两个 P0 修复也一起带上）：
| 路径 | 大小 | 说明 |
|---|---|---|
| `/lib/hw/gralloc.z1.so` | 6,352 | 换成 `out/gralloc.z1.so`：alloc 走 `/dev/dma_heap/system` 产出真 dma-buf，失败自动回落 ashmem |
| `/vendor/lib/egl/libGLES_mesa.so` | 94,488 | Mesa 19.1.8 合并库（sf9），SONAME 不撞框架库 |
| `/vendor/lib/dri/gallium_dri.so` | 4,662,060 | 同上 |
| `/vendor/lib/dri/lima_dri.so` | symlink 14 | → `gallium_dri.so`（镜像里原本是悬空链接，现在目标补上了） |

全部文件已设 SELinux 标签：`gralloc.z1.so`=system_file、`libGLES_mesa.so`=same_process_hal_file、
`gallium_dri.so`=vendor_file。`e2fsck -fn` 干净（2703/57600 inodes，202054/230400 blocks）。

**为什么必须换 gralloc**：原 `gralloc.default.so` 只用 `ashmem_create_region`，
ashmem fd 不是 dma-buf，mesa 的 `drmPrimeFDToHandle()` 必然 EINVAL
（10-20 已实测 `EGL-MAIN: failed to create DRI image from FD`）。
内核侧无需改动（`DMABUF_HEAPS_SYSTEM=y` / `ANDROID_ASHMEM=y` / `DRM_LIMA=y` 都在）。

### 验收判据
- `getprop ro.hardware.egl` = `mesa`
- logcat：`libEGL : loaded /vendor/lib/egl/libGLES_mesa.so`
- `SurfaceFlinger: vendor : lima` / `renderer : Mali400` / `version : ... Mesa 19.1.8`
- 没有 `failed to create DRI image from FD`
- 已知风险：19.1.8 的 lima 在这块 SoC 上可能刷 `gp error irq state=4000 status=3`
  （10-20 见过）。若渲染错乱，退回 A 即可，长期需重编 Mesa 24+/26+。

## 回滚
- 任一步异常：刷 `out/z1_hafm_boot/boot.img`（当前线上那版）+ `out/z1_r5_system/system_hafm.img`
- 彻底兜底：原厂 `1/boot.img`（sha256 `2dd41702e839ec2547ce0dea2bbd3603a5d019786502d939ebba4a6b578eaab5`）
- 刷写命令：`Z1_VERIFY=0 tools/flash_with_retry.sh bootimg <img> <rollback> 12`

## 还没解决（与上面两个镜像无关）
- **黑屏的内容侧**：HOME intent 解析到 `com.android.settings/.FallbackHome`（纯黑兜底页），
  `mCurrentFocus=null`，20 个 layer 只有状态栏有 buffer。镜像里有 `com.example.android.home`
  但没被选中，典型是 `device_provisioned=0` 还在开机引导态。
  设备能起来后一条命令可验：`settings put global device_provisioned 1; settings put secure user_setup_complete 1`
- **bootanim 被主动杀**：`/system/etc/init/z1_speedup.rc:32` 的 `stop bootanim`，
  "二屏没看到"是配置如此，不是故障。
