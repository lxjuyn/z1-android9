# Z1 原厂蓝牙与扬声器镜像对照

2026-11-22。只读拆解原厂 `1/boot.img`（SHA256 `2dd41702e839…`）和 `1/system.img`（`0aad817b3f87…`）。候选取自项目归档 `r7-r7-bt-stack-compat-20261121_234156`，boot `0d19730f…`、system `a3fc264d…`。板级证据来自外层项目 `mainline_recon/z1_boot_mainline_20261121_225400.log`，该日志对应之前的测试，不能当成此候选的验收。

## 蓝牙

1. 原厂 ramdisk `init.project.rc:29–43` 建立字符设备 `/dev/stpbt`，设置 `0660 bluetooth:radio`。第 90、97 行配置 `wmt_loader` 及 `6620_launcher -p /system/etc/firmware/`。`init.rc:1137–1141` 以 `nvram` 用户、`nvram system` 组启动 `nvram_daemon`。R7 则在蓝牙 HAL 的服务定义增加 `nvram` 附加组，用 `z1_linux7_radio_init.sh` 检查记录长度、调整权限和发布 `nvram_init=Ready`。
2. 原厂 `libbluetoothdrv.so` 的 `mtk_bt_enable@0x778` 进入 `libbluetooth_mtk.so` 的 `mtk@0xd6c`，打开 `/dev/stpbt`，读取 NVRAM、确认芯片号，再执行 `BT_InitDevice@0x11ec` 和 `GORM_Init@0x1ec8`。初始化表包含地址、射频、TX 功率偏移、休眠及 Reset 命令。`bt_read_nvram@0xab8` 等待 Ready，再调用 `NVM_GetFileDesc` 并读取 64 字节有效载荷。旧板级日志 11225–11230 记录了 66 字节 `BT_Addr` 和 514 字节 `WIFI`。
3. R7 中的原厂 `libbluetoothdrv.so`、`libbluetooth_mtk.so`、`libnvram.so` 与原厂一致。用 `debugfs dump` 从原厂 `/lib` 和 R7 `/vendor/lib` 再取出三个依赖，SHA256 同样一致：`libcustom_nvram.so` 为 `cf34ca23a06d…`，`libnvram_sec.so` 为 `ade22e2f50cd…`，`libnvram_platform.so` 为 `e7be213ca2d9…`。
4. `libcustom_nvram.so` 指定 `/data/nvram/APCFG/APRDCL/FILE_VER`。`libnvram.so` 的 `NVM_GetFileDesc@0x6e2c` 在 `0x7038` 调用 `NVM_CmpFileVerNo`。`NVM_CmpFileVerNo` 在 `0x4f22` 检查版本文件，含 `0x4f32` 的生成分支。打开记录的路径也可走从备份区域恢复、转换版本、恢复默认值及文件保护。候选脚本仅检查并授权 `APRDEB` 及其祖先目录和两条记录，没有检查 `APRDCL/FILE_VER` 或以 HAL 身份确认它可读。旧日志记录了 `nvram_init` 等待失败（5328、5331），没有记录该次启动的 `FILE_VER` 元数据或进入版本校验后的结果。判定这个差异是否触发故障，需要用目标候选实际启动后读取元数据并对齐相同启动轮次的 NVRAM 日志。原厂路径可能写入校准记录，板上取证先用只读检查。
5. 旧板级日志还记录了 WMT 超时和整片复位（4431–4467）、Settings 对空 `UsbManager` 调用 `getPorts()`（4853、4866），以及直接 H4 测试通过 Reset、Read Local Version 和特性页 0/1，页 2/3 返回 `0x30`（14664–14670）。H4 测试绕过 GORM。设置页闪退、WMT 首次开启失败、原厂 NVRAM 初始化失败和 Android 协议栈特性页处理都要各自验证。当前 R7 候选限制了上报的特性页上限，Settings APK 仍与 R5 一致。

## 扬声器

1. 原厂 `/etc/audio_policy.conf` 默认选择扬声器，主输出为 44.1 kHz PCM16。实际厂商逻辑在 `/lib/libaudio.primary.default.so`（`0b8648a73239…`），`/lib/hw/audio.primary.default.so` 是另一份小模块。原厂 `ueventd.rc:67` 把 `/dev/eac` 授权给 `root:audio`。厂商库 `AudioMachineDevice::AnalogOpen@0x61374` 的相应分支下发 `0x400443a6`、`0x400443a4`、`0x400443a1`。
2. 原厂内核 `Sound_Speaker_Turnon@0xc048729c` 把 GPIO140 配为输出、拉高、等待 10 ms。`Sound_Speaker_Turnoff@0xc04872e8` 拉低 GPIO140。PMIC 播放偏置序列写 `AUDTOP_CON5=0x1114`，再以 `0x37a2 → 0x37e2` 设置 `AUDTOP_CON6`。原厂内核取证文件是外层项目 `out/forensic/stock_kernel.bin`，基址 `0xc0008000`。
3. R7 使用 tinyalsa/ALSA。`/vendor/etc/primary_audio_policy_configuration.xml` 仍默认选扬声器，主输出为 48 kHz 双声道 PCM16。R7 `/vendor/lib/hw/` 同时有 `audio.primary.default.so`（`3f1b3a841aea…`）和 `audio.primary.mt6572.so`（`029ed847bb14…`），两份库都调用 `pcm_open` 和 `pcm_write`，但 `mt6572` 版的输入入口只有 12 字节，默认版另有 `pcm_read`，两者不能视为同一构建。镜像 `ro.board.platform=mt6572`，实际加载路径需从设备进程核实。回读 boot 的 DTB 确认 GPIO140 高有效及 AFE 寄存器窗口 `<0x11140000 0x10000>`。回读 `/lib/modules/mt6572-mt6323.ko`（`23977c4fdcf1…`），`mt6572_speaker_event` 确有拉高、等待 10 ms 及拉低。R7 codec 模块（`3fa88d5aa073…`）与已验证包含原厂 PMIC 偏置修复的产物一致，见 [原厂音频时序验证](Z1_AUDIO_FACTORY_CONTRACT_20261119.md)。
4. 旧板级日志 4307 显示 PCM `RUNNING`，用户仍反馈媒体无声。目标镜像验收用同一无耳机条件，分别播放 Android 媒体和 `tinyplay` 测试音，期间取实际 HAL 与模块、PCM 硬件指针、Speaker DAPM、GPIO140 及 PMIC 寄存器。若两条路径都无声且 PCM 指针推进，优先比较编解码器、功放实际电平及模拟路由。若只有 Android 媒体无声，优先查 HAL 选择、输出路由及音量。以听到连续测试音为通过条件。
