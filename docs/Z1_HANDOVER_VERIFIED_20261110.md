# Z1 驱动/功能恢复 —— 交接文档（2026-11-10 收尾）

> 本文由**独立验收方**在 2026-11-10 深夜对 `b0b2f3c`（Android 仓库）与
> `e6489564b`（内核仓库）逐条复核后写成。所有"已核实"结论都附了可复现的命令或文件位置；
> **没有硬件证据的一律标注为"未验收"**，不要把两者混读。
>
> 上一位 agent 写的 682 行功能手册仍在 `docs/Z1_FEATURE_OPERATIONS_20261110.md`，
> 本文是它的**验收结论 + 交接风险清单**，不重复其内容。

---

## 0. 一页结论

**主机侧交付属实、可复现；硬件侧一个都没验收。**

| 维度 | 状态 |
|---|---|
| 仓库 | Android `b0b2f3c`（已推远端 main）、内核 `e6489564b`（已推），两树干净 |
| 测试 | 我在本机重跑 **9 套、187 个用例，全部通过**（明细见 §2） |
| 外围候选镜像 | `833a65f2…12cc8dd`、943,718,400 B、e2fsck rc=0；**全树 2134 文件只有 6 个被替换**（我实测，非采信声明） |
| 图形组件 | 三件产物可从当前树**逐字节复现**（`fb7240ed…` / 149,496 B） |
| 硬件 | **WiFi 联网、蓝牙配对、3D 渲染、耳机/录音、实体键物理码、TF、ADB、GNSS、休眠唤醒 全部未上板验收** |
| 板子 | `boot` 分区现为 **r14 满血 OVL**（写入并读回一致），但**刷完后未验证启动**（串口无 r14 的 `z1hwc2` 行） |

---

## 1. ⚠️ 三个必须知道的坑（接手第一件事先读这节）

### 坑 1：树里能编出来的 HAL ≠ 板上验证过的那一版

| 来源 | 版本 | 大小 | sha256 | 硬件状态 |
|---|---|---|---|---|
| `releases/ovl_v83_r14/hwcomposer.z1.so` | r14 | 147,176 B | `03d2f200…` | ✅ **实测 `device=4 client=0`**，4 层全 OVL，11009 帧无失败 |
| 当前树 `build.sh` 产物 | r16+ | 149,496 B | `fb7240ed…` | ❌ **从未上板**（含未验证的 vsync 线程） |

树里现在是 r16 源码（`#define Z1_HWC_VERSION "2026-11-10-r16"`）。**直接用树编出来的
二进制刷板，等于把"已验证的满血 OVL"换成"未验证的 vsync 实验版"。**
要回到已验证状态，用 `releases/ovl_v83_r14/` 里的整套（boot.img + 两个 .so）。

### 坑 2：r16 的 vsync 修复**没有板上证据**

r15 把 `DRM_IOCTL_WAIT_VBLANK` 写成 `DRM_VBLANK_RELATIVE` + `sequence=0`，
内核 `drm_wait_vblank_is_query()`（`drivers/gpu/drm/drm_vblank.c:1685`）判定这是**查询**，
立即返回 → 线程空转，SF 每 ~46 秒打印一次 `Timed out waiting for hw vsync; faking it`。
r16 已改为 `sequence=1`，但**`z1hwc2: first vsync ts=` 这条里程碑从未在板上出现过**
（板子随后因电量耗尽无法稳定启动）。接手后第一件事就是验证这一行。

### 坑 3：v79 回滚镜像自带的旧 HAL 会让 composer 崩溃 → 黑屏有背光

v79 的 ramdisk 会往 `/vendor/lib/hw/` 装一份 **109,296 B 的旧 HAL**，它在
`getFunction(3)` 返回 NULL：

```
E ComposerHal: failed to get hwcomposer2 function 3
E ...: Could not get passthrough implementation for graphics.composer@2.1::IComposer/default
```

后果：composer 无限重启（`getprop init.svc.vendor.hwcomposer-2-1` = `restarting`）、
SF 没有合成器、**屏幕有背光但全黑**。这不是硬件坏。r14 镜像自带 147KB 的完整实现，没有这个问题。

---

## 2. 验收明细（我实际跑的命令与结果）

### 2.1 测试套件（全部由我在本机重跑）

| 套件 | 命令 | 结果 |
|---|---|---|
| HWC fence 契约（普通） | `python3 scripts/z1hwc2/test/test_fence.py --out-dir /tmp/v` | **34 tests, 0 failed** |
| HWC fence 契约（ASan+UBSan） | 同上加 `--sanitize` | **34 tests, 0 failed** |
| 改动前源码对照 | 同上加 `--source <0e6b50f 版>` | **34 tests, 27 failed**（证明测试真的能抓 bug） |
| USB 候选 | `python3 tests/test_z1_usb_candidate.py` | Ran 34, OK |
| TF 存储 | `python3 tests/test_z1_tf_storage.py` | Ran 34, OK |
| 电池采样 | `python3 tests/test_z1_battery_sample.py` | Ran 21, OK |
| 按键表打包 | `python3 tests/test_z1_keylayout_packaging.py` | Ran 4, OK |
| 驱动候选 | `python3 tests/test_z1_driver_candidate.py` | Ran 27, OK |
| WiFi STA 固件路径 | `python3 wifi/test_z1_patch_wifi_hal_sta_fw.py` | Ran 16, OK |
| 音频 HAL 合约 | `bash scripts/r5g_audio/tests/run_tests.sh` | **SUITE: 6/6 passed** |
| 电源键处理函数 | `python3 tests/powerkey/run_regression.py` | `"passed": true`，11 用例 × (-O0/-O2) |

> 注：音频测试会往 `bridge_backups/products/products_audio_hal_<时间戳>_*/` 写新的产物目录，
> 属正常行为，但会让 `bridge_backups` 目录数量增长。

### 2.2 外围候选镜像（我自己挂载核对的，不是看 JSON）

```bash
sudo mount -o loop,ro out/z1_driver_candidates_20261110/system_peripherals_v1.img /mnt/z1cand
sudo mount -o loop,ro out/z1_v35_selftest/system_v35.img /mnt/z1base
```

* sha256 = `833a65f23ceada548339d9f48db4765931136698755d00c9b67f2f4de12cc8dd` ✓
* 字节数 = 943,718,400 ✓；`e2fsck -fn` 返回 **0** ✓
* 全树哈希对比：base 2134 文件、cand 2134 文件，**diff 只有 12 行 = 6 个文件**，
  且正好是这 6 个（其余全部逐字节相同 ⇒ `base_unchanged` 属实）：

| 路径 | sha256（实测 = 声明） | 权限 | SELinux |
|---|---|---|---|
| `/vendor/lib/libwifi-hal.so` | `bc7e0525…` | 0644 0:0 | `u:object_r:vendor_file:s0` |
| `/vendor/lib/libbt-vendor.so` | `43944199…` | 0644 0:0 | `u:object_r:vendor_file:s0` |
| `/vendor/lib/hw/audio.primary.mt6572.so` | `d037d76c…` | 0644 0:0 | `u:object_r:vendor_file:s0` |
| `/lib/modules/z1_cst836u.ko` | `255b08bb…` | 0664 0:0 | `u:object_r:system_file:s0` |
| `/usr/keylayout/mt6779-keypad.kl` | `ccbd0e9c…` | 0644 0:0 | `u:object_r:system_file:s0` |
| `/usr/keylayout/z1_cst836u.kl` | `621e1de4…` | 0644 0:0 | `u:object_r:system_file:s0` |

* 归档副本 `bridge_backups/products/products_peripherals_candidate_20261110_223218/` 存在且同 sha ✓
* 基底 `out/z1_v35_selftest/system_v35.img` sha = `f180dac2…` ✓（与声明一致）

### 2.3 图形三件套（3D 相关）

| 产物 | sha256 | 字节 | 我的复现结果 |
|---|---|---|---|
| `graphics_stage/hwcomposer.z1.so` | `fb7240ed…` | 149,496 | **本机重建逐字节一致** ✓ |
| `graphics_stage/gralloc.z1.so` | `728b18de…` | 27,512 | 源在 `products_gralloc_build_sources_20261110_214824_352735/` |
| `graphics_stage/gles2probe_android28_armv7` | `6697ca88…` | 65,956 | —— |
| `libGLES_mesa.so` | `bb0ac77c…` | 324,832 | 从 v35 镜像提取（非本轮重建） |
| `libgallium_dri.so` | `492f0b5e…` | 17,428,828 | 同上 |

> manifest 自己标注 `"status": "HOST_BUILT_GRAPHICS_COMPONENTS_NOT_DEPLOYABLE_AS_COMPLETE_3D"`、
> `"hardware_tested": false` —— 这个自知之明是准确的。

### 2.4 他们改了我写的 HAL：**是真修复，建议保留**

`5d72aca` 改了 `scripts/z1hwc2/z1hwc2.cpp` 三个地方，我逐条审查并跑测试确认：

1. **`wait_fence`**：旧版 `poll` 指数退避（100/200/400ms，实际预算 700ms）且**不校验 revents**，
   任何唤醒都返回 0（可能在 buffer 未就绪时继续）。新版用单调时钟做**有限预算**，
   校验 `POLLNVAL→-EBADF` / `POLLERR|POLLHUP→-EIO` / 仅 `POLLIN` 才算成功，`EINTR` 不延长预算。
2. **acquire fence 失败**：旧版打日志后**照样提交**（用未完成 buffer，表现为撕裂/旧帧）。
   新版返回 `HWC2_ERROR_NO_RESOURCES` 并保留 fd 供重试 —— 符合 HWC2 契约。
3. **client target 缺失**：旧版 `client_fb=0` **仍然提交** → **黑帧**（很可能就是用户看到的"闪/黑"）。
   新版在提交前返回 `NO_RESOURCES`。

⚠️ **副作用预警**：改 2/3 会让失败帧变成"不提交、让 SF 重试"。如果板上出现
**持续 `NO_RESOURCES` 导致界面卡住**，要立刻看 `z1hwc2: ... acquire fence ... failed`
或 `required client target unavailable` 日志——那说明上游 fence/缓冲区真的坏了，
而不是本改动的问题。

### 2.5 部署脚本改动

* `scripts/z1deploy/z1flash_image.sh`：改用 `"$MTK_DIR/mtk.py"` 绝对路径 + 前置存在性检查（**改进**）。
* `scripts/z1deploy/z1stage_boot.sh`：净删 51 行，把"一次性 auto_once"简化成
  **每次开机都装**（`install_one`：`active/` 优先 → 否则用 ramdisk 里的 `/hwcomposer.z1.so`
  → 否则删 `/vendor` 覆盖回退 stock），kill switch `.disable_<module>` 保留。
  我核对：`dma_heap` 建节点、`/dev/graphics/fb0`、`debugfs`+`sw_sync` chmod、
  `z1stage.hwcomposer.sha` / `z1stage.gralloc.sha` 身份属性**全部仍在** ✓ 语义正确。

---

## 3. 仓库状态（含一个记账问题）

```
z1a9_gpt   HEAD=b0b2f3c  分支 work/driver-recovery-20261110-215207  工作树 0 变更
           远端 main = b0b2f3c（已确认）
           ⚠ 本地 main 分支落后在 c7e25dc（b0b2f3c 不是本地 main 的祖先）
fork_linux HEAD=e6489564b  分支 main  工作树 0 变更
```

**记账问题**：本地 `main` 与 `origin/main` 不一致。接手时**不要**直接
`git checkout main && git push`，会得到分叉。正确做法：

```bash
git -C z1a9_gpt fetch origin
git -C z1a9_gpt branch -f main origin/main      # 或 git checkout -B main origin/main
```

### 上一轮已验证的发布产物（还在树里，别弄丢）

| 目录 | 内容 | 状态 |
|---|---|---|
| `releases/ovl_v83_r14/` | boot.img `6c85b6a0…` + HAL `03d2f200…` + gralloc + z1kmstest | ✅ 板上实测 `device=4 client=0` |
| `releases/ovl_v83_r11/` | boot.img `10e92e2a…` + HAL `8966bc66…` | ✅ 单层 DEVICE 首个通过 |
| `docs/ovl/Z1_SESSION_20261110_OVL_RESULT.md` | OVL 达成的证据链 | ✅ |

---

## 4. 硬件侧现状与操作手册

### 4.1 板子当前状态

* `boot` 分区：**r14 镜像**（`6c85b6a0…`，写入并逐字节读回一致）
  —— 但**刷完后没有验证启动**，串口日志里没有 r14 的 `z1hwc2` 行
* `system` 分区：未在本轮改动（上一轮设备上跑的具体 system 版本需重新确认）
* 电池：最后一次读到 4.26 V / 100%（`Discharging`，因为正常启动必须拔掉板子 USB）
* 串口：CH340 @ 921600 8N1

### 4.2 启动模式（最容易踩）

| 现象 | 原因 | 处理 |
|---|---|---|
| 黑屏、串口打 `[PLFM] USB/charger boot!` | **插着板子 USB 上电**，LK 判成充电启动 | 拔掉板子 USB 再按电源键 |
| USB 出现 `0e8d:2000` 且一直不启动 | preloader **download 模式**在等命令 | 拔 USB + 按电源键 |
| USB 出现 `0e8d:0003` + CDC 接口 | 关机充电/引导器 VCOM，**mtkclient 连不上** | 只能等充电，或按电源退出 |
| 反复插拔、`Handshake failed` | 供电不足 | 换 5V/2A 充电头 / 带电源 hub |

**铁律：正常开机必须拔掉板子的 USB；刷写才插 USB + 按复位进 preloader。**

### 4.3 刷写

```bash
# 刷 boot 分区（偏移 0x2980000），写完自动逐字节读回校验
z1a9_gpt/scripts/z1deploy/z1flash_image.sh <img> --minutes 20
```

`--minutes N` 不是墙钟，而是 **N×12 次尝试**，每次最长 60 秒。

**刷写失败诊断表**：

| 日志 | 含义 | 处理 |
|---|---|---|
| `DaHandler - Please disconnect, start mtkclient and reconnect.` | preloader 会话已脏/已消失 | 拔插 USB 重新给 preloader |
| `USBError(5, 'Input/Output Error')` + `Couldn't send sdmmc_write_data header`，且**每次精确停在 DA stage2 的 4096 字节(2.5%)** | 线缆/端口供电撑不住 DA 初始化 eMMC | **换线换口**（本项目实测：换过之后 attempt 1 即过） |
| `no preloader window within N minute(s); nothing written` | 全程没抓到 preloader | 检查启动模式（见 4.2） |

### 4.4 已知环境坑（会让人白干半小时）

1. **`/tmp` 会被清理**：`/tmp/z1devlibs`（HAL 链接所需的设备库）和
   `/tmp/rootfs_v79.bin`（`pack_boot.sh` 的基底 ramdisk）都会消失。
   重新生成：
   ```bash
   SUDO_PASS=1 z1a9_gpt/scripts/z1_extract_devlibs.sh /tmp/z1devlibs
   # rootfs_v79.bin：从 out/z1_v79/boot.img 的 ramdisk 区提取（MTK header 512B + gzip，
   # 头部 [4:8] 是 size 字段，[8:14] 是 "ROOTFS"）
   ```
2. **串口掉波特率**：CH340 一旦重新枚举就回到 9600，用 921600 读会得到纯二进制乱码。
   抓取脚本必须在每次设备出现时重设 `stty 921600 ... raw`。
3. **console shell 会被输入打爆**：日志里出现过 `ttyS ttyS0: 1 input overrun(s)`，
   之后 shell 只回显不执行。**命令要短、要慢**；刷大文件别走串口（146KB 都不稳）。
4. **`pkill -f <名字>` 会杀掉自己**：`bash -c` 的命令行包含目标字符串时，正则同样命中本进程。
   一律先 `pgrep` 拿 PID，再按 PID `kill`。
5. **`/dev/mem` 读显示寄存器需要 `iomem=relaxed` 且必须放在 cmdline 最前面** ——
   LK 传 cmdline 会截断尾部，放末尾等于没写。

---

## 5. 各功能模块：主机侧做完的 vs 仍需上板验收的

| 模块 | 主机侧已做（有测试） | **仍需上板验收** |
|---|---|---|
| 3D / GPU | HWC fence 契约 34 项、gralloc 构建链、GLES2 像素探针、glalloc/heap 后端 | **Mali 实际渲染、GPU 写完到 OVL 扫描的同步、长时间跑帧** |
| Wi-Fi | STA 固件路径修复（16 项测试）、`libwifi-hal.so` 换新 | **关联、认证、DHCP、DNS、真实上网** |
| 蓝牙 | 厂商 ABI 适配、假成功/关闭失败重试、ARM 库入镜像 | **控制器初始化、扫描、配对、A2DP；SCO 未实现** |
| 底部导航三键 | 布局解析、触摸释放、新触摸模块 + 映射入镜像 | **三键物理坐标区域采样，不能保证已正常** |
| 实体按键 | 映射装配（保留方向键）、电源键错误上报防护（内核 `e6489564b`） | **复读/跟读/播放的物理码仍需确认；电源键补丁还没重打 boot** |
| 音频 | PCM 打开失败重试、音量接口不再假成功（6/6 通过）、新 HAL 入镜像 | **3.5mm 听音、左右声道、插拔路由；录音未实现** |
| 存储 TF | 被动检测、前置条件检查（34 项） | **实际识别、供电、host 使能** |
| USB/ADB | opt-in ACM ADB 候选（34 项）+ 门禁 | **认证与运行时集成；候选默认不部署** |
| GPS | 拒绝无效启动前置、反对假成功补丁 | **ABI 适配器与真实定位** |
| 电源/电池 | 电池采样不连续分析（21 项）、休眠唤醒评估、电源键回归（11 项） | **实际休眠/唤醒、RTC 唤醒、显示恢复** |

---

## 6. 建议的下一步顺序（按"风险最低、收益最高"排）

1. **先把板子带到"已验证的满血 OVL"**：刷 `releases/ovl_v83_r14/boot.img`，
   确认 `z1hwc2: open(composer) version … r14`、`device=4 client=0`、不闪。
   （现在板上就是这个镜像，只差一次启动验证。）
2. **单独验证 vsync**（不要和主版本混）：用树里 r16 源码编的 HAL 换上去，
   只看两件事：`z1hwc2: first vsync ts=` 是否出现、SF 的
   `Timed out waiting for hw vsync; faking it` 是否停止。若失败 → 回到 r14 行为
   （不启 vsync 线程），保留 present fence。
3. **验证上一位 agent 的 HAL 加固**（`fb7240ed…`）：在板上确认不再出现黑帧/缺层，
   同时盯 `NO_RESOURCES` 频率（见 §2.4 预警）。
4. **部署外围候选镜像**：刷 `system_peripherals_v1.img` 到 system 分区，
   逐项验收 WiFi → 音频 → 蓝牙 → 触摸/导航 → 按键；每项都要有板上日志。
   （注意：该镜像不含显示栈，HWC/gralloc 仍由 boot 镜像在开机时安装，两者不冲突。）
5. 3D 真正落地：Mali 渲染 + GPU→OVL 同步，再考虑 `libGLES_mesa.so` 的启用开关。

---

## 7. 交接时的一句话总结

**主机侧：9 套 187 个用例全绿、6 个文件的外围镜像可复现、HAL 加固是真修复且被测试证明有效。**
**硬件侧：一个都没验收，其中 vsync 和有问题的旧 HAL 是当前两个最可能踩的雷。**
