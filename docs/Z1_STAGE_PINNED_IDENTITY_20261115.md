# Z1 stage 持久覆盖身份钉扎（2026-11-15）

## 解决的问题

`/data/local/tmp/z1stage/active/` 会跨 boot、system 与 data 清理保留。旧语义让其中
任意的 `hwcomposer.z1.so` / `gralloc.z1.so` 无条件压过新 boot.img 烘入的 ramdisk
candidate。于是刷入一个新 HWC 后，板上仍可能运行上一轮的 active 文件，而日志中的
这一轮结论会被错误归因。

新语义把当前 ramdisk candidate 设为 boot identity authority。persistent active
仍然是开发通道，但必须证明自己属于当前 boot。

## 运行时规则

对 `hwcomposer.z1.so` 与 `gralloc.z1.so` 分别执行：

1. `active/<name>` 与当前 `/<name>` 逐字节相同时，允许 active 生效。将
   `/<name>.ramdisk` 复制到 active 的常规操作因此不需要额外文件。
2. 自定义 active 与当前 candidate 不同，但
   `active/.<name>.pin` 仅含当前 candidate 的 SHA256 时，允许 active 生效。
3. candidate 不存在时，pin 使用当前 system 模块的 `system:<SHA256>`；candidate
   与 system 模块都不存在时使用 `absent`。
4. pin 缺失、pin 来自其他 candidate、pin 或 active 是符号链接时，保留两者的原始
   字节，记录 `WARN ... ignored`，并回退当前 ramdisk candidate；没有 candidate
   时回退 system 模块。
5. kill switch 与显式 rollback 仍删除 `/vendor` 覆盖，并令实际来源为 system。

pin 是固定路径下的一行文本，不执行 active 目录中的代码。`active/` 仍是由 shell
用户可写的开发目录；该机制防止无意混用版本，不构成抵御已取得该用户权限者的安全
边界。

## 板上可读身份

stage 完成后，每个模块发布：

| property | 值 |
|---|---|
| `z1stage.<module>` | `active` / `ramdisk` / `system` |
| `z1stage.<module>.source` | `active:candidate-match` / `active:pinned` / `ramdisk` / `system` |
| `z1stage.<module>.pin` | `candidate-match` / `match` / `mismatch` / `missing` / `system` |
| `z1stage.<module>.boot` | 当前 ramdisk candidate SHA256、`system:<sha>` 或 `absent` |
| `z1stage.<module>.sha` | 最终 `/vendor` 文件 SHA256；回退时为 `system:<system-sha>` |

`z1_recovery_probe.sh` 的 version 4 输出同时读取这些 property，并只对固定白名单中的
active 文件和 pin 文件做元数据与 SHA256 取证。它不执行 hook，也不输出文件正文。

## 操作方式

在线安装自定义 HAL 使用：

```sh
Z1LOG=/tmp/z1_serial.log \
Z1DEV=/dev/ttyUSB0 \
z1a9_gpt/scripts/z1deploy/z1_install_hal.sh install \
  path/to/hwcomposer.z1.so hwcomposer
```

安装器上传并核对文件后，把当前 `hwcomposer.z1.so.ramdisk` 的 SHA256（或 system
模块身份）写入 `active/.hwcomposer.z1.so.pin`，再安装 `/vendor` 覆盖。rollback
会同时删除 active 文件和对应 pin。

手工激活当前 candidate 可继续使用：

```sh
cp /data/local/tmp/z1stage/hwcomposer.z1.so.ramdisk \
   /data/local/tmp/z1stage/active/hwcomposer.z1.so
```

不要用未 pin 的旧工作副本覆盖 active 后假定它会跨新 boot 生效。

## 主机验证

- `dash -n scripts/z1deploy/z1stage_boot.sh`
- `bash -n scripts/z1deploy/z1_install_hal.sh`
- `python3 scripts/z1deploy/test_z1stage_boot.py`：37 项通过
- `python3 scripts/z1deploy/test_z1_recovery_probe.py`：20 项通过

故障测试覆盖候选完全匹配、正确 pin、`absent` pin、缺失 pin、旧 pin、candidate
缺失时回退 system、active/pin 符号链接拒绝，以及原有安装失败和存储门禁路径。

本轮只做主机实现与测试，未刷板，未删除板上 persistent active/kill-switch，也未声称
真实 Android 进程已加载某一候选。上板后应先比较 `z1stage.<module>.boot`、`.source`、
`.pin`、`.sha` 与实际 `/vendor` 文件，再判断显示功能。
