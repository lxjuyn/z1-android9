# R4 配对刷写已读回通过（2026-11-18）

本轮进入 Preloader-Mode，下载代理和 eMMC 初始化通过。普通只读确认512字节
与DEBUG探针逐字节一致后，执行boot单块和system19块写入，每块写后完整读回比较。

| 分区 | 已验证字节数 | SHA256 |
|---|---:|---|
| bootimg | 5,984,256 | 632d790d7fe1cf8014f4b84fe71ed8c44399f84d9c480790b5d6e8b42395410b |
| android | 943,718,400 | 7806f9153389fe681e7aada3cd8737114b1854132af792dae83ad63f3b66dc75 |

状态 `PAIR_VERIFIED_READY_TO_BOOT`，两件后端result均ok=true，verified_bytes与清单相同。
没有操作preloader、uboot、NVRAM、cache、userdata分区。

证据日志：
- mainline_recon/flash_r4_bootimg_20261118_224114_confirmed.log
- mainline_recon/flash_r4_android_20261118_224114_confirmed.log
- out/adaptation_candidate_20261115_flash/flash_progress.json

开机抓取已运行，CH340 by-id，921600：
mainline_recon/z1_boot_mainline_20261118_224601.log
此日志的镜像元数据只声明待测产物，运行时身份仍须启动后读取实际文件hash确认。

当前仅证明刷写与读回成功；音频、蓝牙、锁屏、GPU帧率、按键与导航栏仍待设备验收。

