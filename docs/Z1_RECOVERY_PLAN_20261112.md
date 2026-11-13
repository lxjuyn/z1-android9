# Z1 恢复与分项验收：2026-11-12 会话续作

开始于 2026-11-12，收尾及音频时序补强于 **2026-11-13（Asia/Shanghai）**。

本文件接续上次在“写完整计划”时中断的审查。上次实际留下了
`bridge_backups/repair_plan_review_20261112_230948/` 的快照，没有留下完整计划。
用户随后要求继续实施并尽可能多完成。本轮完成本机修复、故障测试、镜像打包、
独立反解和归档；**未刷板、未清除 data/cache，未把主机验证写成板上验收**。

## 1. 已完成的交付

| 工作 | 实际结果 | 证据 |
|---|---|---|
| 分块刷写 | 每块写后严格读回；完成块恢复时也复读；完整 SHA/尺寸/块长绑定状态；全设备锁 | `scripts/z1_chunked_flash.py`、`docs/Z1_FLASH_VERIFIED_20261112.md` |
| 开机存储检查 | `on fs` 挂载后、post-fs 写者前检查；fsck 仅 0/1 可恢复 RW；2/3 要求重启，其余阻塞 | `scripts/z1deploy/z1stage_boot.sh`、`docs/Z1_BOOT_STAGE_GUARD_20261112.md` |
| 安装失败处理 | 同内容不写；临时文件比对后 rename；检查元数据、恢复 system RO；实际安装身份回读 | 同上 |
| init 调用链 | 早期 storage_ready 和后期 stage.status 两个等待闸；移除 legacy HWC cp 和重复 stage；rootd 默认 disabled、随后由 init 显式启动 | 新 boot 反解 `init.z1.rc` |
| 音频首次加载 | 把原 post-fs 后台加载移到 post-fs-data 安装成功之后，避免首次恢复先加载旧 ko | 两份 init rc、`audio_order_review.json` |
| CPU/GPU 策略 | 去掉不存在的 1508 MHz 请求；检查 available、写返回值和回读；保留 CPU 806–1300/schedutil、GPU 416 MHz 策略 | stage 脚本及故障测试 |
| 内核遗留改动 | 之前未提交的 DVFS config/DTS 已保存提交；清理残留超频注释，独立重编 DTB 与 cmpfix 内嵌件逐字节相同 | 内核提交 `187434377`、本地 `dvfs_validation.json` |
| 新恢复 boot | 从已核实 cmpfix 精确派生，保持 zImage/r14 图形；仅三个 CPIO 文件和 initrd-end 改动 | 下节 manifest/SHA256SUMS |
| WiFi 身份 | staging、v35、外围候选中的实际模块完全相同；拒绝另一套旧驱动替代 | `scripts/z1_wifi_module_identity.py`、`docs/Z1_WIFI_SOURCE_IDENTITY_20261112.md` |
| 板上验收工具 | 只读取证，默认限量；可显式输出 lib/bin/usr 全文件哈希，不落 data | `scripts/z1deploy/z1_recovery_probe.sh`、`docs/Z1_RECOVERY_PROBE_20261112.md` |

主机故障测试覆盖：假成功、短读、错数据、错误返回码、尾块、重试、旧状态、
块长变化、设备内容变化、并发；fsck 返回码、busy、工具失败、挂载来源、
复制/比对/rename/元数据失败、就绪属性失败；boot 截断、头长度、CPIO 保持和双闸时序。
最终逐套结果见 `out/z1_recovery_20261112_233945/validation_summary.json`，
仓库内保留 [脱敏验证摘要](validation/Z1_RECOVERY_20261113.json)。
不重复计入定向复测次数。六套测试共 120 项通过、1 项缺历史日志跳过；
这包含刷写 19、旧入口 19、stage 32、打包器 12、WiFi 18、probe 20。

另外已用候选 Android bionic shell 在 qemu-arm 的低 PID namespace 中执行实际 `mksh -n`：
`z1stage_boot.sh` 和 `z1_recovery_probe.sh` 均返回 0，证据在
`out/z1_recovery_20261112_233945/android_shell_check/probe_v3_validation.json`。这一步只检查
脚本解析，不执行脚本正文，不挂载文件系统，不改 property，不加载模块，也不替代板上行为验证。

## 2. 可复核恢复包

现行候选目录：`out/z1_recovery_20261112_233945/package_audio_ordered/`。
先前的 `package/`（SHA `6db350bc…`）保留作版本证据，已被此音频时序补强候选取代。

| 项 | 值 |
|---|---|
| boot.img SHA256 | `ef5f07e4459b6d2fcf42b64a50202349b00e80a979274f85bf9101f1052f1468` |
| 大小 | 5,982,208 B，低于 6 MiB 分区 |
| zImage SHA256 | `6a1cc648ac856b30c109fc5f6a8955f00fa2dc0001b7e917fec4fce6b7bcaa4c`，与 cmpfix/final_perf 相同 |
| ROOTFS body | 1,637,987 B；MTK 头长度字段已回读 |
| initrd-end | `0x8428fe63 = 0x84100000 + 1637987` |
| 变更 CPIO 记录 | `init.z1.rc`、`init.z1.network.rc`、`z1stage_boot.sh`；其他原始记录字节/顺序/权限类型保持 |
| HWC | r14，147,360 B，`03d2f200f4ea8f8fd1b4bc8d8dd06c190ef2a23f69811fad875b56296acffa95` |
| gralloc | 28,548 B，`0ed9fb2b573bfe4bf2e533b7f709ad054a1dc95482d420aca184d6c95823b77d` |
| stage SHA256 | `3df321e69bbed45cd3f198c2e3ce56f8751f134cac7ec38acd37353c86330503` |
| 归档 | `bridge_backups/products/products_recovery_audio_ordered_20261113_002822/` |

完整字段在现行候选目录的 manifest.json。打包器从最终镜像再次反解；两路独立复核了
90 个 CPIO 记录、MTK/Android 长度、Android ID、DTB 仅 initrd-end 变化和图形身份。
音频时序修复已提交为 `36c9f65`，清单补充构建器身份为 `61fd15f`。主代理从后者
提取三份已提交源码到隔离目录重建，现行包及产品归档的 7 个文件全部逐字节一致，
证据为 `out/z1_recovery_20261112_233945/committed_rebuild_review.json`。
先前重建报告中的旧 manifest/SHA256SUMS 属于补录构建器身份之前的版本，不能当作
现行清单的全文件一致证明；boot 的字节内容和 SHA256 在补录前后均未变化。
**r14 保持指 ramdisk 内二进制；`/data/local/tmp/z1stage/active/` 仍有明确优先权，
上板必须核对最终安装哈希，不能直接假设运行时也是 r14。**

恢复候选仍保留台架 root 命令通道：`z1_rootd` 虽声明为 `disabled`，后续 init 在安装
通过后会显式 `start z1_rootd`。在写入恢复基线前，只读留证
`/data/local/tmp/z1stage/install.sh`、`cmd.sh`、`active/` 和 `.disable_*`；这些持久
hook 若仍存在，可能覆盖 system 或改变验收结果。因此本包是诊断台架候选，不能当作已
关闭 root 通道的正式产品镜像。

确定性重建（输出目录必须不存在）：

```sh
python3 z1a9_gpt/scripts/z1_prepare_recovery_boot.py \
  --base out/boot_cmpfix.img --output-dir out/z1_recovery_rebuild
```

system 推荐恢复对象仍为已审计外围候选，避免丢失 WiFi HAL、音频 HAL、触摸和键位修复：

- `out/z1_driver_candidates_20261110/system_peripherals_v1.img`
  943,718,400 B，SHA256 `833a65f23ceada548339d9f48db4765931136698755d00c9b67f2f4de12cc8dd`。
  本轮重新核对 SHA256、`e2fsck -fn` 返回 0。
- v35 保留作比较/回退材料：`out/z1_v35_selftest/system_v35.img`，
  SHA256 `f180dac2dfddef70a6783937c099394b160a66485ff22c6e9a160eb738afd6df`。
- boot 回退材料保留：cmpfix `6388be86…`、final_perf `7e6987ee…`、r14 release。
  这些旧 boot 含本轮已修的安装/检查缺陷，不能把回退理解为存储问题已解决。

本轮额外逐文件核对了 boot 的五件外围覆盖物：WiFi/BT/audio HAL 与候选 system
一致；两件音频 `.ko` 与候选 system、v35 都不一致。完整哈希在
`out/z1_recovery_20261112_233945/system_boot_overlay_identity.json`。
旧 `post-fs` 动作会先加载音频，stage 到 `post-fs-data` 才安装新文件。
现行候选已将唯一的 `load_audio.sh` 调用移到 `wait_for_prop z1stage.status ready`
之后，保证安装失败时不会启动它，首次安装成功后才开始加载新文件。
独立扫描了 ramdisk 27 份 rc/shell 和 system 74 份 init rc，未找到其他抢先加载路径。
该 loader 同时加载 charger，也随之延后；保持后台执行，因此**不保证 HAL 启动前
已经加载完成**，loader 自身 exit0 也不能当作模块加载成功。须结合 insmod 日志、
`/proc/modules`、sysfs build-id note 和最终文件身份验收，并复测干净重启；未宣称声音已修好。

## 3. 对上一份交接的必要纠正

1. 坏 `libutilscallstack.so` 的 linker 错误属实，但不是所有历史崩溃的共同根因。
   `mainline_recon/z1_boot_r14_verify.log`：3126 行 SF signal6、3422 行 HIDL
   DEAD_OBJECT、3882 行 EGL 0x300b、30259 行 p6 ext4 错误、177735 行 p5
   doubly allocated；坏库 `.dynamic` 错误到 363766 行才出现。各次启动需按时间轴分开。
2. 364547–364550 行四次普通 sha 一致只证明重复读取相同，不能证明绕过页缓存，
   不能独凭它确定损坏来自写入、存储介质或内存。完成块严格读回才是本轮补上的证据。
3. `dev_addr_check` 是告警，不等于注册失败。实际模块为 `wlan_gen2.ko`，
   SHA `2cf27905d382fb8ac7e6bd8e000c6db1417c4415b5718086562be11399580e9b`。
   已找到绑定目标哈希的历史来源清单，指向 `e9961bec…` 及临时兼容修改；
   完整补丁和当时的脏内核状态仍缺失，不能把该提交或当前上游直接冒认成出货源码。
4. cmpfix/final_perf 的图形仍是 r14；当前 r16+ 源码/产物存在不代表进入了这些镜像。
5. 原先“内核改动已提交”不符工作树实况；本轮才将 .config/DTS 留存提交并推送。
   zImage 与 cmpfix 相同、DTB 可逐字节复现；未提取到嵌入 IKCONFIG，不声称完整配置
   已由运行中内核验证。
6. CPU 表是 598/806/1001/1209/1300 MHz，全部 1.15 V。驱动软件电压限制
   `proc_max_volt=1250000` 不能证明 1.3 GHz 是芯片物理极限；一次 491 MHz GPU
   失败也不能证明物理上限。恢复阶段保留已有可用策略，不扩展 OPP。

## 4. 上板恢复顺序和通过条件

### A. 先留证，再刷写

执行前重新核对主机的 USB 枚举和串口归属；本轮没有开启刷写等待进程。
正常启动仍需拔下载线；刷写时先启动工具再由用户让设备进入下载窗口。

先保留现有日志及可读取的持久 hook、图形 active/kill-switch 状态和关键哈希。v3
取证脚本只输出这些固定文件的身份和元数据，不执行、删除 hook；会拒绝符号链接
祖先和特殊文件。`SYMLINK_SKIPPED` 仅表示探针跳过链接，rootd/stage 仍可能跟随
该链接执行或使用其目标。运行只读取证脚本
或按其白名单发短串口命令；不要因日志里曾经失败就再次执行 destructive repair。
多个串口采集进程可能竞争同一 CH340，实际抓取前确认只保留一个预定采集者；
本轮没有擅自终止既有会话。

需要完整原始存储取证时，先走 MTK `ro offset length filename`，严格检查命令结果、
精确尺寸、归档 SHA；不要把 backend 的退出码或“Dumped”单独当完整性证明。
缺少原始取证不授权清 data/cache，也不允许把它们当空白分区格式化。

### B. 恢复内容并严格读回

以下是待执行命令，**本轮没有执行**。同一设备任一时刻只运行一个工具；
全部块读回通过后才能把该分区记为已恢复。

```sh
tools/z1_flash_chunked.sh android out/z1_driver_candidates_20261110/system_peripherals_v1.img 48 25
tools/z1_flash_chunked.sh bootimg out/z1_recovery_20261112_233945/package_audio_ordered/boot.img 4 8
```

如果 system 尚未完整写回，不把一次 boot 尝试当作图形回归。新断点状态只作为提示；
再次运行会复读所有已登记块，不会凭旧块号跳过。各次 MTK 原始输出与 result.json
保存在 `mainline_recon/flash_chunks/`。

### C. 存储和启动先通过

验收：早期 `sys.z1.storage_ready=1`，后期 `z1stage.status=ready`；system 最终 RO，
data/cache 挂载设备正确且 RW；无新的 ext4 journal abort；`sys.boot_completed=1`。
若 fsck 返回 2/3、失败、不可读或 busy，init 保持等待，UART 中应有 BLOCKED，
不会自动格式化、不会自动重启。e2fsck 暂无超时：卡住会保持阻塞，仍需人工诊断。

核对库、HWC/gralloc 的实际哈希，再用 probe 的可选完整 system 哈希列表比较
lib/bin/usr。保留新旧启动计数和日志时间窗，分别判断 zygote/SF/BT 等服务。
完成一次干净 reboot，核对新模块路径、哈希和加载日志；确认没有重新写坏才进入下一项。

### D. 图形单独验收

先固定 r14 ramdisk及相符的实际安装哈希，检查 SF/zygote 重启、EGL、device/client
composition 和持续显示。然后另做 r16+ 单变量候选，验证 first vsync、超时回退、
fence/NO_RESOURCES 和真实像素；不能用“桌面显示”替代 Mali 渲染及 GPU→OVL 同步验证。
最后复测相同 15-swipe 工作负载，单独评估 Dalvik heap 改动，避免把不同 system/启动
状态下的 22.3% 与 16.3% 直接当作新回归基准。

### E. 外围逐项关闭

| 项目 | 下一步与通过条件 |
|---|---|
| WiFi | 找回出货 gen2 源/构建来源后才修MAC API；分开检查注册、扫描、关联、DHCP、DNS和真实数据 |
| 蓝牙 | 独立捕获 initializationComplete(ERR)/SIGABRT，核对vendor ABI与固件/HCI；通过扫描、配对、音频再记完成 |
| 音频 | 干净重启确认新ko生效；tinyplay可闻左右声道、耳机插拔路由；录音需独立路线，不能以声卡注册代替 |
| 触摸/导航/实体键 | 确认实际加载z1_cst836u及布局，采物理坐标/按键事件，检查按下释放/长按 |
| GPS/FM | 先核对固件、设备节点、厂商ABI与服务失败日志；oneshot止血不算功能完成 |
| TF/USB/ADB | 保持既有候选隔离，先读供电/host/枚举信息，再分别验收卡读写和ADB认证通道 |
| 电源/休眠 | 分开验电压/SOC、按键中断、待机、RTC和显示恢复；不拿缺失日志推断成功 |

## 5. 已知边界

此恢复包尚未在真实 MTK 链路运行，主机测试不能替代上板。
全流程保留了旧 active/kill-switch 语义及原有外围候选；不会自动选择 r16、删除操作者
覆盖文件或清理用户数据。`restorecon_recursive /system/etc` 在 system RO 后可能报
非致命 EROFS，当前 permissive 下不作为存储修复成功的判据。主机成果完整保存，
剩余工作从上面的板上留证与恢复开始。
