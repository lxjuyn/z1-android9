# NVRAM 只读预检

在目标 Android shell 手动运行 `sh z1_nvram_preflight.sh`，输出重定向由调用者决定。此脚本不启动 daemon，不改属性、挂载、校准文件或设备；本轮只在主机隔离目录运行测试。

退出码：`1` 表示前置文件缺失或格式不符；`2` 表示证据仍不完整。当前总是保留校准来源及动态链接待验证项，因此不会返回硬件成功。`PASS` 仅表示该行的格式或瞬时状态检查通过。daemon 的 `nvram_init=Ready` 是自报状态，不能证明校准恢复成功；运行中也只是瞬时快照。

检查范围：8 个直接依赖的 ELF32 ARM 头、daemon 执行位、init 状态、RestoreFlag 的小端标记、两个 BT 记录路径的 64/66 字节长度、512/514 字节 `WIFI` 记录和 3404 字节 `APRDCL/FILE_VER` 版本表。不会打印校准内容、地址或校准 hash。ELF 头不证明二进制完整、依赖闭包或能被 Android linker 加载；记录长度不证明内容有效或属于本机。`/dev/nvram` 只检查节点类型，不打开设备。

请在受信任、路径稳定的设备文件系统中运行。脚本拒绝末级符号链接和非普通校准文件；它不是抵御路径并发替换的沙箱。测试覆盖 dash、bash 与可用的 BusyBox ash，并拦截变更命令、检查文件内容和模式不变；尚未在目标 mksh/toybox 上运行。

主机测试：`python3 -B test_nvram_preflight.py`。蓝牙配对、音频、定位及 factory daemon 手动启动都需要另行实测。旧自动启动 NVRAM 镜像 `out/adaptation_candidate_20261115_nvram/system.img` 已判定不可刷写，预检脚本不改变此结论。
