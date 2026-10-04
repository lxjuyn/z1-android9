# Z1 Android 9 S1L 诊断候选

基于 Android 9 / LineageOS 16.0 和 Z1 原厂 Linux 3.4.67 内核。boot 与 system 镜像已通过静态结构、ext4 和 init/ELF 闭包检查；这两个镜像尚未在设备上完成本轮启动验证，仍是 bring-up 候选。

早期一次原厂内核启动中，`system_server` 因内核的 timerfd 不接受 `CLOCK_BOOTTIME` 而反复崩溃。S1L 的 `libandroid_servers.so` 已加入回退：改用 `CLOCK_BOOTTIME_ALARM` 保持计时基准，但非唤醒闹钟也可能唤醒设备、增加耗电。该回退还没有在 S1L 上实机验证；更合适的最终修复是给匹配的 3.4.67 内核加入上游 timerfd 修复。

## 刷写

1. 下载 Release 资产 `boot.img` 和 `system.img.xz`。
2. 解压系统镜像：`xz -dk system.img.xz`，得到原始 ext4 `system.img`。
3. 在 SP Flash Tool 中选择原厂 scatter，使用 **Download Only**：将 `boot.img` 写入 `bootimg`，将原始 `system.img` 写入 `ANDROID`。
4. 只选择这两个分区。不要选择 PRELOADER、NVRAM/NVDATA、PRO_INFO、PROTECT、userdata 或其他分区；不要执行 Format。
5. 用 Release 的 `SHA256SUMS` 核对下载文件。

## 功能状态

- 启动镜像保留原厂 3.4.67 kernel。静态 boot 校验通过；本轮 system 镜像尚未上板启动验证。
- 蓝牙 Android 9 passthrough 适配器、HIDL 实现库和通用原厂控制器驱动已进入镜像，尚未测试控制器初始化、配对和 SCO。
- Wi-Fi NL80211 私有命令和 supplicant/hostapd 已编译。原厂内核没有独立 hwbinder 通道，Android 9 Java Wi-Fi framework 还不能控制 supplicant；Wi-Fi 目前未完成。
- 显示使用 framebuffer 软件合成和 SwiftShader；Mali GPU 加速未接入。原厂 Mali Android 4.4 二进制对 Android 9 的私有图形/ION ABI 检查未通过。
- 触摸、音频、实体按键和屏幕底部电容键仍需本机验证。早期日志包含 `hyn_ts` 探测失败，当前面板和触摸行为还需新的设备日志确认。键位表已映射 Back、Home、Recents，并隐藏虚拟导航栏。
- SELinux 为 permissive policy 26，且允许未知规则。该镜像仅用于受控调试，不适合作为日常系统。

镜像只包含 boot 与 system 分区，不包含 NVRAM、校准分区、userdata、凭证或串口日志。保留设备原有 NVRAM 与校准数据。
