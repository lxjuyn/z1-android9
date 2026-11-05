# 佳伯听力机 Z1：Android 9 / Linux 7 移植

[English](README.md) | **简体中文**

本仓库保存佳伯听力机 **Z1 / MT6572** 的 Android 9（LineageOS 16.0）设备树、主线 Linux 适配、镜像构建与验证工具。目标是在双核 Cortex-A7、1GB 内存、240×320 屏幕的设备上逐步恢复可用系统，而不是把“能编译”当成“能使用”。

## 当前进展

### 已上板验证：显示控制台接管

R1 显示接管 boot 配合原 R9 system 已验证：

- Android 可以正常进入系统；
- 屏幕上的闪烁 `_` 消失，VT/fbcon 不再与 Android 显示争夺 framebuffer；
- 内核日志确认 `KD_GRAPHICS` 生效，并切换到 dummy console；
- SwiftShader 可以完成软件显示合成。

这不代表显示问题全部解决。测试中仍观察到 SystemUI / ImageWallpaper ANR、明显卡顿、壁纸缺失和屏幕超时休眠，需继续修复。

### 正在验证的候选

- **轻量默认壁纸**：使用 240×320 PNG 和 Android 9 支持的 `ro.config.wallpaper`，尝试减少 1080×1080 默认壁纸在软件渲染设备上的开销。已有 `/data` 用户壁纸优先，不清除用户数据。
- **ACM + ADB 调试接口**：诊断 ACM 与 ADB FunctionFS 共用 USB gadget；保留 ADB 安全认证，不向仓库或镜像写入密钥。
- **触摸取证**：提供针对 I2C1 `0x15` 的窄地址只读探针，以及按 R9 配置与符号表编译的 `edt-ft5x06.ko`。不会自动探测、加载模块或启用未经验证的 GPIO/IRQ。

## 驱动状态

| 功能 | 状态 |
|---|---|
| framebuffer / SwiftShader | 已能进入 Android；性能与稳定性仍需优化 |
| VT/fbcon 接管 | 已上板验证 `_` 消失 |
| SystemUI / 壁纸 | 正在修复；不能把默认壁纸配置等同于已解决 ANR |
| 触摸 | 候选协议有依据；实体响应、IRQ、reset、供电仍待验证 |
| 物理按键 | 当前 DTS 仅证实音量键，不能套用其他设备的按键映射 |
| Wi-Fi / 蓝牙 | 按 MT6572 集成 CONNSYS 路线保留功能屏蔽，等待固件、wiphy/HCI 实测 |
| 音频 | AFE/codec 寄存器与 clock 资料不足，保持既有 NO-GO |
| 电池/充电 | ADC 电压遥测有限，capacity 未实现；没有新增充电控制 |

## 镜像与发布

已验证的候选和 SHA-256 会放在 GitHub Releases。发布包不包含：

- 用户数据、NVRAM、Wi-Fi MAC 或校准数据；
- 串口/logcat 原始日志；
- GitHub token、ADB 私钥、API key 或其他凭据；
- 与本次发布无关的主机路径和实验文件。

大镜像不直接提交到设备树 Git 历史。下载后请核对 `SHA256SUMS.txt`，按 Release 说明的 boot/system 配对刷写，不要把不同实验阶段的镜像混用。

## 构建与验证

仓库中的脚本支持从原始 R9 镜像制作增量候选：

- 保留原始 zImage、命令行和允许列表外的 ramdisk 内容；
- 核对 MTK ROOTFS 长度、DT `linux,initrd-end`、boot 分区容量；
- 验证 ELF 架构、模块 vermagic、文件权限及镜像读回；
- 主机测试通过不等于设备功能通过，每版都需单独抓日志验证。

上传前使用 `scripts/z1_secret_scan.py --staged` 做敏感信息扫描，并人工检查暂存 diff。该工具是辅助检查，不替代发布者审核。

## 当前测试顺序

1. R1 显示接管 boot + 原 R9 system：验证 `_` 与 Android 显示。
2. 配套显示接管 boot + 轻量壁纸 system：验证壁纸与 SystemUI。
3. R2 boot + R2 system：显示无回归后，手动进行触摸只读取证。

详细候选说明见 [Z1_CANDIDATES_20261025.md](Z1_CANDIDATES_20261025.md)。未经上板验证的候选只标记为诊断/实验版本。

## 许可与边界

设备树中基于 AOSP 的代码保留原许可说明。厂商固件、校准资料和其他设备的私有信息不纳入公开仓库。主线驱动适配仍以实际 Z1 板级证据为准，不能用其他 MT6572 设备的原理图或 GPIO 配置替代。
