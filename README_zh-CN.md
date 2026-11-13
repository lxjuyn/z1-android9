# 佳伯听力机 Z1：Android 9 / Linux 7 移植

[English](README.md) | **简体中文**

本仓库保存佳伯听力机 **Z1 / MT6572** 的 Android 9（LineageOS 16.0）设备树、
Linux 7.0-rc7 板级支持、构建与恢复工具，以及固定版本的 Android 清单。上游
大型源码树、用户数据和生成镜像不进入 Git。

[文档入口](docs/README.md) | [恢复与验收计划](docs/Z1_RECOVERY_PLAN_20261112.md)

## 现行恢复候选

最新主机侧验证对象见
[Z1 恢复与分项验收](docs/Z1_RECOVERY_PLAN_20261112.md)。这套候选**尚未刷入已
恢复的 Z1**，下表只证明主机侧产物身份和测试结果，不能据此宣称设备已经恢复。

| 部件 | 身份 |
|---|---|
| `boot.img` | 5,982,208 B；SHA-256 `ef5f07e4459b6d2fcf42b64a50202349b00e80a979274f85bf9101f1052f1468` |
| 推荐 `system.img` | 943,718,400 B；SHA-256 `833a65f23ceada548339d9f48db4765931136698755d00c9b67f2f4de12cc8dd` |
| 主机验证 | 116 项通过，1 项因历史日志缺失而跳过 |
| 板上验证 | 本轮未执行 |

boot 从已核实的 `cmpfix` 基线派生，保留 r14 图形件和原 zImage，新增存储门禁、
带校验的文件安装与正确的首启音频模块顺序。system 使用已审计的外围候选。具体
路径、刷前只读取证、刷写顺序和通过条件必须以恢复计划为准。

设备仍需完成存储与启动验收，项目才能把状态标记为“已恢复”。历史日志中的
system 链接失败、ext4 错误和各类服务失败发生在不同时间，不能拿其中一个错误
解释之前全部崩溃。恢复候选仍保留台架 root 命令通道，写恢复基线前必须先检查
`/data` 中持久化的 hook。

## 当前能力边界

| 项目 | 状态 |
|---|---|
| 显示与图形 | DRM/OVL 与 HWC 路径有既往板上证据；恢复候选保留 r14 图形件，仍需恢复后的干净启动复验。 |
| CPU / GPU 调频 | 内核保留已验证 DVFS 配置和 416 MHz GPU performance 设置；一次 491.52 MHz 失败不能证明芯片物理上限。 |
| 触摸、按键、电源 | 已有分项上板工作，但必须从干净存储状态重新验收。 |
| Wi-Fi / 蓝牙 / GPS / FM | 已固定实际 `wlan_gen2.ko` 身份，出货精确源码仍未找回；注册、扫描、关联及 BT/FM 功能需分别验收。 |
| 音频 | 首次启动已改为先安装文件再启动模块，但加载仍后台执行，扬声器、耳机和录音均未取得新一轮板上证据。 |
| 存储与 Android 启动 | 主机测试覆盖新门禁和安装器；真实挂载、属性、journal、模块加载与 `sys.boot_completed` 仍待实机验证。 |

## 仓库结构与构建

- `mainline/patches/`、`drivers/`、`graphics/`、`network/` 等保存 Linux 7
  板级补丁、驱动、Android 侧图形与连接工作；
- `rootdir/`、`keylayout/`、`art_patches/`、`native_path_patches/`、`sepolicy/`
  等保存 Android 设备组件；
- `scripts/` 保存构建、刷写、恢复、取证和测试工具；
- `docs/` 保存现行恢复计划、分项结论和带日期的历史记录；
- `manifests/` 固定 `repo` 使用的上游 revision；
- `stock_patches/` 记录同步 Android 树后应用的补丁。

干净工作区以本仓库作为 Repo manifest：

```sh
repo init -u https://github.com/lxjuyn/z1-android9.git -b main -m default.xml
repo sync -j4
python3 device/jiabo/z1/scripts/apply_stock_patches.py --source "$PWD"
```

`manifests/lineage16-pinned.xml` 固定上游 revision；`stock_patches/manifest.json`
记录每个补丁的 base 与结果 SHA-256。设置 `LOS_ROOT`、`HOST_TOOLS` 后执行
`scripts/build_z1.sh systemimage`。

主机侧语法、归档、哈希和故障注入测试不能替代上板。尤其不能用脚本可解析、
镜像哈希正确或模拟挂载通过，代替真实块设备、模块加载和 Android 启动通过。

## 发布纪律

boot 与 system 必须按公布哈希成对使用，不能混用不同恢复轮的产物。成品镜像
只放 GitHub Releases，不提交进 Git。发布包不得包含用户数据、NVRAM、校准数据、
串口/logcat 原始日志、凭据、token、SSH 私钥或个人环境文件。Z1 正常开机与
下载模式对 USB 连接有不同要求，按对应候选的操作文档执行。
