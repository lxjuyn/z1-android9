# Z1 功能完整性盘点 — 2026-11-05

证据来源：`mainline_recon/z1_boot_mainline_20261105_183443.log`（6.9 MB，35 次内核启动，
其中仅 boot 3 到达 `boot_completed`）、`out/z1_v15_system/system_v15.img`（debugfs 只读）。

## A. 彻底死的（有实证，不是"没验证"）

| 功能 | 判定 | 证据 |
|---|---|---|
| **FM 收音机** | 驱动从未被加载 | `mtk_fm_drv.ko`(244,596 B, vermagic 匹配) 在 `/lib/modules/`；`load_audio.sh` 里 fm 命中 **0**；镜像里无任何 fm 脚本/rc；日志里 `/dev/fm`、`mtk_fm_drv` 均 **0 命中** |
| **蓝牙** | 无 `hci0` | `vendor.bluetooth-1-0` 启动后**从不退出**(日志 `exited` 计数 0)，`/dev/stpbt` 就绪，但日志无 `hci0` |
| **GPS** | mnld 崩 | `/dev/stpgps` 建好、mnld 启动，**3.7 s 后 `exited with status 1`** |
| **WiFi 联网** | 无可用 supplicant | 固件能下载(`Open FW image done, 160464 bytes`)，遗留 supplicant 链接失败已退掉，HIDL 路径未打通 |

## B. 活着但从未端到端验证

| 功能 | 现状 |
|---|---|
| 按键 | `mt6779-keypad`(input0) / `mtk-pmic-keys`(input1) / `z1_cst836u`(input2) **三个都注册了**，从未按过 |
| 音频输出 | mixer 5 条控件回读 On，**从未真的出声** |
| MIC 采集 / 助听回环 | 内核通路已写，**从未验证采集** |
| 耳机检测 | `mt6323-accdet.ko` 已加载，**无任何插拔事件** |
| 充电 | CHRDET 检测正常，**充电电流/终止电压从未核实** |
| 亮度 | light@2.0 HAL 已加，**从未验证** |
| 触摸→Android | 驱动层稳，**"能点"从未端到端确认** |

## C. 本轮已修

**FM 加载链路**（此前完全缺失）：
- `/lib/modules/load_fm.sh` 1,825 B：insmod → 等 `/dev/fm` 出现 → `chmod 0666` →
  设 `FM Playback Volume`，但**不预先打开 `FM Playback Switch`**（避免空 I2S 灌进喇叭）
- `/etc/init/z1_fm.rc`：`on post-fs-data` 后台拉起
- 产物 `out/z1_v15_system/system_v15.img`
  sha256 `ffef7c0386a7de4cd24d1e9f873e211a69140f8feba45441d11cfe00d218ae89`，`e2fsck -fn` 干净

## D. 离线能做完 / 必须上板的两类

**离线可做**：把每个死功能"接上线"（像 FM 这样），并在镜像里放好诊断脚本，
让第一次上板就能拿到明确的报错，而不是"没反应"。

**必须上板**：mnld 为什么 exit 1、BT 卡在哪一步、音频到底出不出声、
充电是否真的在充 —— 这些只能靠真实硬件。

## E. 上板优先级（等电压 ≥3.6 V）

1. 刷 `fix3j` + `v13` 确认能进系统（这是唯一验证过能到 boot_completed 的基线）
2. 有 shell 后立刻跑：`insmod /lib/modules/mtk_fm_drv.ko` 看 `/dev/fm` 是否出现、
   `dmesg | grep -i fm`；`z1_mnld` 前台跑一次看 stderr；
   `hcidump`/`dmesg | grep -i btif` 查 BT
3. 音频：`tinypcminfo -D 0 -d 0` → `tinyplay` 放一段 wav；
   `tinycap` 录一段验证 AWB 采集
4. 充电：读 MT6323 充电寄存器与 `power_supply` 的 `current_now`/`voltage_now`
