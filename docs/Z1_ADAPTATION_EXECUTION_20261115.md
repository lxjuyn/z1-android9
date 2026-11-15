# Z1 继续适配实施记录（2026-11-15）

本轮目标是交付可复核代码与待测候选，按存储、启动、图形、外围顺序验证。当前没有执行刷机、格式化、板上播放或录音。离线通过与上板通过分别记录。

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

## 本轮进展

已完成主机侧实现和候选装配，仍未刷机：

- 内核 `f259bce95` 已推送：MTK mutex 超时返回错误并取消请求；CRTC 进入粘性冻结，错误完成 fence，固定仍在扫描的 framebuffer。9 个故障场景、OVL 回归、ARM 对象编译和 checkpatch 通过。
- MT6323 codec `8b816f317` 已推送：`AIF1 Capture` 经过 `MIC Route` 接到 MIC signal path；39 个 host capture 检查和 ARM 模块编译通过。Android 输入 HAL、物理 MIC 接线、真实录音仍未完成。
- stage 持久覆盖增加 boot candidate 身份核验：当前 ramdisk candidate 相同或 pin 匹配才允许 active 覆盖；旧 active 会回退当前 candidate；probe 输出 version 4 和模块来源/hash。stage 37 项、probe 20 项测试通过。
- 新的 [z1_build_adaptation_boot.py](../scripts/z1_build_adaptation_boot.py) 只接受显式 SHA256 的 zImage、DTB 和 ramdisk 替换；检查 ELF 类型、内核模块 vermagic、cpio 文件类型、DTB boot contract、initrd-end、6 MiB 限制并反解回读。专用测试 11/11 通过。
- 候选 boot 已装配但未刷：`out/adaptation_candidate_20261115_pkg/boot.img`，SHA256 `1bfa05465065318236132223d6957e962060c922d7f5ccaf2462fdf09ec858a3`，5,978,112 B；使用重编 CRTC 内核、r17 HWC、已审查播放 HAL、CST836U 修正版和 MT6323 codec。归档在 `bridge_backups/products/products_adaptation_candidate_20261115_135935/`。

候选仍需板上逐项确认：先核 `/data/local/tmp/z1stage` 的模块来源属性和 `/vendor` SHA，再看启动稳定性、真实 KMS/fence、Lima/Mesa 3D、播放、按键、GPS 和蓝牙。没有这些日志，不把离线通过写成硬件通过。
