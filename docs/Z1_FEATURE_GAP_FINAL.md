# Z1 功能缺口终盘盘点（2026-10-29）

盘点对象：

| 件 | 路径 | 大小 | 备注 |
|---|---|---|---|
| boot | `out/z1_r5c_boot/boot.img` | 6,209,536 B | mtime 10-28 19:18，**r5d 未重打 boot**（r5d 只改 system） |
| system | `out/z1_r5_system/system.img.r5d_final` | 943,718,400 B | mtime 10-28 22:08 |
| 回滚 boot | `out/z1_r41_final/boot.img` | 6,209,748 B | |
| 回滚 system | `out/z1_r5_system/system.img.r5_final` | 943,718,400 B | |

方法：`debugfs` 只读读盘 + `readelf`/`strings` + 板载日志 `mainline_recon/z1_android9_acm_20261025_105535_309533.log`（2,602,100 B，26,251 行，现役镜像最后一次上板抓的完整启动日志）+ 素材树 `out/z1_hal_a9/`、`los14/`、`downloads_gsi/jflt_ota_v2/`、`1/`（原厂 4.4.2）。

---

## 一句话结论

**第 9 条（`/vendor/etc/init/hw/` 目录不存在会不会让 15 个 HIDL service 全不启动）的答案是「不会」—— 这是误报，实测反证如下**。真正卡住"用户能感知功能"的只剩两件：**① HAL 是空壳/停摆但驱动全都在（音频 primary、lights、vibrator、power、sensors 五个 provider 集体缺失）**；**② 应用层没有任何播放器 + 没有 TTS，且两者素材都在 `los14/` 里（零编译可落地）**。合一之后，**听力机的"能放音频"这条路在 mainline 侧已经只剩"用户空间接上"这一步**，不再是内核/驱动欠缺。

---

## 表 A：素材已备好、零编译可落地（最高优先）

按"用户感知 ÷ 投入"排序。**A1–A4 可合批为一次 system 镜像写入**（同一 debugfs 会话，共用一次磁盘拷贝，省 ~1GB 写入，本机现在只剩 5.8 GB）。

| # | 项 | 用户感知 | 当前状态（实测） | 素材位置（已核实可用） | 动作量 | 可合批 |
|---|---|---|---|---|---|---|
| **A1** | **音乐播放器 App** | 🔴 **听力机核心：现在连播放器都没有** | `/priv-app` 40 项、`/app` 28 项，**无 Eleven / Music / Recorder / FMRadio**（逐条 `debugfs stat` 全 MISSING） | `los14/system/priv-app/Eleven/Eleven.apk`；**LOS16 同源件 `downloads_gsi/jflt_ota_v2/system.img:/priv-app/Eleven/Eleven.apk` = 8,679,786 B**（本 rootfs 是 LOS16 base，用 LOS16 件 ABI/API 风险最低）；`Recorder.apk` = 2,599,105 B | 2–4 次 debugfs 写入（apk + oat/x86 目录 + 权限 xml） | ✅ A1–A4 |
| **A2** | **TTS（PicoTts）** | 🟠 听力机若需朗读则有价值；原厂**有**，我们**丢了** | 镜像 `/tts` **整个目录不存在**、`/app/PicoTts` MISSING、`/lib/libttspico.so` MISSING（板载 log 已实证 codespell 走 default instance；`AudioService: onLoadSoundEffects() Error` 同族） | `los14/system/app/PicoTts/PicoTts.apk` 23,379 B + `libttspico.so` 236 K + `libttscompat.so` + `los14/system/tts/lang_pico/` **14 个语音库共 6.2 MB**（含 en-US / en-GB）。⚠️ **不要从 LOS16 取——LOS16 上游已删 PicoTts（jfltexx OTA `/tts` 也是 File not found）** | 1 次目录写入（+17 文件） | ✅ A1–A4 |
| **A3** | **light HIDL 服务（RGB 指示灯）** | 🟡 状态灯不亮；板载三连败之一 | `/lib/android.hardware.light@2.0.so` = 75,952 B **已在镜像里**、`/vendor/lib/hw/lights.default.so` 在；**但 `/vendor/bin/hw` 无 `*light*service*`**，`/vendor/etc/init` 无 `.rc`，`/vendor/etc/vintf/manifest.xml`（5,825 B）里 `light` **0 命中** | `out/z1_hal_a9/vendor/bin/hw/android.hardware.light@2.0-service.samsung`（41,836 B）**已在手上**；jfltexx 同名件 41,836 B；对齐 `.rc` 在 `out/z1_hal_a9/vendor/etc/init/android.hardware.light@2.0-service.samsung.rc`（已读，内容与 jfltexx 同款：`interface ... ILight default` / `class hal` / `shutdown critical`） | 3 次写入（bin + rc + manifest 1 条 fqname） | ✅ A1–A4 |
| **A4** | **manifest.xml 补 light fqname** | 同 A3 | 现 manifest 里**只有 16 个 hal**：audio / audio.effect / bluetooth / cas / configstore / gnss / graphics.allocator / graphics.composer / graphics.mapper / health / keymaster / media.omx / sensors / wifi / wifi.hostapd / wifi.supplicant。**light / vibrator / power 各 0 条** | 同上 | 见 A3 | ✅ A1–A4 |
| **A5** | **`ueventd.z1.rc` 补 3 个节点** | 🟡 蓝牙 | ramdisk `ueventd.z1.rc` 全文 **114 B / 2 行**（注释 + `/dev/dri/renderD128 0660 system graphics`），无 `/dev/stpbt` / `/dev/stpgps` / `/dev/wmtWifi` | 无需素材 | 1 次 ramdisk 重打包 | 可单独 |
| **A6** | **perf_stage：只做 `debug.sf.nobootanimation=1` + 删 `ro.lmk.swap_free_low_percent`** | 🟡 开机流畅度 | `out/z1_perf_stage/` **6 个文件一个都没应用**（实测确认，见复核记录 R6）；`build.prop` 里 `debug.sf.nobootanimation` / `dalvik.vm.dex2oat-threads` / `pm.dexopt.disable_bg_dexopt` **三个键全 ABSENT**；`/etc/init` 54 个 rc 无一个 `z1*.rc`；`ro.lmk.swap_free_low_percent=20` 仍在 build.prop:104（A9 lmkd 不认，A11+ 键） | `out/z1_perf_stage/apply_build_prop.sh`（已写清理逻辑）+ `build_prop_append.prop` | 1 次写入 | 可单独 |

**表 A 里明确"素材本身不存在"、不能零编译落地的**：

- **vibrator HIDL service 二进制**：素材树 `out/z1_hal_a9/vendor/bin/hw/` **只有 3 个**（gnss@1.0-service、light@2.0-service.samsung、sensors@1.0-service.jf）。**`android.hardware.vibrator@1.0-service` 不在素材树里** `∈` 确认。可用件在 **LOS16 jfltexx OTA**：`/vendor/bin/hw/android.hardware.vibrator@1.0-service.jf`（28,876 B，二进制直接在手上，零编译可搬）。
- **power HIDL service 二进制**：同样不在 `out/z1_hal_a9/`；LOS16 jfltexx OTA 有 `/vendor/bin/hw/android.hardware.power@1.1-service.jf`（29,084 B）。

---

## 表 B：需做工但价值高

| # | 项 | 用户感知 | 当前状态（实测证据） | 差什么 | 动作量 |
|---|---|---|---|---|---|
| **B1** | **音频 primary HAL（听力机的命）** | 🔴 **核心** | 内核侧**已就绪**：mainline `SND_SOC_MT6572` + MT6323 codec，`/lib/modules` 11 个 snd*.ko + `load_audio.sh` 全在；用户空间侧 `/vendor/lib/hw/audio.primary.mt6572.so`（shim）在、`/vendor/lib/libaudio.primary.default.so` **20,844 B 空壳**（`open/ioctl/read/write/mmap/pcm_open/mixer_open` UND 导入全 0）。板载三连：`MTK Audio loadLibrary() failed to open dlopen failed: library "libaudio.primary.default.so" not found` → `DevicesFactoryHAL: couldn't open audio hw device in audio.primary` → `AudioFlinger: loadHwModule() error -22 loading module primary`（AudioFlinger 本身正常，`r_submix` 起来了） | 一个真 A9 v2.0 primary HAL | **已写成 PoC + 交叉编译过**：`z1a9_gpt/scripts/r5d_audio_hal/audio_primary_z1_poc.c`（316 行）+ `libaudio.primary.z1.so`（11,812 B）+ `apply_audio_hal.sh`，**只是从未落进镜像**（磁盘 3.3 GiB < 自定 4 GiB 红线时放弃）。落 2 处：`/vendor/lib/libaudio.primary.default.so` + `/vendor/lib/hw/audio.primary.default.so` |
| **B2** | **sensors HAL（加速度计）** | 🟡 屏幕旋转/计步类 | **硬件实证存在**：原厂 3.4.67 内核里 **`sc7a20_i2c_probe` 25 处，其它加速度计驱动（BMA/KXTJ/MMA/LIS/ADXL/MC3）全 0 处** → 唯一在用的加速度计是 **SC7A20（Silan 三轴）**，走 I2C；原厂 `/etc/permissions/android.hardware.sensor.accelerometer.xml` 声明该 feature；原厂 ramdisk 自启 `msensord`；`1/EXPDB` 运行时有 `<HWMSEN> hwmsen_release`。**mainline 侧现成可绑**：`fork_linux/drivers/iio/accel/st_accel_i2c.c:122` 有 `{ .compatible = "silan,sc7a20" }`，`z1_defconfig` 已 `CONFIG_IIO_ST_ACCEL_3AXIS=m`。但 `mt6572-z1.dts` **无任何 sensor 节点**（grep accel/gsensor/msensor/magnet/compass/alsps=0） | ① DTS 加一个 i2c 子节点（I2C 从地址未知，需上板扫）② `/vendor/lib/hw/sensors.mt6572.so` 是 **MTK 专有件**（`/dev/hwmsensor`+`/sys/class/misc/m_acc_misc/accdevnum`），mainline 无此节点 → **需自写 A9 sensors HAL**（可照 B1 的 tinyalsa 外壳思路，iio sysfs 读 raw） | DTS 1 节点（需重编 dtb）+ HAL 自写 |
| **B3** | **vibrator（振动马达）** | 🟡 按键反馈 | **加载 GPL 内核支持有、DT 节点无**：mainline `.config` `CONFIG_NEW_LEDS=y`/`LEDS_CLASS=y`/`LEDS_MT6323=y`/`LEDS_GPIO=y`/`INPUT_LEDS=y` **都开着**，但 **`CONFIG_TIMED_OUTPUT` 完全不存在（0 命中）**——AOSP `vibrator.default.so` 的两条路径正是 `/sys/class/timed_output/vibrator/enable` 与 `/sys/class/leds/vibrator`；`mt6323.dtsi:38-42` 有 `mt6323_leds: leds { compatible="mediatek,mt6323-led"; status="disabled"; }` → **节点被禁用且 mainline 内核无该驱动**。原厂侧有 `vibrator`(24)/`mt65xx_leds`(8)/`led_class`(4)，原厂 ramdisk 有 `chown system system /sys/class/timed_output/vibrator/enable` → **马达硬件存在** | ① 内核：mt6323-led 驱动（mainline 无）+ `TIMED_OUTPUT` 类（或改用 LED trigger）② 启用 `mt6323_leds` DT ③ 搬 LOS16 的 vibrator service 二进制 + 写 `.rc` + manifest | 中（要改内核） |
| **B4** | **power HIDL HAL** | 🟡 待机策略/交互提速 | 板载实锤 `hwservicemanager: Cannot find entry android.hardware.power@1.0::IPower/default`、`vendor.lineage.power@1.0::ILineagePower/default` 也缺。镜像里接口库全在：`/lib/android.hardware.power@1.0/1.1/1.2/1.3.so` + `/lib/vendor.lineage.power@1.0.so`；**缺的只是 provider**。LOS16 件 `android.hardware.power@1.1-service.jf`（29,084 B）NEEDED 干净（libhidlbase/libhidltransport/libhwbinder/libutils/libhardware/**android.hardware.power@1.1.so + vendor.lineage.power@1.0.so**/libc++），**不需要板级 .so** → 大概率能直接起 | 搬二进制 + `.rc` + manifest 1 条 | 小（1–3 写入，待上板验） |
| **B5** | **GNSS 服务（现在起了就崩）** | 🟡 定位 | 交付镜像里 `/vendor/bin/hw/android.hardware.gnss@1.0-service` sha256 `0f5be792…` 与 **LOS16 jfltexx OTA 同名件逐字节相同**（16,024 B）。该二进制 `NEEDED` 只有 13 个通用库（liblog/libcutils/libbase/libutils/libhardware/libbinder/libhidlbase/libhidltransport/android.hardware.gnss@1.0.so/libc++/libc/libm/libdl），它是**通用的 hw_get_module 壳**（兼容 Matrix 里 `arch="32"`，所以没有 64 位字面量）。它能 `dlopen("gps.default.so")`，而 `/vendor/lib/hw/gps.default.so` NEEDED = `libxlog.so`✅ / `liblog`✅ / `libcutils`✅ / `libhardware`✅ / `libc`✅ / **`libstdc++.so`**（在 `/lib`✅，sphal namespace 含 /system/lib）/ **`gps.default.so`… 自引用**。且 `/vendor/lib` 下 **`libgps.utils.so` / `libloc_core.so` / `libloc_api_v02.so` / `libgeofence.so` / `libizat_core.so` / `libsam.so` / `libloc_eng.so` / `libloc_ds_api.so` 全 MISSING**（jfltexx 原装是齐的）。`mnld` NEEDED 里 `libstdc++.so`（/lib✅）、`libnvram.so`（/lib NO、/vendor/lib YES → **sphal namespace 可能找不到**） | 补 `/vendor/lib` 的 loc 系列；把 `libnvram.so` 放 `/vendor/lib` 或加 namespace 可见性；`gps.default.so` 自引用问题待上板 `readelf`/`logcat` 定位 | 中 |
| **B6** | **cpufreq / CPU_THERMAL** | 🟡 流畅度+省电 | `.config` 与 defconfig **确实不一致**（见复核记录 R7）：`fork_linux/.config` = `# CONFIG_CPUFREQ_DT is not set` + `# CONFIG_CPU_THERMAL is not set` + `# CONFIG_ARM_MEDIATEK_CPUFREQ is not set`，而 `arch/arm/configs/z1_defconfig:117 CONFIG_CPUFREQ_DT=y` / `:542 CONFIG_CPU_THERMAL=y`。DT 侧数据齐：`mt6572.dtsi:26-56` 有 `cpu_opp_table`（`operating-points-v2`/`opp-shared`，598M/806M/1001M/1209M…带 `opp-microvolt`）。板载 log 73 次 / 本日志 ≥6 次 `Failed to read cpu-freq: /sys/.../time_in_state` | 开 2 个 config + 核 `cpu@0` clocks 语义；**必须同时**把 defconfig 与交付 .config 一次对齐 | 需重编 zImage + dtb + 重打 boot.img |
| **B7** | **`media_profiles_V1_0.xml`** | 🟡 编码档位 | **板载 log 有报，且板载 log 自己给出了影响**：`W MediaProfiles: Could not find a validated xml file. Using the default instance instead.`（2 处，L1104 / L23831）。镜像里只有 `/etc/media_profiles_V1_0.dtd`（2,727 B）**没有 `.xml`**；LOS16 jfltexx OTA 有 `/vendor/etc/media_profiles_V1_0.xml`（对应 `.conf` 在 /etc 是旧的） | 该文件 | 1 次写入（`/vendor/etc/`，Z1 无摄像头但录音/编码读档位） |
| **B8** | **`audio_effects.xml`（A9 期望格式）** | 🟢 音效（对听力机低） | **A9 仍认 `.conf`，是 fallback 不是缺口**：`E EffectsConfig: Could not parse effect configuration in any of the default locations.` → `W AudioPolicyEffects: Failed to load XML effect configuration, fallback to .conf`（2 处）。也就是说 XML 找不到后**自动退回 /etc/audio_effects.conf，且该 .conf 存在（5,668 B）**、其引用的 8 个 effect 库也全在。jfltexx 是两种都放（`/vendor/etc/audio_effects.xml` + `/etc/audio_effects.conf`） | 想要 XML 就搬 jfltexx 的 | 1 次写入（**收益低**） |

---

## 表 C：需大工程（缓做）+ 「硬件是否存在都未知」的项

| # | 项 | 状态（实测） | 差什么 | 为什么缓做 / 硬件判定 |
|---|---|---|---|---|
| **C1** | **真 DRM/KMS 显示管线** | 现走 simplefb，够用 | panel + DSI 真管线 | 架构级；`mt6572-z1.dts` 里 `&dsi` 与屏节点都还 `status="disabled"` |
| **C2** | **SELinux 转 enforcing** | `androidboot.selinux=permissive`（cmdline 实测）；板载 **167 条 avc denied** | 见下方量化 | **现在不能转**（转了全线崩）。这是 U 盘级工程不是一天活 |
| **C3** | **FM 收音** | **硬件实证存在（四项里证据最硬）**。原厂 3.4.67 内核有完整 MT6627 FM（`do_fm_drv_init` / `mt_fm_probe` / `MT6627fm_low_ops_register` / `MT6627fm_rds_ops_register` / RDS RX+TX，`mt6627` 68 命中）；原厂镜像 `/etc/firmware/mt6627/mt6627_fm_v1_patch.bin`(1,028 B, magic `0020FF21`) + `_v1_coeff.bin`(1,092 B)；`/etc/fmr/mt6627_fm_cust.cfg`（中国区 `FM_RX_DEEMPHASIS_MT6627=0` / `FM_RX_RSSI_TH_LONG_MT6627=-296`，与 LOS14 件逐字节相同）；原厂 ramdisk `mknod /dev/fm c 193 0`；原厂 `/app/FMRadio.apk` + `fmradio.driver.enable=1`；`/etc/factory.ini` 有实测频点 885/1058/980/1080 | **mainline 侧无任何 FM 驱动**；交付镜像只有 `/lib/hw/radio.fm.mt6572.so` 一个 stub（且它 SELinux 无标签） | 与 WiFi/BT/GPS 共用 CONNSYS 天线，优先级低于 WiFi/BT；**不要报成"硬件未知"** |
| **C4** | **摄像头** | **无硬件证据，判为不存在**。原厂内核里那套 `kd_sensorlist`/`CAMERA_HW`/`KD_CAMERA_FLASHLIGHT`/`libcam*`/`camera.default.so`/`android.hardware.camera.xml` 全是通用 MTK 4.4 BSP 内容，**没有任何 Z1 专属的 sensor/pin 识别**；同一内核里 Z1 专属指纹只有屏 `st7785m_qvga_dsi_vdo_hsd`、触摸 `HYN_CST836U_..._20181106`、MT6627；内核自带 `No imgsensor alive` / `NULL ppSensorList` / `dummy_cam_cal` 无模组路径；原厂 `/app` 无相机 APK（只有看图用的 Gallery2）；**佐证**：`PLAN.md:25`「无相机、无 modem」、`HANDOFF_20261028.md:19`「无摄像头/调制解调器使用」。注：`1/NVRAM` 有 `CAMERA_*` LID 但同表还带 `HWMON_GYRO`（本机也没有）→ LID 名不能当证据 | — | **判不存在**，不做 |
| **C5** | **sensors 里的磁力计 / 陀螺仪 / 光距** | **无证据，判不存在**。原厂内核里 `akm*/bmm*/mmc*/yas*/qmc*/mc6420/mag3110/hscdt/ist8303` **全 0 命中**，`m_mag_misc` 符号不存在；`mag_sensor device!` 只是 SoC 平台层注册串（同批还有 gyro/baro/temp，本机都没有）。加速度计（SC7A20）**有**，见 B2 | — | 只做加速度计（B2），其余不做 |
| **C6** | **TTS 的"朗读能力"本身** | 素材在（见 A2），但要真出声**依赖 B1 音频通路** | 音频先通 | 与 B1 串行 |
| **C7** | **RIL / 电话** | 无 SIM 槽（`HANDOFF` 明写"无调制解调器使用"） | — | 不做 |

---

## 复核记录（确认 / 推翻）

### ✅ 确认（文档对，我独立复现）

- **R1 确认**：`HIDL light/vibrator/power/sensors 四连败`。板载日志逐字抓到（L20353-20357 / L20545-20554 / L20678-20688）：
  ```
  init: Received control message 'interface_start' for 'android.hardware.light@2.0::ILight/default' from pid: 89
  init: Could not find service hosting interface android.hardware.light@2.0::ILight/default
  hwservicemanager: getTransport: Cannot find entry android.hardware.light@2.0::ILight/default in either framework or device manifest.
  LightsService: Unable to get ILight interface.
  ```
  `vibrator` 同款 + `VibratorService: vibratorOff command failed (1).`；`sensors` 同款（两种措辞都抓到了，见 R4）。文档说的四者全对。
- **R2 确认**：`out/z1_hal_a9/` 素材**只有 3 个 service 二进制**（gnss@1.0-service / light@2.0-service.samsung / sensors@1.0-service.jf）。「vibrator / power service 二进制在素材树里根本不存在」——**确认**。
- **R3 确认**：**`dev/mem` 与 `/dev/gpiomem` 无关**——不对，改为：**`lights.default.so` 走的确实是 sysfs LED 路径**。`strings` 实测：`/sys/class/leds/lcd-backlight/brightness`、`/sys/class/leds/button-backlight/brightness`、`/sys/class/leds/keyboard-backlight/brightness`、`/sys/class/leds/jogball-backlight/brightness`、`/sys/class/leds/red|green|blue/brightness`+`trigger`+`delay_on/off`，并带 `MTK lights Module` 字样。这正是文档里"需确认 `lights.default.so` 用的是 sysfs LED 还是 MTK 专有 ioctl"那一问的答案：**是 sysfs**（但**目标节点集是 jfltexx/MTK 通用名，Z1 板上 RGB 是 GPIO141/144/136 且 dts 无 leds 节点 → 仍需写 drivers/leds 节点或自写 HAL**）。
- **R4 确认**：`sensors` 的两种失败措辞都在同一版日志里 —— 归档文档用的 `Could not find service hosting android.hardware.sensors@1.0` 在**这批日志里 grep 0 命中**，实际措辞是 `init: Could not find service hosting interface android.hardware.sensors@1.0::ISensors/default` + `hwservicemanager: ... Cannot find entry ... in either framework or device manifest.`。**结论一致，措辞需更正**（原文档引的字符串可能来自更早一版日志）。
- **R5 确认**：**`vibrator.default.so` 确实是 timed_output/LED 路径**，字符串实测：`Vibrator using timed_output` / `Vibrator using LED trigger` / `Vibrator device does not exist. Cannot start vibrator` / `/sys/class/timed_output/vibrator/enable` / `/sys/class/leds/vibrator`。而 mainline `.config` **`TIMED_OUTPUT` 0 命中** → 文档「需自写或找 LOS14 件」成立，但**补一层**：内核类都没有，搬 HAL 也不动。
- **R6 确认（perf_stage 一个都没应用）**：`/build.prop`（3,415 B 级）grep `debug.sf.nobootanimation` / `dex2oat-threads` / `disable_bg_dexopt` → **三键全 ABSENT**；`/etc/init` 无 `z1*.rc`；`ro.lmk.swap_free_low_percent=20` **仍在 build.prop:104**。`out/z1_perf_stage/` 实测 **6 个文件**（Doc A 的 6 件计数对，Doc B 的"四件齐"漏计 `apply_build_prop.sh`+`disable_list.txt`）。
- **R7 确认（cpufreq 缺口与 defconfig 不一致）**：`fork_linux/.config` vs `z1_defconfig` 逐项复核，**分歧是真的**，且 `fork_linux` 自己已经在 commit `b6e1d7897` 里把这个分歧写成了"仍未解决的不一致 (另案, out/Z1_CPUFREQ_GAP.md)"。OPP 表在 `mt6572.dtsi:26-56`（不是 z1.dts，文档没写位置）——`cpu-opp-table` + `operating-points-v2` + `opp-shared`。注意分歧**不止 cpufreq 两行**：`.config` 里连 `CONFIG_ARM_MEDIATEK_CPUFREQ_HW=m` 都开着但主驱动 `# ... is not set`。
- **R8 确认（媒体/时钟链路已在 r5c 修好、r5d 干净）**：6 个 `media_codecs*.xml` 全在 `/vendor/etc`（4,830 / 3,900 / 973 / 5,693 / 858 / 2,348 B），且 `media_codecs_performance.xml` 已是 858 B 的 **qcom=0** 版（r5d 的 N3 修复到位）。

### ❌ 推翻（文档错，我实测反证）

- **R9 推翻 —— 第 9 条：`/vendor/etc/init/hw/` 不存在 ≠ HIDL service 不启动。这是本轮最重要的结论。**
  - **实测证据（六条独立链，全部指向同一结论）**：
    1. **板载日志直接证明 `/vendor/etc/init/*.rc` 里的 service 起来了**（最硬）：`L710-711` `D vndkservice: Loading /vendor/lib/hw/android.hardware.bluetooth@1.0-impl.so` / `I android.hardware.bluetooth@1.0-service: Registration complete for android.hardware.bluetooth@1.0::IBluetoothHci/default.`；`L714` `android.hardware.graphics.allocator@2.0-service: Registration complete for ...IAllocator/default.`；`L737` `android.hardware.graphics.composer@2.1-service: Registration complete for ...IComposer/default.`。**这三个 service 只由 `/vendor/etc/init/*.rc` 定义**（`/vendor` 目录**不是** symlink —— `debugfs` 里 `/vendor` 是真实目录、`/system/vendor` 不存在），所以这些 rc 必然被扫到了。
    2. **`init.svc.<name>` 属性实证**：`init.svc.health-hal-z1=running`（`health-hal-z1` **只在** `/vendor/etc/init/android.hardware.health@2.0-service.z1.rc` 定义）、`init.svc.vendor.keymaster-3-0=running`（**只在** `/vendor/etc/init/android.hardware.keymaster@3.0-service.rc` 定义）、`init.svc.vndservicemanager=running`（**只在** `/vendor/etc/init/vndservicemanager.rc` 定义）。三者全 running。
    3. **`class_start hal` 在 ramdisk `init.rc:673`**，日志里 `class hal` 的服务确实在跑（allocator/composer 都是 `class hal animation`）。
    4. **A9 init 的 import 在缺失路径上是静默非致命**：`init.rc:9` 的 `import /vendor/etc/init/hw/init.${ro.hardware}.rc` 无任何报错（该目录在 r3f/r5/r5d 三镜像都不存在，`debugfs` = `File not found by ext2_lookup`）。
    5. **A9 init 会无条件扫 4 个目录**：从 ramdisk 解出的 `init` 二进制里提取到 `/vendor/etc/init`、`/system/etc/init`、`/odm/etc/init`、`/product/etc/init` 四条路径串（`init/hw` 字面量 0 命中）。
    6. **反向对照**：**provider 缺失的 HAL 恰好是"manifest 里没有 fqname"的那几个**（light/vibrator/power），而 **manifest 里有 fqname 的 vendor HAL（audio/audio.effect/bluetooth/health/keymaster/cas/media.omx/wifi/wifi.hostapd/wifi.supplicant）其二进制都在 `/vendor/bin/hw`**。这条对照把"服务起不来的原因"锁定在 **manifest 缺条目**，而不是"rc 没被扫到"。
  - **为什么原文档会误判**：它从 `grep 'Parsing file'` 只有 12 行就推断目录没被扫。实际上 **`init: Parsing file ...` 被内核 `printk` ratelimiting 吃掉了** —— 同一段日志里有 `printk: init: 4 output lines suppressed due to ratelimiting`（L519）与 `printk: z1_bootdiag: 42 output lines suppressed`（L382）。而 `/system/etc/init` 有 52 个 rc、`/vendor/etc/init` 有 15 个，共 67 个，日志里只出现 12 行。**"Parsing file 日志少"是 ratelimit 假象，不是"文件没被解析"**。这一点也适用于 `Doc B`（它把 `/vendor/etc/init/hw/` 完全没提）。
  - **真结论**：**不需要建 `/vendor/etc/init/hw/`，也不需要把 15 个 rc 搬到别处**。第 9 条从"关键阻塞"降级为**已澄清、无需动作**。（唯一残留：`active` 没在 `/vendor/etc/init/*.rc` 定义 service、但它在 `/vendor/bin/hw/android.hardware.audio@2.0-service` 二进制内部自己调 `registerAsService()` —— 同理 cas/configstore/composer/allocator 也注册成功，都是同一机制。）
- **R10 推翻 —— "音频 HIDL service 起不来"是错的**：`audiohalservice: Registration complete for android.hardware.audio@2.0::IDevicesFactory/default.`（L666）**注册成功**，`audio.effect@2.0::IEffectsFactory/default` 也成功（L670）。真正失败在**下一层**：`loadAudioInterface couldn't open audio hw device` → `AudioFlinger: loadHwModule() error -22`。即**HIDL 壳没问题，是 primary HAL 空壳**。这修正了"事是 HIDL service 缺失"的表述。
- **R11 推翻 —— 文档说 `sensors@1.0-service.rc` 的 exec 路径带 `.jf` 导致"即使补了驱动也起不来"**：路径 bug **确实存在**（`/vendor/etc/init/android.hardware.sensors@1.0-service.rc` 写的是 `/vendor/bin/hw/android.hardware.sensors@1.0-service.jf`，而实际文件是 `...service` 无 `.jf`）—— **但真正先炸的原因不是它，是 manifest 里 `sensors` 那条的 fqname 对不上**：交付 manifest 有 `android.hardware.sensors`（1 命中）但**没有 `@1.0::ISensors/default` 的 fqname 供 hwservicemanager 派发**（对照 `hwservicemanager: Cannot find entry android.hardware.sensors@1.0::ISensors/default in either framework or device manifest.`）。两个 bug 要一起修，`.jf` 只是第二层。
- **R12 推翻 —— Doc A「镜像 `/app` 30 项 + `/priv-app` 43 项」计数**：实测 `/app` 28 项、`/priv-app` **40 项**（`debugfs` 逐项列全）。结论（无播放器）不变。
- **R13 推翻 —— Doc A「`/etc` 44 项 / `/vendor/etc` 18 项，均无 media_codecs*.xml」**：`media_codecs*.xml` 无 → **已被 r5c 修复**（6 件在 `/vendor/etc`，见 R8）。这条已过期。
- **R14 推翻 —— Doc B「`ro.lmk.swap_free_low_percent` 无效键」需更正表述**：键确实在（build.prop:104），A9 lmkd 确实不认（`strings /system/bin/lmkd` 只有 low/medium/critical/debug/kill_heaviest_task/kill_timeout_ms/use_minfree_levels），**但删它没有任何功能收益**（不被读的键 = 惰性）——它是 `apply_build_prop.sh` 的清理项，属"整洁"不属"修功能"。同理 `debug.sf.nobootanimation` 是**有收益的**（板载 bootanimation pid 504 吃 23–24% CPU）。

### 🆕 文档完全没有的新发现

- **N1（重要）音频路径的"下一道墙"是 `/vendor/lib` 的 loc 系列缺失 + `gps.default.so` 自引用**。交付镜像 `/vendor/lib/hw/gps.default.so`（65,453 B，r5d 的 patched 版）`readelf -dW` 显示 NEEDED 含 **`gps.default.so`（自己）**，且 `/vendor/lib` 下 `libgps.utils.so`/`libloc_core.so`/`libloc_api_v02.so`/`libgeofence.so`/`libizat_core.so`/`libsam.so`/`libloc_eng.so`/`libloc_ds_api.so` **全部 MISSING**（jfltexx 原装齐备）。`mnld` 的 `libnvram.so` 只在 `/vendor/lib`（`/lib` 没有），能否被 sphal namespace 找到需上板验。**B5 要按这个清单做，不是"补 mnld 就行"**。
- **N2** `CONFIG_TIMED_OUTPUT` 在 mainline `.config` **完全不存在** → vibrator 无论搬哪个 HAL 都不会动（`vibrator.default.so` 两条路径都依赖它或 LED trigger）。**B3 必须改内核**，文档把 vibrator 当"1–3 次写入"低估了。
- **N3** `mt6323.dtsi:38-42` 的 `mt6323_leds` 节点 **`status = "disabled"`**，而 mainline `.config` 里 `CONFIG_LEDS_MT6323=y` 是开着的。**这是"驱动开了、节点关了"**，与"驱动没有"是两回事，反而是好消息（可能只需改一行 DT + 补驱动）。
- **N4** `lights.default.so` 的目标是 `/sys/class/leds/<name>/brightness` 系列**通用名**（lcd-backlight/button-backlight/keyboard-backlight/jogball-backlight/red/green/blue），**不含 Z1 的 GPIO141/144/136**。所以 A3 即使把 service 装上也**不会亮** —— 要么给 dts 加 `leds-gpio` 节点把 RGB 映射成 `/sys/class/leds/red|green|blue`，要么自写 HAL。**A3 的收益因此为零（只是消掉三项 log 报错）**，要真亮灯需并入 B3 的内核工作。这条修正了表 A 的 A3 收益预期。
- **N5** r5d 系统镜像里 `/usr/keylayout/` 同时存在 `mt6779-keypad.kl`（连字符，新增）**与 `mt6779_keypad.kl`（下划线，旧）**、以及 `z1_cst836u.kl` / `z1_cst836u.vk`（均无标签）。`FMRadio.apk` **不在** LOS16 base，原厂有（`1/system.img:/app/FMRadio.apk`）—— 若要 FM 应用，素材来源是原厂件或 LOS14，**不是** LOS16。
- **N6** `/vendor/etc/init` 里 **rdb 全量 15 个 rc 已列全**，其中 `android.hardware.audio@2.0-service.rc` 的 service 名是 `vendor.audio-hal-2-0`、`android.hardware.wifi@1.0-service.rc` 是 `vendor.wifi_hal_legacy`（文档提到的重名冲突风险：`wpa_supplicant.rc` 和 `hostapd.android.rc` 都定义了 `on post-fs-data` 段 `mkdir /data/vendor/wifi`，**这是幂等的，不是冲突**）。

---

## 量化：SELinux（问题 8）

**实测口径**：对 `/vendor/bin/hw`、`/vendor/etc/init`、`/vendor/etc/permissions`、`/vendor/etc/vintf`、`/vendor/etc/wifi`、`/vendor/lib/hw`、`/lib/hw`、`/etc/init`、`/xbin`、`/lib/modules`、`/lib/modules/z1-radio`、`/usr/keylayout`、`/bin` 共 593 个文件，用 `debugfs ls -l` 的 mtime 分出"10-26 之后被动过的"59 个，再逐个 `ea_list` 查 `security.selinux`：

- **59 个新增/改动文件里，30 个无 `security.selinux` xattr（约 51%）**。
- **对照组：pre-existing（10-04~10-06 base 文件）抽样 7 个全部有标签**（`android.hardware.audio@2.0-service`、`bluetooth@1.0-service`、`audio@2.0-impl.so`、`audioserver`、`libstagefright_soft_mp3dec.so`、`audio_effects.conf`、`vndservicemanager` 全 `selinux=1`）。
- **所以文档"新增文件普遍无标签"是准确的**，且**不是"镜像级既存"**（`Z1_R5B_STATUS.md` 那句"与镜像内既有文件一致"**不准确** —— base 文件是带标签的）。
- **无标签的 30 个里，与 HAL 直接相关的 14 个**：
  ```
  /vendor/bin/hw/android.hardware.gnss@1.0-service
  /vendor/bin/hw/android.hardware.sensors@1.0-service
  /vendor/etc/permissions/android.hardware.location.gps.xml
  /vendor/etc/permissions/android.hardware.sensor.proximity.xml
  /vendor/etc/permissions/android.hardware.sensor.light.xml
  /vendor/etc/vintf/manifest.xml            ← 最要命：整个 HAL 声明文件无标签
  /vendor/etc/wifi/wpa_supplicant.conf
  /vendor/etc/wifi/wpa_supplicant_overlay.conf
  /vendor/lib/hw/android.hardware.graphics.mapper@2.0-impl-2.1.so
  /vendor/lib/hw/lights.default.so
  /vendor/lib/hw/gps.default.so
  /vendor/lib/hw/android.hardware.gnss@1.0-impl.so
  /vendor/lib/hw/android.hardware.sensors@1.0-impl.so
  /vendor/lib/hw/sensors.mt6572.so
  /lib/hw/radio.fm.mt6572.so
  /xbin/libmnlp_mt6572
  /lib/modules/snd*.ko + soundcore.ko (8 个) + load_audio.sh
  /lib/modules/z1-radio/mtk_stp_gps_soc.ko
  /usr/keylayout/{mt6779-keypad.kl, mt6779_keypad.kl, z1_cst836u.kl, z1_cst836u.vk}
  ```
- **做对的那批确认无误（wifi HAL 15 件）**：`android.hardware.wifi@1.0-service` = `hal_wifi_default_exec`、`libwifi-hal.so` = `vendor_file`、3 个 rc = `vendor_init_rc`、`/lib/android.hardware.wifi@1.0.so` = `system_file`。
- **转 enforcing 的工作量**：① 30 个文件 `ea_set security.selinux`（**注意：`debugfs` 默认写不出 xattr，必须走 `debugfs -w` + `ea_set`，或改脚本在 `mkuserimg` 前预置** —— 这是 A1–A4 落盘时就要顺手做的，事后补要重拷 943 MB 一次）；② 167 条 avc denied 逐条裁决补规则（按域分布：`init` 36 / `hal_graphics_allocator_default` 22 / `toolbox` 16 / `storaged` 15 / `logpersist` 15 / `kernel` 14 / `system_server` 11 / `netd` 9 / `hal_health_default` 8 / `sdcardd` 6 / `wificond` 3 / `untrusted_app` 3）；③ 上板 `setenforce 1` 灰度。**量级：1–2 天，且必须在做完表 A 之后**（否则新文件又无标签）。

---

## 建议执行顺序（按「用户感知 ÷ 投入」）

**第 0 步（前置，非功能）**：`df -h /` —— 现在 **98G/109G，仅剩 5.8 GB**。B1 的镜像产出 + 备份要 ~2 GB，A1–A4 合批也要 ~1 GB 拷贝。**先清理再动手**（`out/` 里有大量历史 boot.img/ramdisk 可归档）。

1. **A1–A4 合批一次写入（零编译，收益最大）**：Eleven + Recorder + PicoTts(+14 语音库) + light service + light `.rc` + manifest 补 light fqname。同一 `debugfs -w` 会话，**顺手把 30 个文件的 `security.selinux` 一起补**（不补的话下次还得重拷 943 MB）。预期用户感知：**有播放器可点、能朗读、ILight 报错消失**。
   - ⚠️ 收益诚实说明：A3 的灯**不会真亮**（N4：`lights.default.so` 找的是 `/sys/class/leds/*` 而 Z1 无 leds 节点）。A3 只是消 log 报错 + 为后续铺路。
   - 开关灯要真做 → 并入第 4 步。
2. **B1 音频 HAL 落地（零编译，PoC 已有）**：`z1a9_gpt/scripts/r5d_audio_hal/apply_audio_hal.sh` 一条命令产 `system.img.r5d_audio_final`。上板先用 `/bin/tinyplay` 验内核路径（`tinyplay /data/test48k.wav`）——**若 tinyplay 不出声，问题在 HAL 之下，别在 HAL 上耗**。这是"能放音频"的最后一厘米。
3. **B7 + B4 + A5 小批量**：`media_profiles_V1_0.xml`（消 `MediaProfiles` 报错）+ power service（LOS16 件，NEEDED 干净）+ `ueventd.z1.rc` 补 3 节点。各 1–3 次写入。
4. **B3 内核：LED/振动（需重编）** —— 顺手把 **B6 的 cpufreq/CPU_THERMAL 一起裁决**（同一次重编，同时把 `z1_defconfig` 与交付 `.config` 永久对齐，消灭"重编与交付行为不同"这个复现性 bug）。产出新 zImage+dtb+新 boot.img。
5. **A6 perf_stage 分批**：先只上 `debug.sf.nobootanimation=1`（纯收益，板载实证 bootanimation 吃 23–24% CPU）+ 删 `ro.lmk.swap_free_low_percent`（整洁）。`dalvik.vm.dex2oat-threads=2` / `pm.dexopt.disable_bg_dexopt` / `z1_disable_services.py` 的 8 个停服务 **单独一轮评估**（有副作用，别和功能改动混）。
6. **B2 sensors（需先补 DTS + 自写 HAL）** → **B5 GNSS 补库** → **B8 audio_effects.xml**（收益最低）。
7. **缓做**：C1 真 DRM、C2 SELinux enforcing、C3 FM、C4/C5 摄像头与磁力计（判不存在，不做）。

---

## 附：devmem/其它澄清

- **`/vendor/etc/init/hw/` 不用建**（R9）。这一条原被列为"关键问题"，结论是**无影响**。
- **HIDL 服务到底会不会被 init 拉起**：**会，且已在板载日志实证**（bluetooth/allocator/composer 的 `Registration complete` + `init.svc.*=running`）。**不需要任何动作**。
- **不要碰镜像**：本报告未对 `out/z1_r5c_boot/boot.img`、`out/z1_r5_system/system.img.r5d_final` 或任何其它产物做任何写入；未刷写；未挂载写。所有读盘走 `debugfs` 只读，临时导出件在 `/tmp`（`/tmp/r5c_rd2`、`/tmp/z1r5d_mnt` 为只读挂载/导出）。

**未做任何修改。**
