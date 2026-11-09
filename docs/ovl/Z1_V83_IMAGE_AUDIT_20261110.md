# Z1 v83 boot.img 独立字节审计（2026-11-10）

> **审计对象**：`out/z1_v83/boot.img`，**5,900,288 B**，sha256 `5b8d9c0a30a2f3e38eb99a697fa9e0ace9403d79cf2e698faf3ff27f36b5cc25`
> （任务里写的 5,900,288 B / builder 报告 2026-11-09 21:58 那一版）。
>
> **方法**：不采信 `pack_boot_report.txt` 的任何结论；所有数字都由镜像字节重新推导（解析器/命令见附录 A）。
> 报告只把 builder 的声明当作“待验证假设”，逐条重算后给结论。
>
> **时间**：2026-11-09 22:00–22:20 CST。审计期间工作区被并发重建（见 §1），
> 因此本报告的**每一个数字都只对上面这个 sha256 的字节序列成立**。

---

## 0. 判定（结论先行）

**PASS-WITH-NOTES** —— 对 5,900,288 B / `5b8d9c0a…` 这一确切字节序列：布局、尺寸关系、cpio 记录、`/init.z1.rc`、暂存二进制的 hash/ELF/HMI 全部通过；风险都在“部署语义 / 操作流程 / 未经上板的内核与 HAL”层面，不是镜像结构缺陷。

**三个最重要的数字**

| # | 数字 | 值 | 证据 |
|---|------|----|------|
| 1 | 镜像尺寸（分区余量） | **5,900,288 B**；自限 6,000,000 余 99,712 B；6 MiB 分区（6,291,456）余 **391,168 B = 382 KiB** | `stat -c%s`；§2.2 页对齐算术 |
| 2 | sha256 | **`5b8d9c0a30a2f3e38eb99a697fa9e0ace9403d79cf2e698faf3ff27f36b5cc25`** | `sha256sum out/z1_v83/boot.img`（22:02 快照） |
| 3 | 内核/ramdisk 尺寸关系 | **kernel_size 4,335,091 = 4,334,579 + 512**；**ramdisk_size 1,561,825 = 1,561,313 + 512** | MTK KERNEL/ROOTFS 头 size 字段（§2.3） |

**操作员首 60 秒要盯的一件事**

串口日志里必须先看到

```
z1stage: done @<N>s: seeded=… working_copies=… applied=… removed=… errors=0
```

且它的时间戳要**早于** `init: Command 'class_start hal' action=boot (/init.rc:673) took … and succeeded`。
若这行缺失、`errors>0`、或出现 `Command 'exec … /z1stage_boot.sh' … timed out`，说明部署 exec 没跑完/被 init 超时砍掉——此时 `active/` 集合没被应用，板子会退回 `/system` 的原厂 HAL（能开机，但不是这次要测的状态）。

---

## 1. 审计目标漂移（必读，影响“刷哪个文件”）

审计开始时（22:02）路径上确实是 5,900,288 B 的目标镜像；审计进行中该路径被重建了两次，
现在**已经不是**本报告描述的那份字节。时间线（全部由 `stat`/`sha256sum` 实测）：

| 时间（CST） | 事件 | 大小 | sha256 |
|---|---|---|---|
| 2026-11-09 21:58:01 | 本任务指定的构建（builder 报告写成） | 5,900,288 | `5b8d9c0a30a2f3e38eb99a697fa9e0ace9403d79cf2e698faf3ff27f36b5cc25` |
| 22:02–22:07 | 本次审计读取/解析（解析输出、cpio、各段提取） | 5,900,288 | `同上（全程字节一致）` |
| 22:11:13 | `out/z1_v83/stage/hwcomposer.z1.so` 更新 | 143,048 B | `ef6389e3dfc8ec4ef0de7f1de43d8b65b2ee0dbe13a8aab18907b25a33807a01` |
| 22:11:16 | `out/z1_v83/boot.img` 被重打 | 5,902,336 | `306fe78ecbbf91d6d7413aa9d9f0ef81d67bf5ba06d198b7ed95e8e8e756963b` |
| 22:12:44 | `z1deploy/z1stage_boot.sh` 变为 11,968 B | 11,968 B | `e2c9b9e709c17a3a60e713ce8d48e5daf86d2a9e7ecea4af183bcbee34b70ec8` |
| 22:13:15 | `boot.img` 再次写出（内容同上） | 5,902,336 | `306fe78e…` |
| 22:16 | 当前 `z1hwc2.cpp` 重编得 143,272 B（与两版镜像都不同，源码仍在变） | 143,272 B | `f355896b853f805ce3f37db2b0da28dc595455b4d692e7d76d6791e32857d458` |

结论：

1. 本报告的一切结论只对 `5b8d9c0a…` / 5,900,288 B 成立；**该字节序列已不在 `out/z1_v83/boot.img` 路径上**。
   我用解析出的各段在 `/tmp/z83audit/boot_v83_5900288_RECON.img` 重建了一份，sha256 与审计值逐字节一致
   （证明 §2 的布局解析是完备的；/tmp 是临时目录，重启即失）。
2. 当前路径上的 5,902,336 B / `306fe78e…` 与审计版仍是同一套记录集（85 条），只替换了 2 条：
   `z1stage_boot.sh`（9,130/`892566a8` → 11,968/`e2c9b9e7`）和 `hwcomposer.z1.so`（142,360/`fb45175e` → 143,048/`ef6389e3`）；
   其余 83 条（含 `init.z1.rc` `454e1beb`、`gralloc.z1.so` `0ed9fb2b`、`z1kmstest` `19541326`、`z1rootd.sh` `0ab441da`）逐字节不变。
3. **不要按路径刷写**。刷写前先跑 §8 的一行命令，确认打印出的 `size=`/`sha256=` 与你批准的那份一致。

---

## 2. 字节级布局

### 2.1 ANDROID! 头逐字段（v79 对照）

解析方式：`struct.unpack('<8s 10I 16s 512s 8I 1024s', d[:1632])`（标准 boot image v0 头，1632 B，页填充到 2048）。

| 字段 | 偏移 | 长度 | v79（已知可启动） | v83（审计对象） | 判定 |
|---|---|---|---|---|---|
| `magic` | 0x000 | 8 | ANDROID! | ANDROID! | same |
| `kernel_size` | 0x008 | u32 | 4,335,091 | 4,335,091 | same |
| `kernel_addr` | 0x00C | u32 | 0x10008000 | 0x10008000 | same |
| `ramdisk_size` | 0x010 | u32 | 1,470,144 | 1,561,825 | **CHANGED**（ramdisk 内容增长，预期内） |
| `ramdisk_addr` | 0x014 | u32 | 0x11000000 | 0x11000000 | same |
| `second_size` | 0x018 | u32 | 0 | 0 | same |
| `second_addr` | 0x01C | u32 | 0x10f00000 | 0x10f00000 | same |
| `tags_addr` | 0x020 | u32 | 0x10000100 | 0x10000100 | same |
| `page_size` | 0x024 | u32 | 2048 | 2048 | same |
| `dt_size` | 0x028 | u32 | 0 | 0 | same（本设计把 DTB 追加在 zImage 尾部，见 §2.4） |
| `unused` | 0x02C | u32 | 0 | 0 | same |
| `name` | 0x030 | 16 | z1_v79hwc | z1_v83 | **CHANGED**（仅标识，无功能影响） |
| `cmdline` | 0x040 | 512 | 302 字符 | 302 字符 | **逐字节相同**（见 §2.1 末尾） |
| `id[0..7]` | 0x240 | 32 | 29c3ae28 2c8137c6 + 24×0 | 808e0af5 8ea66672 + 24×0 | **CHANGED**（内容哈希，规则与 v79 相同，见 §6.7） |
| `extra_cmdline` | 0x260 | 1024 | 空 | 空 | same |

**头字段结论一句话**：v79→v83 只有 `ramdisk_size`（ramdisk 内容增长，预期）、`name`（仅标识）、`id[8]`（内容哈希）三处变化；
`kernel_size`、`kernel_addr`、`ramdisk_addr`、`second_*`、`tags_addr`、`page_size`、`dt_size` 全部逐字节相同——
**没有一个“意外的”字段变化**。

`cmdline`（两版 302 B 逐字节相同）：

```
console=ttyS0,921600n8 root=/dev/ram panic=10 loglevel=7 clk_ignore_unused
regulator_ignore_unused pd_ignore_unused oops=panic sysrq_always_enabled rdinit=/init
earlycon=mtk8250,0x11005000 androidboot.hardware=z1 androidboot.selinux=permissive
firmware_class.path=/system/etc/firmware printk.devkmsg=on
```

（原文是一行，302 字符；NUL 填充到 512 B，尾部全 0。`console=`、`androidboot.hardware=z1`、
`androidboot.selinux=permissive` 三项都在。）

### 2.2 布局与偏移

page_size = 2048，页对齐规则与 mkbootimg 一致：

| 偏移 | 长度（B） | 内容 |
|---|---|---|
| 0x000000 | 2,048 | ANDROID! 头（1,632 B 有效 + 416 B 零填充） |
| 0x000800 | 512 | MTK KERNEL 头：magic `0x58881688`、size 4,334,579、`"KERNEL"`，其余 0（非零仅 13 B） |
| 0x000A00 | 4,334,579 | kernel payload = zImage（4,313,600）‖ dtb（20,979） |
| 0x422DF3 | 525 | 页对齐零填充（全部为 0） |
| 0x423000 | 1,561,825 | MTK ROOTFS blob = 512 B MTK 头 + 1,561,313 B gzip 流 |
| 0x5A04E1 | 799 | 页对齐零填充（全部为 0） |
| 0x5A0800 | — | 文件结束 = 5,900,288 B（页对齐） |

kernel 段结束于 `0x800+0x4225F3 = 0x422DF3`，页对齐到 `0x423000`（gap 525 B 全 0）；
ROOTFS 段结束于 `0x5A04E1`，页对齐到 `0x5A0800` = 文件尾（799 B 全 0）。
头区 1632–2048 也全 0（v79/v83 都是）。

### 2.3 尺寸关系证明（本项目最危险的坑）

**内核**：

- MTK KERNEL 头 `size` 字段（偏移 0x804）= **4,334,579**
- 实际 kernel payload 字节数 = `kernel_size(4,335,091) − 512 = 4,334,579` → **完全相等**
- 反向：`kernel_size − payload = 512` 恰为 MTK 头长度。若打包时把 `kernel_size` 写成 payload 值（少 512），
  section 末尾 512 B 会被排除在外；而 payload 的**最后 512 B 就是 appended DTB 的尾部**（payload 无任何尾部填充，见 §2.4），
  正好复现“截断 DTB 尾 → 启动砖”的已知故障模式。本镜像**没有**犯这个错。

**ramdisk**：

- MTK ROOTFS 头 `size` 字段（偏移 0x423004）= **1,561,313**
- 实际 gzip 流字节数 = `ramdisk_size(1,561,825) − 512 = 1,561,313` → **完全相等**
- 若 `ramdisk_size` 少 512，gzip 流尾部 512 B（cpio 归档/记录）会被切掉。

### 2.4 kernel payload 恰为 `zImage ‖ dtb`

| 对象 | 大小 | sha256 |
|---|---|---|
| `fork_linux/arch/arm/boot/zImage` | 4,313,600 | `9af27204a17e91209c0a96e267822f960d58edaf5c48db377d2b97a36a2246fc` |
| `.../dts/mediatek/mt6572-z1.dtb` | 20,979 | `444c944dcc7d91feed27680b6fcf46bee01124e43770d83204b084f65f9066d3` |
| 两者拼接（4,334,579） | — | `6d87964026d6144a7320a0bfeb91f7b8e4ca1c68841edfbd2a97b34f4a900743` |
| **v83 payload（从镜像取出）** | **4,334,579** | **`6d879640…`（与拼接值相同）** |

逐字节检查：`payload[:4313600] == zImage`（True），`payload[4313600:] == dtb`（True）；
DTB magic `d00dfeed` 位于 payload 偏移 **4,313,600**，DTB `totalsize = 20,979` = DTB 文件长度；
zImage ARM 头 magic `0x016f2818`（0x24 处），`end = 0x41d200 = 4,313,600` = zImage 文件长度。
payload 后面没有多余字节（`kernel_size` 把 512 B MTK 头算在内，payload 到 DTB 最后一字节为止）。

> **注意（风险 R3）**：v79 的 kernel 段大小相同（4,334,579）但内容不同：
> v79 zImage sha256 = `1baaa00d…`，v83 zImage = `9af27204…`；DTB 两版相同。
> 解压内核镜像得到的版本串：v79 = `#159 SMP Mon Nov 9 19:45:27 CST 2026`，v83 = `#160 SMP Mon Nov 9 21:48:02 CST 2026`。
> 所以 v83 **不是**“只换 ramdisk”的增量。

### 2.5 MTK wrapper（KERNEL / ROOTFS 头）

KERNEL 头（512 B，位于 0x800），前 64 B：

```
88 16 88 58 f3 23 42 00 4b 45 52 4e 45 4c 00 00
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
```

- magic = `0x58881688`（小端 `88 16 88 58`），size = `0x004223f3` = 4,334,579，type = `KERNEL`
- 整个 512 B 只有 13 个非零字节，其余全 0。

ROOTFS 头（512 B，位于 **0x423000** = ROOTFS blob 起点），前 64 B：

```
88 16 88 58 e1 d2 17 00 52 4f 4f 54 46 53 00 00
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
00 00 00 00 00 00 00 00 ff ff ff ff ff ff ff ff
ff ff ff ff ff ff ff ff ff ff ff ff ff ff ff ff
```

- magic = `0x58881688`，size = `0x0017d2e1` = 1,561,313，type = `ROOTFS`
- 与 `/tmp/rootfs_v79.bin`（= v79 镜像 ROOTFS 段，已逐字节验证）的头相比**只有 size 字段的 4/5/6 三个字节不同**
  （1,469,632 → 1,561,313）；`0xff` 尾（472 B）是从已知可启动的 v79 原样继承的，
  LK 只读 magic/size/type 后跳过 512 B，因此该 0xff 尾无功能影响（v79 上板已证明）。

### 2.6 ROOTFS blob 首/尾 64 B 与 gzip 解码

ROOTFS blob 起点 **0x423000**，长度 **1,561,825 B**（= 512 B 头 + 1,561,313 B gzip）。

首 64 B（含 MTK 头）：

```
88 16 88 58 e1 d2 17 00 52 4f 4f 54 46 53 00 00
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
00 00 00 00 00 00 00 00 ff ff ff ff ff ff ff ff
ff ff ff ff ff ff ff ff ff ff ff ff ff ff ff ff
```

gzip 流首 64 B（偏移 0x423200）：

```
1f 8b 08 00 00 00 00 00 02 03 bc bd 0b 60 54 d5
99 38 7e 67 92 40 18 88 3c 44 0a a2 78 99 04 99
00 99 cc 24 e1 15 8c cd 63 32 24 bc 92 92 a8 a8
d8 e1 66 e6 26 73 9b 79 39 77 26 0f 1e 6b a8 6e
```

尾 64 B（blob 尾 = gzip 流尾，中间无额外填充）：

```
4b 8e 68 62 7a 3f e3 f2 af 19 99 87 ff 96 50 ba
cf fa 49 17 98 42 ff c6 05 6c 7f 65 81 93 fe 8d
0b fc ef fe 66 5a 81 85 f3 67 5c 5a 71 c9 fc 91
23 47 8a ff 87 7f ff 03 a7 fc 93 3b 30 4a 2e 00
```

gzip 流解码结果（`zlib.decompressobj(16|MAX_WBITS)`）：

| 项 | 值 |
|---|---|
| 压缩长度 | **1,561,313 B**（= MTK ROOTFS size 字段；用完整个 blob 剩余） |
| 解压后 cpio | **3,033,648 B**，sha256 `f55ab4b3c909e991241e9ec39f9c6998a8756ba65550b17e308bbed0efc60249` |
| 流完整性 | `eof=True`，`unused_data` 长度 0，gzip CRC 由 zlib 校验通过 |
| gzip trailer（最后 8 B） | `a7 fc 93 3b 30 4a 2e 00` → ISIZE=`0x002e4a30`=**3,033,648**，与解压长度一致 |
| 压缩比 | 3,033,648 / 1,561,313 ≈ 1.94 |

（对照 v79：压缩 1,469,632 B，解压 2,831,760 B，81 条记录。）

---

## 3. ramdisk（newc cpio）记录审计

### 3.1 总览

| 项 | 实测 |
|---|---|
| cpio 总长 | 3,033,648 B |
| 记录数 | **85**（84 条 + `TRAILER!!!`） |
| 类型分布 | 14 × `040000`（目录）、52 × `0100000`（普通文件）、19 × `0120000`（符号链接） |
| **缺文件类型位的记录** | **0 条**（项目历史上会“内核静默丢弃”的那种记录不存在） |
| 重名记录 | 无 |
| 目录/符号链接模式 | `.`=040775、`sbin`=040750、`storage`=040751、`cache`=040770、`data`=040771、`config`=040555；19 条 symlink 均为 0120777 |
| ino 异常 | `ino=810731` 被 **7 条**记录共用（4 条新增 + 3 条基础记录），但全部 `nlink=1`；内核 `maybe_link()` 只在 `nlink>=2` 时按 (ino,dev) 做硬链接，故**无实际影响**。v79 基础归档里该 ino 已被 3 条共用（继承行为）。 |
| TRAILER | 124 B 原始记录，v79/v83 逐字节相同：`070701…TRAILER!!!\0\0` |
| TRAILER 之后 | 72 B 全 0（v79 也是 72 B），gzip 流在此之后结束 |

### 3.2 新增/替换的记录（全部从**最终镜像**里取出）

| 动作 | 记录 | 大小（B） | mode | sha256 | 说明 |
|---|---|---|---|---|---|
| replace | `init.z1.rc` | 6,138 → 7,569 | 0100750 | `454e1beb23659a0be5562b00125d956dc587d8e30c1f2cb97396c367c4a5bcb9` | 追加部署块（§4.1） |
| replace | `hwcomposer.z1.so` | 109,296 → 142,360 | 0100644 | `fb45175e7d56bee148f61783fe24f9260a5e473b2b1d120489c9239a8a245cba` | 换成 21:56 的新 HWC2 构建（§5） |
| add | `z1stage_boot.sh` | 9,130 | 0100755 | `892566a84b5fa560958e99275fb713387e474674700ef76d30a0f2f6b1152727` | 部署脚本（注：已被后续修订，§6.2） |
| add | `z1rootd.sh` | 6,240 | 0100755 | `0ab441da8956130adbe691f6339aa7df775a9a3b23a4c7f9fd2fe384b9fd374a` | root 命令守护 |
| add | `gralloc.z1.so` | 28,548 | 0100644 | `0ed9fb2b573bfe4bf2e533b7f709ad054a1dc95482d420aca184d6c95823b77d` | 新 gralloc（dma-heap 路径） |
| add | `z1kmstest` | 122,976 | 0100755 | `1954132627e18d96cc5fe4d2c2baa652d122aa0c2df75618c94fa57b7820ccaf` | KMS 自检工具 |

### 3.3 与 `/tmp/rootfs_v79.bin` 的逐记录 diff

先证明 base 就是 v79 镜像里的那份：`/tmp/rootfs_v79.bin` 1,470,144 B，sha256 `9f069ed1d3945b2d3ad7370e61d3ef1b4db69ecb94d8481c93236087a3b2b2c3`
== v79 `boot.img` 里 0x423000 起的 ROOTFS 段（**逐字节相同，True**）。然后：

| 项 | v79 base | v83 | 备注 |
|---|---|---|---|
| 共同记录数（含 TRAILER） | 81 | 81 | — |
| 逐字节相同的记录 | — | 79（78 条普通记录 + TRAILER） | 含全部 .ko/.sh/.rc/prop/sepolicy/init |
| 有差异的记录 | — | 2 | `init.z1.rc`（6138→7569）、`hwcomposer.z1.so`（109296→142360） |
| 新增记录 | — | 4 | z1stage_boot.sh / z1rootd.sh / gralloc.z1.so / z1kmstest |
| 被删除记录 | — | 0 | — |
| 记录顺序 | 1..81 | 原 80 条顺序不变，TRAILER 从 80 移到 84 | 新记录只插在 TRAILER 之前 |
| TRAILER 原始字节 | 124 B | 124 B 完全相同 | `070701…TRAILER!!!\0\0` |
| TRAILER 之后 | 72 B 全 0 | 72 B 全 0 | gzip 流内尾部填充，与 v79 相同 |

两条“变了”的记录的细节：

- `init.z1.rc`：6,138 → 7,569 B；diff 只有“在 EOF 追加 1,431 B（`\n` + 1,430 B fragment）”，前缀 6,138 B 原样保留（§4.1 证明）。
- `hwcomposer.z1.so`：109,296 → 142,360 B；这是换成了 2026-11-09 21:56 的新 HWC2 构建（旧版是 v79 的 HWC2 构建），
  cpio 头除 `filesize`/数据外无变化（ino/mode/uid/gid/mtime 保持）。

### 3.4 全量记录表（85 条，直接来自最终镜像）

| # | name | size | mode | uid:gid | nlink | data sha256（前 16 hex） |
|---|------|-----:|------|---------|-------|------------------------|
| 0 | `.` | 0 | 040775 | 0:0 | 15 | `e3b0c44298fc1c14` |
| 1 | `plat_hwservice_contexts` | 7,212 | 0100644 | 0:0 | 1 | `852ec77ce415d58b` |
| 2 | `product` | 15 | 0120777 | 0:0 | 1 | `c0b274b992ff0d8f` |
| 3 | `z1_audio_probe.sh` | 700 | 0100755 | 0:0 | 1 | `8eaee82dc2f3da56` |
| 4 | `sbin` | 0 | 040750 | 0:0 | 2 | `e3b0c44298fc1c14` |
| 5 | `plat_service_contexts` | 14,700 | 0100644 | 0:0 | 1 | `19a3921576befaac` |
| 6 | `z1_art_seed.sh` | 1,528 | 0100755 | 0:0 | 1 | `2071e548f2a3f23b` |
| 7 | `ueventd.z1.rc` | 466 | 0100644 | 0:0 | 1 | `1cb150de0952f13f` |
| 8 | `plat_property_contexts` | 7,102 | 0100644 | 0:0 | 1 | `b23709b086265e0a` |
| 9 | `dev` | 0 | 040755 | 0:0 | 2 | `e3b0c44298fc1c14` |
| 10 | `fstab.z1` | 1,232 | 0100640 | 0:0 | 1 | `529753c44e37e46e` |
| 11 | `charger` | 13 | 0120777 | 0:0 | 1 | `20c053e10688ef4d` |
| 12 | `d` | 17 | 0120777 | 0:0 | 1 | `5a5c30126e2ec878` |
| 13 | `z1_gps_prep.sh` | 2,416 | 0100755 | 0:0 | 1 | `4042b0557905bca6` |
| 14 | `ueventd.rc` | 5,359 | 0100644 | 0:0 | 1 | `d40b80e5c1063191` |
| 15 | `z1_wifi_connect.sh` | 3,759 | 0100755 | 0:0 | 1 | `f7a401b051592b15` |
| 16 | `z1_boot_heal.sh` | 892 | 0100755 | 0:0 | 1 | `e083ecbb9c14b602` |
| 17 | `vendor_file_contexts` | 7,165 | 0100644 | 0:0 | 1 | `1d2c6c5ddc5dbf30` |
| 18 | `plat_seapp_contexts` | 1,551 | 0100644 | 0:0 | 1 | `62ef07f4b361026d` |
| 19 | `z1_fbcon_takeover.sh` | 1,437 | 0100755 | 0:0 | 1 | `c56a3843e081747a` |
| 20 | `z1_touch_load.sh` | 2,948 | 0100755 | 0:0 | 1 | `73a415c88d62c64f` |
| 21 | `z1_logpersist.sh` | 2,718 | 0100755 | 0:0 | 1 | `1aa6f042b26ff8f7` |
| 22 | `z1_provision.sh` | 1,483 | 0100755 | 0:0 | 1 | `8f5fe4869ce8ea01` |
| 23 | `z1_bootdiag` | 406,756 | 0100755 | 0:0 | 1 | `3759abd5894abf68` |
| 24 | `proc` | 0 | 040755 | 0:0 | 2 | `e3b0c44298fc1c14` |
| 25 | `oem` | 0 | 040755 | 0:0 | 2 | `e3b0c44298fc1c14` |
| 26 | `storage` | 0 | 040751 | 0:0 | 2 | `e3b0c44298fc1c14` |
| 27 | `system` | 0 | 040755 | 0:0 | 2 | `e3b0c44298fc1c14` |
| 28 | `sepolicy` | 384,652 | 0100644 | 0:0 | 1 | `e01a4c589f1363bb` |
| 29 | `config` | 0 | 040555 | 0:0 | 2 | `e3b0c44298fc1c14` |
| 30 | `init.zygote32.rc` | 511 | 0100750 | 0:0 | 1 | `2c49f23834f32566` |
| 31 | `init.rc` | 29,382 | 0100750 | 0:0 | 1 | `e7b97d1a10b30c3f` |
| 32 | `cache` | 0 | 040770 | 0:0 | 2 | `e3b0c44298fc1c14` |
| 33 | `sys` | 0 | 040755 | 0:0 | 2 | `e3b0c44298fc1c14` |
| 34 | `acct` | 0 | 040755 | 0:0 | 2 | `e3b0c44298fc1c14` |
| 35 | `init.environ.rc` | 1,154 | 0100750 | 0:0 | 1 | `475d3a9df35bb935` |
| 36 | `bin` | 11 | 0120777 | 0:0 | 1 | `5a846af391ce716f` |
| 37 | `init` | 1,637,512 | 0100750 | 0:0 | 1 | `285382ea87dada34` |
| 38 | `sdcard` | 21 | 0120777 | 0:0 | 1 | `3c52a31296303ea9` |
| 39 | `z1_klog_relay.sh` | 4,851 | 0100755 | 0:0 | 1 | `19dd1df2fd5af757` |
| 40 | `z1_wifi_pre.sh` | 3,103 | 0100755 | 0:0 | 1 | `d80e6aab3ba5b44c` |
| 41 | `z1_pm_guard.sh` | 2,228 | 0100755 | 0:0 | 1 | `d74c579b4476040a` |
| 42 | `regulatory.db` | 6,248 | 0100644 | 0:0 | 1 | `872b1654f84b09d7` |
| 43 | `odm` | 0 | 040755 | 0:0 | 2 | `e3b0c44298fc1c14` |
| 44 | `z1_sf_dump.sh` | 594 | 0100755 | 0:0 | 1 | `7bfe5e936c829f8f` |
| 45 | `vendor_seapp_contexts` | 0 | 0100644 | 0:0 | 1 | `e3b0c44298fc1c14` |
| 46 | `z1_cst836u.ko` | 14,068 | 0100644 | 0:0 | 1 | `b48245e3be139518` |
| 47 | `z1_radio_finish.sh` | 3,703 | 0100755 | 0:0 | 1 | `9adada4b764da924` |
| 48 | `vndservice_contexts` | 65 | 0100644 | 0:0 | 1 | `81594a08b6880f5a` |
| 49 | `bugreports` | 50 | 0120777 | 0:0 | 1 | `760916c0eec6d699` |
| 50 | `wifi-nvram-WIFI.bin` | 512 | 0100644 | 0:0 | 1 | `d28c5264265f5705` |
| 51 | `data` | 0 | 040771 | 0:0 | 2 | `e3b0c44298fc1c14` |
| 52 | `z1_heartbeat.sh` | 1,421 | 0100755 | 0:0 | 1 | `4a24dd003552319f` |
| 53 | `default.prop` | 1,598 | 0100600 | 0:0 | 1 | `38f4799fb9a1183b` |
| 54 | `init.z1.network.rc` | 6,675 | 0100750 | 0:0 | 1 | `358c274d37f3d591` |
| 55 | `vendor` | 14 | 0120777 | 0:0 | 1 | `ebc95e6449b8e848` |
| 56 | `z1_gpio_hunt.ko` | 18,592 | 0100644 | 0:0 | 1 | `1935d5651018e57f` |
| 57 | `z1_wifi_probe.sh` | 6,467 | 0100755 | 0:0 | 1 | `cbbbac7b55ddac71` |
| 58 | `init.z1.rc` | 7,569 | 0100750 | 0:0 | 1 | `454e1beb23659a0b` |
| 59 | `vendor_property_contexts` | 218 | 0100644 | 0:0 | 1 | `ad5da6b58f63561f` |
| 60 | `vendor_hwservice_contexts` | 1,989 | 0100644 | 0:0 | 1 | `383597d8c5d8b4df` |
| 61 | `z1_display_takeover.sh` | 825 | 0100755 | 0:0 | 1 | `f71e0393661c8560` |
| 62 | `plat_file_contexts` | 25,053 | 0100644 | 0:0 | 1 | `06e5046601ecafb8` |
| 63 | `vendor_service_contexts` | 0 | 0100644 | 0:0 | 1 | `e3b0c44298fc1c14` |
| 64 | `etc` | 11 | 0120777 | 0:0 | 1 | `fa7072dfb48cf30b` |
| 65 | `mnt` | 0 | 040755 | 0:0 | 2 | `e3b0c44298fc1c14` |
| 66 | `z1_wpa_start.sh` | 3,664 | 0100755 | 0:0 | 1 | `62b487207a3fe84b` |
| 67 | `odm/app` | 15 | 0120777 | 0:0 | 1 | `6baf243aaa7f64a9` |
| 68 | `odm/firmware` | 20 | 0120777 | 0:0 | 1 | `25802d5eebbceb98` |
| 69 | `odm/lib64` | 17 | 0120777 | 0:0 | 1 | `03d9fef5f2366ae4` |
| 70 | `odm/overlay` | 19 | 0120777 | 0:0 | 1 | `67f67e690ce38caf` |
| 71 | `odm/bin` | 15 | 0120777 | 0:0 | 1 | `ff48448a8a945088` |
| 72 | `odm/framework` | 21 | 0120777 | 0:0 | 1 | `010b5a62527a0442` |
| 73 | `odm/lib` | 15 | 0120777 | 0:0 | 1 | `f3f2c93f9f848c65` |
| 74 | `odm/priv-app` | 20 | 0120777 | 0:0 | 1 | `b643fcc5122e1374` |
| 75 | `odm/etc` | 15 | 0120777 | 0:0 | 1 | `33c86902a880dbe1` |
| 76 | `sbin/watchdogd` | 7 | 0120777 | 0:0 | 1 | `2d2c3fd77b630270` |
| 77 | `sbin/ueventd` | 7 | 0120777 | 0:0 | 1 | `2d2c3fd77b630270` |
| 78 | `z1drmtest` | 81,260 | 0100755 | 0:0 | 1 | `adaa9ddbc451b86e` |
| 79 | `hwcomposer.z1.so` | 142,360 | 0100644 | 0:0 | 1 | `fb45175e7d56bee1` |
| 80 | `z1stage_boot.sh` | 9,130 | 0100755 | 0:0 | 1 | `892566a84b5fa560` |
| 81 | `z1rootd.sh` | 6,240 | 0100755 | 0:0 | 1 | `0ab441da8956130a` |
| 82 | `gralloc.z1.so` | 28,548 | 0100644 | 0:0 | 1 | `0ed9fb2b573bfe4b` |
| 83 | `z1kmstest` | 122,976 | 0100755 | 0:0 | 1 | `1954132627e18d96` |
| 84 | `TRAILER!!!` | 0 | 0100000 | 0:0 | 1 | `e3b0c44298fc1c14` |

---

## 4. `/init.z1.rc` 审计

### 4.1 身份证明（逐字节）

| 对象 | 大小 | sha256 |
|---|---|---|
| 从 v83 最终镜像取出的 `/init.z1.rc` | 7,569 B | `454e1beb23659a0be5562b00125d956dc587d8e30c1f2cb97396c367c4a5bcb9` |
| 从 v79 镜像取出的 `/init.z1.rc` | 6,138 B | `1b497b013fd1e9e6b62a517f6039d6fc0500f1cb9ee25c701e78b9c2d8f21d22` |
| `z1a9_gpt/scripts/z1deploy/init_fragment.rc` | 1,430 B | `027a16763043e487f978e479ca0721d042f11c22c8e2c3e7272782488ce66066` |

- `rc_v83 == rc_v79 + b"\n" + fragment` → **True**（逐字节比较；v79 的 rc 本身以 `\n` 结尾，所以拼接后是一个空行分隔）。
- `rc_v83[:6138] == rc_v79` → True（v79 的 6,138 B 一个字节都没改）。
- builder 另外 dump 的 `out/z1_v83/embedded_init.z1.rc` 也是 `454e1beb…`（与独立提取一致，仅作交叉核对）。

### 4.2 服务（9 个）

| 服务 | 命令行 | class | user | group | seclabel | 标志 | 行号 |
|---|---|---|---|---|---|---|---|
| `watchdogd` | `/sbin/watchdogd 10 30` | core | root | root | `u:r:watchdogd:s0` | — | 25 |
| `z1_acm_log` | `/z1_bootdiag --acm` | core | root | root | `u:r:init:s0` | disabled | 54 |
| `z1_logcat_relay` | `/z1_bootdiag --logcat` | core | root | log | `u:r:init:s0` | disabled | 65 |
| `z1_klog_relay` | `/system/bin/sh /z1_klog_relay.sh` | core | root | root log | `u:r:init:s0` | disabled | 109 |
| `z1_sf_dump` | `/system/bin/sh /z1_sf_dump.sh` | core | root | root log | `u:r:init:s0` | disabled, oneshot | 116 |
| `z1_heartbeat` | `/system/bin/sh /z1_heartbeat.sh` | core | root | root | `u:r:init:s0` | disabled | 124 |
| `z1_logpersist` | `/system/bin/sh /z1_logpersist.sh` | core | root | root system sdcard_rw | `u:r:init:s0` | disabled | 131 |
| `z1_6620` | `/system/bin/6620_launcher -m 3 -p /system/etc/firmware/` | core | root | root wifi inet | `u:r:init:s0` | — | 148 |
| `z1_rootd` | `/system/bin/sh /z1rootd.sh` | core | root | root | `u:r:init:s0` | —（长驻，无 oneshot） | 176 |

### 4.3 `on` 块（11 个、30 条命令）

| 行号 | 触发器 | 命令数 | 命令 |
|---|---|---|---|
| 6 | `on fs` | 1 | `mount_all /fstab.z1` |
| 11 | `on init` | 1 | `start watchdogd` |
| 19 | `on property:ro.persistent_properties.ready=true` | 2 | `setprop persist.sys.usb.config none`；`setprop sys.usb.config none` |
| 32 | `on post-fs-data`（legacy 主体） | 7 | `swapon_all /fstab.z1`；`exec_background … z1_art_seed.sh`；`… z1_boot_heal.sh`；`… /z1drmtest`；**`exec … cp /hwcomposer.z1.so /vendor/lib/hw/…`**；**`exec … chmod 0644 …`**；`restorecon_recursive /system/etc` |
| 47 | `on post-fs` | 1 | `exec_background … /z1_bootdiag` |
| 51 | `on init` | 1 | `start z1_acm_log` |
| 62 | `on post-fs-data` | 1 | `start z1_logcat_relay` |
| 73 | `on property:sys.boot_completed=1` | 3 | `exec … z1_display_takeover.sh`；`exec_background … z1_pm_guard.sh`；`exec_background … z1_provision.sh` |
| 89 | `on post-fs` | 10 | `write /sys/power/wake_lock z1diag`；`start z1_heartbeat`；`start z1_logpersist`；`start z1_klog_relay`；`start z1_sf_dump`；`exec_background ×5（display_takeover / z1_linux7_radio_init / z1_radio_finish / z1_wifi_probe / z1_touch_load）` |
| 145 | `on property:sys.z1.radio.modules_ready=1` | 1 | `start z1_6620` |
| 170 | `on post-fs-data`（**新增部署块**） | 2 | **`exec u:r:init:s0 root root -- /system/bin/sh /z1stage_boot.sh`**；**`start z1_rootd`** |

命令直方图：`exec` 4、`exec_background` 11、`start` 9、`setprop` 2、`mount_all` 1、`swapon_all` 1、`restorecon_recursive` 1、`write` 1（合计 30）。

### 4.4 静态语法检查（Android 9 init 语义）

| 检查 | 结果 |
|---|---|
| Tab 字符 | **0**（全文无 `\t`） |
| CRLF / CR | **0**（无 `\r`，LF only） |
| 末尾换行 | 有（文件以 `\n` 结束；没有多余空行，末尾不是 `\n\n`） |
| 最大行长 | 114 字符 |
| 重复服务名 | 无 |
| 未知 builtin | **0**（`mount_all/swapon_all/exec/exec_background/start/setprop/write/restorecon_recursive` 全在 A9 builtin 表内） |
| 未知 service option | **0**（只用了 `class/user/group/seclabel/oneshot/disabled`，全部是 A9 选项，且同一文件 v79 前缀里已大量使用） |
| `exec` 参数形式 | 4 条均为 `exec [seclabel] user group -- cmd args`，合法 |
| 前向引用 | `start z1_rootd`（172 行）在 `service z1_rootd`（176 行）之前——合法（init 先解析完整文件再跑 action；v79 里 `start z1_acm_log`(52) 先于 `service`(54) 是同一模式） |
| 重复触发器 | `on init` ×2、`on post-fs` ×2、`on post-fs-data` ×3 —— **合法**，init 按解析顺序把同触发器的 action 排进队列 |
| `class` 缺失的服务 | 0（9 个服务全部有 class core） |

> **关于“关键字是否被这份 `/init` 识别”的说明**：`/init` 二进制里 `class/group/user/oneshot/disabled/console/rlimit/socket/...` 等关键字字符串可见，
> 但 `seclabel`、`writepid`、`keycodes` 等**不以连续 ASCII 形式出现**（LLVM 对短字符串比较做了内联/常量折叠，不能据此判“不支持”）。
> 因此本行判定不依赖字符串表，而依赖：① A9 语义；② 这些关键字在 v79 的 6,138 B 前缀里已原样使用；
> ③ v79 上板日志里 `init` 解析 `/init.z1.rc` **0 条** `Invalid keyword/Unknown` 错误，且服务域实际生效
> （日志 `scontext=u:r:logpersist` 64 次、`u:r:bootanim`、`u:r:netd`、`u:r:storaged` 等，说明 `seclabel` 被应用）。
> 新增块只用了 `exec/start/service/class/user/group/seclabel`，与文件里既有用法完全相同，**没有引入新关键字**。

### 4.5 顺序审计（legacy 安装 → 新 exec → `class_start hal`）

**静态证据**

- legacy 安装两行在 **42/43 行**（`on post-fs-data`，32 行块内）：
  `exec … /system/bin/cp /hwcomposer.z1.so /vendor/lib/hw/hwcomposer.z1.so`、`exec … chmod 0644 …`。
- 新部署 exec 在 **171 行**（第二个 `on post-fs-data` 块，170 行起）→ 42/43 < 171 ✅。
- `/init.z1.rc` 里**没有**真正的 `class_start hal`（168 行只是一句注释）；它只在 `/init.rc:673`，位于 `on boot`（578 行）里。
- `/init.rc:277` 的 `on late-init` 在 296 行 `trigger post-fs-data`，在 306–307 行才 `trigger early-boot` / `trigger boot`。
  init 的触发队列是 FIFO：post-fs-data 的**所有** action（含本文件第三个块）跑完，boot 的 action 才会跑。
- 导入方式：`/init.rc:8` `import /init.${ro.hardware}.rc`，`androidboot.hardware=z1` ⇒ 本文件被导入；
  同文件内三个 `post-fs-data` 块按解析顺序 32 → 62 → 170 执行。

**运行时证据（v79 实机日志，rc 前缀与本镜像逐字节相同）**

| 启动样本（日志行号） | block1 `/init.z1.rc:32` | block2 `/init.z1.rc:55`（v83 为 62 行） | `class_start hal` 完成 | 余量（block2→class_start） |
|---|---|---|---|---|
| #1（1426 / 1447 / 1610） | 16.985 s | 17.760 s | 20.533 s | 2.773 s |
| #2（3192 / 3220 / 3359） | 16.649 s | 17.654 s | 19.741 s | 2.087 s |
| #3（17916 / 17934 / 18082） | 20.532 s | 21.210 s | 23.453 s | 2.243 s |
| #4（28039 / 28058 / 28209） | 18.620 s | 19.384 s | 21.776 s | 2.392 s |
| #5（64096 段 / 64141 / 64288） | 20.420 s | 21.513 s | 24.018 s | 2.505 s |
| **合计** | 16.65–20.53 s | 17.65–21.51 s | 19.74–24.02 s | **2.09–2.77 s** |

⇒ 新增的第三个 post-fs-data 块（同步 `exec /z1stage_boot.sh`）只有 **约 2.1–2.8 s** 预算（5 次启动实测）；
脚本本身只做 ~20 次小文件操作（cp 142 K/28 K/123 K/6 K + 4 次 sha256），正常 <1 s，但 /data 有病时会吃掉余量（R6）。

---

## 5. 暂存二进制审计

### 5.1 sha256 链（镜像 ← 构建输出 ← 源码/对象）

| 记录 | 镜像内大小 | 镜像内 sha256 | `/tmp` 构建输出 | 独立重编/重链 | ELF/符号结论 |
|---|---|---|---|---|---|
| `hwcomposer.z1.so` | 142,360 | `fb45175e…` | `fb45175e…`（21:56 构建输出 /tmp/hwc_m） | 用该构建的 `obj/01_z1hwc2.o` 按 manifest 命令重链 → **逐字节相同** `fb45175e` | ELF32/ARM/EABI5(0x5000200)，ET_DYN，SONAME `hwcomposer.z1.so`，BIND_NOW；`HMI` @0x1e9b8，128 B，OBJECT GLOBAL，.data |
| `gralloc.z1.so` | 28,548 | `0ed9fb2b…` | `0ed9fb2b…`（/tmp/gralloc_m） | 用当前源码 `z1_build_hal_stubs.sh` 重编 → **逐字节相同** `0ed9fb2b` | ELF32/ARM/EABI5，ET_DYN，SONAME `gralloc.z1.so`，BIND_NOW；`HMI` @0x7c10，448 B，OBJECT GLOBAL，.data |
| `z1kmstest` | 122,976 | `1954132627…` | `1954132627…`（/tmp/z1kmstest） | 用当前 `z1kmstest.c` 重编 → **逐字节相同** `1954132627` | ELF32/ARM/EABI5，ET_DYN(PIE)，interp `/system/bin/linker`，NEEDED libdl/libc；`main` @0x9690，2,588 B；`undefined drm* = 0` |

补充说明：

- **hwcomposer.z1.so**：`/tmp/hwc_m/manifest.txt` 记录该输出 `sha256=fb45175e…`、源码 `z1hwc2.cpp sha256=a4889bc4… bytes=73220`。
  我用该构建留下的 `obj/01_z1hwc2.o`（67,120 B，21:56:12）按 manifest 的链接命令重链，得到 **逐字节相同** 的 `fb45175e`。
  但**当前** `z1hwc2.cpp` 已是 `def2733a…`（75,653 B），且用它重编得到 `f355896b…`/143,272 B（≠ 镜像值、≠ 新构建值）
  ⇒ “镜像里的 `hwcomposer` 来自 a4889bc4 版源码”这一点只有 manifest 单方声明，**未验证**（该源码版本已被覆盖）；可验证的是“镜像字节 == 21:56 构建产物 == 该 object 的重链结果”。
- **gralloc.z1.so**：用当前源码（`gralloc.cpp` `e959286c…` + `framebuffer.cpp` + `mapper.cpp` + `lima_allocator.cpp` + `z1_heap.cpp`）
  跑 `z1_build_hal_stubs.sh` 重编 → **逐字节相同** `0ed9fb2b`。
- **z1kmstest**：用当前 `z1kmstest.c`（`b5a45bcc…`）跑 `build.sh` 重编 → **逐字节相同** `1954132627`。
- **z1stage_boot.sh**：镜像内 `892566a8…`（9,130 B）**不等于**当前源 `e2c9b9e7…`（11,968 B，22:12:44）
  ⇒ 镜像带的是旧修订（这正是 R2 的来源）。
- **z1rootd.sh**：镜像内 `0ab441da…` == 当前源 `0ab441da…` ✅。
- **init_fragment.rc**：`027a1676…` == 当前源 ✅（且 rc 拼接已逐字节验证）。

### 5.2 ELF 头（从镜像取出后实测）

| 文件 | Class/Data | Machine | Flags | Type | SONAME / interp | NEEDED | BIND_NOW |
|---|---|---|---|---|---|---|---|
| `hwcomposer.z1.so` | ELF32 / LSB | ARM | `0x5000200`（EABI5, soft-float） | `ET_DYN` | `hwcomposer.z1.so` | liblog, libcutils, libm, libc++, libdl, libc | 有（`FLAGS_1 NOW`） |
| `gralloc.z1.so` | ELF32 / LSB | ARM | `0x5000200` | `ET_DYN` | `gralloc.z1.so` | libcutils, liblog, libm, libdl, libc++, libc | 有 |
| `z1kmstest` | ELF32 / LSB | ARM | `0x5000200` | `ET_DYN`（PIE） | interp `/system/bin/linker` | libdl, libc | 有（`NOW PIE`） |

`z1kmstest` 还验证了：`main` 在 `.dynsym`（`T main` @0x9690，2,588 B）、**0 个未定义的 `drm*` 符号**（libdrm 静态链入，
`drmModeAtomicCommit`/`drmModeAddFB2` 等为 `T`），与 build 日志一致。

### 5.3 `HMI` 数据符号（HAL 加载契约）

AOSP 的 `hw_get_module()` 是 `dlsym(handle, "HMI")`（`HAL_MODULE_INFO_SYM` 宏展开）。

| 文件 | `nm -D` | `readelf -sW` | 段 |
|---|---|---|---|
| `hwcomposer.z1.so` | `0001e9b8 D HMI` | `0x1e9b8, 128, OBJECT, GLOBAL, DEFAULT, Ndx 19` | `.data` |
| `gralloc.z1.so` | `00007c10 D HMI` | `0x7c10, 448, OBJECT, GLOBAL, DEFAULT, Ndx 19` | `.data` |

两者都是**已定义的数据符号**（D/OBJECT），不是 U，也不是函数——符合 HWC2/gralloc HAL 的加载方式。

### 5.4 依赖存在性

`hwcomposer`/`gralloc` 的 NEEDED 里 `libc++.so`(636,332 B)、`libcutils.so`(67,476 B)、`liblog.so`(88,992 B)
在 `/tmp/z1devlibs`（从设备 system 镜像提取的库）中都存在；`libc.so`/`libm.so`/`libdl.so` 是 bionic 基线。
**未验证**：板子 `/system/lib` 当下的实际文件（本次审计未挂载 system 镜像；v79 日志显示这些库能正常加载）。

---

## 6. 风险发现（按影响排序，全部带字节/字段证据）

| R1 | 高（操作） | 审计对象在审计期间被替换：路径上现在是 5,902,336 B / `306fe78e…` 的另一版镜像；本报告只对 `5b8d9c0a…` 成立 | §1 时间线；`stat`/`sha256sum` 显示 22:11:16、22:13:15 两次写出 | 刷机前核 sha256；冻结构建后再刷 |
| R2 | 中（部署） | 镜像内 `z1stage_boot.sh`（9,130 B / `892566a8`）是旧修订：① 无 `/dev/dma_heap/*` 节点创建，而镜像内 gralloc 会打开 `/dev/dma_heap/default_cma_region`，且 ramdisk 的 `ueventd.rc`（`d40b80e5`）**没有 dma_heap 规则**；② 自动安装把 HWC 候选永久留在 `active/`，坏 HAL 会每次开机重装 | 镜像内脚本 §2b/2c 对比当前源（11,968 B / `e2c9b9e7` 含 mknod 段）；`grep -a dma_heap ueventd.rc` 无输出；gralloc 字符串 `/dev/dma_heap/default_cma_region` | 若要启用 gralloc：先补 mknod 或换新脚本；HWC 坏了在串口 shell `rm /data/local/tmp/z1stage/active/hwcomposer.z1.so` 后重启 |
| R3 | 中（内核） | v83 的内核不是 v79 那份：zImage 不同、DTB 相同；镜像不是“只换 ramdisk”的增量 | 解压 vmlinux：v79 `#159 2026-11-09 19:45:27`，v83 `#160 21:48:02`；DTB `444c944d…` 两边相同；payload `6d879640…` vs v79 `262890c5…` | 上板首测把内核当变量；回滚用 v79 镜像 |
| R4 | 中（显示） | 首启会把 ramdisk 里的新 HWC2（`fb45175e`）自动装到 `/vendor/lib/hw/hwcomposer.z1.so`；v79 实测里合成器一直回退原厂 ranchu 模块并 5 s 一次重启 | v79 日志：`ranchu_hw_composer hwc_open could not open framebuffer`、`vendor.hwcomposer-2-1` 反复 exit 1、全程 0 条 `z1hwc2` 日志；ramdisk 模块字符串 `z1 HWC2 (mediatek-drm)` | 看首启是否出现 `z1 HWC2`/`z1hwc2:` 日志；黑屏则删 active 副本 |
| R5 | 低（可写性） | `/vendor` 是 `/system/vendor` 的符号链接；v79 日志里 11 条 `/proc/mounts` 快照中 `/system` 有 10 次是 `ro`，此时 legacy cp 与 apply 都会失败（只记 ERROR，不阻塞启动） | ramdisk `vendor -> /system/vendor`（cpio symlink 记录）；日志 `[/dev/block/mmcblk0p4 /system ext4 ro,…]` ×10 / `rw` ×1 | 关注 `z1stage: ERROR active/…` 行 |
| R6 | 低（时延） | `exec` 在 post-fs-data 同步执行，z1stage 无内部超时；预算约 2.1–2.8 s（v79 5 次启动实测） | 日志：post-fs-data 16.984 s / 17.760 s → class_start hal 20.455 s；慢启动 20.420 s → 24.018 s | 看 `z1stage: done … errors=` 是否早于 class_start hal |
| R7 | 中（安全） | `z1_rootd` 以 root 执行 `/data/local/tmp/z1stage/{cmd.sh,install.sh}`，该目录 0777、属主 shell(2000)——任何 shell 权限进程可提权到 root | 镜像内 `z1rootd.sh` 逻辑；脚本自身 `chmod 0777/chown 2000:2000` | 仅限台架；产品镜像应删掉该服务 |
| R8 | 提示（记录） | cpio `ino=810731` 被 7 条记录共用（含 4 条新增），但 `nlink=1`，内核只在 `nlink>=2` 时做硬链接，故无实际影响；v79 里该 ino 已被 3 条共用 | cpio 头解析：`ino`/`nlink` 字段 | 无需处理 |
| R9 | 通过（记录） | 0 条记录缺文件类型位；无重名；TRAILER 完整；类型分布 14 dir / 52 reg / 19 symlink | cpio 模式直方图 `040000/0100000/0120000`；无裸权限位 | — |

### 6.7 几个“指定要查”的点，逐条回答

1. **是否低于 6 MiB 分区、余量多少**：是。5,900,288 B < 6,291,456 B，余量 **391,168 B（382 KiB，6.2%）**；
   项目自限 6,000,000 B 的余量 **99,712 B**。新构建 5,902,336 B 的余量 389,120 B（仍安全）。
2. **cmdline 还有串口控制台吗**：有，且与 v79 逐字节相同（302 B）：`console=ttyS0,921600n8`、`earlycon=mtk8250,0x11005000`、
   `androidboot.hardware=z1`、`androidboot.selinux=permissive` 全在。
3. **`dt_size` 是否自洽**：`dt_size=0`，与 v79 相同，且与本项目的 appended-DTB 设计自洽——
   DTB 被拼在 zImage 尾部（`CONFIG_ARM_APPENDED_DTB=y`，kernel 在 `_edata` 找 `d00dfeed`），不占独立 dt 区。
   若有人用 `mkbootimg --dt` 重新打包，`dt_size` 会变成非 0 且 DTB 被搬到独立区——那会破坏本设计（这是已知的坑，本镜像没踩）。
4. **Android sha1 字段是否有效/与 v79 同约定**：有效且**同约定**。两版都满足
   `id_bytes[0:8] == sha1(kernel_payload ‖ ramdisk_section)[:8]`：
   - v83：`808e0af58ea66672` == `sha1(4,334,579 B payload ‖ 1,561,825 B ROOTFS 段)` 前 8 字节（重算命中）；
   - v79：`29c3ae282c8137c6` == 同一规则（重算命中）。
   其余 24 B 为 0。注意这不是 AOSP mkbootimg 的规则（AOSP 会连 `dt` 和 `len(image)` 一起哈希），
   但 LK 不校验该字段，且 v79 用同一规则已上板，所以**无回归**。
5. **MTK 头 0xff 尾是否异常**：不是本镜像引入的——与 v79 的 ROOTFS 头逐字节比较只有 size 字段 3 个字节不同；
   0xff 尾在 v79（当前能启动）上已存在，LK 不读。

### 6.8 验证矩阵（每一行都对应实际运行过的命令）

| # | 检查项 | 实测/判据 | 实际使用的命令 |
|---|---|---|---|
| V01 | 文件大小/sha256 | 5,900,288 / `5b8d9c0a…` | `sha256sum out/z1_v83/boot.img; stat -c%s …` |
| V02 | ANDROID! magic | `ANDROID!` | `xxd -l 32 out/z1_v83/boot.img`；python `struct.unpack('<8s…')` |
| V03 | 15 个头部字段逐字段解析 | 见 §2.1 表 | `python3 /tmp/z83audit/bootimg_audit.py`（输出 parse_out.txt） |
| V04 | kernel_size = MTK KERNEL size + 512 | 4,335,091 = 4,334,579 + 512 | 同上；`struct.unpack('<I', d[0x804:0x808])` |
| V05 | ramdisk_size = MTK ROOTFS size + 512 | 1,561,825 = 1,561,313 + 512 | 同上；ROOTFS 头在 0x423004 |
| V06 | kernel payload = zImage‖dtb | `6d879640…` == concat sha | `cat zImage dtb | sha256sum` 对比 payload |
| V07 | payload 在 4,313,600 处切分 | 前缀==zImage，尾部==dtb（20279 B） | python 逐字节比较；dtb magic `d00dfeed` |
| V08 | zImage ARM 头 | magic `0x016f2818`，end `0x41d200`=4,313,600 | python 读 0x24/0x2c |
| V09 | dtb 头 | magic `d00dfeed`，totalsize 20,979 = 文件长 | python 读 dtb[:8] |
| V10 | MTK KERNEL 头 | `0x58881688`, 4,334,579, `KERNEL`，非零 13 B | `xxd -s 0x800 -l 64` |
| V11 | MTK ROOTFS 头 | `0x58881688`, 1,561,313, `ROOTFS`，非零 485 B（0xff 尾继承自 v79） | `xxd -s 0x423000 -l 64`；与 /tmp/rootfs_v79.bin 头逐字节 diff（只 4..6 变） |
| V12 | ROOTFS blob 首/尾 64 B | 见 §2.6 | `python` 打印 blob[:64]/[-64:] |
| V13 | gzip 解码 | eof=True, unused=0, ISIZE=3,033,648=解压长, CRC 由 zlib 校验通过 | `zlib.decompressobj(31)` |
| V14 | cpio 记录数/类型位 | 85 条；0 条缺类型位；14/52/19 | `python3 /tmp/z83audit/cpio_audit.py` |
| V15 | 与 base 逐记录 diff | 79 相同 / 2 变 / 4 增 / 0 删 | 同上（base 来自 /tmp/rootfs_v79.bin，已验证 == v79 镜像 ROOTFS 段） |
| V16 | TRAILER/尾部 | 124 B 相同；其后 72 B 全 0 | 同上 |
| V17 | init.z1.rc 身份 | `454e1beb…` == v79rc + `\n` + fragment | python 拼接比较；sha256sum |
| V18 | init 语法/顺序 | 服务 9 / on 块 11 / 未知关键字 0 / tab 0 / CR 0 / 末尾 LF | `python3 /tmp/z83audit/rc_audit.py` |
| V19 | legacy cp < 新 exec < class_start hal | 42/43 行 < 171 行；class_start hal 只在 init.rc:673（on boot） | rc_audit.py + `grep -n 'class_start hal' init.rc` |
| V20 | 运行时顺序 | post-fs-data 16.98/17.76 s → class_start hal 20.46 s | `grep -a -n 'processing action (post-fs-data)' 日志` |
| V21 | 暂存二进制 hash 链 | 镜像 == /tmp 构建输出（3/3） | `sha256sum` + `nm -D` |
| V22 | HMI 数据符号 | hwc 0x1e9b8/128 B；gralloc 0x7c10/448 B，均 D/OBJECT/GLOBAL | `arm-linux-gnueabihf-nm -D`；`readelf -sW` |
| V23 | ELF 头 | ELF32 LSB ARM EABI5 flags 0x5000200；.so=DYN；kms=PIE+interp | `file`；`readelf -hW/-dW/-lW` |
| V24 | 镜像尺寸/余量 | 5,900,288 < 6,000,000 < 6,291,456；余量 391,168 B | python 算术；分区 6 MiB 来自项目事实 |
| V25 | cmdline | 302 B 与 v79 逐字节相同，含 console/hardware/selinux | python 比较 + `grep -a` |
| V26 | Android id 规则 | 两版都 = `sha1(kernel_payload‖ramdisk_section)[:8]` | python 穷举 4 种候选并比对 |
| V27 | 目标漂移 | 22:11:16 后为 5,902,336 / `306fe78e…` | `stat`；`sha256sum`；`cp --preserve=timestamps` |
| V28 | 新构建记录 diff | 85→85，只替换 hwcomposer/z1stage_boot.sh | python cpio diff（v83new.cpio vs v83.cpio） |

### 6.9 builder 声明的独立核对（不采信，只对照）

| builder 报告声明 | 本次由字节重算 | 结论 |
|---|---|---|
| `size 5900288` | `stat -c%s` = 5,900,288 | ✅ |
| `sha256 5b8d9c0a…` | `sha256sum` 命中 | ✅ |
| `kernel_size 4335091 = MTK 4334579 + 512` | 头字段 + MTK 头 size 字段 | ✅ |
| `ramdisk_size 1561825 = MTK 1561313 + 512` | 头字段 + MTK ROOTFS size | ✅ |
| `kernel payload == zImage+dtb（6d879640…）` | 拼接 sha256 命中 | ✅ |
| `rootfs blob sha256 22ac739c…` | 取出 1,561,825 B 段重算命中 | ✅ |
| `records=85, added=4, replaced=2, untouched=78` | 85 / 4 / 2 / 78（+TRAILER=79 逐字节相同） | ✅ |
| `gzip blob 1561313`（=ROOTFS size 字段） | zlib 解码 + ISIZE | ✅ |
| `init.z1.rc 7569 B / 454e1beb…` | 独立提取命中 | ✅ |
| `id field == sha1(kernel+rootfs)[:8]` | 精确规则 `sha1(payload‖ROOTFS 段)[:8]` 命中（v79 同规则） | ✅（需注明规则） |
| `ALL CHECKS PASSED` | 结构项确实全过；但报告**没有**检查“镜像内脚本与当前源码是否一致”、也没检查 dma_heap 节点，因此它看不到 R2 | ⚠️ 结论范围有限 |


---

## 7. 判定

**PASS-WITH-NOTES**（限 5,900,288 B / `5b8d9c0a…`）：

- 结构：ANDROID! 头、MTK 包装、两条 512 B 尺寸规则、`zImage‖dtb`、gzip/cpio、TRAILER 全部正确；
- 记录：85 条、0 条缺类型位、79 条逐字节保留、4 增 2 换、无重名；
- rc：与 `v79 + "\n" + fragment` 逐字节一致，语法/顺序通过，legacy 安装在新 exec 之前、两者都在 `class_start hal` 之前；
- 二进制：3 个暂存件的 hash 链闭合（gralloc/z1kmstest 可由当前源码逐字节复现；hwcomposer 可由构建 object 逐字节复现），ELF/ABI/HMI 全部符合；
- 风险：R1（审计期间目标被替换）、R2（旧版部署脚本 + 无 dma_heap 节点 + 自动安装持久化）、R3（内核是另一构建）、R4（HWC 首启候选）、R7（root 命令通道）需要操作员知情。

**不通过项**：无（没有发现会直接导致“刷进去不启动”的字节级缺陷）。

---

## 8. 操作员复验（一行命令）

把下面整段粘进 shell（只读，不改文件）：

```
python3 -c "import struct,hashlib,zlib;d=open('/home/lxj/claude/6572/out/z1_v83/boot.img','rb').read();f=struct.unpack('<8s10I16s512s8I1024s',d[:1632]);ks,rs,ps=f[1],f[3],f[8];ro=(ps+ks+ps-1)//ps*ps;mk=struct.unpack('<I',d[ps+4:ps+8])[0];mr=struct.unpack('<I',d[ro+4:ro+8])[0];gz=d[ro+512:ro+rs];cp=zlib.decompressobj(31);c=cp.decompress(gz)+cp.flush();print('size=%d sha256=%s kernel_size=%d mtk_kernel=%d kdelta=%d ramdisk_size=%d mtk_rootfs=%d rdelta=%d dt_size=%d console=%s payload_sha=%s cpio=%d gzip_eof=%s'%(len(d),hashlib.sha256(d).hexdigest(),ks,mk,ks-mk,rs,mr,rs-mr,f[9],b'console=ttyS0,921600n8' in f[12],hashlib.sha256(d[ps+512:ps+ks]).hexdigest()[:16],len(c),cp.eof and not cp.unused_data))"
```

**对审计对象（5,900,288 B）的期望输出**：

```
size=5900288 sha256=5b8d9c0a30a2f3e38eb99a697fa9e0ace9403d79cf2e698faf3ff27f36b5cc25 kernel_size=4335091 mtk_kernel=4334579 kdelta=512 ramdisk_size=1561825 mtk_rootfs=1561313 rdelta=512 dt_size=0 console=True payload_sha=6d87964026d6144a cpio=3033648 gzip_eof=True
```

判读：`kdelta=512` 且 `rdelta=512`（两条尺寸规则成立）、`payload_sha=6d87964026d6144a`（内核就是 `zImage‖dtb`）、
`cpio=3033648`、`gzip_eof=True`。若 `sha256=` 不是 `5b8d9c0a…`，就说明路径上是另一版镜像
（例如 22:11 之后的 5,902,336 B 会打印 `size=5902336 sha256=306fe78e…`）——**先停下来确认刷哪份**。

---

## 附录 A：本次实际运行的命令（可复现）

> 所有解析脚本都在 `/tmp/z83audit/`（临时目录）。核心解析逻辑已固化在 §8 的一行命令里；
> 下面是各类检查实际用过的命令，报告里的每一行都能由其中一条复算。

**A.1 头/布局/尺寸**

```bash
sha256sum out/z1_v83/boot.img out/z1_v79/boot.img \
          fork_linux/arch/arm/boot/zImage \
          fork_linux/arch/arm/boot/dts/mediatek/mt6572-z1.dtb /tmp/rootfs_v79.bin
stat -c '%s %y %n' out/z1_v83/boot.img out/z1_v79/boot.img
xxd -l 256 out/z1_v83/boot.img
xxd -l 64  out/z1_v79/boot.img
xxd -s 0x800    -l 64 out/z1_v83/boot.img     # MTK KERNEL 头
xxd -s 0x423000 -l 64 out/z1_v83/boot.img     # MTK ROOTFS 头
python3 /tmp/z83audit/bootimg_audit.py        # 15 字段 + 布局 + gzip + sha1 候选
```

**A.2 cpio / ramdisk**

```bash
python3 /tmp/z83audit/cpio_audit.py           # 记录表、mode 直方图、与 v79 的逐记录 diff
```

自包含的 cpio 解析片段（与 cpio_audit.py 等价，可直接粘贴复算记录表）：

```python
import struct,hashlib
d=open('/tmp/z83audit/boot_v83_5900288_RECON.img','rb').read()
f=struct.unpack('<8s10I16s512s8I1024s',d[:1632]); ks,rs,ps=f[1],f[3],f[8]
ro=((ps+ks+ps-1)//ps)*ps
import zlib; c=zlib.decompress(d[ro+512:ro+rs],31); off=0; n=0
while True:
    h=c[off:off+110]; fs=int(h[54:62],16); ns=int(h[94:102],16)
    name=c[off+110:off+110+ns-1].decode(); nend=(off+110+ns+3)&~3
    data=c[nend:nend+fs]
    print(n, name, fs, oct(int(h[14:22],16)), hashlib.sha256(data).hexdigest()[:16])
    n+=1; off=(nend+fs+3)&~3
    if name=='TRAILER!!!': break
print('records',n,'cpio bytes',len(c))
```

**A.3 rc / 顺序**

```bash
python3 /tmp/z83audit/rc_audit.py             # 服务/on 块/关键字/tab/CRLF/重复/顺序
grep -n -E 'class_start|import /init|post-fs-data|on boot|on late-init' /tmp/z83audit/init.rc
grep -a -n 'processing action (post-fs-data)' mainline_recon/z1_boot_20261109_1838.log
grep -a -n "class_start hal\|starting service 'vendor.hwcomposer" mainline_recon/z1_boot_20261109_1838.log
grep -a -c 'Invalid keyword' mainline_recon/z1_boot_20261109_1838.log     # 0
```

**A.4 二进制/ELF/符号/重编**

```bash
file /tmp/z83audit/extract_*.so /tmp/z83audit/extract_z1kmstest
readelf -hW -dW -sW -lW /tmp/z83audit/extract_hwcomposer.z1.so
arm-linux-gnueabihf-nm -D /tmp/z83audit/extract_hwcomposer.z1.so | grep ' D HMI'
arm-linux-gnueabihf-nm -D /tmp/z83audit/extract_gralloc.z1.so    | grep ' D HMI'
arm-linux-gnueabihf-nm -D /tmp/z83audit/extract_z1kmstest        | grep -E ' T main$| U drm'
# 用构建 object 重链 hwcomposer（复现镜像字节）
/home/lxj/claude/6572/android-ndk-r26d/toolchains/llvm/prebuilt/linux-x86_64/bin/armv7a-linux-androideabi28-clang++ -shared \
  -o /tmp/z83audit/hwc_relink.so /tmp/hwc_m/obj/01_z1hwc2.o mesa_android/libdrm-static-build/libdrm.a \
  -L/tmp/z1devlibs -llog -lcutils -lm -Wl,-soname,hwcomposer.z1.so
# 用当前源码重编 gralloc / z1kmstest
z1a9_gpt/scripts/z1_build_hal_stubs.sh /tmp/hal_rebuild
z1a9_gpt/scripts/z1kmstest/build.sh    /tmp/kms_rebuild
```

**A.5 目标漂移/新构建 diff**

```bash
stat -c '%y %s' out/z1_v83/boot.img out/z1_v83/stage/* z1a9_gpt/scripts/z1deploy/z1stage_boot.sh
cp --preserve=timestamps out/z1_v83/boot.img /tmp/z83audit/boot_v83_NEW.img
python3 - <<'EOF'   # 解新镜像的 cpio 并与审计版逐记录 diff
EOF
grep -a 'RESULT|' out/z1_v83/pack_boot_report.txt | tail -3
```

---

## 附录 B：明确“未验证”的项（以及需要什么数据才能验证）

| # | 未验证项 | 为什么验证不了 | 需要什么 |
|---|---|---|---|
| B1 | 镜像内 `hwcomposer.z1.so` 是否来自 `z1hwc2.cpp` 的 `a4889bc4` 版本 | 该源码版本已被覆盖（当前 `def2733a`）；只能验证“字节 == 21:56 构建产物 == 其 object 重链” | 找到 `a4889bc4` 的历史源码，或构建时的完整物证 |
| B2 | A9 `exec` 的精确超时秒数 | `/init` 二进制里有 `… timed out and took …` 字符串，但具体常量需反汇编才能确认（AOSP 9 为 5 s） | 反汇编 `/init` 的 `WaitForExec`/超时常量，或上板故意跑 6 s 脚本观察 |
| B3 | 板子 `/system/lib` 里 NEEDED 库的当前实际文件 | 未挂载 system 镜像（只读约束），只用 `/tmp/z1devlibs` 与 v79 日志间接推断 | 只读挂载 `out/…/system_*.img` 后逐库 sha256 |
| B4 | 新内核（#160）与 HWC 候选在板上能否正常启动/显示 | 本次是纯字节审计，未上板、未刷机（硬约束） | 上板首测 + 串口日志 |
| B5 | 5,900,288 B 原始镜像是否还能从别处找回 | 已被 22:11/22:13 的构建覆盖；只能用解析段重建（sha256 已验证一致） | 查 `bridge_backups/products/` 是否有归档，或找 21:58 那次的原始文件 |

---

*报告生成：2026-11-09 22:20 CST。所有数字来自当次运行的命令输出；没有引用 builder 报告的结论。*
