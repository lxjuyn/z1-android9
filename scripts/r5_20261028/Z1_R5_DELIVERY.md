# Z1 R5 最终交付（2026-10-28 13:15 定稿）

> **本文件取代 `Z1_R5_DELIVERY.md` 的早期版本**（那版写于多项修复之前，数字已过时）。
> 全部结论为本机实测；**尚未上板**（设备不在线，刷写队列在等）。

---

## 一、两个产物

| 产物 | 路径 | 大小 | sha256 |
|---|---|---|---|
| **R5 boot** | `out/z1_r5_boot/boot.img` | 6,209,536 B (5.92 MiB) | `b426dadf18960350d425f2902a8d3e0bec4120f011f4cc198a38429c15ed54df` |
| **R5 system** | `out/z1_r5_system/system.img.r5_final` | 943,718,400 B | 见 `sha256sum`（本文件末尾会随最后变更固定） |

内部构成：
- zImage 4,207,104 B `da89c08884f80584454db955ad0ef629a6d7547401ee0569537a378273ae676b`
- dtb 20,741 B `cea1457bf6dbc97f76199ea7e817aaaffc2b91338c0a6facb1996d9cdabc9a55`
- ramdisk 1,977,444 B（与 R4/R4.1 逐字节相同）
- cmdline 285 B（含 `printk.devkmsg=on` / `androidboot.selinux=permissive` / `console=ttyS0,921600n8` / `earlycon`）

**回滚件**：boot → `out/z1_r41_final/boot.img`；system → `out/z1_r3f_system/system.img`（sha `ebb58810…`）

---

## 二、R5 相对 R4.1 的全部改动（逐项）

### boot 侧（内核 + dtb）

| 改动 | 说明 | 验证 |
|---|---|---|
| **SND/ASoC 音频栈 =m** | R4 内核 `snd-soc-core`/`snd_pcm` 符号 0 命中，音频 .ko 全加载不了（实测 `snd-soc-core.ko` 的 175 个未解析符号里 31 个 R4 提供不了） | 新内核 Image 15,377,208 B；11 个音频 ko 依赖**全量解析 = 0 缺失** |
| **MFD cell: z1-charger** | `mt6323_devs[]` 加 `{ .name="z1-charger", .of_compatible="z1,mt6323-charger" }`。**没有它，dts 里的 charger 节点是死的**：DT 子节点不会自动变 platform device，MFD 只按本表建子设备 → `insmod z1_charger.ko` 永远 -ENODEV | 内核 Image 里 `z1-charger` / `z1,mt6323-charger` 各 1 命中 |
| **z1_charger 驱动入树** | Kconfig `CONFIG_CHARGER_Z1_MT6323=m` + Makefile + `drivers/power/supply/z1_charger.c` | 编出 `z1_charger.ko` 10,148 B |
| **ramoops 崩溃取证** | dts `ramoops@be200000`（1 MiB, no-map, record 0x20000/console 0x40000/pmsg 0x20000） | 与 connsys@be000000 / framebuffer@bf400000 **无重叠**；内核 PSTORE/RAM/CONSOLE/PMSG 全 =y |
| **usb-charger dts 节点** | `&pmic { usb-charger { compatible="z1,mt6323-charger"; io-channels=vcdt; interrupts=<10 LEVEL_HIGH> } }` | `status="okay"`，phandle 可解析 |
| **initrd-end 同步** | dts 里陈旧值 0x842e1abf → **0x842e2a64**（= 0x84100000 + body 1,976,932） | 组装器自校验通过 |

### system 侧

| 改动 | 数量 | 验证 |
|---|---|---|
| **WiFi 框架 HAL** | 15 文件 | 全存在 + **SELinux 标签 15/15**（loop 挂载 + getfattr 实测） |
| **tinyalsa 工具** | 4 工具 + libtinyalsa | `/bin/tinyplay|tinymix|tinycap|tinypcminfo`，0755，NEEDED 5 库全部解析，`/bin/linker` 在位 |
| **音频 .ko** | 12 个（11 音频 + 1 充电器） | 逐个 dump 回读 sha256 **与归档件全等** |
| **F1 音频 HAL** | `/vendor/lib/libaudio.primary.default.so` 20,844 B | shim `dlopen` 的目标，此前**缺失**（音频 HAL 根本加载不了） |
| **load_audio.sh 重写** | 2,969 B | 修 accdet 缺失 + 顺序（accdet 必须在 machine 前）；BASE 路径本就正确 |
| **z1_audio_probe.sh 补入** | `/bin/` 968 B | rc 引用它但镜像里**原本缺失** |
| **BT 三死文件删除** | 3 个 | `/lib/bt14_bluetooth.default.so`、`/lib/android.hardware.bluetooth@1.0-impl.so`、`/lib/libbtcust.so`（vendor 的补丁版 impl 55,268 B 保留） |
| **SELinux 标签全量补齐** | 34 个文件 | /bin 12 + /vendor/bin 2 + /lib 11 + /vendor/lib 6 + /vendor/etc/init 2 + /xbin 1；终检**缺标签数 = 0** |

---

## 三、我在自查中发现并修掉的问题（重要，防止误判"已完成"）

这份清单是对"是否还有疏漏"这个质疑的直接回答：

| # | 问题 | 后果（若不修） | 状态 |
|---|---|---|---|
| 1 | `/vendor/lib/libaudio.primary.default.so` 缺失（F1） | 音频 HAL shim `dlopen` 失败 → **Android 完全无音频**，内核音频栈白编 | ✅ 已补 |
| 2 | `load_audio.sh` 漏 `mt6323-accdet.ko` 且顺序错 | machine 驱动 `insmod` 必报 `Unknown symbol mt6323_accdet_enable_jack_detect` → **声卡绑不上** | ✅ 已重写 |
| 3 | `/bin/z1_audio_probe.sh` 缺失（rc 引用它） | 开机 `sys.boot_completed=1` 时 exec 失败，取证脚本不跑 | ✅ 已补 |
| 4 | **MFD cell 缺 z1-charger** | dts 节点存在但永远不 probe → 充电检测**完全无效**，且 `insmod` 也救不了 | ✅ 已补 + 重编内核 |
| 5 | 34 个文件无 SELinux 标签 | 转 enforcing 后全部被拒（WiFi/BT/音频/诊断全线崩） | ✅ 已全补 |
| 6 | BT 三死文件未删 | LOS14 遗留 + 未补丁版 impl + 断链 libbtcust 会干扰 BT HIDL | ✅ 已删 |
| 7 | dtb 的 `initrd-end` 是陈旧值 | 组装器 `initrd-end == start+body` 校验必拒；若强刷则 initramfs 截断 | ✅ 已同步 |

**另外纠正了我自己的 3 处误判**（都已复核推翻，未写入产物）：
- 误以为 `/system/lib/modules` 路径是错的 —— 实际 fstab 把 `mmcblk0p4` 挂到 `/system`，该路径**正确**，原写法无需改
- 误以为 `mt6572-machine.c` 不存在 —— 实际 machine 实现在 `mt6572-mt6323.c`
- 误以为 AFE 缺 dts 节点 —— 实际 `audio@11140000` + `sound` 两节点**都已 okay 且资源齐全**

---

## 四、刷写（队列已在等设备）

```bash
# 已在跑（/tmp/flash_r5.sh，日志 /tmp/flash_r5.log）：
#   第1步 bootimg <- out/z1_r5_boot/boot.img      回滚 out/z1_r41_final/boot.img
#   第2步 android  <- out/z1_r5_system/system.img.r5_final  回滚 out/z1_r3f_system/system.img
# boot 失败则停止，不刷 system（避免半套状态）
```
**触发方式**：起脚本已就绪，**长按电源 10 秒断电 → 插下载线 → 开机**即自动刷写。
铁律：先起脚本再让设备进 BROM 窗口；下载线直插主板后置 USB 不经 hub；**必须 sudo**（cdc_acm 抢占 preloader 接口）。

---

## 五、上板验收（按序，每步判据明确）

### P0 启动
```sh
ls /sys/fs/pstore/          # 目录存在 = ramoops probe 成功（R5 新增）
dmesg | grep -i ramoops
```

### P1 触摸（R4.1 修复，R5 继承）
```sh
dmesg | grep -a z1_gpio_hunt   # 期望"基线探测即命中…（VGP1 always-on 修复生效）"
dmesg | grep -a z1_cst836u     # 期望 chip ACKed at 0x15 + polled CST836U registered
getevent -lt /dev/input/eventN # 手指出 ABS_X/Y + BTN_TOUCH
```

### P2 音频（R5 新增，最需要盯）
```sh
# load_audio.sh 应已由 init 在 post-fs 自动跑，先看它的日志：
dmesg | grep -a Z1Audio | head -30
#   ✅ 期望每行 "insmod OK <ko>"，末尾 "audio load done: N audio modules"
#   ❌ 若见 "insmod FAIL ... Unknown symbol" → 说明依赖或版本不匹配
#   ❌ 若见 "MISSING /system/lib/modules/xxx.ko" → 路径或文件问题
cat /proc/asound/cards         # ✅ 期望 card0 出现
tinyplay -h                    # 能跑（无需 /dev/snd）
tinypcminfo -D 0 -d 0          # 能打开 PCM
tinymix                        # 能列控件
tinyplay /data/local/tmp/test.wav   # 真出声（需自备 16bit PCM wav）
# 开机后 z1_audio_probe.sh 会在 boot_completed 时把状态打给 kmsg：
dmesg | grep -a z1audio
```

### P3 WiFi
```sh
service list | grep -i wifi       # 期望 android.hardware.wifi@1.0 + supplicant@1.0/1.1
ps -A | grep -E 'wpa_supplicant|wificond'
logcat -d | grep -iE 'wifi|IWifi' # "Failed to start vendor HAL" 属预期降级，之后应能扫描
```

### P4 充电器（R5 新增）
```sh
insmod /system/lib/modules/z1_charger.ko   # 若未自动加载
dmesg | grep -i 'charger\|CHR_CON0'        # 期望 probe dump 出寄存器原值
cat /sys/class/power_supply/z1_charger/online   # 插拔 USB 应 1/0 变化
```

### P5 其余
电池百分比 / 电源键 / 按键 / GPS / BT → 按 `out/Z1_R4_FLASH_RUNBOOK.md` §3、§4、§3.5。

---

## 六、必须诚实说明的未验证项

1. **全部为静态验证，未上板**。符号可解析 ≠ 运行时 probe 成功。
2. **AFE 驱动（F3 重写版）的寄存器序列未在 Z1 上验证过**。虽然 dts 资源齐全（reg/clocks/IRQ 64/clock-names）、符号闭合，
   但 IRQ 64 是 vendor 反推值，dts 注释自称 "unverified placeholder"。可能 probe 成功但不出声。
3. **accdet 无 dts 节点** → 不会 probe（`mediatek,accdet` 是可选 phandle，machine 驱动有 NULL 保护，不阻塞绑卡），
   所以**耳机插拔检测大概率不工作**，但不会影响扬声器出声。
4. **WiFi vendor HAL 必然降级**（libwifi-hal 是 bcmdhd 构建，GOOGLE_OUI 命令 MTK 不应答）。
5. **z1_charger 的 CHR_CON0 bit5 语义**来自 vendor 3.4.67 反查，未在 Z1 实测。
6. **git push 失败**（网络超时，`aa2089b54` MFD cell 提交尚未推到 origin）。源码在本机 commit 里安全，但**未上远端**。

---

## 七、磁盘教训（本轮踩到两次）

`/` 只剩 3–5 G 时：
- `debugfs` 写入会**静默失败**（rc=0 但没写入）
- 内核编译报 `fixdep: error opening file ... .o.d: No such file or directory`
所以：大操作前先 `df -h /`；debugfs 写完必须回读验证（dump 比 sha，或 loop 挂载 getfattr）。
