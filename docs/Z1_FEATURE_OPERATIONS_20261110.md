# Z1 功能原理、实施状态与操作交接

> 日期：2026-11-10；时区：UTC+08:00。供第一次接手本项目的 agent 阅读。
>
> **本轮交付级别：HOST ONLY / NOT DEPLOYED。** 调查代理完成初稿后由主会话冻结、修订并填写最终构建/回归/提交结果。本轮主会话执行了本文列明的主机测试、离线镜像构建与读回；没有操作板子或刷写。历史上板事实与本轮候选严格分开。
>
> **最终状态以 §22 为准。** 前面各项调查读取窗口从21:59开始，§22由主会话收尾时按实物与测试填写；历史段落保留其证据时间。不要凭本文名称、旧文档标题或测试数量执行刷写。

## 0. 阅读规则与证据边界

### 0.1 路径约定

本文所有相对路径均以 **`/home/lxj/claude/6572/`** 为根；不是以当前 shell 工作目录为根。

| 简写 | 绝对位置 | 用途 |
|---|---|---|
| `z1a9_gpt/` | `/home/lxj/claude/6572/z1a9_gpt/` | Android 设备配置、HAL、脚本、测试、发布物 |
| `fork_linux/` | `/home/lxj/claude/6572/fork_linux/` | 当前主线 Linux 7.0-rc7 派生源树；不能用旧 `a9_wt` 推断当前内核 |
| `out/` | `/home/lxj/claude/6572/out/` | 历史/候选镜像和提取件；同一路径可能被重建 |
| `mainline_recon/` | `/home/lxj/claude/6572/mainline_recon/` | 运行日志、原厂分析与历史调查 |
| `bridge_backups/` | `/home/lxj/claude/6572/bridge_backups/` | 修改前备份；`products/` 是按轮次存档的构建/测试证据 |

设备路径另行写明，例如 `/system/lib/modules/z1_cst836u.ko`。**system 镜像内部的 `/lib/modules/...` 与设备挂载后的 `/system/lib/modules/...` 是同一文件的两种视角**；镜像内 `/vendor/...` 常在设备上对应 `/system/vendor/...`，也可能经 `/vendor` 链接访问。必须查实际 mount/symlink，不能凭字符串推断有两份独立库。

### 0.2 四种结论与五道完成门禁

- **已验证·历史板端**：有指定启动会话的原始日志/读回/人工观察。只对当时组合成立。
- **已验证·主机**：读到源码、ELF、反汇编或既有测试记录。编译和 mock 不等于硬件通过。
- **待验证**：缺运行路径、物理输入、端到端数据或最终独立审核。
- **推测/方案**：用于设计下一次单变量检查，不能写成已经找到根因。

每项功能按顺序过五道门：**源码正确 → 目标 ABI/构建正确 → 镜像实际包含正确文件和标签 → 运行进程/模块实际加载该文件 → 端到端用户功能通过**。第五道未过就不写“功能完成”。哈希核对只证明字节身份，不自动证明语义或硬件安全。

日志行号以 LF 换行计算。日志中可能有二进制、CR、重复转发和多轮启动：检索使用 `grep -a`/`rg -a`，不要将 `[z1warn]` 重放的同一 logcat 条目算成独立复现次数。设备日志中的 01-01、10-30 等时间不保证是真实日历时间；优先按本次 boot 的 uptime 和记录文件关联。

### 0.3 本文纠正的旧结论

| 容易误读的说法 | 应采用的结论 |
|---|---|
| 四层 DEVICE 表示 GPU 3D 已完成、全程零拷贝 | OVL 是显示合成，Mali 是渲染；四层是快照；小层存在 CPU 补齐拷贝 |
| `DMA_BUF_IOCTL_SYNC` 足以等 GPU 完成 | 它处理 CPU cache coherency；producer fence、互斥和扫描生命周期另行保证 |
| r14/v79 boot 就是完整回滚 | 缺配套 system、库、模块、属性、持久覆盖状态时，只是历史候选 |
| 4.28V/100%/Discharging 能证明健康或排除软件故障 | 这些是软件读数/状态；端电压、电流、SOC、健康状况分别取证 |
| LK 的 5×3 证明有 15 个实体按钮 | 只证明配置过交叉点；实体标签到事件映射必须逐键采样 |
| `LD_LIBRARY_PATH` 能隔离 Android EGL | loader、绝对路径、linker namespace、已加载库缓存都可能绕过；必须查 maps 和库哈希 |
| GPIO7、EINT16、Linux IRQ46 是同一个数字空间 | 分别是引脚、硬件中断编号、内核分配 IRQ；不能互换 |
| 同族板的 TF 引脚能直接启用 | Z1 接线、卡检测、电压和电源轨未证，不可照搬 |
| GNSS/蓝牙/WiFi 服务 running 就是功能正常 | 服务注册之后仍有 transport、固件、协议、数据和 UI 多层验收 |
| 录音 PCM 存在就表示 Android 录音可用 | 当前 primary HAL 的输入打开仍返回 `-ENOSYS` |
| `/bin/mtpd` 是 MTP 文件传输 | 该历史程序是 PPP 相关 daemon；不能替代 Android MTP 链路 |
| `powerux_check.sh` 名称像检查器，所以可直接运行 | 旧脚本会写亮度；`screen_off_test.sh` 会改设置和发按键；两者不属于只读检查 |

## 1. 系统总图与当前状态索引

### 1.1 三条基础链

1. **启动链**：MTK preloader → LK → Android boot 容器内的内核/DTB/ramdisk → init → 挂载 system/cache/data → init 服务与 HIDL → Android framework → 应用。
2. **硬件控制链**：应用 API → framework service → HIDL/legacy HAL 或 daemon → `/dev`、sysfs、ALSA/DRM/ioctl → 内核驱动 → DT 资源/pinctrl/regulator/IRQ → 物理硬件。
3. **部署链**：仓库源码 → 编译产物 → system/boot 里的文件 → boot-time installer/持久覆盖 → 进程 maps/已加载模块。一个源码目录“修过”不能跳过这条链。

`HIDL` 是 Android 9 上层与硬件实现的接口/服务契约；`legacy HAL` 是经 `hw_get_module()` 等加载的 C ABI 模块；`WMT/STP` 管理本项目 MTK 连接子系统的功能开关与传输复用；它们不自动代替 Android WiFi/BT/GNSS 服务。`DT` 描述硬件资源，不是硬件接线的证据。

### 1.2 先看哪些章节

| 功能 | 当前可说到哪一步 | 接手优先事项 |
|---|---|---|
| GPU/OVL/gralloc | 历史 OVL 四层快照；Mesa/Mali400 字符串与旧离屏证据；本轮 HWC/gralloc 主机修复 | §2：正确性、实际库身份、共享 buffer 同步 |
| WiFi STA | 已定位错误 Broadcom 固件切换；STA-only ELF 候选与 16 项既有测试 | §3：HIDL→关联→DHCP→DNS，不复活旧 supplicant |
| BT | vendor adapter/ABI/失败处理候选；cleanup 修复仍属主机结果 | §4：本机 NVRAM、HCI、配对/A2DP；SCO 未实现 |
| GPS | 服务 stub；mnld 配置/服务名/ioctl7 阻塞 | §5：真实 transport 与回调，不伪造 running |
| 矩阵键/触摸条 | 非法 `.kl` flag 与打包缺口已修候选；物理映射未知 | §6–7：按设备分开逐键与坐标采样 |
| 电源键 | PMIC IRQ46/输入设备有历史注册；读失败防护修复 | §8：按放事件、IRQ 增量、framework 行为 |
| 电量/health | 瞬时电压查表根因明确；health 独立 bug 已修；采样工具主机测试 | §9：同时测真实端电压；温度目前占位 |
| 3.5mm/录音 | 播放 HAL 重试修复候选；输入 HAL 缺实现 | §10：ALSA→模拟路由→Android 分层验收 |
| TF | 用户确认卡槽；接线未证；有只读检查工具 | §11：先完成 Z1 专属证据 |
| USB | 历史 ACM gadget 绑定；ADB opt-in 候选未部署；MTP/OTG 未闭环 | §12：独占 UDC、鉴权与 FunctionFS 时序 |
| 亮度/睡眠/RTC/FM | 有驱动或接口资产，分别缺物理闭环 | §13–16 |
| 存储/版本/SELinux/回滚 | 有 fsck 历史执行、FUSE 方案；多层持久覆盖影响回滚 | §17–19：先固定组合再验证功能 |

## 2. GPU 渲染、OVL 显示、gralloc 与同步

### 2.1 用途和真实分工

| 字段 | 操作说明 |
|---|---|
| 用途 | GLES2 绘图、HWUI/SurfaceFlinger 客户端合成，以及 OVL 多平面显示输出；这三者应分别验收 |
| 实际链 | 应用/HWUI → Android EGL loader → `libGLES_mesa.so` + 配套 `libgallium_dri.so` → lima render 节点 → dma-buf；SurfaceFlinger → HWC2 → DRM atomic/KMS → MTK OVL/显示链 → 面板；gralloc 决定缓冲区分配和跨进程 handle |
| 实施状态 | 历史 `z1_boot_v83.log:54728` 有 r14 `frames=10997 ... device=4 client=0`；同日志 `:12127/:53532` 有 `frame 6000: device=1`。本轮 HWC acquire/失败帧修复、gralloc 源文件列表修复有主机证据，未上板 |
| 已证根因 | 小层低于 DRM 64×64 最小尺寸时普通 `AddFB2` 失败；r14 小层补齐路径解决该限制。旧 HWC 错把 poll 错误/超时当可继续、client target 失败仍提交。旧 mainline gralloc Makefile 漏 `z1_heap.cpp`。GP `0x4000` 是 PLB_INVALID_CMD，具体本机成因仍未闭环 |
| 具体文件/测试 | `z1a9_gpt/scripts/z1hwc2/z1hwc2.cpp`、其 `test/test_fence.py`/`test_fence_mock.cpp`；`graphics/gralloc/Android.mk`、`tests/test_build_sources.py`；`scripts/gles2test/`；证据包 E-HWC/E-GRALLOC/E-GLES，见 §21 |
| 故障分支及下一步 | EGL 初始化失败查 loader/依赖/maps；像素错误查 stride/format/modifier/offset/cache；GP 错误先最小离屏复现；OVL 导入失败查连续性/CMA；撕裂或旧帧查 acquire/release/实际 commit 完成；不要先用重置/禁 fence 掩盖问题 |
| 通过门禁 | 固定完整库集合；像素断言通过；动态识别 render/KMS 节点；延迟 fence、错误 fence、导入失败、失败 commit、静置超过 250ms、关闭 vsync 均不提前复用扫描 buffer；再做 UI/压力与帧时间验证 |
| 回滚 | 恢复已验收的 boot/system/Mesa 双库/gralloc/HWC/模块/属性/持久覆盖整套并重启相关进程，查 maps；单删 HWC、只换 boot 或关闭 fence 都不是充分回滚 |

Mali-400/Utgard 的本项目目标是 GLES2，Mesa 也提供 ES1.1 支持；不能声明 GLES3/Vulkan/OpenCL 功能。Mesa 官方明确渲染 GPU 与 SoC 显示引擎分离。OVL 的 `DEVICE` 表示由显示硬件处理该层；`CLIENT` 表示 SF 先合成该层集合，客户端渲染可以是 Mesa，也可以是软件实现。四层 DEVICE 没有证明 GPU 一直在工作。[M-LIMA26、M-LIMA24]

历史 `mainline_recon/z1_lima_offscreen_240x320_render_20260717_231744.log:22` 有 `GL_RENDERER=Mali400`；11-08 日志 `:5879` 已出现 `Mesa, Mali400, OpenGL ES 2.0 Mesa 26.1.1`。这纠正“还没换到新 Mesa”的笼统说法，但字符串不能代替本轮像素、稳定性和实际库文件身份验证。旧 19.1.8、24.0.9、26.1.1 资产同时存在，不能按文件夹名字混配。

### 2.2 缓冲区契约与同步时序

推荐先验证显示侧 CMA/连续分配 → dma-buf 导出 → lima 导入渲染 → OVL 扫描的路径。lima 的 SG/shmem 页映射能力与 OVL 的物理连续要求不同；当前关闭 IOMMU 时，不能把任意 GPU BO 当可扫描 buffer。首个实验固定 LINEAR、offset=0，再逐项扩大格式/尺寸范围。具体本地实现入口：`fork_linux/drivers/gpu/drm/lima/lima_gem.c`、`lima_vm.c`、`mediatek/mtk_gem.c`；实际配置仍需随最终 manifest 固定。

正确生命周期应记录为：

1. producer（GPU/CPU）完成写入后提供 acquire fence；consumer 在 fence 成功前不得读源 buffer，包括小层 CPU 拷贝。
2. CPU mmap 访问时用 START/END 与 READ/WRITE 包围访问，解决 cache coherency。**这不提供互斥，不替代 fence 等待**。[K-DMA、K-DMA-UAPI]
3. 成功 atomic commit 后，按驱动实际完成事件/可靠 out-fence 管理 present；周期性 vsync 仅证明节拍，不能凭空证明某次 commit 成功。
4. AOSP HWC2 release fence 保护上一帧 buffer，只有设备已结束对它的读取，生产者才可复用。新帧开始显示也不能证明新 buffer 已可重写。[A-HWC、K-SYNC]
5. import/commit 失败不能推进对应完成状态；空闲超过 250ms 不代表扫描器停止。

**本轮已修边界**：读取到的 `wait_fence()` 优先拒绝 `POLLNVAL/POLLERR/POLLHUP`；present 在 acquire 失败时返回 `HWC2_ERROR_NO_RESOURCES`，保留 fd 所有权；缺少必需 client target 时中止本帧。E-HWC 记录旧版 7 PASS/27 FAIL，新版及 sanitizer 各 34 PASS/0 FAIL、ARM 构建成功。归档 HWC 候选 SHA256 为 `fb7240ed1febce56c6cbb5ebe5607e14b3641757e7999ed2d4fbdff1a3d45ba7`。

**明确未修边界**：该验证记录写着 timeline/vsync 未改；源码仍有 `fence_watchdog()` 的 250ms 强制推进。handle 校验、实际扫描结束、present/release 时序都不得被这 34 项替代。独立复核已通过本补丁范围；完整释放时序仍未通过。最终产物见§22。

### 2.3 gralloc 与 EGL 版本身份

`graphics/gralloc/Android.mk:31–36` 的读取快照已在 `Z1_KERNEL_VARIANT=mainline` 分支加入 `lima_allocator.cpp z1_heap.cpp`，避免弱符号后端缺失造成 ashmem 回退。E-GRALLOC 有 7 项构建列表测试，以及既有像素转换/handle 测试 5,121,415 次检查；这些不是 5,121,415 次板端帧验证。测试说明仅覆盖 GNU make fixture、NDK ARM link/本机算法，不是完整 AOSP 产品编译。尤其注意：键布局用“非 stock”分支，而此处明确等于 `mainline`；不同 variant 字符串不能互相替代。

Android 9 loader 有系统路径搜索、sphal/driver namespace 和缓存。[A-EGL] 因此单设 `LD_LIBRARY_PATH`、`EGL_PLATFORM`、`ro.hardware.egl` 或 `debug.hwui.renderer` 都不足以证明已隔离驱动或只影响一个应用。`debug.hwui.renderer` 属系统属性，进程还会缓存选择；不要称它为单应用开关。

下一轮先使用独立 GLES2 probe，保存 EGL 前后和读回后的 `/proc/self/maps`、实际库 SHA、GL/EGL 扩展、原始 RGBA 与像素分析。`scripts/gles2test/gles2probe.c` 支持 `--out NEW_DIRECTORY`，会运行 GPU 并创建文件，属于 **DEVICE 主动测试**，不在本次执行范围。既有 E-GLES 的 12 项 analyzer 测试与 `hardware_status=UNPROVEN` 只证明工具不会把合成样本冒充板端结果。

## 3. WiFi：沿 Android 9 STA HIDL 链修复

| 字段 | 操作说明 |
|---|---|
| 用途 | 设置界面开 WiFi、扫描、关联接入点、获取 IP 和正常联网 |
| 实际链 | Settings/WifiService → WiFi HIDL service/chip/STA iface → libwifi-hal；supplicant HIDL 处理认证/关联 → cfg80211/MTK WLAN；底层 WMT/BTIF/STP/固件加载提供无线设备；IP 配置再经 framework/netd/DHCP/DNS |
| 实施状态 | 历史日志实际 BSS/SSID 条目是扫描证据，旧 `scan OK` 探针曾误报；当前 donor HAL 的错误固件路径已定位。STA-only patch、目标 ELF 与 16 项测试已有归档；本轮没有设置界面连接验收 |
| 已证根因 | v35 donor `libwifi-hal.so` 包含 `dhd/parameters/firmware_path` 与 Broadcom 固件名；MTK 上打开失败导致 `ChangeFirmwareMode` 返回失败，挡在 STA 初始化阶段。v83 日志 `:37258–37259` 可复核 |
| 具体文件/测试 | `z1a9_gpt/wifi/z1_patch_wifi_hal_sta_fw.py`、`test_z1_patch_wifi_hal_sta_fw.py`、`test_wifi_hal_branch_harness.c`；`rootdir/init.z1.network.rc:63–79`；E-WIFI |
| 故障分支及下一步 | 无 wlan→模块/WMT/固件；HIDL 不注册→rc/VINTF/ELF/标签；仍固件模式错→实际映射的 HAL 是否旧；能扫描不能关联→supplicant/认证；能关联无 IP→DHCP/netd；有 IP 无网→路由/DNS/连接检测，逐层留证 |
| 通过门禁 | 精确输入 SHA 与 ELF 契约校验；安装后读回同 hash、maps 命中；STA 创建、扫描、关联、DHCP、DNS、数据传输、断开重连分别通过；AP/P2P 不在本轮通过范围 |
| 回滚 | 恢复旧 HAL 的文件及 mode/uid/gid/SELinux 标签、对应服务/manifest 配套；保留 HIDL 主链；不替换全局 SSL，不启动第二个 legacy supplicant |

精确补丁输入是 95,116B，SHA256 `0a9ba41186680851eb559224c01b9c5231c6cea748c4cfa18424a0c2116b46f7`；输出 `bc7e05250c6849e883d66856470e185e5cb61e71cd56675c01fd441cd9493088`。它只替换 `wifi_get_fw_path` 的 24B 函数范围：STA 返回 NULL，让 AOSP `DriverTool::ChangeFirmwareMode()` 走“此模式不需重载固件”的既有分支；AP/P2P 表项保持原样。NULL 不是在所有错误场景无条件成功。[A-WIFI-DRIVER、A-WIFI]

旧手工 `/system/bin/wpa_supplicant` 因 `SSL_set_session_ticket_ext` 缺失而失败的链已在 rc 中退役；当前目标是 `/vendor/bin/hw/wpa_supplicant` 与 supplicant HIDL。不要把旧脚本恢复后和 framework 争抢同一接口。扫描成功也不证明认证、DHCP、DNS 或互联网连通。采样日志可含 SSID/BSSID/地址，保存于本地授权证据区，公开文档仅保留去标识状态。

## 4. 蓝牙：transport、NVRAM、HCI 与音频分开验收

| 字段 | 操作说明 |
|---|---|
| 用途 | 蓝牙开关、发现/配对、ACL 数据与 A2DP 媒体；SCO/HFP 语音另列 |
| 实际链 | Bluetooth stack → `android.hardware.bluetooth@1.0` → `libbt-vendor.so` → 原厂 `libbluetoothdrv.so` → `/dev/stpbt`/WMT func-on → 控制器；vendor 初始化还依赖本机 NVRAM/校准和底层库 |
| 实施状态 | 无参 `mtk_bt_enable(void)` ABI、USERIAL_OPEN/FW_CFG、未实现 SCO 的明确失败返回已有候选。22:07 读取快照还包含 cleanup 失败保留资源供重试的修复；本机 NVRAM和实际控制器未验 |
| 已证根因 | 旧 adapter 对原厂入口签名/生命周期处理不准确；旧 cleanup 在 disable 失败后卸载唯一释放入口，留下 fd/state；新故障注入证实并覆盖此路径。不是对“为什么整机蓝牙不能配对”的完整根因认定 |
| 具体文件/测试 | `z1a9_gpt/bluetooth/libbt-vendor-mtk/libbt-vendor-mtk.c`、`tests/`；`scripts/btgps/bt/build_candidate.sh`；E-BT；官方 A-BT/A-BTVENDOR |
| 故障分支及下一步 | dlopen/符号失败→依赖与 namespace；无 stpbt→节点/WMT；enable 失败→固件/NVRAM/transport；初始化回调成功仍无事件→HCI 数据通路；配对成功无 A2DP→profile/audio routing；SCO 返回不支持→尚需真正 PCM/SCO 实现 |
| 通过门禁 | 目标 ARM ABI+依赖+maps；本机校准来源不被替换；HCI command/event、重复开关、发现/配对/重连、A2DP 双端实测；SCO 单独通过后才能声明通话音频 |
| 回滚 | adapter 与原厂依赖、rc/manifest 一并恢复；保留本机 NVRAM，不用别机 MAC/校准替代；失败资源的最终关闭/进程卸载语义需先审计 |

`POWER_CTRL(ON)` 只检查请求，实际控制器打开在 USERIAL_OPEN。`FW_CFG` 回调依据真实 `hci_fd`；`SCO_CFG` 和 `SET_AUDIO_STATE` 返回不支持，避免假成功。LPM 的 no-op/成功路径不能证明低功耗或唤醒已工作。

E-BT 的最新 cleanup probe 记录 8 cases、0 failed，显式故障 case 为 343 assertions；对应源码 hash `29e69c03beec09f47215e92a4882a774ce889aa3ee1f2c0cbd88567b1f817e1b`。这是 mock/匿名 pipe 的资源所有权测试，未使用真实 HCI。AOSP vendor interface 会在 cleanup 后 `dlclose()`；如果释放持续失败而上层卸载 adapter，静态状态仍可能丢失。因此这轮修复只能保证 adapter 仍加载时的重试，不可声称跨卸载/重启已完全恢复。早期候选 metadata 中“cleanup probe expected to fail”是过时文字；22:35重新构建已修正metadata，二进制仍为43944199…，按源hash与实测日志判断。

NVRAM 是本机地址/射频校准的一部分。文档不抄任何实际地址或 NVRAM 内容，也不建议抹掉它“重新生成”。先确认 factory blob 依赖闭包、所读路径、权限和错误，再谈兼容层。

## 5. GPS/GNSS：stub、mnld 与 ioctl 7

| 字段 | 操作说明 |
|---|---|
| 用途 | GNSS 卫星状态、NMEA/定位回调、有效位置；不是仅让 framework 不阻塞 |
| 实际链 | Location/GnssLocationProvider → GNSS HIDL → legacy `gps.default.so` → mnld socket/helper `libmnlp_mt*` → `/dev/stpgps`/WMT-STP → GNSS 硬件 → 回调 |
| 实施状态 | `hal_stub/gps_stub.c` 与 `gpsstub/gps_stub.c` 是不触硬件、无位置回调的 stub。新 prep/installer 已拒绝已知错误配置、伪 running 和 routeB；公共 rc 仍定义 `z1_mnld`。真实定位未实现闭环 |
| 已证根因 | 原错误 `GPS_CHIP.cfg` 写成 key=value 与 mnld 的精确字符串匹配不兼容；stock HAL 等 `init.svc.mnld`，公共服务名不同；helper 调用 `ioctl(fd,7,0)` 与当前驱动已实现命令不匹配 |
| 具体文件/测试 | `z1a9_gpt/scripts/ramdisk_v9/z1_gps_prep.sh`、`scripts/btgps/gps/install_gps.sh`、`z1_gpsdrv_load.sh`、`test_gps_scripts.py`；`rootdir/init.z1.network.rc:98`；`fork_linux/drivers/misc/mediatek/connectivity/common/linux/pub/stp_chrdev_gps.c`；E-GPS |
| 故障分支及下一步 | 服务不存在→统一公共 init/stock HAL 契约；配置不认→保留原件，核字节而非盲设芯片；ioctl7失败→按RTC低功耗标志与已证控制流核对，不补常量成功；transport正常无卫星→天线/射频/时间及真实数据；有数据无定位回调→HAL桥接 |
| 通过门禁 | 禁止 stub 参与“定位通过”；原厂 ABI/ioctl7 语义确证；daemon 不重启、transport 有真实接收、卫星/位置回调可核；已授权环境下完成首次定位与重复启停 |
| 回滚 | 保留/恢复原配置与权限、daemon/helper/HAL/rc 配套；若使用 stub 仅作启动绕阻塞，明确标“定位不可用”；不伪写 `init.svc.*` |

当前驱动 switch 中 0/1 明确返回 `-EINVAL`，HWVER 命令将值 copy_to_user；未知命令返回 `-EFAULT`。不能把“有 case 0/1”称为这两条控制命令已支持，也不能给 7 随便返回 0。一个 sysfs `gpsdrv` shim 的文件可读写，并不实现真正电源、休眠和数据传输。

新 prep 对项目已知错误 seed 做保留后处理，未知配置拒绝覆盖；非空受认可配置仍需对应 helper。读取快照中若只有 `z1_mnld` 就明确退出。E-GPS 有 15 项脚本测试，但公共 init、真实设备 major/minor、SELinux domain、ioctl7 都需后续集成。这些安装/准备脚本会改文件、建节点或启服务，**不得放入只读命令区直接运行**。

### 5.1 GNSS 二进制接口调查最终补充

`contract_tests/test_gps_binary_contract.py` 11项主机检查通过。命令7在固定厂商源码中是 `COMBO_IOC_RTC_FLAG`，读取RTC低功耗标志，命令8才是co-clock。helper在0x2dce调用7后比较1，非1分支记日志后仍汇合到 `mtk_gps_mnl_run`；所以缺命令7不等于退出的直接原因，禁止直接补 `return 1`。

更强阻塞是回调ABI：原厂HAL在初始化中复制44字节回调表，0x41d2从callback+0x24读取创建线程函数；实际HIDL的createThread位于+0x20，+0x24是requestUtcTime。原样接入会误调UTC回调且不启动期望线程。位置/状态/NMEA等部分入口看似相同，也不能替代完整payload与生命周期兼容验证。后续需要显式适配表与数据结构，不能仅比较结构size或将stub换成原厂blob。

离线init改名工具已经修复重复mnld/假成功：11项测试通过，处理的是用户指定的离线rc副本；公共init未改，候选system仍含原stub。工具需要主机Python3，不应当作Android端可直接运行的安装器。

## 6. 矩阵实体按键：扫描码、Linux keycode 与 Android 动作

| 字段 | 操作说明 |
|---|---|
| 用途 | 实体方向键、播放/确定、复读/跟读等控制；用户目标是播放=确定、复读=音量−、跟读=音量＋，现有四方向不改 |
| 实际链 | 物理触点 → KPAD 矩阵扫描槽/MSC_SCAN → DTS `linux,keymap` → Linux EV_KEY → InputReader 选择设备 `.kl` → Android keycode → 应用/系统策略 |
| 实施状态 | 非法 WAKE/WAKE_DROPPED 已从 canonical `.kl` 去掉；mainline 打包加入触摸 `.kl`，旧 installer 改取 canonical 文件已有 4 项测试。没有完整“物理标签→event→EV_KEY”采样，不改猜测键码 |
| 已证根因 | AOSP9 flags 只有 VIRTUAL/FUNCTION/GESTURE；v83 `:44285` 明确因 WAKE_DROPPED 拒绝 matrix keylayout。另有 canonical 修完但镜像仍旧/遗漏打包的问题。不能据此断言所有实体键故障同源 |
| 具体文件/测试 | `z1a9_gpt/keylayout/mt6779-keypad.kl`、`device.mk:152–158`、`tests/test_z1_keylayout_packaging.py`；`fork_linux/arch/arm/boot/dts/mediatek/mt6572-z1.dts:785–805`、`drivers/input/keyboard/mt6779-keypad.c`；E-KEY、A-INPUT |
| 故障分支及下一步 | 无设备→probe/clock/IRQ；有 MSC_SCAN 无 EV_KEY→查该槽 DTS；EV_KEY 正确但动作错→实际 `.kl` 和 parser；矩阵与触摸都报158→先分设备；长按不释放→抓 DOWN/UP/REPEAT，不只单次按下 |
| 通过门禁 | 逐实体键按放两次，记录设备名、节点、MSC_SCAN、EV_KEY十六进制/十进制、Android动作；四方向回归；播放/复读/跟读满足用户要求；重启后映射不被旧安装脚本覆盖 |
| 回滚 | canonical `.kl`、打包配置和镜像内文件成套恢复；保留每个原件 hash/权限；不为了回滚把已知非法 flag 重新引入生产候选 |

关键规则：`.kl` 左侧填写 **Linux EV_KEY code**，不是 MSC_SCAN。当前 matrix 文件 `232→DPAD_CENTER`、250/251/252/254 的映射是候选，尚未证明各自对应哪个物理标签。`158→BACK` 只在确证“矩阵设备的该物理跟读键确实发158”后才讨论改动；触摸条的158独立属于 BACK。

当前 DTS 是 8×9、72槽查表；旧 LK 5×3 和“有15按钮”不能替代当前驱动契约。既有审计将原厂解压内核偏移 `0x713348` 的72×u16表与 DTS 的12个非零项对齐；反汇编指针为 `0xc071b348`。本文没有重复反汇编全部表，引用 E-AUDIT 的证据链，不把它升级成物理映射。槽11/27/29是否对应用户按钮必须由 MSC_SCAN 样本证明，禁止直接补占位行。

建议采样记录格式：

| 物理标注 | input设备名/节点 | MSC_SCAN | EV_KEY(hex/dec) | DOWN/UP完整 | 实际Android动作 | 期望动作 |
|---|---|---|---|---|---|---|
| 中间播放 | 待采样 | 待采样 | 待采样 | 待采样 | 待采样 | DPAD_CENTER |
| 复读 | 待采样 | 待采样 | 待采样 | 待采样 | 待采样 | VOLUME_DOWN |
| 跟读 | 待采样 | 待采样 | 待采样 | 待采样 | 待采样 | VOLUME_UP |
| 上/下/左/右 | 分别采样 | 待采样 | 待采样 | 待采样 | 已有行为需保留 | 对应DPAD |

## 7. CST836U 触摸屏与底部导航条

| 字段 | 操作说明 |
|---|---|
| 用途 | 屏幕坐标触摸与底部 BACK/HOME/APP_SWITCH；不是实体复读/跟读键 |
| 实际链 | CST836U I2C0/0x15 → polled `z1_cst836u` → BTN_TOUCH/ABS_X/Y 或导航 EV_KEY → InputReader `.idc/.kl`/INPUT_PROP_DIRECT → Android触摸与导航 |
| 实施状态 | 历史芯片 identity 与输入设备注册已有证据。本轮修总线失败后的完整释放、导航→屏幕事件顺序，增加限量完整帧日志；新 .ko 已构建和 ABI/符号检查，未加载到板 |
| 已证根因 | 原代码50次I2C失败只释放导航键，不释放BTN_TOUCH，可能卡住手势；原屏幕返回帧事件顺序不妥。更早“修ramdisk那份却从system加载旧模块”是部署根因。底部条实际几何未知 |
| 具体文件/测试 | `z1a9_gpt/drivers/z1_cst836u/z1_cst836u.c`、`test_cst836u.py`、`test_factory_cst836u.py`、`keylayout/z1_cst836u.kl`；E-TOUCH；设备 `/bin/z1_touch_load.sh` 与其实际 insmod 参数 |
| 故障分支及下一步 | 无identity→电源/复位/I2C链；模块有但Android不识别→实际模块/INPUT_PROP_DIRECT/布局；屏幕边缘变按键→阈值或翻转；抬手卡住→总线失败/释放；三键乱序→原始坐标与实体位置，不盲改代码 |
| 通过门禁 | 新模块正确路径、hash、vermagic、运行参数；屏幕边角/拖动/抬手、三键按放、屏幕↔键区滑动、错误恢复；实际坐标样本支持阈值；.kl被解析、重启仍正确 |
| 回滚 | 恢复 `/system/lib/modules/z1_cst836u.ko` 的已验版本及布局/参数；若ramdisk加载路径也改过则一起回滚。不能只恢复一份未使用模块 |

新归档模块在 `bridge_backups/products/products_cst836u_committed_20261110_213928/z1_cst836u.ko`，14,604B，SHA256 `255b08bbf6a654b6561c5a9b5ebbc67b1b42ff902f77bd84f2f412de4937b926`。独立记录核了29个导入在目标内核中的定义和导出，vermagic 为 `7.0.0-rc7-z1+ SMP mod_unload ARMv7 p2v8`；反汇编确认 `INPUT_PROP_DIRECT`。CONFIG_MODVERSIONS关闭，因此相同vermagic/符号名仍不能认证未核对的运行内核ABI。工作树原 `.ko` 仍可能是旧 `b48245e3…`，不能随手从源目录拿成品。

源码当前软件阈值 `key_y_min=300`、X分界80/160，分配158/102/139；Android `.kl` 将139映射 APP_SWITCH，所以“Linux KEY_MENU”本身不是必须改成244的充分理由。要核的是最终动作。flip_y会影响/禁用键条逻辑，翻转、缩放与阈值必须一起记录。

原厂报告循环读0x00起15字节，点数来自byte2低4位、点间距6、X/Y为12位；该循环没有单独读按键寄存器。这只限制当前已看到的循环，不能证明整颗芯片没有其他键机制，也不能证明y≥300就是物理条。`log_frames` 捕获现有0x02..0x0e的13字节，包含空闲/释放，不增加I2C读取。启用该参数是设备变更，须在后续授权采样中限量开启、记录原值并恢复；本轮未执行。

## 8. 电源键与 PMIC 父中断

| 字段 | 操作说明 |
|---|---|
| 用途 | 电源键按下/释放、Android屏幕开关；长按关机、休眠唤醒属于额外链路 |
| 实际链 | 实体PWRKEY → MT6323状态/中断 → GPIO7复用PMIC_EINT/EINT16 → pinctrl irqdomain → mt6397 MFD父IRQ → mtk-pmic-keys子IRQ → EV_KEY116 → InputReader/电源策略 |
| 实施状态 | v83 `:1925` 父IRQ46启用、`:1928`输入设备注册。新 `regmap_read` 错误返回防护有主机测试和历史单文件编译结果，未完成实体按键闭环 |
| 已证根因 | 旧 `interrupts-extended` 覆盖普通interrupts导致改错属性；新源码显式EINT16。另一个独立bug是寄存器读取失败仍使用未初始化值上报；已改失败返回IRQ_HANDLED。不能据此宣称电源键已能唤醒 |
| 具体文件/测试 | `fork_linux/drivers/input/keyboard/mtk-pmic-keys.c:205–228`、Z1 DTS `:510–514`、`mt6323.dtsi`；`z1a9_gpt/tests/powerkey/`；E-POWER |
| 故障分支及下一步 | 父IRQ不增长→父链/极性/引脚；父增子不增→PMIC分发/屏蔽；子增无EV_KEY→状态读取/输入；EV_KEY正确无动作→.kl/策略/遮挡；关屏成功不代表可从suspend唤醒 |
| 通过门禁 | 同一boot按放前后 IRQ计数与EV_KEY116成对；无regmap错误导致假按键；Android短按功能验证；长按和睡眠唤醒各自单测 |
| 回滚 | 内核+DTB+ramdisk/配套模块ABI完整恢复；保留父IRQ证据；不引入未集成的轮询fallback，不直接写PMIC寄存器 |

当前有效 DTS 值是 `interrupts-extended = <&pio 16 IRQ_TYPE_LEVEL_HIGH>`；邻近历史注释仍写LOW，**不能照注释改极性**。GPIO7、电气极性、EINT16和Linux IRQ46分别核验，Linux IRQ号随构建变化，不硬编码下一次必须46。

`CHRSTATUS(0x0142)` BIT(1)的按键状态按低有效解释，是读值语义；它不能直接替代外部中断触发极性的验证。已有 `z1_pwrkey_poll/` 草稿也不能当已部署。采样先读 `/proc/interrupts` 与事件；PMIC原始寄存器读取可能具有硬件副作用，不列成默认只读命令。

## 9. 电池、电量算法、充电状态与 health 独立修复

| 字段 | 操作说明 |
|---|---|
| 用途 | 向系统提供电压、容量百分比、充放电状态和健康信息；同时辅助供电故障诊断 |
| 实际链 | 电池端/采样网络 → MT6323 AUXADC → IIO → generic-adc-battery/sysfs power_supply → health HAL/BatteryMonitor → BatteryService/UI；充电控制是另一路策略/驱动，不能混同遥测 |
| 实施状态 | 当前CAPACITY每次重新读瞬时电压查DT表，≥4.2V给100%；health“二次有效读仍报首次0”独立bug已修源码；有有界采样/分析工具与21项主机测试。真实SOC与电池健康未闭环 |
| 已证根因 | 查表饱和可解释软件读数4.28V时100%。AUXADC通道/倍率审计未找到确定错误。真实电池端是否同时到4.28V未知，因此仍须区分采样链与算法问题 |
| 具体文件/测试 | `fork_linux/drivers/power/supply/generic-adc-battery.c:114–141,185–198`、`drivers/iio/adc/mt6323-auxadc.c`、Z1 DTS `:388–447`；`z1a9_gpt/health/capacity_policy.h`/`tests/run_host_tests.sh`；`scripts/diag/z1_battery_sample.sh`/`z1_battery_analyze.py`；E-BATTERY/E-HEALTH |
| 故障分支及下一步 | sysfs缺失→probe依赖/节点；真实端电压与sysfs不符→采样/板级路径；端电压吻合但SOC跳满→瞬时电压当OCV的算法；sysfs正确UI错误→health/单位/旧服务；状态与物理不符→供电关系和充电状态来源 |
| 通过门禁 | 同步记录万用表端电压、软件voltage/capacity/status/online、插线事件与负载；解释量纲和数据缺失；真实校准与独立health回归；新服务目标构建及运行身份核验 |
| 回滚 | health服务与配置可独立回滚；电量算法/DT/内核与充电控制变更分别记录，恢复完整对应组合。禁止以固定100%、固定电压或强开充电掩盖失败 |

### 9.1 量纲与为什么4.28V不证明健康

Linux `voltage_now`单位µV，`capacity`为0–100%，`temp`一般为0.1°C；`_NOW`是瞬时值，`VOLTAGE_OCV`才是开路电压属性，`HEALTH`又是独立状态。[K-POWER] AOSP BatteryMonitor会将sysfs电压除1000转为mV。[A-HEALTH] 必须写明采样来自哪个供电节点，不能把充电器输入电压当电池端电压。

`mainline_recon/z1_boot_mainline_20261108_105346.log:230` 内层uptime15.8s电压3354000，`:247`为charging ENABLED，`:332`为31.2s电压4287000。它证明软件输入随充电阶段变化且越过表顶；不能证明真实端电压相同，也不能独自证明电池满、衰老、过充或板子故障的唯一原因。**4.28V不证明健康**，`Discharging`也不能证明一定没有电流流入。

旧文档以高电压/USBError推断“已排除软件、一定电池耗尽/线缆坏”的结论不采用。先保留连接方式、时间、DA阶段、完整返回码与读回证据，再分别验证。

### 9.2 health修复与仍缺的目标构建

`health/capacity_policy.h`现在将通过校验的那份样本写回batteryLevel；拒绝超范围、截断、嵌入NUL和尾部垃圾，未知容量与真实0%区分。该修复没有 `online→100` 逻辑，因此不是插电100%的算法修复。

已有交接记录报告1600组组合主机测试通过；本文核到头文件修复与测试入口，但没有重跑该测试。独立目标语法检查记录仍因 `health2/service.h`、随后 `batteryservice/BatteryService.h` 等真实AOSP构建头依赖不足失败，不能称新health服务已产出。应在完整AOSP环境、正确 `Z1_KERNEL_VARIANT=mainline` 下构建并记录依赖、ELF与哈希；默认stock产品可能剔除此服务。v35基线中的旧health ELF为 `667c104bcc65eac3bbb0a5128d43fbbc92abd606f6acb379e3a5217db7e664ce`。

### 9.3 当前温度和容量曲线也有占位

读取到 `generic-adc-battery.c:171–184` 的TEMP分支固定返回250，即25.0°C；源码说明缺本机NTC标定。DTS容量2,000,000µAh和曲线也标TBC。**温度显示25°C不是实测温度，不能证明充电热保护有效**。要做真实容量/健康估算，需本机OCV-SOC、温度/内阻、Qmax或经过验证的电量计方案；原厂有软件fuel-gauge实现并不证明当前Z1实际执行该分支，不能直接搬策略。原厂某初始化暂停充电的动作属于控制变更，本轮不执行。

### 9.4 有界采样工具用途

`z1_battery_sample.sh`有样本数、总时限、逐文件时限和最多供电设备数；读`/proc/uptime`用于boot相对时间，包含suspend，不能冒称严格等于CLOCK_MONOTONIC。它只读取sysfs但驱动读取本身可能触发ADC采样；这仍需设备采样授权。分析器报告趋势/缺失/online转换，不能据此自动判断电池健康或校准正确。E-BATTERY归档21项测试只证明解析和失败处理。

## 10. 音频播放、3.5mm与录音

| 字段 | 操作说明 |
|---|---|
| 用途 | 扬声器/3.5mm耳机播放、音量与插拔路由、麦克风录音；BT SCO和FM各有额外链路 |
| 实际链 | 应用AudioTrack/AudioRecord → AudioFlinger/AudioPolicy → audio HIDL/legacy primary HAL → tinyalsa/ALSA PCM+mixer → MT6572 AFE/machine → MT6323 codec/ACCDET/放大器 → 耳机、扬声器、麦克风 |
| 实施状态 | 本轮primary播放HAL改为失败后持续有界重试，硬件master volume返回不支持以保留软件音量。既有6类mock与目标ELF记录；3.5mm听音未验。输入入口仍明确`-ENOSYS`，Android录音未实现 |
| 已证根因 | 旧open_fails=1后退避分支不再更新导致永不重试；set/get_master_volume假成功影响上层软件增益；这些不是所有无声/耳机/录音问题的总根因 |
| 具体文件/测试 | `z1a9_gpt/scripts/r5g_audio/audio_primary_z1_poc.c:196–230,310–316,394–398`、`tests/`；`fork_linux/sound/soc/mediatek/mt6572/{mt6572-afe-pcm.c,mt6572-mt6323.c}`、`sound/soc/codecs/{mt6323.c,mt6323-accdet.c}`；E-AUDIO |
| 故障分支及下一步 | 无声卡→模块/DT/时钟/codec；PCM打开失败→实际card/device/格式；PCM推进无声→mixer/DAPM/放大器/耳机路由；插拔不变→ACCDET/PMICIRQ/input-jack；底层capture有数据但App失败→输入HAL/policy；全零录音→真实采样源/ADC/MICBIAS |
| 通过门禁 | 实际HAL maps/hash；声卡迟到后能恢复；低音量左右声道/单声道、静音/音量、插拔、播放停止重开；录音单独核ALSA有效样本与Android AudioRecord、时长/采样率/声道/非全零及无泄漏 |
| 回滚 | audio HAL、tinyalsa依赖、policy、mixer初始路由、内核codec/AFE/machine/ACCDET模块成套恢复；保存用户音量与插拔状态，不能只换一份库 |

新HAL用独立 `open_retry_wait`，一次失败后跳过9次write、在下一轮尝试，不靠失败计数无限停留。候选SHA256 `d037d76ca6ab8231169647fcf39cb46c76ffb098f0059efa7f226a392603813a`；归档记录依赖libtinyalsa/liblog/libm/libdl/libc，19个未定义命名符号不变，ARM softfp、无TEXTREL。该记录显式 `android_runtime_tested=false`、`audio_listening_tested=false`、`capture_implemented=false`。[A-AUDIO]

**3.5mm不是一个布尔开关**：耳机输出、插拔检测、耳机麦克风、按键、插头标准可能分别成功/失败。先确定本机插座触点/支持范围，不把某个ALSA Jack注册当作全部支持。设备无指示灯是用户事实，不列为故障。

内核已有AWB capture/mic/FM相关代码和源选择，但当前HAL不开放输入。不能因有 `pcmC*D*c` 节点就称“录音完成”。录音前置检查工具现已完成，主会话补修O_PATH固定inode后39项测试通过；它不执行录音。codec缺MIC Route→AIF1 Capture信号边、capture对AUDCLK依赖不足，须先核实再修生产路由。MIC实际支持8000/16000/32000/48000Hz、S16_LE、1–2声道，policy中额外频率不能视为已支持。

未来只读起点是 `/proc/asound/cards`、`/proc/asound/pcm`、`/dev/snd`和 `dumpsys media.audio_flinger`/policy。`tinymix`无参数列控件与带参数写控件应严格区分；历史不存在的 `tinymix set/controls` 用法不要复用。播放/录音、路由切换均属主动测试，不在本文自动执行。

## 11. TF/microSD：卡槽存在，Z1接线仍未证

| 字段 | 操作说明 |
|---|---|
| 用途 | 可移除存储识别、文件访问、卸载与拔插；不涉及修改eMMC配置 |
| 实际链 | 卡槽供电/VMMC/VQMMC+CLK/CMD/DAT/CD → 正确MSDC host/pinctrl → MMC/SD枚举/block → 分区/文件系统 → vold的removable配置 → Android存储界面 |
| 实施状态 | **用户已确认TF槽存在**。当前板DT只见mmc0的eMMC配置，mmc1基础节点disabled、缺Z1启用/引脚/供电/CD与vold配套；有被动元数据与证据清单工具。没有板端识卡证据 |
| 已证根因 | 配置缺口是源码事实，不是接线结论；不能据“没枚举”判无槽，也不能据同族板DTS判Z1接线 |
| 具体文件/测试 | `fork_linux/arch/arm/boot/dts/mediatek/{mt6572.dtsi,mt6572-z1.dts}`；`z1a9_gpt/rootdir/fstab.z1`；`scripts/storage/z1_tf_detect.sh`、`z1_tf_check_prerequisites.py`、example JSON；`tests/test_z1_tf_storage.py`；A-VOLD |
| 故障分支及下一步 | 无host→核DT/clock/pinctrl；host有无card→供电/CD/pins/协议；card有无block→读卡/错误；block有无volume→fstab/vold/fs；volume有App无权限→存储框架/标签。每层先只读取证 |
| 通过门禁 | Z1本机host映射、pinmux、VMMC/VQMMC电压/电源轨、CD极性/替代检测证据齐全；单独审核DT候选；专用卡只读识别与现有文件校验；后续获准后仅专用测试文件写入/卸载/重插 |
| 回滚 | 恢复原DTB、removable fstab/vold配置和用户挂载状态；不格式化原卡、不更动mmc0 eMMC。若动供电配置需恢复原调控关系 |

同族 `mt6572-jty-d101.dts` 的GPIO91–96、VMCH/VMC只是研究线索；MC1_INS可能有多个复用候选，均不能照搬。`broken-cd`仅改变检测策略，不证明电气连接正确。host编号/mmcblk编号不稳定，按sysfs关联和type识别；`SD`证明卡被枚举，仍不自动证明这是用户所指插槽。

`z1_tf_detect.sh --root SNAPSHOT`默认要求显式离线数据源；`--live`才读设备。它不mount、不打开块设备、不写GPIO、不调用vdc/dumpsys。退出0表示报告生成，unknown不是不存在。清单checker返回documented只证明给出的文件/哈希齐全，**不能证明这些证据的技术判断正确或准许刷写**。

## 12. USB：ACM、ADB、MTP与OTG是四个任务

### 12.1 ACM诊断串口

| 字段 | 操作说明 |
|---|---|
| 用途 | Z1作为USB device向PC提供串口诊断功能；不同于外接UART桥 |
| 实际链 | PHY → MUSB peripheral UDC → configfs gadget/config/function ACM → UDC绑定 → 设备ttyGS0 → PC枚举CDC ACM/主机tty节点 → 字节传输 |
| 实施状态 | v83 `:1955`有MUSB probe，`:2181`有ACM bound，`:2184–2186`有ttyGS0创建记录；仅证明到设备侧绑定/节点，未证明本轮主机收发 |
| 已证根因 | 旧历史“g_serial ready但未绑定”不代表现版仍失败；反之bound也不保证电缆/主机枚举。没有新的收发根因结论 |
| 具体文件/测试 | `z1a9_gpt/rootdir/init.z1.rc:41–53`、`z1_bootdiag --acm`相关源码与测试；Z1 DTS `dr_mode=peripheral`；E-LOG、K-USB |
| 故障分支及下一步 | 无UDC→PHY/MUSB/DT；有UDC未bound→gadget配置/所有权；bound无PC枚举→物理连接与host日志；枚举无字节→端口识别/进程占用/实际诊断协议 |
| 通过门禁 | 稳定boot后授权接线、确认Z1端口而非桥/其他ESP；host枚举与双向有界数据校验，断线重连无竞争 |
| 回滚 | 保持已验收的ACM owner/configfs/rc组合；撤销候选时恢复该组合而非同时启动两个owner |

设备USB插线可能影响preloader/LK启动模式；外接C3桥USB和Z1下载USB是不同连接。不要自动枚举后向任意 `/dev/ttyACM*` 发命令，也不把PC的ttyACM0与Z1的ttyGS0等同。先用户确认设备和工作模式，本轮没有探测端口。

### 12.2 ADB

| 字段 | 操作说明 |
|---|---|
| 用途 | 已授权调试、文件传输和shell；必须具备真实adbd服务与鉴权 |
| 实际链 | init服务定义/认证socket → FunctionFS `ffs.adb`与mount → adbd写描述符、ffs.ready → configfs链接ACM+ADB → 单一UDC绑定 → PC授权与ADB协议 |
| 实施状态 | v35/v83基线审计未找到完整adbd服务/USB rc链。22:06读取到opt-in候选rc、device脚本和离线predicate checker，明确NOT_DEPLOYED、未被device.mk导入。它们不是当前可用ADB |
| 已证根因 | 有adbd文件/属性动作不等于有service；当前主链强制sys.usb.config=none，由z1_acm_log独占ACM。缺少服务/FunctionFS/时序不能只靠setprop修好 |
| 具体文件/测试 | `z1a9_gpt/scripts/usb/{init.z1.acm_adb.candidate.rc,z1_usb_candidate.sh,check_z1_usb_candidate.py}`、`tests/test_z1_usb_candidate.py`；A-USB、K-USB |
| 故障分支及下一步 | 无service→先独立镜像集成；ffs.ready不出→adbd/FunctionFS/权限；UDC被占→旧owner未停；鉴权失败→正常授权流程；不能通过开启网络ADB或关闭鉴权绕过 |
| 通过门禁 | 独立候选preflight、明确opt-in、ro.secure/ro.adb.secure符合候选契约、enforcing domain已审核、唯一MUSB owner、ffs.ready正确顺序；host授权后功能与ACM共存验证 |
| 回滚 | 后续受控测试中仅清理候选gadget/服务并恢复原ACM owner及导入关系；先保留原rc/属性快照；本轮不触发prepare/bind/stop |

AOSP r61的顺序是准备FunctionFS、启动adbd、等待描述符ready后绑定。[A-USB] 不能把“ep0必须已存在”设为启动adbd前提而制造依赖环。候选要求v35/v83缺服务时失败，离线 `OFFLINE_PREDICATES_PASS` 仅说明提交的状态满足检查，不证明内核枚举、SELinux策略或host授权已经成功。

### 12.3 MTP与OTG

| 字段 | MTP文件传输 | OTG/USB host |
|---|---|---|
| 用途 | 通过媒体对象协议访问Android共享存储 | Z1主动连接外设、供电和枚举 |
| 实际链 | framework USB/媒体服务 → MTP function（实际内核可为mtp.gs0或其他后端）→ configfs/UDC → PC MTP客户端 | ID/role/VBUS能力 → PHY/controller host配置 → hub/USB class driver → 外设 |
| 实施状态 | 当前未集成完整MTP链；不能把/bin/mtpd当MTP | 当前DTS强制peripheral，现配置未验host；不能称硬件永久不支持 |
| 已证根因 | 缺完整服务/function/存储策略；没有端到端传输证据 | 软件角色配置不提供host；板上VBUS供电/ID接线未知 |
| 文件/测试 | rootdir USB配置、USB候选、A-USB；当前候选仅ACM+ADB | Z1 DTS、内核.config、MUSB驱动；无本轮host测试 |
| 分支及下一步 | 先确认内核后端与framework激活、共享存储权限，再单独设计MTP | 先核Z1接线/供电/保护/role，再决定隔离候选；不能仅翻dr_mode |
| 通过门禁 | 枚举、列目录、专用测试文件往返hash、断线重连；保护私人文件 | 硬件条件明确、供电安全、外设枚举/断开，不能损坏现有device模式 |
| 回滚 | 恢复原USB functions/服务及存储策略，不自动暴露mass storage | 完整恢复peripheral DT/config/电源关系；本轮不提供寄存器写或启VBUS命令 |

MTP并不必然要求 `ffs.mtp`；AOSP r61配置中有 `mtp.gs0`。判断未实现应看整条配置和服务链，不以缺某一个固定目录名作为唯一证据。

## 13. 亮度与熄屏

| 字段 | 操作说明 |
|---|---|
| 用途 | 亮度滑条、真正背光亮灭、屏幕OFF/ON时恢复用户亮度 |
| 实际链 | Settings/DisplayPowerController → Light HAL → sysfs亮度 → leds-mt6323 ISINK → 背光；HWC power mode控制呈现是另一条路径 |
| 实施状态 | DT包含lcd-backlight/ISINK0、驱动mt6323 max_brightness=6；旧powerux补丁/工具存在，新被动inspector已出现。当前HWC2 setPowerMode只更新powered_on并停止present，未在该函数关闭背光；物理亮度闭环未验 |
| 已证根因 | “停present”不等于“灭背光”；旧脚本会写亮度而非纯检查。是否存在HAL缩放/权限/trigger竞争需实际采样，不能预设 |
| 具体文件/测试 | Z1 DTS `:908–922`、`fork_linux/drivers/leds/leds-mt6323.c`；`z1hwc2.cpp:2032–2044`；`scripts/display_check/inspect_backlight.sh`/`test_inspector.py`/`source_provenance.json`；A-LIGHT |
| 故障分支及下一步 | 无provider→DT/driver；HAL写失败→DAC/SELinux；值变光不变→provider/硬件；低档全黑→检查映射是否floor(b×6/255)；resume亮度错→HAL/HWC/LED trigger多方覆盖 |
| 通过门禁 | 正确provider与range、原值/trigger留存；用户滑条与可见亮度相关、0关闭、非零策略明确；OFF/ON恢复同值，不能只靠sysfs读回；另测真suspend |
| 回滚 | 恢复用户亮度、自动亮度、trigger、HAL与HWC配套；旧脚本改过timeout/stay_on则恢复原值，不随意写固定值 |

读取数值只证明sysfs报告，不能证明面板实际发光。用户已确认没有指示灯；`lcd-backlight`属于背光，不能由LED class推断有通知LED。若最大值6，线性向下取整可能让1–42映射到0，这只是需要验证的条件分支，不是已观察的bug。

只读首选新 `inspect_backlight.sh --root SNAPSHOT`；设备`--root /`必须后续明确授权。不要运行旧 `scripts/powerux/light-hal/powerux_check.sh`：它会写0/1/6；`screen_off_test.sh`还会改screen_off_timeout、stay_on_while_plugged_in并发SLEEP/WAKEUP。历史针对其他composer的补丁存在，不证明当前z1hwc2二进制应用了它。

## 14. 睡眠、省电与唤醒

| 字段 | 操作说明 |
|---|---|
| 用途 | 屏幕关闭后减少功耗、可靠恢复显示/输入/无线；区别于只是停止绘图 |
| 实际链 | PowerManager/Power HAL interactive状态 → wakelock/suspend策略 → 内核PM suspend/resume → IRQ wake source/regulator/clock状态 → 各驱动恢复 → UI |
| 实施状态 | `rootdir/init.z1.rc:67–74`明确保留诊断不睡眠路径并写z1diag wake_lock。实际是否生效需运行证据；未取得可用唤醒源和整机suspend回归。当前不能宣称省电完成 |
| 已证根因 | 现有配置有意阻止/回避睡眠；旧注释与静态画面不能证明当前进入过suspend。无新板端故障归因 |
| 具体文件/测试 | `rootdir/init.z1.rc`、PMIC keys/rtc、当前HWC power mode；A-POWER；旧powerux工具只作只读资料 |
| 故障分支及下一步 | 不sleep→wakelock/策略；sleep不醒→wake IRQ/PMIC；醒但黑屏→显示/背光恢复；USB断掉→gadget resume；无线掉线→各transport PM；按层分离 |
| 通过门禁 | 先建立已验证唤醒/恢复路径；保留日志，单独批准真suspend测试；内核suspend/resume时间、功耗、输入/显示/音频/无线恢复共同验收 |
| 回滚 | 恢复已知诊断wake-lock/电源配置和完整boot组合；不能在没有恢复通道时释放wake lock或直接写/sys/power/state |

AOSP `setInteractive(false)`表示交互状态变化，不保证系统进入suspend。[A-POWER] “屏幕黑了”“RTC有节点”“电源键设备已注册”都不足以解除本节门禁。先完成屏幕OFF/ON而保持现有wake lock，再独立设计睡眠测试。

## 15. RTC、系统时间与闹钟

| 字段 | 操作说明 |
|---|---|
| 用途 | RTC持时、系统时间来源、Android闹钟、休眠唤醒；关机闹钟另需bootloader/PMIC支持 |
| 实际链 | MT6323 RTC → rtc-mt6397/MFD IRQ → rtc0/timekeeping；Android AlarmManager → timerfd/alarmtimer → 内核时钟与唤醒，必要时RTC |
| 实施状态 | v83 `:1926`注册rtc0；内核驱动有alarm与wakeup实现。没有本轮走时、断电保持、闹钟IRQ或suspend唤醒证据 |
| 已证根因 | 不将注册成功等同闹钟成功。旧stock timerfd fallback是framework syscall兼容修复，测试明确不覆盖内核权限/睡眠 |
| 具体文件/测试 | `fork_linux/drivers/rtc/rtc-mt6397.c`；`z1a9_gpt/tests/test_z1_alarm_timerfd_fallback.py`；本地 `android9/los16/frameworks/base/services/core/jni/com_android_server_AlarmManagerService.cpp` |
| 故障分支及下一步 | 时间明显错→RTC读取/系统设置来源；UI闹钟不响→AlarmManager/应用权限；awake正常suspend失败→wake IRQ/RTC；关机不启动→独立bootloader链。不要将系统日期错误直接归因RTC坏 |
| 通过门禁 | 被动两次读取走时一致；awake闹钟、获准后suspend唤醒分别验；校时/写alarm为主动操作，先保存状态 |
| 回滚 | 恢复时间/闹钟配置和内核/framework配套；不清RTC寄存器、不默认写wakealarm |

该timerfd测试只针对CLOCK_BOOTTIME在EINVAL时的fallback，其他错误不得隐藏。本文未运行它；不能把历史fixture PASS当本轮主线系统RTC通过。

## 16. FM收音

| 字段 | 操作说明 |
|---|---|
| 用途 | 接收FM电台、调谐、音频播放；RDS和录制需要另验 |
| 实际链 | FM app/厂商接口 → `/dev/fm`/FM ioctl → WMT-STP/CONNSYS FM → 数字音频I2S/AFE ASRC与FM mixer → codec/耳机或扬声器；AWB可作为独立capture路径 |
| 实施状态 | v83 `:2892`insmod OK、`:2911`/dev/fm ready、`:2966`mixer touched；仅证明模块/节点/初始化。没有本轮调谐、收听、RDS或录音验收 |
| 已证根因 | 历史loader引用不存在的/lib/modules路径，已改/system/lib/modules；脚本还会在部分失败时exit0，不能用退出码0当成功 |
| 具体文件/测试 | `z1a9_gpt/rootdir/z1_fm.rc`、`load_fm.sh`；`fork_linux/drivers/misc/mediatek/connectivity/fmradio/`及`PORTING_NOTES.md`；AFE/machine源码 |
| 故障分支及下一步 | 无/dev/fm→模块/WMT/路径；能open不能tune→ABI/firmware；tune有信号无声→FM mixer/ASRC/输出；无信号→天线/现场条件/射频，不能仅以噪声判断驱动 |
| 通过门禁 | 真实有效频率/状态、可辨听音、停止释放、与普通播放切换；RDS/录制单独记录；音频增益受控 |
| 回滚 | 恢复FM模块/loader与音频路由、原音量；不在本轮启动loader、调频或写mixer |

源码注释给出了FM-I2S路径，但芯片型号称呼在历史资料中混有MT6620/MT6627/SoC集成描述；本节不以名字推断Z1射频板的全部实现。先按实际加载模块与板级证据继续。`load_fm.sh`会加载模块、改权限和写FM音量，不是只读诊断命令。

### 16.1 FM 调查最终补充与最短落地路线

本地驱动有真实WMT/STP收发、上下电和动态字符设备分配，不是整层空桩。v35所查模块244596B、SHA `0c25055e8fdc6fa3eb8d6606a46e741afd474a57c8ffc19bbcf968c60f3e8014` 与树内产物一致；历史日志仅确认insmod和节点创建，未有本轮调台/听音。

必须纠正旧FM文档：`radio.fm.mt6572.so`确有动态符号HMI。AOSP r61将 `HAL_MODULE_INFO_SYM` 定义为HMI；搜宏字面量得不到符号不能判无入口。候选HAL42,628B、SHA `9a7487dbe01a276beb8f17436ebacd4cfbb827daa72079cf7aba2d8ba5c92417`，id=radio，tag正确；这些仅是静态子检查，未证明A9成功打开。

1. v35限定路径缺配套FM app/JNI/配置和预期firmware。v44在 `/lib/firmware` 有v1备料，但历史boot的 `firmware_class.path=/system/etc/firmware`；必须按实际配对验证可达路径，不能把某目录找不到概括成整镜像没有。
2. v1 patch/coeff为1028/1092B，v2–v5有0B或2B占位。根据真实ROM版本选有效固件，不随意改名冒充其他版本；CONFIG_FW_LOADER_USER_HELPER未开时不能期待ueventd补救。
3. 旧JNI调频请求是6B、频率offset4；内核按8B、offset6读取且用数值猜旧格式。尾部碰巧落入合法频率时会误调台，返回copy还可破坏旧布局。应明确统一ABI，不能只加模糊fallback。
4. 旧SEEK/SCAN ioctl无case返回错误，但高层还有逐频软件搜索；不可断言所有搜台未实现。先确认应用走哪条调用链。
5. loader尝试设FM音量0且忽略错误，不开FM Playback Switch。AFE有I2S/ASRC/FM路由，但活动DL1非44.1kHz时可能EBUSY；普通播放默认48kHz，需处理共享路由与恢复。

后续顺序：确认模块/ROM/有效固件路径 → 明确调频ABI → 选择JNI或broadcastradio一条控制链 → open/powerup/tune及状态 → 兼容采样率下路由和低音量听音 → 搜台/RDS/退出恢复。没有本轮收音验收，禁止只装APK就写成功。

## 17. 内部存储、/data、FUSE与启动持久化

| 字段 | 操作说明 |
|---|---|
| 用途 | system只读系统文件、cache/data持久化、应用启动缓存与共享存储；与TF分开 |
| 实际链 | eMMC/mmc0 → 实际分区表与block → ext4/fs_mgr/fstab → /system /cache /data → vold/FUSE → Android用户存储；boot ramdisk可覆盖仓库fstab和脚本 |
| 实施状态 | v83 `:2207`运行p5 e2fsck、`:2232`附近为p6；FUSE替代sdcardfs有实现/主机测试背景，板端挂载卸载仍待确认。读取的仓库rootdir/fstab.z1本身尚无check，与历史boot反解内容不同 |
| 已证根因 | 历史/data ext4错误→journal abort→只读→zygote/dalvik-cache写失败，是实测启动阻塞链。不能看到开机慢就认定全是GPU或dexopt；当前数据分区状态未采样 |
| 具体文件/测试 | `z1a9_gpt/rootdir/fstab.z1`、device.mk FUSE配置、`tests/test_z1_stock_partitions.py`、fs_mgr相关产品补丁；E-LOG、E-AUDIT；最终boot反解为准 |
| 故障分支及下一步 | 分区设备不符→先核分区表/容量；mount失败→fstab/fs；只读→保留ext4日志，不强行remount；data可写zygote仍失败→权限/SELinux/boot.art ABI；内部共享存储缺→vold/FUSE挂载时序 |
| 通过门禁 | 离线镜像完整性、正确分区几何；设备mount来源/选项与data持久化；重启后专用测试文件保留、应用启动、共享存储权限/卸载；修复文件系统属于另行批准的写操作 |
| 回滚 | boot/system配对恢复不能恢复已改变/data；先保留用户data备份/恢复策略。禁止格式化data、整盘覆写或在已挂载文件系统运行修复 |

历史p4=system900MiB、p5=cache、p6=data的约定必须对将操作的设备重新确认。boot“有check标志”还需要镜像内实际e2fsck可执行、运行时路径与结果；v83日志也有system初挂载阶段未找到e2fsck的记录，不能把所有分区都称检查完成。

开机慢需先分阶段计时：内核/init、fsck、zygote、dex2oat、PMS、boot_completed。`arm.noimage`、boot.art/oat的历史处理有互相冲突记录，恢复目录名可能触发不同ABI/指令问题；不要仅凭“文件完整”就启用。二屏动画正常也不证明framework已进入桌面。用户数据、应用缓存或数据库变化可能让“只回滚boot/system”仍不能回到原运行状态。

## 18. boot/system版本身份、候选构建与完整回滚

### 18.1 本次可核的基线身份

| 对象 | 路径（项目根相对） | SHA256 / 状态 |
|---|---|---|
| 历史r14 boot | `z1a9_gpt/releases/ovl_v83_r14/boot.img` | `6c85b6a0a705f75401ed0b7867b1dd5d80ca30bcc7d93b47b182a5bbf73e0af4`；5,900,288B，本次读字节核hash |
| 历史v79 boot | `out/z1_v79/boot.img` | `d332b5e962438b8f07efa22ebb785f895516454fb3cc8548990d11c75bf0fc84`；5,808,128B，本次核hash |
| 原厂boot单件 | `1/boot.img` | `2dd41702e839ec2547ce0dea2bbd3603a5d019786502d939ebba4a6b578eaab5`；6,291,456B，本次核hash；不代表完整原厂系统 |
| 本轮system输入基线 | `out/z1_v35_selftest/system_v35.img` | 主会话实际重算943,718,400B、`f180dac2dfddef70a6783937c099394b160a66485ff22c6e9a160eb738afd6df`，e2fsck_rc=0、board_verified=false；构建再次确认基底未变 |
| 进行中状态 | `out/z1_driver_candidates_20261110/session_state.json` | 读取时IN_PROGRESS_NO_DEPLOYMENT、hardware_executed=false；不能当最终构建manifest |

**r14/v79非完整回滚包。** 即便boot曾成功，也必须固定当时system、Mesa/gralloc/HWC、所有模块、属性、持久installer和/data相关状态才有可重复性。路径`out/z1_v83/boot.img`历史被r14/r16等覆盖过，不按目录名猜版本。

### 18.2 功能表：版本与回滚本身也要验收

| 字段 | 操作说明 |
|---|---|
| 用途 | 确保测试的是预期字节，失败后能回到已验证组合 |
| 实际链 | 源hash/commit → 编译器和命令 → ELF/ko → 打包文件与元数据 → boot/system镜像hash → 写入读回 → 设备实际mount/maps/模块 → 功能证据 |
| 实施状态 | 有历史候选与新产物归档；builder已通过27项及独立故障注入，六文件副本已生成。最终manifest和边界见§22 |
| 已证根因 | 同名镜像被重建、system/ramdisk多份模块、持久active和install.sh覆盖，使“换了boot”不等于换了实际HAL；刷写脚本失败末尾nothing written可能误导 |
| 具体文件/测试 | `z1a9_gpt/scripts/release/z1_build_driver_candidate.py`、`tests/test_z1_driver_candidate.py`；`scripts/z1deploy/{z1stage_boot.sh,z1rootd.sh,z1_install_hal.sh,z1push.sh,z1flash_image.sh}` |
| 故障分支及下一步 | hash不符→停止使用该身份；新文件未加载→查优先级/maps；重启又变旧→持久installer；写请求后超时→视为可能部分写，完整读回判定；RC丢失→读输出中的###RC |
| 通过门禁 | §18.4完整冻结；新镜像反解逐文件hash/权限/标签；读回与运行身份一致；一次受控回滚并重启确认不被覆盖；所有功能记录同一组合ID |
| 回滚 | 按已审核全套恢复，不靠仅boot或单个disable标记；任何需要硬件写入的步骤由主会话独立门禁决定，本文没有可直接执行刷写命令 |

### 18.3 持久覆盖优先级必须一起回滚

`z1stage_boot.sh`的STAGE是 `/data/local/tmp/z1stage`，目标是 `/vendor/lib/hw`；其逻辑优先级为：

1. `.disable_hwcomposer`/`.disable_gralloc`存在时删除相应vendor覆盖；只影响对应模块。
2. 否则 `active/<name>` 优先。
3. 否则采用ramdisk根目录的候选文件。
4. 二者都无时才移除vendor覆盖以回到其他搜索路径。

`z1rootd.sh`启动时还会执行持久 `install.sh`。`z1_install_hal.sh rollback`并不自动建立disable标记，下次启动ramdisk可能重新安装。`.disable_hwcomposer`不移除Mesa，不恢复gralloc，也不保证剩余组合相容。删除库必须配合已知完整候选，不把“能fallback”当可用性证据。

`z1push.sh --rootcmd`曾只检查`###DONE`而忽略`###RC`；拿到输出后必须核真实远端退出码和读回。`z1flash_image.sh`在发写请求后的某些失败路径最终也可能输出nothing written；它不是未写入的证明。USBError/DA stage2失败、换线后成功只支持相关性，不排除软件或部分写。

### 18.4 完整回滚冻结清单

| 层 | 必留内容 | 验证方式 |
|---|---|---|
| boot | boot原件、size/hash、反解kernel/DTB/ramdisk/default.prop/fstab/rc、内核配置与版本 | 容器结构、载荷身份、≤6MiB；保留构建命令与反解结果 |
| system/vendor | 整镜像hash、base hash、replacement manifest、文件权限/owner/group/xattrs | e2fsck离线结果、逐文件dump/hash、SELinux标签 |
| 图形 | EGL/Mesa双库、libdrm依赖、gralloc/HWC、属性、加载路径 | readelf/SONAME/NEEDED、进程maps与文件hash |
| 驱动 | ko实际路径、vermagic、依赖/导出符号、对应kernel ABI | modinfo/nm/Module.symvers与运行加载来源 |
| 持久覆盖 | stage active、install.sh、disable标记、其他bind mount/installer | 内容hash、缺失/存在状态、启动顺序与实际执行日志 |
| /data | 用户数据保护/备份策略、持久属性、应用缓存/数据库兼容范围 | 不能用重刷system代替；先确认本轮是否改变数据 |
| 硬件写入 | 分区名/offset/大小、写入请求、结果、完整读回hash | 任何不确定失败均按可能部分写处理 |
| 最终运行 | boot ID/uptime、实际构建信息、mounts/maps/模块/功能日志 | 回滚后重启再次采样，证明没有被持久脚本再覆盖 |

打包仍须遵守MTK容器实际约定：kernel/ramdisk大小含各自MTK512字节头、appended DTB位置与当前LK兼容；旧资料中独立`--dt`和appended处理有不同阶段，使用当前已审计pack工具并反解核对，不从历史命令机械拼接。新ramdisk文件必须有正确类型位/mode；改了rc、ko、prop或脚本就重新打包并反解，不能沿用旧rootfs再称“已包含”。

候选builder的默认inspect也会使用临时提取文件和e2fsck检查，所以它是**离线检查、会产生临时文件**，不是零写入的纯读命令。主会话已在独立复核通过后实际执行inspect/build，结果见§22。它验base/replacement/original hash和元数据，生成副本，不代表device运行或完整回滚验证。

## 19. SELinux、权限与运行路径

| 字段 | 操作说明 |
|---|---|
| 用途 | 让正确服务以预期身份访问节点/文件，避免permissive掩盖集成缺口 |
| 实际链 | init service user/group/seclabel → 进程domain → 文件path与xattr label → SELinux type规则和DAC mode → ioctl/socket/sysfs操作 |
| 实施状态 | v83日志多处permissive=1；源码file_contexts含health执行文件与/dev/dri规则，但不能代表镜像所有路径已标好。新USB候选要求独立enforcing domain，当前不可默认满足 |
| 已证根因 | 历史某些APK/dir unlabeled；路径复制/改名/镜像注入可丢标签。是否造成当前某功能失败必须看具体AVC和DAC，不靠全局放宽判断 |
| 具体文件/测试 | `z1a9_gpt/sepolicy/vendor/file_contexts`；init rc；候选builder metadata检查；base manifest各metadata_*.txt；设备ls -lZ、getenforce、目标AVC |
| 故障分支及下一步 | ENOENT先查路径/mount而非SELinux；EACCES查uid/gid/mode与AVC；无AVC不证明DAC正确；permissive下成功不能证明enforcing；旧进程maps可能仍加载旧inode |
| 通过门禁 | 预期文件标签、实际服务domain、必要最小权限、无相关拒绝；完整产品策略编译与实际模式验证；不能用chmod666/seclabel init/关闭enforcing作为最终修复 |
| 回滚 | 恢复文件xattr/mode/owner、服务定义和策略配套；禁止只复制字节忽略标签；不在本轮运行restorecon/setenforce/chmod |

具体路径应区分三层：镜像内部路径、设备经symlink/mount看到的路径、进程maps实际映射的inode。health规则已覆盖`/(vendor|system/vendor)/bin/hw/...`，图形节点规则针对`/dev/dri`；这不是所有HAL/ko/脚本的完整标签声明。离线 `debugfs stat/ea_list` 与运行 `ls -lZ` 要能对上。公开交接不携带完整私人logcat、NVRAM、SSID、地址或认证材料。

## 20. 官方原始资料与适用版本

以下资料用于核对接口契约，不证明Z1已实现。Android源码固定android-9.0.0_r61；Linux文档按接口语义使用，板上实现仍以fork_linux实际源码为准。Mesa滚动文档不作为最新版本推荐。下载失败记录保留，不把镜像副本当第二个独立实现。

| 文中编号 | 原始来源与核对内容 |
|---|---|
| M-LIMA26 / M-LIMA24 | https://docs.mesa3d.org/drivers/lima.html — Mali400/450、GLES2目标、ES1.x部分支持、渲染与显示分离；这些编号不是两份独立规范 |
| K-DMA / K-DMA-UAPI | https://docs.kernel.org/driver-api/dma-buf.html — CPU cache同步、dma_fence/dma_resv；本地include/uapi/linux/dma-buf.h核对实际ioctl |
| K-SYNC | https://www.kernel.org/doc/html/v6.12/gpu/drm-kms.html — Explicit Fencing Properties与flip_done |
| K-POWER | https://docs.kernel.org/power/power_supply_class.html — 电压µV、电量%、NOW瞬时；不支持的属性可缺失 |
| K-USB | https://docs.kernel.org/usb/gadget_configfs.html — gadget/function/configuration/UDC关联 |
| A-HWC | https://android.googlesource.com/platform/hardware/libhardware/+/android-9.0.0_r61/include/hardware/hwcomposer2.h — descriptor、client target、release/present |
| A-EGL | https://android.googlesource.com/platform/frameworks/native/+/android-9.0.0_r61/opengl/libs/EGL/Loader.cpp — 库搜索与namespace；libs/hwui/Properties.cpp在frameworks/base同tag中定义HWUI选择 |
| A-WIFI / A-WIFI-DRIVER | https://android.googlesource.com/platform/frameworks/opt/net/wifi/+/android-9.0.0_r61/libwifi_hal/driver_tool.cpp 及同目录wifi_hal_common.cpp — NULL固件路径、STA切换契约 |
| A-BT | https://android.googlesource.com/platform/hardware/interfaces/+/android-9.0.0_r61/bluetooth/1.0/default/vendor_interface.cc — 初始化/关闭顺序 |
| A-BTVENDOR | https://android.googlesource.com/platform/system/bt/+/android-9.0.0_r61/hci/include/bt_vendor_lib.h — vendor ABI；测试保留该头及版权 |
| A-INPUT | https://android.googlesource.com/platform/frameworks/native/+/android-9.0.0_r61/include/input/InputEventLabels.h 与libs/input/KeyLayoutMap.cpp — keylayout合法标签/解析 |
| A-HEALTH | https://android.googlesource.com/platform/system/core/+/android-9.0.0_r61/healthd/BatteryMonitor.cpp — sysfs采样、单位和board hook |
| A-AUDIO | https://android.googlesource.com/platform/hardware/libhardware/+/android-9.0.0_r61/include/hardware/audio.h 及external/tinyalsa同tag — read字节返回与pcm_read返回码、tinycap -T/-n |
| A-USB | https://android.googlesource.com/platform/system/core/+/android-9.0.0_r61/rootdir/init.usb.configfs.rc — FunctionFS ready、ADB与MTP配置 |
| A-LIGHT | https://android.googlesource.com/platform/hardware/interfaces/+/android-9.0.0_r61/light/2.0/types.hal — BACKLIGHT能力与颜色/亮度 |
| A-POWER | https://android.googlesource.com/platform/system/core/+/android-9.0.0_r61/libsuspend/autosuspend_wakeup_count.cpp — suspend握手；PowerHAL交互状态不是睡眠证明 |
| A-VOLD | https://android.googlesource.com/platform/system/vold/+/android-9.0.0_r61/ — VolumeManager/磁盘事件；按实际fstab与kernel host核对 |
| A-HMI | https://raw.githubusercontent.com/aosp-mirror/platform_hardware_libhardware/android-9.0.0_r61/include/hardware/hardware.h — 行197/202宏定义HMI；同一AOSP代码镜像 |
| GNSS vendor | https://android.googlesource.com/kernel/mediatek/+/3be93a68c209393cfe24a842e2d5896a17ea37dc/drivers/misc/mediatek/conn_soc/common/linux/pub/stp_chrdev_gps.c — RTC_FLAG=7，不能当Z1运行事实 |

来源哈希/准确版本另存 `scripts/display_check/source_provenance.json`、`scripts/power_check/sources/manifest.json`、`scripts/audio_capture_check/sources/provenance.json`、`scripts/btgps/gps/contract_tests/source_evidence.json`。前3份的完整下载源码和调查中间件只在本机归档，不要求提交到公开仓库。

## 21. 本地证据和复现入口

除特别说明，以下目录均相对工作区；测试只在主机临时目录/归档输出，不执行板子动作。测试退出0仅对应表中范围。

| 编号 | 本地证据 | 实际结果 |
|---|---|---|
| E-HWC | bridge_backups/products/products_hwc_fence_20261110_214932/independent_readonly_20261110_220437；hwc_final_20261110_223556 | 34/34；原版27失败；ARM产物及独立范围复核 |
| E-GRALLOC | products_gralloc_build_sources_20261110_214824_352735 | 7构建测试、5,121,415像素/布局检查；stock产物未变 |
| E-GLES | products_gles2test_20261110_215026_833126664；products_gles2test_resume_20261110_214719_413930 | 12分析测试、60主机画面/1,336,350像素；ARM探针；Mali未运行 |
| E-WIFI | wifi_sta_fwfix_verified_20261110_213440；review_completed_20261110_214857/wifi.log | 16/16；ARM函数仿真、重定位与旧坏补丁反例 |
| E-BT | products_btvendor_independent_20261110_223556_02XqcCnl；bt_cleanup_fix_20261110_215901 | cleanup失败保留/重试；正常675断言、显式probe1018断言；ARM身份 |
| E-AUDIO | products_audio_hal_20261110_214857_YEqccV；review_completed_20261110_214857/audio.log | 6/6；旧版5失败，ARM库逐字节复现 |
| E-KEY | keylayout_packaging_20261110_214645；tests/test_z1_keylayout_packaging.py | 4/4 GNU make/mock复制；未证明实体键身份 |
| E-TOUCH | products_cst836u_committed_20261110_213928；products_cst836u_verify_20261110_214935_675768 | 10驱动测试，29导入符号/实际vermagic核对 |
| E-POWER | bridge_backups/pwrkey_audit_20261110_205314；bridge_backups/products/review_tools_20261110_220057/powerkey | O0/O2当前11通过；旧/去guard负例7失败符合预期 |
| E-BATTERY | bridge_backups/products/battery_tools_20261110_215408；bridge_backups/products/review_tools_20261110_220057/battery.log | 21/21；不改SOC/充电算法 |
| E-HEALTH | bridge_backups/health_capacity_20261110_204909；health/tests/run_host_tests.sh | 1600组合；目标服务未重建，未进本次system |
| E-GPS | review_completed_20261110_214857/gps.log；final_regressions_20261110_223256 | 原脚本15、改名11、二进制契约11；功能仍未闭环 |
| E-AUDIT | docs/Z1_HANDOVER_20261110_DRIVER_AUDIT.md及其原始LF日志/原厂hash | 历史调查索引，最终状态须以本文§22为准 |
| E-LOG | mainline_recon/z1_boot_v83.log、z1_boot_20261109_1838.log等 | 历史多次启动，禁止回放计数当独立运行 |

上表未写 `bridge_backups/products/` 前缀的products_*、review_*、keylayout_packaging_*、wifi_sta_fwfix_* 都位于该目录；原始日志不是公开发布内容。

主机复跑（先阅读脚本，需本地编译器/NDK/原件的项会明确失败；不要用跳过代替通过）：
```sh
cd /home/lxj/claude/6572/z1a9_gpt
python3 -B wifi/test_z1_patch_wifi_hal_sta_fw.py
bash bluetooth/libbt-vendor-mtk/tests/run_host.sh --probe-cleanup-failure
bash scripts/r5g_audio/tests/run_tests.sh --link-libs /home/lxj/claude/6572/mainline_recon/audio_headphone_audit_20261110/linklibs
python3 -B graphics/gralloc/tests/test_build_sources.py
bash graphics/gralloc/tests/run_host_tests.sh
python3 -B tests/test_z1_keylayout_packaging.py
python3 -B scripts/gles2test/test_analyze.py
python3 -B scripts/z1hwc2/test/test_fence.py --out-dir /home/lxj/claude/6572/bridge_backups/products/hwc_manual_recheck
python3 -B tests/test_z1_driver_candidate.py
python3 -B scripts/btgps/gps/test_gps_scripts.py
python3 -B scripts/btgps/gps/test_init_rc_patch.py
python3 -B scripts/btgps/gps/contract_tests/test_gps_binary_contract.py
python3 -B tests/test_z1_battery_sample.py
python3 -B tests/test_z1_tf_storage.py
python3 -B tests/test_z1_usb_candidate.py
python3 -B scripts/power_check/test_inspect_power.py
python3 -B scripts/audio_capture_check/tests/test_capture.py
python3 -B scripts/display_check/test_inspector.py
```

这些命令可以写主机临时文件或生成新构建归档，不是完全零写入。原厂blob仅本机存在；GNSS契约测试依赖pyelftools/capstone。HWC重测目录选新的唯一目录。

## 22. 收尾冻结：实际交付与下一轮门禁

### 22.1 外围system候选（已生成；不自动刷）

- 文件：`out/z1_driver_candidates_20261110/system_peripherals_v1.img`
- 字节数：943718400；SHA256：`833a65f23ceada548339d9f48db4765931136698755d00c9b67f2f4de12cc8dd`
- 原始基底：`out/z1_v35_selftest/system_v35.img`，SHA `f180dac2dfddef70a6783937c099394b160a66485ff22c6e9a160eb738afd6df`；基底读回未变。
- 清单：同目录 `peripherals_v1_manifest.json`、`system_peripherals_v1.img.manifest.json`、`independent_verification.json`。
- 独立备份：`bridge_backups/products/products_peripherals_candidate_20261110_223218/`。
- 装配器27测试+额外失败注入独立复核通过；真实候选e2fsck -fn退出0；六文件字节、模式/属主及SELinux逐项读回，包含尾部NUL的标签原样保留。
- **状态：HOST_VERIFIED_NOT_DEVICE_TESTED。** 不表示已验证启动或可直接覆盖板上镜像。

| 镜像内部替换路径 | SHA256 | 本次目的 |
|---|---|---|
| /vendor/lib/libwifi-hal.so | bc7e05250c6849e883d66856470e185e5cb61e71cd56675c01fd441cd9493088 | STA固件切换路径修复 |
| /vendor/lib/libbt-vendor.so | 4394419927598342982a0960f3d3cf954a991e167866155ec4a86fc6c4cd4665 | ABI/失败状态/关闭重试 |
| /vendor/lib/hw/audio.primary.mt6572.so | d037d76ca6ab8231169647fcf39cb46c76ffb098f0059efa7f226a392603813a | 打开PCM恢复、音量契约 |
| /lib/modules/z1_cst836u.ko | 255b08bbf6a654b6561c5a9b5ebbc67b1b42ff902f77bd84f2f412de4937b926 | 触摸释放、诊断采样参数 |
| /usr/keylayout/mt6779-keypad.kl | ccbd0e9c95bca15d64b702667b5c4426b35bdf47b9927f916393ddd4d1256f46 | 合法flag，保留方向与未证映射 |
| /usr/keylayout/z1_cst836u.kl | 621e1de42d08bb8d9bf9e649d243d1b66c79070741ad07eb334c252ee9347484 | 底部导航BACK/HOME/APP_SWITCH |

**没有换入** HWC、gralloc、Mesa、health可执行服务、真实GPS后端、boot/kernel、TF启用配置或ADB启动配置。基底原有这些内容仍保留，不是将它们修复/删除。外围候选中原Mesa仍存在；所谓“分离图形”仅指本次未把新的图形组件混入这六项修改，并不保证基底本身没有图形问题。当前板上boot/system身份与完整回滚组合仍须验证。

### 22.2 独立3D组件包（不是完整可部署图形系统）

目录：`out/z1_driver_candidates_20261110/graphics_stage/`，归档 `bridge_backups/products/products_graphics_stage_20261110_223642/`。

| 文件 | SHA256 | 验证边界 |
|---|---|---|
| hwcomposer.z1.so | fb7240ed1febce56c6cbb5ebe5607e14b3641757e7999ed2d4fbdff1a3d45ba7 | 本轮ARM构建，等待/必需target34项及独立复核 |
| gralloc.z1.so | 728b18de63172b4cece8cfc2825b60051076906dafd4c847aab6842fdcc49590 | mainline链接含heap；未验实际buffer共享 |
| gles2probe_android28_armv7 | 6697ca88a14c2c66359f1d74d69b919297ec87566ceb0d05e1d30c55bc7e25a0 | ARM API28探针；主机llvmpipe像素通过不是Mali通过 |
| libGLES_mesa.so | bb0ac77c03e79648db5dc8a480ec478be566b78c6105e9f781fd22100b46f445 | 原样提取v35现有双库之一，本轮未重建 |
| libgallium_dri.so | 492f0b5e871868f55023351722a609da6b65e186fbf31366a28d7faf981232b9 | 与上一库配对；宿主RUNPATH等打包债务未自动清理 |

未附自动安装器。先读manifest里的blocked_by：250ms release watchdog、present时序、真实maps/import、Mali像素和持久覆盖仍未闭环。不能因为有五个文件就全局替换，不能关闭fence假装稳定。

### 22.3 其余交付状态

| 项 | 本轮结果 | 仍缺/下一步 |
|---|---|---|
| HWC/gralloc/GLES | 代码与目标组件、测试、独立复核 | 首先解决释放生命周期，再做共享buffer和HWUI/SF；当前3D未整机验收 |
| WiFi/BT/3.5播放 | 源修复、目标库、六文件镜像包含 | 运行maps/hash、实际联网/配对/低音量听音 |
| 复读/跟读/播放 | 目标语义已记录，方向保留 | 缺逐键物理标注采样；不可宣称键位已全部修复 |
| 底部三键 | keylayout、释放bug、新ko和采样入口 | 是否坐标条、分区阈值仍需样本；不盲改y/x |
| PMIC电源键 | 内核e6489564b源补丁与host回归 | 没有重打新boot；原始IRQ事件与Android消费仍需测 |
| 电池/health | health源修复，1600测试；21项采样工具 | 新health目标服务缺完整AOSP构建环境，未进system；插电100的SOC算法未修 |
| GPS | prep15、改名11、二进制契约11通过 | 原厂callback ABI适配、服务集成、真实HAL与数据链尚未实现 |
| TF | 被动工具与34fixture | 接线/供电/CD/fstab/驱动启用仍未完成 |
| ADB | 独立opt-in候选与34fixture | 实际基线缺service且secure配置不满足；未接入开机 |
| 亮度 | dash/BusyBox各37fixture | 无物理亮度/息屏恢复测试 |
| 休眠/RTC/看门狗 | 27fixture离线检查器与证据 | 全部硬件行为未验；不能释放诊断锁冒险验证 |
| 录音 | 39fixture前提工具与HAL/codec契约方案 | 输入HAL仍ENOSYS，MIC路由/时钟/接线未完成 |
| FM | 有真实驱动/控制资产与明确ABI/固件/路由缺口 | 未集成配套app/JNI，未听音/搜台/RDS |
| MTP/OTG | 详细缺口/前提已列 | 未实施，不能通过翻属性/DR_mode宣称完成 |
| 指示灯 | 用户明确没有 | 不适用，不列故障 |

### 22.4 接手顺序与故障应对

1. **先固定当前设备身份和恢复渠道**：完整boot/system/持久覆盖/模块/数据备份，确认能返回原状态；不依据v79/r14名称决定。脚本发写请求后失败就按可能部分写处理，必须读回。
2. **先做纯元数据取证**：当前加载路径与hash、相关节点/服务状态；复读/跟读/播放按设备逐键记录EV_KEY和MSC_SCAN；本轮不修改任何物理映射。
3. **选择单变量实验**：六文件外围候选是离线集成便利件，若遇回归，用manifest拆单项，而非反复换整套不记版本。WiFi先STA/认证/IP/DNS；BT先初始化/HCI，再配对/A2DP；音频先低音量左右，后插拔。
4. **GPU分层**：目标linker/maps → 同步离屏像素 → 合法buffer导入 → 释放生命周期的延迟/错误注入 → 单应用 → 全局HWUI/SF。任一步不通过不要跳到版本升级或频率电压调节。
5. **遇超时**：保留完整boot内时间、返回码、最后成功层级；不要只看最后一行怪WiFi，不把重复z1warn算新故障。出现GP4000时冻结最小负载、用户态/内核版本和命令流。
6. **遇缺库/不兼容**：先ELF32 ARM、softfp、SONAME/NEEDED/版本符号、namespace，再看SELinux；不要全局换libssl或借其他机NVRAM。
7. **遇电量跳变**：被动连续电压/online/容量样本与独立电池端测量同时间关联。固定压降、平滑、强制100、乱改ADC倍率不属于解决方案。
8. **遇按键无反应**：物理动作→节点→MSC_SCAN→EV_KEY→InputReader选中的kl→App动作逐层排查。完全无scan与有scan无key是不同问题。
9. **停止条件**：写入结果未知、模块ABI不明、功耗/温度异常、触摸失控、无恢复途径、文件系统报错；先保留证据、回到已验收组合，不盲重试。

### 22.5 提交与完成定义

代码按功能小提交推送到 `lxjuyn/z1-android9` 的origin/main；内核PMIC补丁在 `lxjuyn/z1-mt6572-mainline`。本地Android分支 `work/driver-recovery-20261110-215207` 用于收尾，推送为HEAD:main。详细提交表与最终仓库HEAD由同目录冻结记录给出。镜像、厂商blob、原始日志和个人数据不进公开git。

“已提交/已构建/已装配/板上已加载/功能已通过”是五个不同状态。不得将本文测试数量合并成硬件通过率；最终状态是本地源码、候选与工具交付，整机功能仍需后续验证。

最终联网核验补充：2026-11-10 22:54主会话重新获取Mesa Lima、Linux dma-buf、power_supply与AOSP r61 hardware.h，均HTTP200；页面和SHA位于 bridge_backups/products/official_final_20261110_225427/verification.json。滚动文档不作为Z1版本证据。
