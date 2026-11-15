# Z1 继续适配实施记录（2026-11-15）

本轮按存储、启动、图形、外围顺序验证。用户已于 2026-11-15 晚授权刷写；r3 boot 与配套 system 均完成写入和严格读回。实机本轮 boot id 为 `3d41079d-6389-4594-a4f6-e408d06748d9`，约 204 s 到达 `boot_completed`；首轮约 77.6 s 曾复位一次，原因待定位，因此不能称无限重启或稳定性全通过。已结束的主取证日志为 `mainline_recon/z1_boot_mainline_20261115_210603.log`，当前轮日志为 `mainline_recon/z1_boot_mainline_20261115_215411.log`。

## 当前执行版本：r3（2026-11-15 晚）

以下成对镜像取代后文 13:59 的初始候选；不要混用旧 system 或自动启动 NVRAM daemon 的试验 system。

| 文件 | 字节数 | SHA256 |
|---|---:|---|
| `out/adaptation_candidate_20261115_r3/boot_pkg/boot.img` | 5,982,208 | `af9f5448fbeca7fedee41f1949bc751f0d8ff7ef23fb9bec81a162482f42602e` |
| `out/adaptation_candidate_20261115_r3/system.img` | 943,718,400 | `c2adda7709d7be0f5012ba2530bd9c55beea97b261b778652ab9763269171875` |

刷前重新计算了这两件镜像及下面两件恢复镜像的 SHA256。独立反解通过：boot 内 5 个替换文件的类型/hash、MTK 头、appended DTB、initrd-end `0x8429024d`；system 内 7 个文件的类型/mode/uid/gid/SELinux/hash，以及 `e2fsck -fn` 返回 0。证据位于候选目录的 `independent_*`，已补入 `bridge_backups/products/products_adaptation_r3_20261115_181052/candidate/` 并更新归档 SHA256 清单。

- boot：20:41 启动 `flash_with_retry.sh`，首次擦除命令返回 0 并显示 `Formatted`，但完整输出未通过严格日志判定，随后恢复 boot 写入并严格读回成功；第二次 r3 boot 写入及完整分区读回成功。已保存日志只有该命令末尾，不能据此确定首次被拒绝的具体错误标记。日志 `mainline_recon/flash_r3_boot_20261115_2041.log`。这证明 boot 内容已写入，不能据此宣称图形或外围可用。
- system：`z1_flash_chunked.sh android ... 48 8` 退出 0，全部 19 块、943,718,400 字节读回逐字节一致。日志 `mainline_recon/flash_r3_system_20261115_2043.log`，逐次 MTK 日志及 `result.json` 在 `mainline_recon/flash_chunks/20261115_204309_write_612mgn7o/`。成对刷写证据 `out/adaptation_candidate_20261115_r3/flash_result_20261115.json` 已归档。
- CH340 抓取已启动，按本镜像 DT bootargs 实解值使用 921600；日志 `mainline_recon/z1_boot_mainline_20261115_204133.log`。system 校验完成后拔下载线，再由用户开机；串口线保持连接。
- 只读回查分区表得到 bootimg `0x2980000/0x600000`、android `0x4340000/0x38400000`，与传输范围相符。此版本 `printgpt` 在完整输出 19 项后计算总容量时因 `totalsectors=None` 抛 TypeError（退出 1），不能写成命令成功；已单独记录，传输读回证明不依赖该命令退出值。
- 20:53 本次启动早期证据：内核 `#166`、console 921600；`/data` 与 `/cache` 的 stage e2fsck 都返回 0，随后验证 RW mount；`storage gate ready`（约 20.22s）。Lima 与 PMIC keys 注册成功。仍需等待 Android 完整启动并实测各功能，注册不等于功能通过。

### r3 已完成的板上取证（边界仍按下表执行）

- 启动：上述 boot id 在约 204 s 到达 `boot_completed`；约 77.6 s 的首轮复位仍未定位，不能把本轮写成稳定性全通过。
- 音频：OpenSL 3 次 callback 成功，PCM 为 `RUNNING` 且 `hw_ptr` 推进；root `tinyplay` 可正确播放 2 s、48 kHz、2 ch、`amp4000` WAV，speaker-only/HP0 及全部 DAPM On 组合仍由用户确认无声，播放物理结果为 **FAIL**。录音 HAL 仍返回 `ENOSYS`。已确定 48 kHz 与 PMIC 固定 44.1 kHz 不一致；rate 修复仍是候选、未部署，物理根因待 A/B。
- 图形/GPU：`bridge_backups/products/products_gpu_r3_board_20261115_2130/analysis.json` 记录 20 files、445450 pixels、0 mismatch，像素校验 PASS；`hardware_status=UNPROVEN`，UI jank 仍约 72--85%，尚未解决。
- 触摸/导航：live `key_y_min` 从 300 调至 320，用户确认现在正常；source/module 与 system rc 的修复已由 commit `d2654d1` 记录。新的 `system/etc/init/z1_touch_navigation.rc` 已通过 root 安装，SHA256 为 `32b84644e3eeb7bb99af897d1f104ce7518fc87beafe919d0b81de4960b56901`，`root:root`、mode `0644`、`system_file` 标签，安装命令返回 0；设备 `/proc/mounts` 确认 system 仍只读，当前 `CURRENT_KEY_Y_MIN=320`。证据位于 `mainline_recon/z1_boot_mainline_20261115_215411.log` 的 `[r4_critical_snapshot_begin]`/`[r4_critical_snapshot_end]` 段。下一次开机自动应用尚未重启验证。当前 `immersive.navigation=*` 生效，但 system `build.prop` 仍为 `qemu.hw.mainkeys=0`，镜像尚未改为隐藏导航栏。
- 锁屏图形：HWC r18（`db8b5fd`）已构建但未部署；当前 r17 锁屏行为仍保留末帧。
- 按键：`key 158 -> VOLUME_UP` 只改在源码，等待真实 `getevent` 证据；不能写成已在板上完成。
- 蓝牙：HCI init abort / WMT timeout 仍未解决。

### r3 的功能边界

| 功能 | 已进入候选的修复/准备 | 仍需验证或完成 |
|---|---|---|
| 图形/GPU | r17 HWC、CRTC 故障处理、Mesa/Lima 候选 | 本次启动的真实模块/maps、正确像素、3D 渲染及稳定性 |
| 音频 | 播放 HAL；MT6323 MIC signal route 与 capture 共享时钟修复，codec SHA `0810cb19072b1a58473f7541ea607fbaf1a35cd456ed43b1a031f09c56036914` | 播放及物理 MIC；Android 输入 HAL 仍返回 `-ENOSYS`，policy 为 playback-only，录音未完成。旧 `z1_diag_audio.sh trigger` 的 WAV/采样率/设备/时长不正确，暂不执行 |
| 触摸/按键 | CST836U 修正版、PMIC IRQ/错误路径修复、当前 EINT16 LEVEL_HIGH | 同一 boot 的 IRQ 增量、EV_KEY 按下/释放、触摸坐标；不凭注册日志认定可用 |
| 蓝牙/WiFi | 保留当前 SOC transport 与现行 WiFi 模块；NVRAM 只读预检 | WMT arbitration/reset/RX timeout、BT HCI/配对/音频、实际连接；须恢复 `mtk_stp_bt_soc.ko` 对应源码，不能改错旧 common 驱动 |
| GPS | gpsdrv/prep/installer 失败处理与持续状态核验 | `mnld` 服务名/回调 ABI 成套适配及真实定位；installer 完成不代表 GPS 完成 |
| 充电 | r2 内核停充失败重试、remove/shutdown 收尾 | 实际充放电、热保护；battery TEMP=250 仍是固定值，真实软件温控未完成 |

禁刷 `out/adaptation_candidate_20261115_nvram/system.img`（SHA `34355c3dae728d9b9195bc7eb7cc9e956b945832d8bbbe42e36338ca0944a447`），其自动 NVRAM 启动可能重置校准缓存。`out/adaptation_nvram_disabled_20261115_r3/` 也仅为 disabled 诊断分支，不作为本轮默认基线；手动启动 daemon 仍可能写缓存。

## 保留恢复基线

- Android 仓库起点 `5712a9e1176a1a1b942d6dc89eba6705498498d8`；内核起点 `187434377e15f1f483d4f8a19ab421e0885ccd84`。
- 恢复 boot `out/z1_recovery_20261112_233945/package_audio_ordered/boot.img`，SHA256 `ef5f07e4459b6d2fcf42b64a50202349b00e80a979274f85bf9101f1052f1468`。该包保留 r14 图形，不能称 r16/r17 已进入恢复镜像。
- 外围 system `out/z1_driver_candidates_20261110/system_peripherals_v1.img`，SHA256 `833a65f23ceada548339d9f48db4765931136698755d00c9b67f2f4de12cc8dd`。
- 新产物使用独立路径，版本/依赖/持久覆盖物成套回退。保留旧 A9 worktree 中未提交工作。

## 分工及实施门禁

| 功能 | 本轮具体工作 | 离线门禁 | 板测要求与回退 |
|---|---|---|---|
| 图形同步（主代理；少量 GPT 关键复核） | 移除 250ms 释放与 vsync 推进；KMS OUT_FENCE_PTR 绑定提交；上一帧 release；失败提交不轮换 copy 槽；小层双缓冲 | 精确生产函数 mock、错误/延迟 fence、失败 import/commit、静置、关闭 vsync、fd 耗尽、ARM 构建与 ELF | 在恢复后单独替换完整图形集合；核 maps/真实像素/静置/压力/无提前复用；回退 r14 整套图形 |
| 音频（DeepSeek 审查，主代理复核） | 查写错误后恢复、配置与输入 HAL；先证明真实缺陷再分文件实施 | 现有 ARM mock 回归与候选 softfp/依赖/符号检查 | 扬声器/耳机左右声道、停止重开；录音须有效样本与 AudioRecord，不能仅看节点 |
| 输入/电源（DeepSeek 审查，主代理复核） | 查实际 CST836U 加载源、键盘/PMIC 错误传播、亮度与充电状态 | 模块身份、目标编译、适当故障测试 | getevent、IRQ 增量、按键/触摸/插拔/亮度；回退对应模块与 DT/HAL 整套 |
| 无线（DeepSeek 审查，主代理复核） | BT transport/回调/资源生命周期；WiFi 源身份、GPS/FM ABI 继续取证 | 现有 mock/ABI/依赖检查；身份不符不改旧驱动 | WiFi 连接与数据、BT HCI/配对/A2DP；GPS 真定位、FM 真调谐收听另验 |
| 存储/启动 | 保留严格分块读回、失败 fsck 阻止 RW、stage guard；新候选不得覆盖已修链 | boot 反解身份、initrd-end、大小、system 文件类型/hash、e2fsck -fn | 先留证恢复 data/cache，若需清空另需用户操作；确认不再 ext4 abort/EROFS、启动稳定后才测功能 |
| health/睡眠/RTC/USB/TF | 查已有候选和硬件证据缺口，能构建的先构建；无接线/ABI证据不猜 DTS | 来源、模块/依赖/脚本契约 | 充电量纲、唤醒/闹钟、ACM/ADB/MTP/OTG逐项；TF需实际接线与卡检测 |

## 联网原始资料

已在本轮实际下载并计算 SHA256，原件与抓取清单在 `mainline_recon/adaptation_sources_20261115/`（工作区外层）。

- AOSP `android-9.0.0_r61` libhardware `hwcomposer2.h`：`ded4274fc2dfcfdeffb780cd4b3a9aa8ef8d3db6659a9555dac476132f9797f7`。`getReleaseFences` 明确保护 prior frame；present 对应此帧开始显示的 vsync。
- Linux `v6.12` `drivers/gpu/drm/drm_atomic_uapi.c`：`d130775eeab9ea43c11d6185fdfe376570765c5d1418a345dd6bbb589313ea16`。OUT_FENCE_PTR 是每次 CRTC atomic commit 返回的 sync file。实现还必须对照本机内核的 `drm_atomic_uapi.c` 和 `mediatek/mtk_crtc.c`，不能把上游版本当成本机二进制证明。
- AOSP `android-9.0.0_r61` libhardware `audio.h`：`dbbbd3a18502fb3f286e46107f6bf182dd889b07ce71336060e2a412a18300c0`。用于核对输入/输出方法与缓冲时序。

原始 URL、抓取时间、字节数保存在来源清单；结论必须同时有本机代码/测试证据。联网搜索接口未返回可引用正文，因此未把空结果记为依据。

## 早前构建记录（13:59 初始候选，已被 r3 取代）

以下记录是本轮早前的主机侧实现与初始候选装配状态：

- 内核 `f259bce95` 已推送：MTK mutex 超时返回错误并取消请求；CRTC 进入粘性冻结，错误完成 fence，固定仍在扫描的 framebuffer。9 个故障场景、OVL 回归、ARM 对象编译和 checkpatch 通过。
- MT6323 codec `8b816f317` 已推送：`AIF1 Capture` 经过 `MIC Route` 接到 MIC signal path；39 个 host capture 检查和 ARM 模块编译通过。Android 输入 HAL、物理 MIC 接线、真实录音仍未完成。
- stage 持久覆盖增加 boot candidate 身份核验：当前 ramdisk candidate 相同或 pin 匹配才允许 active 覆盖；旧 active 会回退当前 candidate；probe 输出 version 4 和模块来源/hash。stage 37 项、probe 20 项测试通过。
- 新的 [z1_build_adaptation_boot.py](../scripts/z1_build_adaptation_boot.py) 只接受显式 SHA256 的 zImage、DTB 和 ramdisk 替换；检查 ELF 类型、内核模块 vermagic、cpio 文件类型、DTB boot contract、initrd-end、6 MiB 限制并反解回读。专用测试 11/11 通过。
- 候选 boot 已装配但未刷：`out/adaptation_candidate_20261115_pkg/boot.img`，SHA256 `1bfa05465065318236132223d6957e962060c922d7f5ccaf2462fdf09ec858a3`，5,978,112 B；使用重编 CRTC 内核、r17 HWC、已审查播放 HAL、CST836U 修正版和 MT6323 codec。归档在 `bridge_backups/products/products_adaptation_candidate_20261115_135935/`。

候选仍需板上逐项确认：先核 `/data/local/tmp/z1stage` 的模块来源属性和 `/vendor` SHA，再看启动稳定性、真实 KMS/fence、Lima/Mesa 3D、播放、按键、GPS 和蓝牙。没有这些日志，不把离线通过写成硬件通过。
