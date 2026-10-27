# Z1 复读/跟读按键 GPIO 猎人实验 — z1_key_gpio_hunt

> 纯主机侧准备好的实验模块。目的：定位"复读""跟读"两个不上报按键的实际 GPIO。
> 模块只读、不抢脚、不驱动、不改任何 pinctrl 配置，对运行中的系统无副作用。

## 背景

- Z1 键盘矩阵经 mainline `mt6779-keypad` 已工作 12 键：VOLUME_UP/DOWN、F 键 250/251/252/254、
  方向键 103/105/106/108、确定 232、BACK 158。dts `&keyboard` 8 行 × 9 列，
  pinctrl 只 mux 了 KCOL0/1/2（107/108/109）+ KROW0/1/2（110/111/112）+ KROW3/4（142/143）。
- "复读""跟读"不上报——怀疑不在 KPD 矩阵上，而是**独立 GPIO**（低有效、内部/外部上拉）
  或独立 EINT。
- fork_linux 的 mt6572 pinctrl 表（`pinctrl-mtk-mt6572.h`）共 **154 脚（0..153）**；
  带专用 EINT 的仅 2 脚：**GPIO7**（PMIC_EINT，EINT16）、**GPIO129**（EINT1，
  复用 PWM2/URTS1/USB_IDDIG）。
- 原厂内核（3.4.67，从 `1/boot.img` 解出的 10.2MB Image）**没有 gpio-keys 驱动**，
  kpd.c 只检查 KCOL0/1/2（3 列），KPD 矩阵字符串只有 `GPIO_KPD_KCOL0/1/2_PIN`。
  复读/跟读若在原厂也是走矩阵，则这两个键的 scancode 落在 dts keymap 没映射的
  位置；若是独立 GPIO，原厂固件里也没有 gpio-keys 处理它们的证据（AccDet 是
  耳机按键 ADC 检测，不是物理键）。

## 编译状态

- 模块: `/home/lxj/claude/6572/z1a9_gpt/drivers/z1_key_gpio_hunt/z1_key_gpio_hunt.ko`
- **vermagic: `7.0.0-rc7-z1+ SMP mod_unload ARMv7 p2v8`** — 与板上 kernel 匹配
- 编译命令:
  ```
  cd /home/lxj/claude/6572/fork_linux
  make ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- \
       M=/home/lxj/claude/6572/z1a9_gpt/drivers/z1_key_gpio_hunt \
       KBUILD_MODPOST_WARN=1 modules
  ```
- modpost 3 条 `__aeabi_uldivmod/__aeabi_l2d/__aeabi_ddiv` undefined warning 是外编
  没喂 vmlinux/Module.symvers 的正常噪声（%.3f 浮点+64 位除法辅助符号），insmod 时由
  内核符号表解析，不是 .ko 生成障碍。
- 产物归档: `bridge_backups/products/products_keyhunt_20261027_131454/`

## 实验步骤

### 1. 传模块上板

Alpine rootfs 场景（adbd 或串口 shell）：
```
# 从主机侧（假设 /sdcard 或 U 盘挂载；或 adb push）
adb push z1_key_gpio_hunt.ko /tmp/
```
板上加载：
```
insmod /tmp/z1_key_gpio_hunt.ko
```
可选参数：
```
insmod /tmp/z1_key_gpio_hunt.ko start=0 count=154 poll_ms=10 summary_sec=30
```
| 参数 | 默认 | 说明 |
|---|---|---|
| `start` | 0 | 起始硬件脚号 |
| `count` | 154 | 扫描脚数（全脚） |
| `poll_ms` | 10 | 轮询间隔（去抖 ≈ 2×poll_ms ≈ 20ms） |
| `summary_sec` | 30 | 周期汇总间隔秒（0=关） |
| `dump_direction` | 1 | 加载时 dump 各脚方向 |

### 2. 加载后先看初始 dump

```
dmesg | grep gpiotest
```
预期输出（示例）：
```
[gpiotest] 锁定 MT6572 gpiochip：base=XXX ngpio=154 label=pinctrl-paris
[gpiotest] Z1 按键 GPIO 猎人启动：只读扫描 GPIO0..153（有效 154/154），poll=10ms 去抖≈20ms，summary=30s，全程不 request 不驱动不改配置
[gpiotest] 初始电平 dump（0..153，1=高 0=低，'?'=不可读）：
[gpiotest]    0- 31: 1111111111110000000000000000...
[gpiotest] 低电平脚: 7 12 44 45 ...
[gpiotest] 输入脚: 107 108 109 129 ...
[gpiotest] 输出脚: 110 111 112 142 143 ...
```
- **低电平脚**：天然低（接 GND/未上拉）。若复读/跟读脚属于"平时高、按下低"
  的低有效设计，它**不在**低电平列表里。
- **输出脚**：KROW 矩阵扫描线（keypad 驱动在驱动它们），属正常噪声源。

### 3. 按键实验

```
dmesg --follow | grep gpiotest    # 或 watch 'dmesg | tail -40'
```
**逐个按住"复读"键 1–2 秒再松开，然后"跟读"键同样**，间隔 3 秒以上。

预期输出：
```
[gpiotest] GPIO74 下降沿 1->0（t=+12.345s）← 按键候选（低有效）
[gpiotest] GPIO74 上升沿 0->1（t=+13.890s，按住 1545ms）
```

### 4. 从输出定位按键 GPIO

- **只看独立变化的脚**：矩阵 KCOL/KROW（107/108/109/110/111/112/142/143）会随
  矩阵扫描抖动，复读/跟读若不在矩阵上，其 GPIO 会**单独**出边沿。
- **判定规则**：
  1. 按复读键 → 唯一出现 `下降沿 1->0` 的脚 = 复读 GPIO（低有效设计）；
     若出现 `上升沿 0->1` 则该脚是高有效设计（平时被拉低）。
  2. 按跟读键同理。两个键若同时在**同一脚**出边沿 → 两键并联同一线（需
     万用表确认）。
  3. 若**没有任何脚**变化 → 按键可能接的是 EINT 专用脚（候选 GPIO7/GPIO129，
     已被 pinctrl 用作 PMIC_EINT/EINT1 的话本模块照读不误，不会被闸门挡），
     或按键线接在 PMIC/KPD 控制器内部不经 GPIO 层。
  4. 若某脚**只出一次边沿**（按住不弹回）→ 可能是自锁键或电平保持设计。
- **去抖**：poll_ms=10 时去抖约 20ms，机械按键抖动 5–20ms，若出现一条
  边沿紧跟几条毛刺，加大 `poll_ms=20` 重试（rmmod 后重新 insmod）。

### 5. 复测确认

定位到候选脚（如 GPIO74）后：
```
rmmod z1_key_gpio_hunt
insmod /tmp/z1_key_gpio_hunt.ko start=74 count=1 poll_ms=5
```
只扫这一个脚，重复按键 10 次，确认 10 次下降/上升沿全部成对出现且无毛刺。

### 6. 固化：gpio-keys dts 节点

确认 GPIO 后，在 `mt6572-z1.dts` 加（例：复读=GPIO74 低有效、跟读=GPIO75 低有效，
**假设值，以实验结果为准**）：

```dts
/ {
	gpio-keys {
		compatible = "gpio-keys";
		pinctrl-names = "default";

		key-fudu {
			label = "repeat";
			/* 实验定位的脚号；若脚已被 pinmux 占用需先去掉该 mux */
			gpios = <&pio 74 GPIO_ACTIVE_LOW>;
			linux,code = <245>;	/* KEY_RECORD：复读/录音语义 */
			debounce-interval = <20>;
			wakeup-source;
		};

		key-gendu {
			label = "follow";
			gpios = <&pio 75 GPIO_ACTIVE_LOW>;
			linux,code = <166>;	/* 例：KEY_STOP / 或自定义 */
			debounce-interval = <20>;
			wakeup-source;
		};
	};
};
```

**注意（mainline 本树逐条核实过的事实）**：
1. `gpio-keys`（IRQ 版）需要 `gpiod_to_irq()` 成功。fork_linux 的
   `mtk_gpio_to_irq`（pinctrl-paris.c）对 `eint_n == EINT_NA` 的脚返回
   `-ENOTSUPP` —— **154 脚里只有 GPIO7（EINT16）和 GPIO129（EINT1）有 IRQ 路径**。
   所以：
   - **若实验定位的脚不是 7/129**：`gpio-keys`（IRQ 版）probe 会失败，必须用
     **`gpio-keys-polled`**（轮询版），且 `.config` 需开
     `CONFIG_KEYBOARD_GPIO_POLLED=y`（当前 =n，要重编内核）。
   - **若是 GPIO7/GPIO129**：可直接用 `gpio-keys`（IRQ 版），零内核重编。
2. `&pio` 标签在 `mt6572.dtsi:321`（`pio: pinctrl@1000b000`）。
3. `gpio-keys-polled` 节点语法同 `gpio-keys`，外加 `poll-interval = <20>;`
   （ms），并把 `wakeup-source` 去掉（轮询版不支持唤醒）。
4. 若该脚 pinctrl 被占用（dts 里 mux 过别的功能），gpio-keys 请求会
   -EBUSY——去掉冲突 mux 或改用空闲脚。
5. KEY code 选择建议：复读/跟读是学习机语义，可用 `KEY_RECORD`(167)/
   `KEY_PLAYPAUSE`(164)/`KEY_F13`(183)/`KEY_F14`(184) 等，上 Androi d
   keylayout `.kl` 再映射成应用事件。

### 7. 若 GPIO 扫描全阴性（无任何脚变化）

备选排查方向：
1. **KPD 矩阵漏映射**：复读/跟读可能挂在矩阵未 mux 的 KCOL3-7/KROW5-7 上
   （68-72/74-78/87-90/97-100/144/145 都有 KCOL/KROW 复用功能）。把 dts
   `kpd_pins` 补上这些脚 + `linux,keymap` 补 scancode，重刷验证。原厂 kpd.c
   只查 KCOL0/1/2 说明**原厂也是 3 列小矩阵**，复读/跟读在原厂若走矩阵，
   只可能是 KCOL0/1/2 × 某行 —— 已被 12 键占满的话就是独立 GPIO。
2. **PMIC(mt6323) 内部按键寄存器**：mt6323 有 HOME/POWER 两个内部键，原厂
   若把复读/跟读接到 PMIC 的辅助 ADC/EINT，需查 mt6323 寄存器手册。
3. **接在基带/PMU 侧不经 AP**：这种情况下软件不可达，只能改接线。

## 附录 A — 原厂 boot.img strings/grep 线索（GPIO 号搜索结果）

原始 `1/boot.img`（6291456B, ANDROID! magic）解析：
- kernel 段：MTK blob 头 `0x58881688`+`"KERNEL"`，内含**未压缩 ARM Image**
  （mov r0,r0 nop sled @kernel-rel 512 + `b 0x2a4`），zImage magic @548 →
  实为 **xz 压缩 zImage**，piggy xz 流 @kernel-rel 15668，解出 **10191379B**
  Image（原厂 3.4.67 内核）。原始 Image 头部无 `Linux version` 字符串——
  整个 payload 是 xz 压缩的 zImage，解压后才有完整字符串区。
- ramdisk 段：MTK blob `0x58881688`+`"ROOTFS"`，512B 头后 gzip-cpio，
  解出 1632256B。

解出的原厂内核 Image strings 扫描结果：

| 搜索串 | 命中 | 结论 |
|---|---|---|
| `kpd`（小写） | 71 | 全是 `kpd_show_register`/`kpd_show_hw_keycode`（debugfs）/`mtk-kpd`（platform 名） |
| `KCOL` | 3 | **只有 `GPIO_KPD_KCOL0_PIN/KCOL1/KCOL2`** — 原厂 kpd 自测只查 3 列 |
| `KROW` | **0** | 原厂内核字符串里无 KROW 线索（行线在 kpd.c 内部寄存器扫，不走 GPIO 检查） |
| `repeat` | 4 | 全是无关（pwrap_wacs2_hal_repeat/zlib/repeated CPUs），**无复读键线索** |
| `follow` | 11 | 全是无关（nf_nat_ftp/follow_pfn/STP protocol），**无跟读键线索** |
| `gpio-keys` | **0** | **原厂内核没有 gpio-keys 驱动** — 复读/跟读若走 GPIO，原厂必有别的驱动处理 |
| `button/Button` | 4 | `/proc/acpi/button`（无关）、`Trigger_Button`（某 sensor 库）、`[Accdet]accdet_ioctl Button_Status`（耳机按键） |
| `Accdet/ACCDET` | 178 | 耳机按键 ADC 检测驱动（KEY_NEXTSONG/PREVIOUSSONG/VOLUMEDOWN/VOLUMEUP 阶梯）— 是 4 线耳机按键，**不是物理复读/跟读键** |
| `KEY_` | 8 | WiFi 驱动加密枚举 + Accdet 音量键 — 无 KEY_RECORD/复读键 |
| `keypad` | 3 | `tpd_mode_keypad_tolerance`（触摸）、`/kernel/mediatek/kernel/drivers/keypad/kpd.c`（源路径）、`usb_chg`（无关） |
| 中文（GBK 编码 `复读`/`跟读`/`按键`） | **0** | 内核镜像无中文按键标签（中文在 Android framework/system.img 层） |
| 中文（UTF-16LE） | **0** | 同上 |

ramdisk（原厂）扫描：**无 .kl keylayout 文件**、无 `keylayout` 目录、
`default.prop` 无按键相关属性。keylayout 在原厂 system.img（未在本轮解包范围）。

**结论**：原厂内核层面找不到复读/跟读键的 GPIO 号。最可能的两条路径：
1. 键在 KPD 矩阵上但原厂 keymap 没有对应 scancode 处理（Android .kl 层没映射）；
2. 键是独立 GPIO，由某个原厂专有驱动（如学习机专用学习控制驱动，在
   system/vendor 层或 kernel 专有目录）处理 —— 解出的 Image 里无此证据。

因此本实验（只读扫描）是当前**唯一直接**能定位的途径。

## 附录 B — MT6572 引脚参考（fork_linux pinctrl 表核实）

- 全部 154 脚（0..153）：`drivers/pinctrl/mediatek/pinctrl-mtk-mt6572.h`
- 专用 EINT 脚仅 2 个：GPIO7（PMIC_EINT → EINT16）、GPIO129（EINT1，
  复用 PWM2/URTS1/USB_IDDIG/MD_CLKM1/LSDA/CONN_GPIO31）
- KPD 矩阵复用：KCOL0/1/2=107/108/109、KROW0/1/2=110/111/112、
  KCOL3-7=68/69/70/71/72/74/75/76/77/78/87/88/90、
  KROW3-7=31/89/97/98/99/100/142/143/144/145
- 串口2：URXD2/UTXD2=101/102；串口1：URXD1/UTXD1=103/104
- eMMC：44-54；DSI 差分：79-84；音频：4/5/6（AUD_*）
- USB：129（IDDIG）、139（DRVVBUS）
- JTAG（JTMS/JTCK/JTDI/JTDO）无专用脚，仅复用（87/88/89/90/91/93/95/96/101/102/103/104/109/112）

## 附录 C — 模块参数速查

```
# 全扫（默认）
insmod z1_key_gpio_hunt.ko
# 只扫候选段
insmod z1_key_gpio_hunt.ko start=70 count=40
# 高频扫（更快响应，CPU 占用高一点）
insmod z1_key_gpio_hunt.ko poll_ms=5
# 关闭周期汇总（减少 kmsg 噪声）
insmod z1_key_gpio_hunt.ko summary_sec=0
```

## 安全设计说明

- 全程 `gpiod_get_raw_value_cansleep()` 只读 DI 寄存器——不 `gpio_request`、
  不设方向、不写电平，对 eMMC/DSI/pwrap/串口等任何活动脚都无副作用，
  因此**不做禁区**，154 脚全扫。
- `desc` 由 `gpio_to_desc()` 取自全局号空间，模块不额外持引用也不 put
  （R3d 触摸猎人同结论）。
- MTK pinctrl 内建（`=y`）永不卸载，`hunt_gc` 指针 module 生命周期内有效。
- 与既有 `drivers/z1_gpio_hunt/`（触摸 IC 电源/复位脚猎人，主动驱动型）
  互不影响——那是另一个实验的工具，本模块是它的只读镜像版。
