# Stock packer：关键 ramdisk 可执行文件检查

适用于 `scripts/z1_make_stock_android9.py`；这里只覆盖 `/init` 和 `/z1_stock_mount_guard` 的最终 newc 元数据与内容保护。不改变 device.mk、build_z1.sh 或 board init。

## 输入与最终读回要求

| 条目 | 要求 | 处理 |
|---|---|---|
| `/init` | root:root、regular、nlink=1、owner 有 read/execute、无 setuid/setgid/sticky 或 group/world write 位 | 输入必须满足，输出保留原字节和权限；最终 newc 再检查 |
| `/z1_bootdiag` | root:root、regular、nlink=1、精确 0755 | 原条目即使是 symlink/hardlink/非 root 也会被改成新 inode 的可信 helper 条目；最终 newc 检查 ACM helper 可执行 |
| `/z1_stock_mount_guard` | root:root、regular、nlink=1、精确 0755 | 不继承旧条目的 owner/link/type；替换时分配新 inode 并清设备字段；新条目也按相同规则生成 |
| `/init.z1.rc` | root:root、regular、nlink=1、owner 可读、无特殊位或 group/world write | 最终 newc 读回检查配置内容和安全 metadata |
| USB ACM 日志 | `init.z1.rc` 在 init 阶段启动 `z1_acm_log`，service 执行 `/z1_bootdiag --acm-stock` | 不依赖已从 `init.usb.rc` 移除的通用 property action；最终 cpio 读回必须保留 start action 和 service |
| 内容 | 非空、newc filesize 与实际长度一致，最终字节等于选定输入 init/guard | 使用显式 ValueError，`python -O` 不能关闭检查 |
| ELF | ELF32 little-endian ARM、ET_EXEC、完整 program-header table、有完整 PT_LOAD、无 PT_DYNAMIC/PT_INTERP | 输入及最终内容都验证；拒绝截断 ELF 和共享对象 |

`ramdisk_index()` 还拒绝归一后的重复路径（例如 init 与 ./init）、绝对路径、父目录跳转及空/内部 dot 路径分量，避免 dictionary 覆盖掩盖真实解包覆盖。

这些规则证明最终条目的元数据及内容与选定来源一致，不证明外部 init 二进制的全部语义、板级启动或任意预编译 guard 与本仓源码相同。默认从本仓源码构建 guard 仍是推荐入口。本次专项没有构建 helper 或 boot 镜像。

## 可执行的静态依据

可直接导入生产函数，不调用 main、编译器或打包输出。已有 guard 产物存在时：

```sh
python3 -O - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, 'scripts')
import z1_make_stock_android9 as packer
elf = Path('out/mount_guard/z1_stock_mount_guard').read_bytes()
fields = [1, 0o100755, 0, 0, 1, 0, len(elf), 0, 0, 0, 0, 0, 0]
index = {'z1_stock_mount_guard': (b'z1_stock_mount_guard', fields, elf)}
packer.check_boot_executable(index, 'z1_stock_mount_guard', elf, 0o100755)
fields[2] = 1000
try:
    packer.check_boot_executable(index, 'z1_stock_mount_guard', elf, 0o100755)
except ValueError:
    print('PASS: non-root owner rejected even with python -O')
else:
    raise RuntimeError('non-root owner accepted')
PY
```

2026-10-05 实际静态检查：在 python -O 下复用生产函数，17 项负例均拒绝，覆盖 symlink、group writable、特殊 mode、不可执行、非 root owner/group、多 link、错误 filesize/内容、重复/危险路径、截断 ELF、PT_INTERP、ET_DYN。既有原厂路线候选 init 与既有静态 guard 通过；纯内存 newc encode/readback 确认旧 symlink/non-root/multi-link guard 元数据被重置且输入 fields 不被意外修改。git diff --check 通过。没有新建 boot 镜像、整构建、提交、上传或清理工作区。
