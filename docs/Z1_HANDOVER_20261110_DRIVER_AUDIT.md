# Z1 驱动/功能调查交接文档（2026-11-10）

> 目的：把本轮所有**已完成、可复核**的调查结论、已落补丁、仍缺的证据一次交代清楚，
> 供其他 agent 无上下文接手。
>
> **本文所有"已验证"都附本地证据路径；"待验证"就是待验证，不要当结论用。**
> 本轮**没有刷机、没有操作板子、没有 push**；补丁都在工作树 + `bridge_backups/`。

---

## 0. 本轮环境与基线（先读，否则会误判）

| 项 | 值 | 说明 |
|---|---|---|
| Android 仓 | `z1a9_gpt`，分支 `main`，HEAD `ae70acc` | 本轮新增改动未提交 |
| 内核仓 | `fork_linux`，分支 `main`，HEAD `1d7b807ae` | 本轮新增 1 个 pmic-keys 改动 |
| **现行内核源树 = `fork_linux`** | `out/z1_v83/pack_boot_report.txt:7-10` 明确取 fork_linux 的 zImage/dtb | **`a9_wt` 是旧树（10-19），不要再用它推断现状** |
| v83 内核载荷 | 4330507 B，sha `be9cb8f8…`，逐字节等于当前 fork_linux zImage+dtb | 已核 |
| boot 分区上限 | 6 MiB = 6,291,456 B | r14 余 391,168 B；v79 余 483,328 B |
| **文档里的哈希不可信** | `out/Z1_STATUS_OVL_20261110.md` 记的 v83 `b8d04db5…` ≠ 实际 `ad3eed1a…` | 一律以 `sha256sum` + 反解为准 |

### 0.1 已知可用产物（本轮不动，作回滚）

| 角色 | 文件 | 大小 | sha256 |
|---|---|---|---|
| 满血 OVL boot | `z1a9_gpt/releases/ovl_v83_r14/boot.img` | 5,900,288 | `6c85b6a0a705f75401ed0b7867b1dd5d80ca30bcc7d93b47b182a5bbf73e0af4` |
| 进 Android 的 boot | `out/z1_v79/boot.img` | 5,808,128 | `d332b5e962438b8f07efa22ebb785f895516454fb3cc8548990d11c75bf0fc84` |
| 原厂兜底 | `1/boot.img` | 6,291,456 | `2dd41702e839ec2547ce0dea2bbd3603a5d019786502d939ebba4a6b578eaab5` |
| r14 HWC | `releases/ovl_v83_r14/hwcomposer.z1.so` | 147,360 | `03d2f200f4ea8f8fd1b4bc8d8dd06c190ef2a23f69811fad875b56296acffa95` |
| r14 gralloc | `releases/ovl_v83_r14/gralloc.z1.so` | 28,548 | `0ed9fb2b573bfe4bf2e533b7f709ad054a1dc95482d420aca184d6c95823b77d` |

**注意**：`releases/ovl_v83_r14/README.md:14` 把 HWC 写成 147,176 B，实际 147,360 B。

---

## 1. 图形（2D 已达成 / 3D 计划）

**完整 3D 计划见 `docs/gpu/Z1_GPU3D_PLAN_20261110.md`。**

### 1.1 OVL 2D 的真实边界（勿夸大）

| 说法 | 结论 |
|---|---|
| `device=4 client=0` | 真实存在，但只是**快照**（`z1_boot_v83.log:54728`、`:55919`） |
| "11009 帧全程 4 层" | **否**。同日志有 `frame 6000: device=1`（`:12127` 等）及 `DEVICE -> CLIENT` |
| "全程零拷贝" | **否**。小层走 `small_layer_sync()` CPU 拷贝（r14 README:60-63） |
| fence / vsync | r14 二进制 `present/retire=-1`；r16 从未出现 `first vsync ts=` |

### 1.2 图形侧确定缺陷（未修，交给后续 agent）

1. `scripts/z1hwc2/z1hwc2.cpp:1257-1276` `wait_fence()` 把任何 `poll>0` 当成功；
   `:2472-2486` 超时后仍关 fd 并继续读写该 buffer。
2. `:2490-2497` client target 两种路径都失败时清空后继续提交。
3. `graphics/gralloc/Android.mk:26-35` 漏 `z1_heap.cpp`，`gralloc.cpp:171` 弱符号
   `z1_heap_alloc_ex` 缺失时**静默回退 ashmem**。
4. `:728` `fb_import` 未先校验 handle `version/numFds/numInts`。
5. `:536` 250ms watchdog 到点强制放行（时间流逝不等于扫描停止）。

### 1.3 3D 关键事实

- Mali-400 = Utgard，**只有 GLES2/ES1.1**；不支持 GLES3/Vulkan/OpenCL（Mesa 官方 Lima 文档）。
- A9 HWUI/SF 都只需 ES2：HWUI 设 `debug.hwui.renderer=opengl`；
  `ro.opengles.version=131072`（**不要写 196608**）。
- Mesa 主候选 26.1.1 双库：`libGLES_mesa.so`=`bb0ac77c…`、`libgallium_dri.so`=`492f0b5e…`
  （已在 v28/v30/v33 确认）；**必须成对部署**，`z1_stage_26` 整包里的 gallium 是旧的。
- 构建：必须 `gles1=enabled`；后处理**先改 SONAME 再 strip**。
- lima 用 shmem/SG 页映射，OVL 要物理连续；IOMMU 关闭 ⇒ 推荐"显示侧 CMA 分配 → lima 导入"。
- GP `0x4000`=`LIMA_GP_IRQ_PLB_INVALID_CMD`（`lima_regs.h:86`）；Mesa #2701 在 Mali450 同样出现，
  **升级版本不等于修复**。

---

## 2. 按键

### 2.1 已验证：keylayout 含 Android 9 拒绝的标志

- 板载日志：`mainline_recon/z1_boot_v83.log:44282/44285` 明确拒绝 `WAKE` / `WAKE_DROPPED`。
- AOSP r61 `InputEventLabels.h:402-407` 只接受 `VIRTUAL/FUNCTION/GESTURE`；
  `KeyLayoutMap.cpp:278-283` 对未知标志返回错误。

### 2.2 已改（仅源码，未部署）

| 文件 | 改动 | 备份 |
|---|---|---|
| `keylayout/mt6779-keypad.kl` | 删除 7 处 `WAKE/WAKE_DROPPED`；**12 项码值→动作全部保留**；四方向行逐字未动；250/251/252/254 标注"未验证候选" | `bridge_backups/keylayout_android9_20261110_204929/` |
| `keylayout/z1_cst836u.kl` | 恢复底部三键 `158 BACK / 102 HOME / 139 APP_SWITCH`（对齐 v35 镜像与 `mtk-tpd.kl:3-5`），删掉"触摸条=复读/跟读"的错误注释 | `bridge_backups/z1_cst836u.kl_before_nav_restore_20261110_205546.bak` |

修后 sha256：matrix `ccbd0e9c…`，touch `621e1de4…`。

### 2.3 部署缺口（**必须处理，否则改了不生效**）

1. `device.mk:148-156` **只复制 `mt6779-keypad.kl`，没有 `z1_cst836u.kl`** → 触摸条映射不会进镜像。
2. v35 镜像里两份 .kl 仍是旧的（matrix `3327e407…`、touch `52329208…`，带非法 flag）。
3. `scripts/r5b_fixes/mt6779-keypad.kl` 是另一份旧副本，某些旧应用脚本取它。

### 2.4 矩阵键盘"复读无用 / 跟读变返回"的最强线索（新）

**驱动无 bug，缺口在 DTS 键表。**

- 原厂内核 `0x713348` 的 72×u16 键表，12 个非零项与现行 DTS **逐项相同**（u32 变体 0 命中）；
  反汇编 `0xc0507688` 的 `ldrh/strh` 0x90=144 字节复制循环，指针字面量正好指向 `0xc071348`，地址吻合。
- 原厂扫描函数读 `0xf0002000+4/8/0xc/0x10/0x14` 五个半字，去抖写 `+0x18`，
  与现行驱动 mem 起始 0x04、5 寄存器、debounce 0x18 一致。
- 板上实测 LK 只接 **5 行（GPIO110/111/112/142/143）× 3 列（107/108/109）= 15 键**
  （`mainline_recon/z1_boot_mainline_20261108_193550.log:55-58`）。
- 现行 DTS `mt6572-z1.dts:785-807` 只映射了 **12 个**，缺 **槽 11（row1,col2）、槽 27（row3,col0）、
  槽 29（row3,col2）**。这三处按下时驱动会发 `MSC_SCAN`（0x12/0x30/0x32），但 `keycode=0=KEY_RESERVED`
  被输入核心丢弃 ⇒ **Android 侧完全无事件**，`.kl` 无法补救。
  ⇒ "复读无用"极可能是这三处之一，**需要改 DTS 键表**（本轮无人有权改 DTS）。
- "跟读变返回"：现行映射里只有 **槽 36（row4,col0）→ MSC 0x40 → keycode 158=BACK**。
  若 getevent 确认跟读出 0x40/158，就是 `.kl` 的 `key 158 BACK` 造成的，
  改 `.kl` 即可（不是驱动问题）。注意触摸屏 `z1_cst836u` 也可能出 158，**必须按 event 节点区分**。
- 250/251/252/254 在官方 `input-event-codes.h` 里**没有定义**，是厂商私有码，仅作占位。

**下一步**：先 `getevent -t` 逐键采样确认"复读/跟读"分别是哪个 MSC_SCAN；
若落在 0x12/0x30/0x32，补 DTS 三行（需主会话授权改 DTS 并重编 dtb+boot）。

### 2.5 仍缺的证据（决定物理映射）

- **没有任何"物理标签→event 节点→EV_KEY code"的原始 `getevent` 记录**（419 份日志查过）。
- 原厂内核 `0x713348` 的 72×u16 键表与现行 DTS `mt6572-z1.dts:794-805` 的 12 项**逐项相同**；
  矩阵驱动 `mt6779-keypad.c` 与官方 v7.0-rc7 逐字节一致，`/9 %9` 与 DTS 8×9 匹配（旧"3 列"假设是错的）。
- 用户新要求：**中间播放键=确定，复读=音量−，跟读=音量＋**；上下左右已正常不得动。
  当前 `232→DPAD_CENTER` 是候选，**未证明物理播放键就是 232**。

采样命令（只读）：
```
cat /proc/bus/input/devices
getevent -pl
dumpsys input
timeout 10 getevent -t /dev/input/eventX   # 每键单独按放两遍
```
把 **EV_KEY 的 code 转十进制**填 .kl，**不要用 MSC_SCAN 值**。

### 2.6 电源键（另一 agent 已交付补丁）

- 正常路径正确：MT6323 `CHRSTATUS(0x0142)` `BIT(1)`、低有效、KEY_POWER=116。
- DTS `mt6572-z1.dts:510-514` = `EINT16 LEVEL_HIGH`；`mt6323.dtsi:263-275` power 节点启用。
  **旧注释说 LEVEL_LOW 是错的，别按注释判极性。**
- 已修缺陷：`mtk-pmic-keys.c:205-216` 忽略 `regmap_read` 返回值，读失败用未初始化值上报按键
  → 已改为失败即返回 IRQ_HANDLED。主机故障注入：旧版 2 项 FAIL、新版 6 项全 PASS；
  `-O2` 单文件内核编译 `MAKE_RC=0` 无警告。
- **轮询 fallback 从未部署**：`z1_pwrkey_poll/` 不在 Kconfig/Makefile、无 .ko、DTS 无节点、
  v73/v83 ramdisk 里也没有。
- 仍未证：物理按下时 CHRSTATUS bit1 是否翻转、`/proc/interrupts` 46 号计数、是否发 EV_KEY 116。
- 历史"IRQ37 失败"结论**已作废**：现行日志是 IRQ46，`irq/46-mt6397-pmic` 线程存在。

---

## 3. 触摸（底部三键 / 触摸屏）

- 芯片确证：**CST836U**（原厂内核串 `HYN_CST836U_DWZN_MT6572_4.4_V1.0_20181106`），
  地址 0x15，`i2c-0`，驱动 `z1_cst836u`；板上 `identity fw=0011 module=01 project=01 chip=0013`。
- 原厂报告循环反汇编：只读寄存器 `0x00` 长度 15，`count=byte2&0x0f`，点步长 6，
  12-bit X/Y，上报 `BTN_TOUCH`+`ABS_MT_POSITION_X/Y`；**该循环没有独立按键寄存器读取**。
- 因此"底部三键"到底是 `y≥300` 的坐标区域还是芯片另有键数据，**仍未证实**；
  现行 `key_y_min=300 / x<80 / x<160` 没有实体标注样本支撑。厂商 PDF 与 EdgeTX 驱动也都是 0x02/0x03 坐标协议。
- 已修（源码）：`drivers/z1_cst836u/z1_cst836u.c`
  1. 连续 50 次 I2C 失败原来只释放导航键、**不释放 BTN_TOUCH**（手势卡住）→ 改为完整释放；
  2. 导航键切回屏幕时同一 sync 帧顺序改为先 `BTN_TOUCH=1` 再 `KEY_x=0`；
  3. 新增 `log_frames` 完整 13 字节采样（**不增加 I2C 读**），用于定位三键。
  - 测试 `test_cst836u.py` 10/10 PASS；旧代码在 3 项上故意失败（回归有效）。
- **实际加载路径**：`/bin/z1_touch_load.sh:24` 加载 `/system/lib/modules/z1_cst836u.ko`
  （sha `b48245e3…`），**不是 ramdisk 里那份**。改完必须重编 .ko 并替换 system 内那份。
- 未改：键区阈值、键码映射、`.kl`、DTS、config。
- 疑点留给映射 agent：驱动第三键用 `KEY_MENU(139)`，Android"最近任务"通常 `KEY_APPSELECT(244)`；
  左/中/右顺序也无实证。

---

## 4. 电池 / 电量（**用户症状最大，根因已收窄但未闭环**）

### 4.1 已严格证明

- 当前内核：`generic-adc-battery.c:185-198` 每次读 `CAPACITY` 都**重新读瞬时电压**再查 OCV 表；
  `:125-128` 电压 ≥ 表顶直接返回表顶百分比；DTS `mt6572-z1.dts:431-447` 表顶是 `4200000 100`。
  ⇒ **任何 ≥4.2V 读数必然 100%**，与真实 SOC、是否充电无关。
- ADC 链路：`mt6323-auxadc.c:71-81` BATSNS=channel 7；`:176-181` 比例 `4*1800/2^15`；
  先清后置 `AUXADC_CON22`、轮询 READY、`fsleep(300)`。与 MTK 厂源码、Google MT6582 参考**完全吻合**，
  **没有找到 ADC 通道/倍数错误**。
- 历史日志（`z1_boot_mainline_20261108_105346.log`）：
  `:230` 15.8s `voltage=3354000` → `:247` charging ENABLED → `:332` 31.2s `voltage=4287000`。
  另两份日志同样 3.88→4.266V、3.427→4.287V。
  ⇒ **软件输入值在充电时确实跳到表顶**；但"真实端电压是否也到 4.2V"未实测。
- 原厂内核（sha `3128816c…`）含完整 `battery_meter`（`SOC_BY_SW_FG`、`oam_init/oam_run`、
  OCV+温度 Qmax+内阻估流并积分），**不是电压查表**。但 Z1 实际是否走该分支未证实；
  且其初始化会**暂停充电 50ms**，属充电策略，需另行授权。

### 4.2 health 层（已修一个独立 bug）

- `health/capacity_policy.h` 原来二次采样读到有效值却仍上报首次的 0；
  已改为"校验与上报同一份样本"，并拒绝嵌入 NUL/尾部垃圾。
- `health/tests/run_host_tests.sh`：1600 组组合 PASS（ASan/UBSan，`-Werror`）。
- **这不是插电 100% 的根因修复**；health 层没有任何 `online→100` 逻辑。
- 部署：`Z1_KERNEL_VARIANT=mainline bash scripts/build_z1.sh android.hardware.health@2.0-service.z1`
  （默认 stock 会把服务从产品移除）。v35 内该 ELF 仍是旧逻辑 `667c104b…`。

### 4.3 必须补的实测（决定是采样错还是算法错）

1. 万用表测静置/充电时**电池端电压**，同时读 `/sys/class/power_supply/battery/voltage_now`；
   若真实 3.8~3.9V 而上报 4.28V → 采样/板级问题；若真实确实 >4.2V → "充电端电压当 OCV"的算法问题。
2. 连续读 ADC0（BATSNS）与 ADC1（ISENSE）raw，看插线前后哪一路跳、是否与充电电流同步。
3. 取 Z1 实际电池 OCV-SOC / 温度-内阻 / Qmax 标定（现有 DTS 曲线标 TBC，是通用值）。

---

## 5. Wi-Fi

- 现行方案是 **HIDL**（`/vendor/bin/hw/wpa_supplicant`，socket `/data/vendor/wifi/wpa/sockets`，
  HIDL 1.0/1.1）；`rootdir/init.z1.network.rc:63-77` **已明确退役 legacy 手动 supplicant**，
  不要再修旧 SSL 或手动 `z1_wifi_connect.sh`。
- 已证缺陷：`system_v35` 内 `libwifi-hal.so`（sha `0a9ba411…`）**硬编码 Broadcom 路径**
  `/sys/module/dhd/parameters/firmware_path` 与 `bcmdhd_sta.bin`；MTK 上 `open` 必失败，
  `ChangeFirmwareMode` 直接返回 false。日志可复核：`Failed to open wlan fw path param`、
  `Failed to change firmware mode`（`z1_boot_20261109_1838.log`，v83 日志亦有）。
- 正在做：`wifi/patch_z1_wifi_hal_sta.py`（按精确输入 sha + ELF 符号校验，只取消不适用于 MTK 的
  STA 固件切换，保留 HIDL；AP/P2P 不冒充支持）。**未完成、未部署。**
- 历史 `wlan0 scan OK` 只证明扫描，不等于能关联/DHCP/上网。

---

## 6. 蓝牙

- 现行：`bluetooth/libbt-vendor-mtk/libbt-vendor-mtk.c`（A9 passthrough HIDL + 原厂 `libbluetoothdrv.so`）。
- 已改（源码，未完成验证）：按原厂 `libbluetoothdrv.so` `mtk_bt_enable` 实际**无参**签名修正
  （`0x778`），`BT_VND_OP_POWER_CTRL` 真正开/关、`FW_CFG` 按真实 `hci_fd` 结果回调、
  `SCO_CFG`/`SET_AUDIO_STATE` 不再假装成功。未上板。
- 仍需：`/dev/stpbt` 存在性、WMT func-on、控制器扫描/配对/A2DP 实测。

---

## 7. GPS

- `scripts/hal_stub/gps_stub.c` 与 `scripts/gpsstub/gps_stub.c` 是**零硬件、零回调 stub**：
  HIDL 服务 running **不等于**能定位。
- mnld 反汇编确认：用 `strcmp` 精确匹配 `GPS_CHIP.cfg` 中的 `0x6620`/`0x6572`；
  现行 prep 写 `MTK_GPS_CHIP=MT6620` 这种文本**不生效**。
- `libmnlp_mt6572` 调 `ioctl(fd, 7, 0)`，而当前内核 `stp_chrdev_gps` 只实现 6 和 0/1，**缺 7 的语义**（待核实，不得瞎补）。
- legacy HAL 硬编码 `init.svc.mnld`，而公共 rc 的服务名是 `z1_mnld`。
- 已识别需禁用：旧 install 脚本伪写 `init.svc.mnld running`、routeB 隐瞒错误。**未改公共 init。**

---

## 8. 音频 / 3.5mm

- 已证缺陷（`scripts/r5g_audio/audio_primary_z1_poc.c`）：
  首次 `pcm_open` 失败后 `open_fails=1`，退避分支**不递增**，此后 `write` 永久不再重试；
  `set/get_master_volume` 虚报硬件支持；输入入口直接 `-ENOSYS`。
  正在按 AOSP9 契约修该 HAL（仅此文件），未完成。
- **3.5mm 是否正常还没有实测**：声卡存在 ≠ 出声。验收需低音量分别听左/右声道、
  拔插切换、播放/录音回环。

---

## 9. TF 卡 / USB / 存储（已完成只读调查）

用户确认：**机器有 TF(microSD) 卡槽**；**没有指示灯**（不列缺陷）。

| 功能 | 状态 | 依据 |
|---|---|---|
| ACM 串口 | 已验证（仅 gadget 绑定） | `z1_boot_v83.log:2175-2186` 有 `ACM bound` + `/dev/ttyGS0`；MUSB PERIPHERAL，UDC `musb-hdrc.4.auto`。主机侧收发未验 |
| **ADB** | **未实现** | 镜像内无 `service adbd`（`lineage-adb.rc` 只有属性动作）；v83 boot ramdisk 85 项无 `init.usb*.rc`、无 `z1_usb_setup.sh`；`rootdir/init.z1.rc:19-21` 置 `sys.usb.config none`，`:41-53` 只启 ACM；`device.mk:75-98` 未打包该脚本 |
| **MTP/文件传输** | **未实现** | `/bin/mtpd` 是旧 PPP 守护；无 MtpService 激活、无 `ffs.mtp`；`persist.sys.usb.config=none`；原厂 `build.prop:88,109` 曾有 `mtp,mass_storage` |
| USB OTG 主机 | 不支持 | DTS `mt6572-z1.dts:477` `dr_mode="peripheral"`；XHCI/EHCI/OHCI 全关、`USB_MUSB_HOST` 未设 |
| **TF/外置 SD** | **未实现** | `mt6572.dtsi:530-550` mmc1 默认 disabled；`mt6572-z1.dts:667-679` 只启用 mmc0 且 `no-sd/non-removable`；**无 `&mmc1`、无 MC1 pinctrl、无 vmmc/vqmmc、无 CD**；`fstab.z1` 无 removable 条目；原厂 `fstab:9-11` 有 `voldmanaged=sdcard1:auto` |
| TF 检测脚 | 硬件未确认 | MC1_INS 可能为 GPIO24/53/67/73/77/86/88，Z1 实际走哪根未知 |
| 内部存储 FUSE | 实现待验证 | `ro.vold.z1_fuse=true`、`ro.sys.sdcardfs=false`、`/bin/sdcard` 存在；仅主机侧 3 个挂载/75 断言通过，**板端挂载时序/卸载未验** |
| fsck/持久化 | 部分已验证 | boot 内 fstab `/data wait,check`，`z1_boot_v83.log:2207,2232` 实测 p5/p6 跑 e2fsck |

**TF 卡最小实现路径**（无人有权改 DTS，需主会话授权）：
照同族样板 `mt6572-jty-d101.dts:169-186,248-265` 加 `&mmc1`
（`bus-width=4`、`cap-sd-highspeed`、`vmmc=mt6323_vmch_reg`、`vqmmc=mt6323_vmc_reg`、
引脚 91-96，先 `broken-cd` 轮询）；`fstab.z1` 加 `voldmanaged=sdcard1:auto` 并去掉
`ro.build.characteristics=nosdcard`；编后看 `mmc1/mmcblk1`。
验收：只读挂载现有卡 → 读文件 → 专用文件写读校验 → umount → 拔插重识别。
**不要格式化用户现有的卡**；若卡是 exFAT，`.config:6655` 未开，需先加或换 FAT 卡。

**ADB 最小实现路径**：把 `z1_usb_setup.sh` + configfs 初始化打进 ramdisk，
或在 `init.z1.rc` 增 `service adbd` + `ffs.adb` 挂载，验证与 ttyGS0 共存。
MTP 与 OTG 主机当前应明确标注"不支持"，单独立项。

---

## 10. 刷写与回滚（血泪教训，务必遵守）

- 刷写失败常见根因是**线缆/供电**：历史上 DA stage2 卡在 4096 字节并报 `USBError(5)`，
  换线换口后一次通过。遇到同一位置失败先换线换口。
- `z1flash_image.sh:84-114` 发出写请求后，超时/部分写/日志不匹配时最终仍可能打印
  "nothing written"，**不能据此断言 boot 未变**；必须完整读回比对。
- `.disable_hwcomposer` 只删 `/vendor/lib/hw/hwcomposer.z1.so` 一个文件
  （`z1stage_boot.sh:154-166`），不切 Mesa、不恢复 gralloc。
- `z1_install_hal.sh:112` 的回滚**不建 disable 标记**，下次开机 ramdisk 里的 HAL 会重新安装；
  `z1rootd.sh:145` 每次启动都执行持久 `install.sh`。
- `z1push.sh:373-375` 的 `--rootcmd` 只认 `###DONE`、忽略 `###RC`，不要依赖退出码 0。
- 刷写前后记录：镜像 sha256、分区名、读回比对结果。

---

## 11. 本轮产物与备份清单

| 内容 | 位置 |
|---|---|
| keylayout 备份 | `bridge_backups/keylayout_android9_20261110_204929/` |
| 触摸条恢复前备份 | `bridge_backups/z1_cst836u.kl_before_nav_restore_20261110_205546.bak` |
| 触摸驱动备份 | `bridge_backups/cst836u_navkeys_20261110_205444/` |
| health 备份 | `bridge_backups/health_capacity_20261110_204909/` |
| pmic-keys 备份/补丁/测试 | `bridge_backups/pwrkey_audit_20261110_205314/` |
| BT 审计备份 | `bridge_backups/bt_audit_20261110_205049/` |
| 3D 计划 | `docs/gpu/Z1_GPU3D_PLAN_20261110.md` |
| 本文 | `docs/Z1_HANDOVER_20261110_DRIVER_AUDIT.md` |

---

## 12. 接手 agent 的第一件事（按优先级）

1. **上板只读取证**（不改任何东西）：`getevent` 逐键采样 + `/proc/interrupts` + `dumpsys input`
   + `dumpsys battery` + `cat /sys/class/power_supply/battery/*`。这一批能同时解开按键、电源、电量三个疑点。
2. 按 `Z1_GPU3D_PLAN_20261110.md` 的 P0→P1 开始 3D，**先不碰 HWC fence**。
3. Wi-Fi/BT/GPS 的修复都需要重新出 system 镜像；先确认现行板上 system 版本与哈希。
4. 任何刷写前：确认电源/线缆、备份、读回校验、保留三级回滚镜像。
