# Z1 R7 蓝牙和扬声器联调包

2026-11-22。包位于项目机械盘 `z1-archive/r7-factory-repair-settings-20261122_005633/`。状态为宿主验证通过，尚未刷写。

- 蓝牙启动脚本检查原厂 `FILE_VER` 和两个校准记录。条件不足时 HAL 直接尝试 H4。若直接打开返回 `ENODEV`，等待 WMT 复位后只重试一次。候选保留先前的 HCI 特性页兼容补丁。校准内容未写入候选镜像。
- Settings 的 USB 后端在获取不到 USB 服务时返回空结果。其 Java 修复位于 `c19a954`。离线包以原 APK 为底，只替换 `classes.dex` 后重新签名。系统镜像读回的 APK 通过 API 28 签名验证，证书与原 APK 一致，其余非签名 ZIP 成员内容保持一致。
- ramdisk 音频载荷和 system 的三个音频 HAL 路径统一为通过 13 项宿主契约测试的实现。系统诊断记录实际映射的 HAL，并采样 PCM、DAPM、GPIO140 和寄存器。

| 文件 | SHA256 |
| --- | --- |
| `boot.img` | `1a5f3c1ac78afbc4112c92a754ac0d3fc7d690959992a349348df1e32463c768` |
| `system.img` | `f48322725f2f5f53d2d4b3c3b9d92379e0aea715b9d3b04ada03755381843cab` |
| `Settings.apk`，镜像内 | `6e9aa0dba297e8eb840b565b625716a7939e32a91bdc531187437721663e4b7a` |

出包由 `scripts/z1deploy/z1_build_r7_factory_repair.py` 基于固定 R7 归档生成。蓝牙适配层 9 项宿主测试和无线电脚本 10 项测试通过。`SHA256SUMS`、`e2fsck -fn`、系统文件类型和 SELinux 标签、ramdisk 成员回读均通过。scatter 的下载集合为 `bootimg` 和 `android`。

上板验收先确认开机和设置页，再测试蓝牙扫描、配对及反复开关。扬声器用无耳机状态检查 Android 媒体和同一个 `tinyplay` 测试音，检查实际发声和 GPIO140。若开机卡住，先保留串口日志判断阻塞位置。
