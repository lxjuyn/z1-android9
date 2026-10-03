# Jiabo Z1 Android 9 S1K 诊断候选

基于 Android 9 和 Z1 原厂 Linux 3.4.67 内核。该候选通过静态构建与镜像检查，**尚未在设备上启动验证**，用于下一轮受控 bring-up，不是可日常使用的成品。

## 刷写

1. 下载 `boot.img` 和 `system.img.xz`，将后者解压为原始 `system.img`。
2. SP Flash Tool 选择 **Download Only**，将 `boot.img` 写入 `bootimg` 分区，将原始 `system.img` 写入 `ANDROID` 分区。
3. 不勾选 PRELOADER、NVRAM/NVDATA、userdata、校准数据或其他分区；不要执行 Format。

解压 `system.img.xz` 后，在同一目录运行 `sha256sum -c SHA256SUMS` 核对 boot、压缩镜像、原始镜像和说明文件。boot 分区为 6 MiB，ANDROID 分区为 900 MiB。

## 当前状态

- `system.img` 是 943,718,400 字节的原始 ext4 镜像；`e2fsck -fn` 和 init/ELF/类路径静态闭包检查通过。
- 完整保留原厂 MT6572 keypad 键位表中 Android 9 支持的 90 个映射。键码 139 映射为“最近任务”，产品配置隐藏虚拟导航栏；实体键事件仍需上板确认。
- 显示使用 framebuffer 软件合成和 SwiftShader。Mali GPU 加速尚未接入。
- 音频选择了原厂 MT6572 HAL；扬声器、麦克风、耳机和休眠恢复尚未实机验证。
- 触摸尚未确认。原厂日志曾记录 Hynitron `hyn_ts` 在 I²C 地址 `0x15` 探测失败，需设备日志确认控制器和资源配置。
- Wi‑Fi 仅放入 WMT loader/固件，Android 9 Wi‑Fi HAL 尚未接通；蓝牙也还缺 Android 9 HCI/HAL 适配。
- SELinux 使用 permissive policy 26 和 `allow_unknown`，只适合受控调试，不能作为日常系统。

本包未包含 NVRAM、Wi‑Fi 密码/MAC、音频校准或用户数据。设备运行状态、触摸、按键、音频和启动稳定性均尚未由这两个镜像验证。
