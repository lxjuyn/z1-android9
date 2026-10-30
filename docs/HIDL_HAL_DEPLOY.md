# HIDL HAL 部署评估 — light / vibrator / power

Z1 (MT6572) Android 9 · 日期 2026-10-30 · 基线 `out/z1_r5_system/system.img.r5i_final`
(sha256 `35616bfd23320b32d195083442670c019309e09ff6d4cc9d7eae0faba44fd1af`)

**本轮只产出脚本 + 素材 + 报告,未产出 system.img**(r5j 由另一 agent 在做,避免撞车)。产物见 §7「待合批」。

---

## 1. 一句话结论

**三个 service 缺失的直接原因就是 `/vendor/etc/vintf/manifest.xml` 里没有对应 fqname —— 这一条被本轮完全证实,并且是失败链的充要原因。**
补 manifest + 搬二进制就能消掉全部三条 log 报错,其中 **light 还能真正控制 LCD 背光**(Z1 确实有 `/sys/class/leds/lcd-backlight` 节点);
**vibrator 是否真能振取决于内核有没有 `/sys/class/timed_output/vibrator/enable`(本轮未确定)**;
**power 能起来但功能大概率是空转**(二进制是高通 `cpufreq/interactive` 实现)。

**必须纠正 brief 里的一条关键前提**:本轮从镜像自己的字节码与反汇编证明,**A9 的 LightsService / VibratorService 是纯 HIDL,没有 legacy `.default.so` 回退路径**。
因此 `lights.default.so` / `vibrator.default.so` / `power.default.so` 在本镜像里是**不可达的死文件**,"退回 legacy" 不是可选项 —— **HIDL 是唯一通路**。

---

## 2. 复核 brief 的 4 条 + 新增纠正

| # | brief 的说法 | 本轮判定 | 证据 |
|---|---|---|---|
| 1 | HIDL 链没坏,缺 service 是因为 manifest 缺 fqname | **CONFIRMED(充要)** | 见 §3;`hwservicemanager` 用 `VintfObject::GetDeviceHalManifest` 判 `getTransport`,查不到就回 `Cannot find entry … in either framework or device manifest`,init 随后报 `Could not find service hosting interface` |
| 2 | `/vendor/etc/init/hw/` 不存在是误报 | **CONFIRMED(不再查)** | `/vendor/etc/init/*.rc` 里的 `service` 均正常启动(gnss/sensors/audio 等的 rc 与 light 的 rc 同目录同格式) |
| 3a | light 素材在,10 个 NEEDED 全解析得到 | **CONFIRMED,升格为 11 个** | 见 §4.1;含传递依赖 `libhidlbase→libhidltransport/libutils/libcutils/libbase/libvndksupport/libhardware/libhwbinder/libc++` 全部命中 |
| 3b | vibrator / power service 二进制素材树里不存在 | **CONFIRMED(素材树)但结论要改**:两者都**存在于 LOS16 jfltexx OTA**,已验证可提取,且**自包含、无板级依赖** | 见 §4.2 |
| 4a | `CONFIG_TIMED_OUTPUT` 在 .config 里完全不存在 → 必须改内核 | **CONFIRMED,且比 brief 说的更彻底** | **符号、驱动源码、`drivers/staging/android/` 整个目录在 7.0-rc7 里都被删了**;`CONFIG_ANDROID_TIMED_OUTPUT` 同样不存在 |
| 4b | `mt6323_leds` 节点 `status="disabled"` | **❗REFUTED** | `mt6323.dtsi:42` 确是 disabled,但 **`mt6572-z1.dts:797` 覆盖成 `status="okay"`**;编译后 dtb 里 `status = "okay"`;`.config` `CONFIG_LEDS_MT6323=y`,驱动在 `System.map` 里 |
| 4c | lights 不会真亮,因为 dts 无 LED 节点 | **❗REFUTED(至少 LCD 背光会亮)** | brief 依据 `lights.default.so` 的 sysfs 路径 —— 但**该 .so 不可达**(§2.1)。内核侧唯一 LED 节点是 `lcd-backlight`,Z1 有它 |
| 4d | Z1 RGB 用 GPIO141/144/136 | **UNKNOWN — 无任何工件支持** | 这三个数字只出现在项目散文文档里,**stock DT、stock kernel、lk.bin、模块里都找不到**。见 §6 |

### 2.1 本轮最重要的新证据:legacy 回退路径不存在

从镜像自身反汇编 + 字节码证实(A9 `services.jar` + `libandroid_servers.so`):

- `LightsService` 只声明一个 native `setLight_native(IIIIIII)V`,整条 Java 链路终点就是它。
- JNI 侧 `android::setLight_native` → `LightHal::associate()` → `ILight::getService("default")` →
  ```
  0x3edc8:  cmp r0, #0
  0x3edd4:  bne.n  0x3ede4          ← 成功:跳过日志
  0x3edd6:  __android_log_print(..., "Unable to get ILight interface.")
  0x3ede4:  store r0 -> LightHal::sLight
  ```
  **失败分支里没有任何第二条路径** —— 没有 `hw_get_module`,没有 fallback。
- `nm -D libandroid_servers.so | grep -c hw_get_module` → **0**;全镜像里也没有任何 `HAL_MODULE_INFO_SYM` 的 legacy id 字面量(`lights`/`vibrator`/`power`)被服务端引用。
- `vibratorInit/vibratorOn/vibratorOff` 这些**老名字还在,函数体已被换成 `halCall<V1_0::IVibrator>`**;`sAvailable=false` 时全部静默短路。
- `POWER: services.jar` 里 `android/hardware/power/` 类型数 = **0**;`PowerManagerService` 用的 `IPower` 是 Binder 的 `android.os.IPowerManager`,不是 HIDL。

**推论**:manifest 补 fqname + 部署 service 是**唯一**能让这三条报错消失的手段;同时也说明**只要 fqname 补上,报错必然消失**(不需要动任何其他东西)。

---

## 3. 失败链逐字证据(板载 log)

`mainline_recon/z1_android9_acm_20261025_105535_309533.log`:

```
20267: init: Received control message 'interface_start' for 'android.hardware.light@2.0::ILight/default'
20268: init: Could not find service hosting interface android.hardware.light@2.0::ILight/default
       hwservicemanager: getTransport: Cannot find entry android.hardware.light@2.0::ILight/default
                          in either framework or device manifest.
       LightsService: Unable to get ILight interface.
```

vibrator(line 20678-20688):
```
init: Received control message 'interface_start' for 'android.hardware.vibrator@1.0::IVibrator/default'
init: Could not find service hosting interface android.hardware.vibrator@1.0::IVibrator/default
hwservicemanager: getTransport: Cannot find entry android.hardware.vibrator@1.0::IVibrator/default …
VibratorService: vibratorOff command failed (1).
```

power(line 20268 / 21175):
```
android.hardware.power@1.0::IPower/default      ← 注意:请求的是 @1.0
vendor.lineage.power@1.0::ILineagePower/default
```

### 3.1 重要:现行镜像(r5k)已看不到 ILight 报错,但**不能**当作已修复

`mainline_recon/z1_r5k_full_20261030_113514.log`(今天 11:35)里:
`Unable to get ILight` / `vibratorOff command failed` / `LightsService` / `VibratorService` / `system_server` **全部 0 命中**。
该日志是一段 **init 视角的 tail**(有 hwservicemanager、无 z1logcat、gcov 时间戳到 1811s),**不含 system_server 日志**,所以这是**覆盖盲区,不是修复证据**。
**必须在真启动、且确实扫到 LightsService 的日志上验证。** 见 §8。

### 3.2 manifest 现状(实测)

`/vendor/etc/vintf/manifest.xml`(5,825 B)共 17 条 fqname,`light` / `vibrator` / `power` / `vendor.lineage.power` **各 0 条** —— brief 说的对。
另一份 `/etc/vintf/manifest.xml`(3,836 B,`type="framework"`)与本题无关。

---

## 4. 素材核实(全部 sha256 实测)

### 4.1 light — 有,且在镜像里自洽

| 项 | 值 |
|---|---|
| 路径 | `out/z1_hal_a9/vendor/bin/hw/android.hardware.light@2.0-service.samsung` |
| size / sha256 | 41,836 B / `822f45a77e5ff87a409bbaaaf055f89fba274fad00302c36ae593b96bb770b7d` |
| NEEDED(11) | libbase, libbinder, libhidlbase, libhidltransport, libutils, **android.hardware.light@2.0.so**, libc++, libc, libm, libdl(**全在镜像**;接口库实测 75,940 B 已在 `/lib/`) |
| `.rc` | `out/z1_hal_a9/vendor/etc/init/android.hardware.light@2.0-service.samsung.rc`,258 B |
| 硬编码设备路径(全部) | `/sys/class/leds/lcd-backlight/brightness`、`/sys/class/leds/lcd-backlight/max_brightness`、`/sys/class/leds/button-backlight/brightness`、**`/sys/class/sec/led/led_blink`(三星专有,MTK 无)** |
| 自包含? | **是**(无 `hw_get_module`/`dlopen`,impl 静态链入) |

### 4.2 vibrator / power — 素材树里没有,但 LOS16 jfltexx OTA 里有,且可搬

来源 `downloads_gsi/jflt_ota_v2/system.img`(1,181,114,368 B,`lineage_jfltexx 9 PQ3A.190801.002`),已提取到 `out/z1_hidl_stage/material/`:

| 项 | vibrator | power |
|---|---|---|
| 路径 | `/vendor/bin/hw/android.hardware.vibrator@1.0-service.jf` | `/vendor/bin/hw/android.hardware.power@1.1-service.jf` |
| size / sha256 | 28,876 B / `3855fe7bf99fd1511691e686533c0db0efb46c2dd71c16cd99bd4dc06e0510c7` | 29,084 B / `8a48bddc7b2f116bbf0dffc98b68ae3a00abed71c74b264850d65c0e3d543f14` |
| NEEDED | libhidlbase, libhidltransport, liblog, libhwbinder, libutils, libhardware, **android.hardware.vibrator@1.0.so**, libc++, libc, libm, libdl | libhidlbase, libhidltransport, liblog, libhwbinder, libutils, libhardware, **android.hardware.power@1.1.so**, **vendor.lineage.power@1.0.so**, libc++, libc, libm, libdl |
| NEEDED 解析 | **全部命中 `/lib/`**(实测,含 `vendor.lineage.power@1.0.so`) | 同左 |
| 硬编码设备路径(全部) | **只有一个**:`/sys/class/timed_output/vibrator/enable` | 15 条,全是高通的:`/sys/devices/system/cpu/cpufreq/interactive/*`(11)+ `/sys/kernel/cpufreq_limit/limited_{min,max}_freq` |
| 自包含? | **是**。`getService` 引用数 = 0,只有 `registerAsService` → impl 静态链入,**不加载 `vibrator.default.so`** | **是**(同上)。**不是三星件,是高通 CAF `power_hint`/`set_power_profile`**;二进制里实测导出 `vendor.lineage.power::V1_0::ILineagePower::registerAsService` |
| `.rc` | 131 B,`class hal` / `user system` / `group system`(无 `interface` 行) | 129 B,同结构 |

> **fqname 版本坑**:板上请求的是 `power@1.0::IPower/default`,而二进制与 jfltexx manifest 都是 **`@1.1`**。
> 必须按 **`@1.1`** 声明(客户端 stub `android.hardware.power@1.1.so` 在镜像里,`.so` 自身再 NEED `@1.0.so`,两者都在)。
> power 二进制还**额外注册 `vendor.lineage.power@1.0::ILineagePower/default`**,而板上也正好请求它 → 一并声明,顺带消掉第二条报错。

---

## 5. 逐项判定 + 最小可行路径 + 投入产出比

### 5.1 light — ✅ 值得做,而且能真正部分生效

- **现在做能不能 work**:能起到 service、能消报错、**并且能真正控制 LCD 背光**。
- **阻塞在哪一层**:仅 **manifest**(+ 镜像里没有 service 二进制)。
- **最小可行路径**:部署 `.rc` + 二进制 + manifest 补 1 条 `@2.0::ILight/default`。**零内核改动、零 dts 改动、零重编。**
- **诚实 ROI**:成本极小(2 文件 + 1 fqname)。收益:
  - 消掉 `Unable to get ILight interface.`(板上三连败之一);
  - `LightsService` 拿到 ILight,能写 `/sys/class/leds/lcd-backlight/brightness` —— **内核侧该节点存在**(§6 证据),所以**亮度控制会真的生效**;
  - ❌ 不生效的:RGB/通知灯(该 .so 只会 4 个路径,其中 `lcd-backlight` 是 Z1 唯一存在的;`button-backlight` 不存在;`/sys/class/sec/led/led_blink` 是三星专有)。
- **结论**:三件里性价比最高的一件,**建议作为下一步单变量验证**。

### 5.2 vibrator — ⚠️ 值得"部署",但"真能振"未确定

- **现在做能不能 work**:service 一定能起(依赖全满足);**能否真振 = 取决于内核有没有 `/sys/class/timed_output/vibrator/enable`,本轮 UNKNOWN**。
- **阻塞在哪一层**:**内核**。这是唯一一个硬阻塞落在内核侧的项目。
- **已确认的坏消息**:`CONFIG_TIMED_OUTPUT` 符号、驱动、整个 `drivers/staging/android/` 在 7.0-rc7 里**都被删除**;mainline 替代品 `CONFIG_INPUT_GPIO_VIBRA` / `CONFIG_INPUT_PWM_VIBRA` **均 `is not set`** 且都不是 MTK 的。该二进制的**唯一**设备路径就是 timed_output。
- **最小可行路径(两步,先做第一步)**:
  1. **零内核成本**:部署二进制 + `.rc` + manifest 补 `@1.0::IVibrator/default`。**这是安全的纯增量**,且能把"内核到底有没有这个节点"变成一个可判定的事实。
  2. **只有第 1 步证明节点不存在时**,才需要改内核。届时**必须先拿到马达 GPIO**(§6),再二选一:
     - 恢复一个 timed_output 风格的平台驱动;或
     - 用 `CONFIG_INPUT_GPIO_VIBRA` + dts 加节点(走 input FF 而非 timed_output,但**这样该 HIDL 二进制就驱动不了它**,得另配实现)。
   - 代价:改内核 → 重编 zImage → 重打 boot.img(即 `tools/z1_make_bootimg.sh` 那一套,并遵守 §5 的 cmdline 纪律)。
- **诚实 ROI**:**低(除非第 1 步证明节点存在)**。部署的意义主要是"把未知变成已知 + 消报错"。

### 5.3 power — ✅ 值得做(低成本),但**功能基本是空转**

- **现在做能不能 work**:service 能起、能注册(`IPower` **和** `ILineagePower`),依赖全满足,**零内核改动**。
- **阻塞在哪一层**:**起服务无阻塞**;但**功能阻塞在硬件代次**:它是高通 `cpufreq/interactive` Power HAL。
- **最小可行路径**:部署二进制 + `.rc` + manifest 补 `@1.1::IPower/default` + `@1.0::ILineagePower/default`。
- **诚实 ROI**:
  - 收益:消掉 `android.hardware.power@1.0::IPower/default` 与 `vendor.lineage.power@1.0::ILineagePower/default` 两条 `Cannot find entry`;`PowerManagerService` 拿到 IPower。
  - 代价:MT6572 由 `mediatek-cpufreq`/`cpufreq-dt` 驱动,`interactive` 那些调优节点(`hispeed_freq`/`go_hispeed_load`/`target_loads`/`boostpulse`…)与 `/sys/kernel/cpufreq_limit/` **一个都不存在** → 每次 power hint 都是失败的 `fopen`,会持续刷 log。
  - **即:消两条报错,换来一类新报错。**建议**先只补 manifest/部署、观察 log 噪声是否可接受**;若要真功能,得重编一个走 MT6572 实际节点的 power HAL(那是新工程,不是搬件)。

---

## 6. 内核 / dts 问题(以及为什么本轮**不给 diff**)

brief 授权"若可行且成本可控,可以给 dts diff"。本轮结论是 **dts 不需要改**,而内核若要改会卡在缺证据上:

1. **light 不需要任何内核/dts 改动** —— 节点已经有了:
   - `fork_linux/arch/arm/boot/dts/mediatek/mt6323.dtsi:38-42` 定义 `mt6323_leds`(`compatible = "mediatek,mt6323-led"`),`status="disabled"`;
   - **`fork_linux/arch/arm/boot/dts/mediatek/mt6572-z1.dts:796-805` 覆盖为 `status="okay"` 并加了 `isink0 { label = "lcd-backlight"; linux,default-trigger = "backlight"; default-state = "on"; }`**;
   - 编译后 dtb 实测 `status = "okay"`;
   - `.config`:`CONFIG_LEDS_CLASS=y` `CONFIG_LEDS_MT6323=y` `CONFIG_LEDS_GPIO=y` `CONFIG_LEDS_TRIGGERS=y` `CONFIG_BACKLIGHT_CLASS_DEVICE=y` `CONFIG_BACKLIGHT_PWM=y`;`leds-mt6323` 在 `System.map`(`mt6323_led_probe`);
   - 预期节点名 **`/sys/class/leds/lcd-backlight`**(driver 只设 `fwnode`、`devicename=NULL`,故取 `label` 且无前缀)。
   - **⚠️ 一个已知小瑕疵(非阻塞)**:`linux,default-trigger = "backlight"` **不生效**,因为 `CONFIG_LEDS_TRIGGER_BACKLIGHT is not set`(`.config:5067`)。亮度写入照常可用。
   - **极强旁证**:反编译的**原厂 stock DT**(`out/z1_audio_stage/evidence/v8_appended_dt.dts:456-467`)是**逐字相同**的 `mediatek,mt6323-led` + `status="okay"` + `isink0/label="lcd-backlight"` —— 厂商自己就是这么配的,我们的 dts 忠实继承。
2. **vibrator 若真要改内核,缺一个关键输入:马达 GPIO 未知。**
   - brief 提到的"RGB 指示灯 GPIO141/144/136"**没有任何工件支持**:只出现在 `HANDOFF_20261028.md:19`、`out/Z1_FEATURE_GAP_FINAL.md:84`、`out/Z1_REMAINING_GAPS_20261028.md:41` 三份**散文文档**里;stock DT、stock kernel、`lk.bin`、模块里都找不到。这三个针脚本身在 MT6572 上存在(`include/dt-bindings/pinctrl/mt6572-pinfunc.h:733 MT6572_PIN_136_…`、`:768 MT6572_PIN_141_…`),但 Z1 的 dts 从未引用。
   - **我拒绝猜一个 GPIO 写进 dts** —— 猜错会去驱一个未知针脚,是硬件风险。
   - **拿到 pin 的最省钱办法(不用拆机)**:原厂内核里 MTK 的 `mt_vibrator` / `cust_leds` 驱动有 `timed_output: Failed to register driver` / `timed_output_dev_register` 等字符串(`out/forensic/stock_kernel.bin` 实测有);可从原厂 ramdisk 的 init.rc(实测有 `chown system system /sys/class/timed_output/vibrator/enable`)与 `cust_leds` 配置回溯。
3. **power 不需要 dts。**

> 因此本轮**不落任何 dts diff**,也不重编任何东西。若后续拿到马达 GPIO,再单独出一版 dts diff + 重编影响评估。

---

## 7. 交付物(待合批)

```
out/z1_hidl_stage/
├── apply_hidl_hal.sh                        # 幂等、非原地、带校验(实测通过)
├── revert_hidl_hal.sh                       # 精确回滚(实测往返字节级一致)
├── material/
│   ├── android.hardware.light@2.0.so        # 75,940 B 备用副本(镜像里已有,非必需)
│   └── vendor/
│       ├── bin/hw/android.hardware.light@2.0-service.samsung          41,836 B
│       ├── bin/hw/android.hardware.vibrator@1.0-service.jf            28,876 B
│       ├── bin/hw/android.hardware.power@1.1-service.jf               29,084 B
│       └── etc/init/{light@2.0-service.samsung,vibrator@1.0-service.jf,power@1.1-service.jf}.rc
```

用法(产出**新镜像**,不动基线,**不刷写**):

```bash
# 建议第一步:只做 light(零内核依赖)
out/z1_hidl_stage/apply_hidl_hal.sh \
  --image out/z1_r5_system/system.img.r5i_final \
  --out   out/z1_r5_system/system.img.r5i_hidl_light

# 三件一起
out/z1_hidl_stage/apply_hidl_hal.sh \
  --image out/z1_r5_system/system.img.r5i_final \
  --out   out/z1_r5_system/system.img.r5i_hidl_all --all

# 回滚
out/z1_hidl_stage/revert_hidl_hal.sh --image <已部署.img> --out <还原.img>
```

### 脚本安全性(已实测,非声明)

| 检查 | 结果 |
|---|---|
| 输出 = 输入 | **拒绝执行**(显式检查) |
| 幂等性 | 二次运行 → `manifest already contains all requested fqnames; left byte-identical`,manifest sha 不变,hal 块仍 20 |
| manifest 合并 | **纯增量**:`diff` 删除行数 = **0**;原 17 条 fqname **17/17 保留**;写入前用 `ET.fromstring` 硬校验,**任何原 fqname 丢失都会 `assert` 中止** |
| manifest 写前校验 | XML well-formed + 无 fqname 回归,双闸门 |
| 文件元数据 | 实测回读:二进制 `0755 0:0`、`.rc` `0644 0:0`、manifest `0644 0:0`;标签 `hal_{light,vibrator,power}_default_exec` / `vendor_init_rc` / `vendor_configs_file`(与镜像内 gnss/sensors/audio 先例一致) |
| sha256 回读 | 与素材逐个比对一致 |
| `e2fsck -fn` | 每次运行后 **clean** |
| 回滚往返 | 还原后的 manifest **与原文件 sha 完全相同**(`0e0ef3c4…`) |
| 磁盘 | **不再自动复制第 2 份 943MB 备份**(输入永不被写,输入本身即回滚件;`--backup` 可显式开)。`df -h /` 现 2.7G 紧 |
| 触碰设备 | **无**。不 mount、不刷写、不碰 USB |

**VINTF 为什么不会把 vendor 搞挂(本轮实测)**:`light` / `power` / `vibrator` 在**镜像里每一份**框架矩阵
(`/etc/vintf/compatibility_matrix.{1,2,3,legacy}.xml`)里都是 **`optional="true"`**;
`vendor.lineage.power` 在矩阵里**根本不存在**(既非 required 也无冲突)。
→ **声明它们不可能触发 "required HAL missing"**。再加"只在前一个 `</manifest>` 前字符串插入、不重写、写前硬校验",风险已压到最低。

---

## 8. 上板验证步骤(给下一轮刷机用)

**前提纪律**:Z1 刷写才插 USB,正常开机拔掉;抓 log 用 C3 桥(`tools/flash_with_retry.sh` / `z1_capture_mainline.sh` 那套)。

1. **服务是否起来**(最重要,先看这个):
   ```
   getprop | grep -iE 'init\.svc\.vendor\.(light|vibrator|power)'
   # 期待:init.svc.vendor.light-hal-2-0=running / vendor.vibrator-1-0=running / vendor.power-hal-1-1=running
   ```
2. **HIDL 注册是否可见**:
   ```
   lshal | grep -iE 'android.hardware.(light|vibrator|power)|vendor.lineage.power'
   # 期待 4 行:ILight/default、IVibrator/default、IPower/default、ILineagePower/default
   ```
3. **报错是否消失**(必须在**真启动、且扫到 system_server** 的 log 上查,别用 init-only 的 tail —— 见 §3.1):
   ```
   logcat -d | grep -E 'Unable to get ILight|vibratorOff command failed|Cannot find entry .*(light|vibrator|power)'
   # 期待:0 命中
   ```
4. **light 是否真能控背光**(这是"真亮"的判据):
   ```
   ls -l /sys/class/leds/            # 期待看到 lcd-backlight
   echo 128 > /sys/class/leds/lcd-backlight/brightness
   # 期待屏幕亮度实际变化(240x320 并口屏)
   ```
5. **vibrator 是否真能振**(本轮最大未知):
   ```
   ls -l /sys/class/timed_output/vibrator/enable   # ← 这一条决定生死
   echo 200 > /sys/class/timed_output/vibrator/enable   # 期待马达响 200ms
   ```
   - **若该文件不存在** → 确认内核侧缺 timed_output,需要走 §5.2 第 2 步(且先要马达 GPIO)。
6. **power 噪声评估**:
   ```
   logcat -d | grep -iE 'power_hint|interactive|cpufreq_limit' | head
   # 期待:能看到一批失败的 fopen —— 用来判断"消两条报错换一类新报错"是否可接受
   ```

---

## 9. 未决风险 / 未确定项(不猜)

| 项 | 状态 | 缺什么证据 |
|---|---|---|
| 内核是否暴露 `/sys/class/timed_output/vibrator/enable` | **UNKNOWN** | 需要能解开当前 boot.img 的 ramdisk 才能静态确认;我按 MTK ROOTFS 头(`88 16 88 58 6a 2c 1e 00 52 4f 4f 54 46 53`)量到 ramdisk 段(1,977,962 B @ 0x409000)但解不开其压缩格式。**最省事的是直接上板 `ls`**(§8.5) |
| 马达 GPIO / RGB LED 针脚 | **UNKNOWN** | 无任何工件支持 GPIO141/144/136。需要原厂 `cust_leds`/`mt_vibrator` 配置或原厂 dts 里的对应节点 |
| `Unable to get ILight` 是否已修 | **未验证** | r5k 日志是 init-only tail,覆盖不到 system_server。**不要用它的 0 命中当已修复** |
| light 的 RGB/通知灯能否生效 | **判定为不能** | 该 .so 只有 4 个 sysfs 路径,其中只有 `lcd-backlight` 在 Z1 存在 |
| `hal_light_default_exec` 等标签是否在策略里定义 | **未能验证** | 本镜像**不含 sepolicy 文件**(`/etc/selinux/` 只有 `selinux_denial_metadata` + `plat_mac_permissions.xml`,无 `precompiled_sepolicy`),策略应在别处/内置。**但 SELinux 是 permissive**(cmdline `androidboot.selinux=permissive`),标签错只会是审计噪声,不影响功能 |
| power 真功能 | **判定为基本无** | 需新写走 MT6572 实际节点的 power HAL,属新工程 |

---

## 10. 本轮改了 / 建了哪些文件

**新建(全部在 `out/z1_hidl_stage/`,无一处原地修改既有交付件)**:

- `out/z1_hidl_stage/apply_hidl_hal.sh` — 部署脚本(幂等/非原地/三重校验,已端到端实测)
- `out/z1_hidl_stage/revert_hidl_hal.sh` — 回滚脚本(已实测往返字节级一致)
- `out/z1_hidl_stage/material/vendor/bin/hw/android.hardware.light@2.0-service.samsung`
- `out/z1_hidl_stage/material/vendor/bin/hw/android.hardware.vibrator@1.0-service.jf`
- `out/z1_hidl_stage/material/vendor/bin/hw/android.hardware.power@1.1-service.jf`
- `out/z1_hidl_stage/material/vendor/etc/init/android.hardware.light@2.0-service.samsung.rc`
- `out/z1_hidl_stage/material/vendor/etc/init/android.hardware.vibrator@1.0-service.jf.rc`
- `out/z1_hidl_stage/material/vendor/etc/init/android.hardware.power@1.1-service.jf.rc`
- `out/z1_hidl_stage/material/android.hardware.light@2.0.so`(备用副本,镜像里已有)
- `out/HIDL_HAL_DEPLOY.md` — 本报告

**未改动**:`out/z1_r5_system/system.img.r5i_final` 及任何既有 system.img / boot.img / 内核源 / dts 一概未动。
**未产出 system.img**(按指示避让 r5j)。
**未刷写、未接设备。**

**临时产物**仅在 `/tmp`(已清理),及 `bridge_backups/out.img.pre_revert_*.` 一个 32MB 测试残留(测试用 mock 镜像的备份,非真实交付件)。
