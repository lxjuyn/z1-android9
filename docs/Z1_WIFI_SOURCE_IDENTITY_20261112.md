# wlan_gen2 身份核对与修复门槛（2026-11-12）

本轮完成镜像与模块的逐字节身份核对，尚未恢复出货模块的精确源码及构建记录。因此没有修改旧 mt6620 驱动，没有重编或部署替代模块。

## 可复核的产物身份

| 载体 | 模块位置 | 字节数 | SHA256 |
|---|---|---:|---|
| `out/z1_wifi_stage` | `modules/wlan_gen2.ko` | 956276 | `2cf27905d382fb8ac7e6bd8e000c6db1417c4415b5718086562be11399580e9b` |
| `out/z1_v35_selftest/system_v35.img` | `/lib/modules/z1-radio/wlan_gen2.ko` | 956276 | 同上 |
| `out/z1_driver_candidates_20261110/system_peripherals_v1.img` | `/lib/modules/z1-radio/wlan_gen2.ko` | 956276 | 同上 |
| `out/alpine_rootfs` | `lib/modules/7.0.0-rc7-z1+/wlan_gen2.ko` | 958904 | `bab166e716d649bd9ae64e13cb0a158d8d1d7ff9830f608237312246cf106ccd` |

前三件的 `name=wlan_gen2`，vermagic 为 `7.0.0-rc7-z1+ SMP mod_unload ARMv7 p2v8`，GNU build-id 为 `571c0d025ce2f459ab4e4ea662f9c308a1737164`，没有 srcversion 或 DWARF。`.comment` 记录 GCC 13.3.0。Alpine 模块的 build-id 是 `725a38cc3ad49550bef7cd7f0fddace15ae3ed07`，即使 vermagic 相同也不是这次 system 的原模块。

取证由只读 `debugfs -R 'cat /lib/modules/z1-radio/wlan_gen2.ko' IMAGE` 完成，未挂载或修改镜像。机器可读结果：`out/z1_wifi_identity_20261112.json`。

`out/z1_wifi_stage/bin/z1_linux7_radio_init.sh:6,133,176-178` 指定运行时从 `/system/lib/modules/z1-radio/` 加载模块；已加载时会跳过。因此这里只证明镜像及加载意图，未证明当前板上实例，仍须读取运行文件的 SHA256、`/proc/modules` 与网卡状态。

## 源码缺口与上游核对

- 历史构建说明 `fork_linux/logs/logs-connsys-modules-rebuilt-20260814.md:22-28` 指向 `/tmp/connsys_build`；该目录现已不存在。
- `mainline_recon/WMT_INIT_SOURCE_20260815.md:3` 指向归档 `bridge_backups/products/products_connsys_modules_20260812_022121/mt6572-connsys-modules/`；该归档现已不存在。历史文档中提到目录不等于目录仍然可用。
- 当前 `fork_linux/drivers/misc/mediatek/connectivity/drv_wlan/mt6620/wlan/` 是另一套驱动，不能因它也写 `dev_addr` 就当成出货 `wlan_gen2.ko` 的源码。
- 公开上游 `gabin8/mt6572-connsys-modules` 的固定提交 `165df1b4a0c6ac767c7e1eaf687494cace78480c` 已只读核对：`wlan/Makefile:104` 定义 `MODULE_NAME := wlan_gen2`；`wlan/os/linux/gl_init.c:3148` 已使用 `eth_hw_addr_set()`。本轮没有把上游当前版本冒充历史出货版，也没有替换驱动树。

恢复工作必须记录源仓与固定 commit、Z1 本地补丁、实际内核配置/头文件、编译器与命令，并先解释或重现原模块的产物差异。若未修改源码的干净重编能与 v35 原模块逐字节一致，可用下述脚本作产物门槛；匹配本身仍不能证明源目录未经替换，须保存构建日志和源码清单。仅 vermagic 相等不足以判断 ABI 或来源一致。

### 2026-11-13 补充：找到绑定目标哈希的历史来源清单

DeepSeek 在本地 r9 发布材料中找到新的线索；主代理随后直接读取
`bridge_backups/dl/r9_swiftshader.tar` 内的
`Linux7-20261006-r9/provenance/radio-modules.json` 并核对归档内部清单：

- 该 JSON 的 SHA256 为 `ad0ec4987b7f6ee2089f4ec6d718f3081275f67e5f3aed7a00b3243637c8e54a`，
  与同一 tar 中 `SHA256SUMS.txt` 的记录及本机解出副本一致。
- `radio_source.commit` 为 `e9961becdc889266c3a8b5a488ff741159b6ad80`，仓库名为
  `gabin8/mt6572-connsys-modules`；记录了 `osal.h/osal.c`、`wmt_plat_alps.c`
  的 wakeup-source API 临时兼容修改，但没有保存完整补丁。
- `modules` 中的 `wlan_gen2.ko` 明确为 956276 B、SHA256 `2cf27905…80e9b`，
  与当前 staging 的实际文件一致；同包 `verification.json` 也记录相同模块哈希。
- 清单还记录了 kernel commit `c6501138edf2ea6458c7c4370d2fc1033d54f50a`，
  并明确 `working_tree_dirty=true`，因此仅有该 kernel commit 仍不能还原构建环境。

这是更具体的候选构建来源，仍未恢复精确出货源码。归档内部校验只能证明这些记录
相互一致，不能证明清单的 `build_reproducible_from_source=true` 已被本轮重建验证。
不得把 `e9961bec…` 的未补丁源码或当前 `165df1b4…` 直接当成目标模块源树。
下一步应恢复该历史提交、完整临时补丁及当时的脏内核配置/符号表，再解释或复现
956276 B 的目标产物；8 月日志中的 958920 B 构建和 Alpine 的 958904 B 均非此产物。
机器可读复核在 `out/z1_recovery_20261112_233945/wifi_source_provenance_review.json`。

## 告警与故障不能混为一谈

`fork_linux/net/core/dev_addr_lists.c:512-521` 的 `dev_addr_check()` 返回 void；它比较地址及 shadow 后报警，本身不返回网卡注册失败。`mainline_recon/z1_boot_r14_verify.log:2049-2061` 的一次告警来自 `ifconfig -> __dev_open`；第30590行出现 `(unregistered)` 是另一上下文。必须分别检查 `register_netdev` 返回值、`/sys/class/net/wlan0` 和扫描/连接结果。

恢复源码后应修复直接写 `dev_addr` 的路径，使用主线地址更新 API 同步 shadow 和地址树；不要通过关闭 WARN 掩盖问题。上游已经有该 API 的事实，也说明精确版本确认是修复前提。

## 只读验收脚本

从项目根运行：

```sh
python3 z1a9_gpt/scripts/z1_wifi_module_identity.py \
  --module out/z1_wifi_stage/modules/wlan_gen2.ko \
  --image out/z1_v35_selftest/system_v35.img \
  --image out/z1_driver_candidates_20261110/system_peripherals_v1.img
```

返回 0 表示所有输入等于上述 v35 模块；返回 1 表示至少一件缺失、ELF 畸形、模块名/ABI/哈希不符。`--prepatch-rebuild PATH` 可核对恢复源码后的**未改动**重编产物。`--output FILE` 可写新 JSON 证据文件，拒绝覆盖已有文件。该工具不执行 insmod、不接设备、不写镜像。

验证覆盖：现行模块及两份镜像匹配；旧 Alpine 模块被拒绝；缺失镜像被拒绝；截短 ELF、错误机器类型被拒绝；内容被更改的模块判为不匹配。

2026-11-13 主机收尾复核：`python3 -m unittest -v scripts/test_z1_wifi_module_identity.py` 的 18 项测试通过，覆盖同 ABI 异哈希、异模块名、损坏 ELF、缺失输入、debugfs 超时或静默缺文件、混合输入中一件失败，以及证据文件拒绝覆盖。测试对 debugfs 的调用使用 mock，断言只读参数和固定模块路径；真实现行模块另行得到 MATCH，真实旧 Alpine 模块得到 MISMATCH。结果及日志保存在 `out/z1_recovery_20261112_233945/wifi_final_validation.json` 与同目录 `wifi_final_*.log`。本次未重复扫描大镜像，也未访问设备。
