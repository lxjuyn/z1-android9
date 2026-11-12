# 恢复前后只读取证（2026-11-12）

脚本：`scripts/z1deploy/z1_recovery_probe.sh`。在板上的 Android shell 中运行，普通 shell 即可先采样；已有 root 通道可提供更多可读项。权限不足会明确标注，不会尝试提权。

脚本只向终端输出；不写属性或 sysfs，不控制服务、不挂载、不修复文件系统、不重启，不把日志写到 `/data`。主机沿用现有串口采集工具保存输出。可以通过现有 shell 的 `sh -s` 向标准输入传入脚本正文，避免为本次采样在设备上创建脚本文件；长脚本须沿用已验证的串口分段传输方式。若脚本已在 ramdisk 或其他既有部署位置，也可 `sh <脚本路径>` 运行。本轮没有向设备上传或执行。

默认采样包含：UTC、uptime、内核、指定 init 服务与启动/存储/安装状态、图形 SHA 属性、三分区挂载状态、坏库及关键执行文件 SHA、ramdisk/安装路径/active 中两件图形库 SHA、CPU/GPU 频率与 governor、wlan0 存在状态及 operstate。只读指定属性，不执行全量 `getprop`；不读取网卡地址、NVRAM、WiFi 配置、用户文件内容。

v2 另核对已安装 `/system/lib/modules/z1-radio/wlan_gen2.ko`、`/system/lib/modules/mt6572-afe-pcm.ko`、`/system/lib/modules/snd-soc-mt6323.ko` 的 SHA256；从 `/proc/modules` 仅列出精确名称 `wlan_gen2`、`mt6572_afe_pcm`、`snd_soc_mt6323`、`mt6572_mt6323` 的加载状态、大小、引用计数、依赖及状态，省略内核地址。每件模块还尝试哈希 `/sys/module/<名称>/notes/.note.gnu.build-id`，明确标记 `BUILD_ID_NOTE_ONLY`；不存在则标缺失。已安装文件哈希不能证明当前已加载代码的版本，build-id note 的哈希也不是完整代码哈希；需与对应构建产物的 note 比对，并结合实际加载日志及一次干净重启确认。脚本不加载或卸载模块。

`/proc/asound/cards`、`/proc/asound/pcm` 只显示设备清单，每份最多 40 行，超过时标 `TRUNCATED`；不读取音频流、不录音、不播放，也不访问用户音频文件。

`dmesg` 只保留指定 ext4、EGL、SurfaceFlinger、WiFi 告警的最后 80 条，隐藏 SSID、口令、credential/secret、PSK/密钥赋值相关行及 MAC 地址；密钥名与 `:`/`=` 之间的空格也会匹配。过滤覆盖这些已定义格式，不代表能识别任意私有驱动的敏感输出格式。`PERMISSION_DENIED` 表示权限不足；`MISSING_OR_INACCESSIBLE` 表示缺失或上级目录不可访问；`READ_FAILED` 表示实际读取失败。没有匹配告警不等于系统没有任何故障，串口日志与 Android logcat 仍需结合使用。

显式增加 `--full-system-hashes` 才遍历 `/system/lib`、`/system/bin`、`/system/usr` 的普通文件并输出 SHA256；这可能耗时较长、产生较多串口输出。默认不跟随目录或文件符号链接，避免走出 system 目录。对于上述目录之外的文件，仍只核对默认的固定白名单。完整模式并非 eMMC 原始读回，也不能绕过页缓存，不可据此单独排除存储读取异常。

恢复前后分别保存一次默认输出。至少比较：`sys.z1.storage_ready`、`z1stage.status`、zygote/SF 状态、三个挂载项是否满足预期、`libutilscallstack.so` 与图形库 SHA、实际频率上下限。`active/` 可以覆盖 ramdisk 的图形库，因此必须同时核对三处身份。

2026-11-13 主机验收：`python3 -m unittest -v scripts/z1deploy/test_z1_recovery_probe.py` 共 16 项通过（其中包括 `bash -n` 与 `dash -n`）。测试仅在临时副本中将固定绝对路径映射到假文件系统，生产脚本没有可重定向根目录选项。覆盖正常/缺失路径、仅读白名单属性、文件内容及修改时间不变、潜在写入命令陷阱、80行限制与顺序、MAC及带空格的无线凭据隐藏、可选全量哈希、不跟随链接进入 data、工具缺失、遍历失败、dmesg 权限不足和无效/过量参数；另核对三件磁盘模块哈希与四件加载状态分开输出、拒绝相似模块名及内核地址、build-id note 单独标识、音频清单上限。没有访问真实设备，也没有将脚本部署到设备。

## 启动工具离线核对

`out/boot_cmpfix.img` 的 ramdisk `init` 是 1637512 B、类型权限 `0100750`，SHA256 为 `285382ea87dada34629516af590626e4932ebe47b2626197b5da8bd8273b083e`，包含 `wait_for_prop`。恢复打包仅替换 rc 与 stage 脚本，保留此 init。

对 v35 与 `system_peripherals_v1.img` 分别用只读 debugfs 核对，两者下列工具相同、类型和执行权限正确，ELF 直接 NEEDED 库均存在：

| 路径（镜像内） | 类型/目标 | 字节数 | SHA256 前16位 |
|---|---|---:|---|
| `/bin/sh` | regular 0755 | 236208 | `77fe8e17219ddd2b` |
| `/bin/awk` | regular 0755 | 126872 | `0e69150e1884d4d3` |
| `/bin/toybox` | regular 0755 | 348548 | `3b78a10fb77fda73` |
| `/bin/toolbox` | regular 0755 | 95544 | `3963c3d66cbd604c` |
| `/bin/e2fsck` | regular 0755 | 194352 | `07e81db7807ae24f` |
| `/bin/setprop`, `/bin/readlink`, `/bin/cmp` | symlink → toybox | — | — |
| `/bin/getprop`, `/bin/dd` | symlink → toolbox | — | — |

toybox 中也实查到 setprop/readlink/cmp/sha256sum/dmesg/find/date applet 名称，toolbox 中实查到 getprop/dd。该核对证明本机镜像内文件存在及基本链接条件，不代替板上权限、动态链接和实际执行验证。
