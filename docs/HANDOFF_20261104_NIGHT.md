# Z1 交接 — 2026-11-04 夜

## 明天第一件事：刷这两件

| 分区 | 镜像 | sha256 |
|---|---|---|
| `bootimg` | `out/z1_fix3e_boot/boot.img` | `de206e0c15841295bf50c24698b9e10eed12bee3a4aa506cc1c6113e221ae8e6` |
| `android` | `out/z1_v3_system_final/system.img` | `b6942cd8f209d0f68ab3b0ab2d51ab21b0e311457cfce1496010595f633db117` |

回滚：`out/z1_fix3_boot/boot.img` + `out/z1_fix3_system/system_fix3.img`（当前线上、能进系统那套）

命令：`Z1_VERIFY=0 tools/flash_with_retry.sh bootimg <img> <rollback> 12`，再 `... android ...`
纪律：先起工具再按键；刷完拔 USB 长按电源 10s 再开机。

### 验收判据
1. `logcat -b crash | grep -c 'FATAL EXCEPTION'` 从 34 → **0**
2. `Could not find service hosting interface android.hardware.radio@1.0::IRadio/slot1` 从 238 → **0**
3. 串口出现 `[z1drop]` 的 `system_app_crash` 内容
4. 桌面是 Trebuchet + 渐变壁纸；能看到开机动画；设置停留 10s 不闪退
5. `[z1audio] set [...]` 出现且 `mixer now lists N controls` N>0

## 这一版包含的修复（都有证据）

| # | 修复 | 根因证据 |
|---|---|---|
| 1 | `ro.radio.noril=yes` | com.android.phone persistent 崩溃循环 34 次/76.6s；镜像无 radio HAL、无 rild；radio 接口缺失日志 238 次 |
| 2 | `ro.hardware.egl=swiftshader`（build.prop 与实际一致） | `/vendor/lib/egl` 只有 swiftshader |
| 3 | relay v5：dropbox 文件名改 `system_app_crash@*` + `dumpsys dropbox --print` | AOSP 9 `AMS.processClass()` 对 FLAG_SYSTEM 返回 `system_app` |
| 4 | relay v4/v5：`logcat '*:W'` 优先级过滤替代关键词 grep | ART 堆栈行不含 FATAL 关键词，历史零堆栈 |
| 5 | 音频路由 `tinymix [控件] [值]`（原 `tinymix set` 是非法子命令） | 五条路由从未生效，`route ok: 0 controls` |
| 6 | audio rc 删 `@4.0` 幽灵 interface 行 | 二进制只注册 2.0 |
| 7 | `z1_speedup.rc` 注释 `stop bootanim` | bootanim 启动 40ms 就被杀 = "看不到二屏" |
| 8 | light@2.0 HAL 部署 | 原镜像无 light HAL |
| 9 | Trebuchet 进 `/priv-app` + 240x320 渐变壁纸 + 删 `Home.apk` | 旧桌面是 AOSP 示例，`Theme` 的 `windowShowWallpaper=false` 把壁纸盖死 |
| 10 | CST836U 驱动补 `INPUT_PROP_DIRECT` | `/proc/bus/input/devices` 显示 `PROP=0` |

## 已验证通过（不用再查）

- **触摸**：`z1_gpio_hunt insmod OK` → `CST836U identity: fw=0011` → `chip ACKed at 0x15 on i2c-0`，三次开机稳定复现
- **显示链路端到端**：`simplefb` @ `0xbfd00000` == LK `LCD_ConfigOVL layer=2 addr=0xbfd00000 pitch=512`；能进系统看到界面
- **`sys.boot_completed=1`** 可达（约 456-501s，软渲染慢）

## 未解决

1. **`netd` SIGSEGV**：`RefBase::incStrong` 在 `_hidl_interfaceChain` 里空指针（fault addr 0x4），tombstone_42/43 同一崩溃。怀疑这套拼装 ROM 的 `libhidltransport`/`libhwbinder`/`libhidlbase` 不自洽 —— agent 报告未完成，明天重派。
2. **Watchdog 内核卡死**：`Binder:2515_1B` 卡在 `sock_def_readable`/`unix_dgram_sendmsg` 自旋锁，与 app 崩溃无关，属 mainline 内核缺陷。本 boot 已重启 zygote 4 次。
3. **`z1_mnld` 每 5s exit 1**（GPS 守护进程）。v5/fix3d 系列 boot 里有 `oneshot` 止血，但真因未查。
4. **GPU/lima**：gralloc 包装库已重链 libdl（`out/gralloc.z1.dl.so`，sha `be99d2bb…`），但**未验证**。上次直接换是崩的（`failed to open gralloc0 device: EINVAL`）。下一步应刷 `out/z1_g1_system/system_g1.img`（= fix3 + 仅新 gralloc）单变量验证。
5. **WiFi**：`z1_wifi_probe.sh` 的 `6620_launcher*` 前缀匹配 bug 已在 fix4/fix5 系列修好，但那些 boot 未上板。

## 环境备忘

- 串口：CH340 `/dev/ttyUSB0`，**921600**；抓取脚本 `./z1_capture_mainline.sh <秒>`（`C3_PORT=/dev/ttyUSB0 Z1_CAPTURE_BAUD=921600 Z1_CAPTURE_PORT_KIND=ch340-uart`）
- ACM gadget `0e8d:2006` 现在**能枚举**了（历史零命中），但 `z1_bootdiag --acm` 还没往里写数据，暂时只是个空通道
- 顶层 `/home/lxj/claude/6572` 不是 git 仓库；`fork_linux`→z1-mt6572-mainline，`z1a9_gpt`→z1-android9
- **往公开仓库放快照必须先脱敏 PAT**
- 磁盘紧张（当前 6.1G）；`out/` 下每个 system.img 约 887MB
