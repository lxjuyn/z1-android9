# Z1 Android 9 候选镜像与明日测试计划

生成于 2026-10-25。此目录只含候选 boot/system 与文档，不包含用户数据、NVRAM、WiFi MAC、校准资料、日志或任何凭据。

**所有候选仅通过主机侧静态/读回验证，尚未上板。不要将 HOST_PACKAGE_PASS 解读为功能验证。Z1 已关机，今晚不安排刷写。**

## 配对

| 候选 | boot | system | 目的 |
|---|---|---|---|
| R1 显示接管 | R1 boot + R9 原 system | 原 R9 system 或 R1 system | 只验证 VT/fbcon 接管，不混入其他改动 |
| R1 轻量壁纸 | R1 boot | R1 system | 验证默认小壁纸路径与软件渲染表现 |
| R2 驱动取证 | R2 boot | R2 system | 在显示基础上人工执行触摸只读取证 |

回滚 boot：原 R9 `1d3b22d0f1b24a32b8ce984c95a06ec4a1d689e27accbbe496f217c591ec663f`。
回滚 system：原 R9 `4289d6c1ed9c155da197a96c06927ffd2dc51d3f7fd0123deed78e6fd5d33a33`。
原 R9 输入和 manifest 保留在 bridge_backups 对应归档目录。

## 修改内容

### 两轮共同 boot

- 原 R9 zImage、内核命令行、watchdog 配置均保留；不采用此前 fix3 的 WDT 禁用、panic 改动或 dexopt 脚本。
- `init.z1.rc` 在 `post-fs-data` 同步执行现有 `/z1_display_takeover.sh`，先尝试 KD_GRAPHICS，再按名称解绑 fbcon。串口 console 保留。
- `default.prop` 设置 `selinux.restorecon_recursive=/system/etc`，使 init 按 R9 已有的 `plat_file_contexts` 给新增文件标 `system_file`。
- ROOTFS body 长度、MTK 头和 DT `linux,initrd-end` 一致；分区容量闸门保持开启。

### R1 system

- 新增 `/system/etc/z1_wallpaper.png`，240×320、RGB、纯色。
- `build.prop` 新增 `ro.config.wallpaper=/system/etc/z1_wallpaper.png`，保留原 0600 权限。
- AOSP 9 `WallpaperManager.openDefaultWallpaper()` 支持该属性。已有 `/data` 用户壁纸优先，**不清除用户数据**。

### R2 system

- 包含 R1 的壁纸改动。
- 增加 `/system/etc/z1-driver-evidence/i2cprobe.gz`：既有静态 ARM32 探针。此次主机缺 ARM libc 头文件，没有重编此探针。
- 增加 `/system/etc/z1-driver-evidence/edt-ft5x06.ko.gz`：在隔离源码树用 R9 配置与 R9 Module.symvers 重新编译的触摸候选模块，vermagic 为 `7.0.0-rc7-z1+`。
- R2 boot 仅新增 `/z1_touch_evidence.sh`。没有自动 insmod、自动 I2C 探测、DT 节点启用、GPIO 操作或充电控制。

## 明日顺序

1. **R1 boot + 原 R9 system**：只验证显示接管。记录 `_`/文本是否仍出现、Android 是否保持运行，不以 `cmdline` 行数推断重启。
2. **R1 boot + R1 system**：检查新属性、壁纸路径实际命中情况、SystemUI/ImageWallpaper ANR、真实显示与性能。boot_completed 不等于显示成功；hw_composer sync 计数不等于 FPS。
3. **R2 boot + R2 system**：确认第 2 步无回归后才运行取证脚本。若失败，回到对应 R1 配对，避免同时引入多个变量。

刷写使用 `tools/flash_with_retry.sh <bootimg|android> <candidate> <original-R9-rollback> <retry>`，先起脚本再由用户按开机键。刷写结束后必须由人确认设备状态，再拔线下一步开机。大分区写失败或读回不一致必须停止，不进入 UI 验收。

每轮使用 USB ACM 抓日志：`tools/z1_capture_android9_acm.py --wait 180 --seconds 300 --status --tombstones`；必要时续抓，确保屏幕超时后的现象也有记录。以用户口述画面变化时间与日志对照。

## 触摸取证（R2，手动）

在设备的 Android shell 或可执行该脚本的诊断 shell 中，依次执行：

```sh
/system/bin/sh /z1_touch_evidence.sh probe
/system/bin/sh /z1_touch_evidence.sh identity
/system/bin/sh /z1_touch_evidence.sh module
```

- 仅针对 I2C1 地址 `0x15`，不全总线扫描。
- identity 只读 `0xa6` 起 6 字节；有响应与合理身份信息后，仍需 GPIO/IRQ/供电证据才启用节点。
- module 只输出解压后模块哈希，**不插入模块**。不要因为没有 DT 节点就人工 insmod 来假装触摸已启用。
- 工具解压临时文件到 `/data/local/tmp/z1-driver-evidence`，会保留至手动清理；不清除其他用户数据。

## 剩余驱动判断

- 触摸：主线 `edt-ft5x06` 的 FT6236 匹配与 EdgeTX 的 CST836U 独立实现支持候选协议，但 Z1 实体响应、IRQ、reset、供电尚未实证。
- WiFi/BT：R9 已按集成 CONNSYS 路线准备模块与固件查找路径；继续保留 feature mask，先取固件 ACK、wiphy/wlan0 或 HCI command-complete，不能用旧外置 MT6620 no-op GPIO 桩替代。
- 音频：寄存器资料和 AFE 时钟缺口仍在，保留既有 NO-GO；本两轮没有音频驱动改动。
- 电池/充电：当前 ADC 电压和 health-z1 仅提供有限遥测，capacity 未实现，不能把单点电压或 level=0 当作低电关机证据；没有新增充电控制。

## 参考

- AOSP 9 `WallpaperManager.java`：https://android.googlesource.com/platform/frameworks/base/+/android-9.0.0_r1/core/java/android/app/WallpaperManager.java
- Linux FT6236 主线实现：本地 `drivers/input/touchscreen/edt-ft5x06.c`。
- EdgeTX CST836U 独立实现：https://github.com/EdgeTX/edgetx/blob/main/radio/src/boards/helloradio-h750/touch_driver.cpp

详细构建命令和 hash 见各产物目录的 manifest.json，以及模块归档 `products_r2_touch_module_*/BUILD.txt`。
