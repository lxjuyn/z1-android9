# Z1 启动存储门禁与安装修复（2026-11-12）

本轮完成主机实现与故障注入，未刷板、未修改 system 镜像、未引入 r16 图形库。

## 先前交付实际状态

反解 `out/boot_cmpfix.img`（sha256 `6388be8659e3b77c3facc233d1eb604343a2d9defe6a014540f7675e44bf0a56`）与 `out/boot_final_perf.img`（`7e6987ee55c55e356c17ff5cfae25b3c20b5eec372524f22ae5c29e5bc7c91f3`）：

- kernel 段都为 `1e3837b5a6d637460daaee1965421692fd9b859d22e1c9ab52a73387c80c624a`，cpio 仅 `z1stage_boot.sh` 不同。
- cmpfix 内 stage 脚本为 `365333229833855a8f9c816e282e59e45b6c5d54528ded3d49dc6f3a58b3eac0`，等于本轮改前源文件；只对五条 `install_sys_file` 加了内容比较。
- 镜像内 `init.z1.rc:42–43` 仍直接 cp/chmod HWC；旧 stage 的 `install_one` 又无条件复制 HWC/gralloc；因此 cmpfix 并未消除重复写入。
- 两件 boot 的 HWC 都为 `03d2f200f4ea8f8fd1b4bc8d8dd06c190ef2a23f69811fad875b56296acffa95`，gralloc 都为 `0ed9fb2b573bfe4bf2e533b7f709ad054a1dc95482d420aca184d6c95823b77d`，逐字节等于 `releases/ovl_v83_r14/` 发布件。HWC 没有 `first vsync ts=` / `WAIT_VBLANK failed:` 字符串。r16 的 `sequence=1` 在 `scripts/z1hwc2/z1hwc2.cpp` 源码中，不能视为进入了这些 boot。
- 旧 `repair_fs` 在 e2fsck 任意返回值后都尝试 RW 挂载；调用前已创建 stage，旧 init 也已启动后台写者。仅增加返回码判断仍不足，必须调整 init 时序。

改前副本：`bridge_backups/boot_stage_hardening_20261112_233732/z1stage_boot.sh`。

## 实现与 init 接口

修改源文件：`scripts/z1deploy/z1stage_boot.sh`。

1. `--storage-only`：在 `on fs` 的 `mount_all /fstab.z1` 紧后同步执行；初始置 `sys.z1.storage_ready=0`。检查 `/data` 和 `/cache` 的挂载来源、超级块、journal 回放。两者都通过后才置 `sys.z1.storage_ready=1`，此模式不创建 stage、不安装库、不运行调频。关键 storage_ready 置值和最终 stage ready 均检查写入返回值并读回，失败不得报告成功。
2. init 必须紧接 `wait_for_prop sys.z1.storage_ready 1`，阻止后续 `post-fs` / `post-fs-data` 写者。仅 exec 非零退出不阻止 Android init 继续。
3. `--skip-storage`：供后期 stage 使用，要求 storage_ready=1，并复核挂载源和 RW 状态；不再运行 fsck，避免与后台写者竞争。
4. 后期 init 在调用前设 `z1stage.status=installing`，exec 后等 `wait_for_prop z1stage.status ready`，再启动 rootd 和既有后台写者。删除旧的 HWC cp/chmod 和原重复 stage exec。

| 情况 | 行为 |
|---|---|
| e2fsck 0 / 1 | 允许 RW 挂载；再核对实际来源与 RW 状态 |
| e2fsck 2 / 3 | 保持卸载，`z1stage.fsck=reboot-required`，不自动重启 |
| 其余返回值 | 保持卸载，失败封锁；不格式化、不清 data |
| busy、挂到其他路径、来源异常、超级块/日志不可读 | 失败封锁，不能称 clean |
| 任一存储门禁失败 | 不执行本脚本后续 stage/system 写入；storage_ready 保持 0 |
| 安装失败或 RO 恢复失败 | `z1stage.status=blocked-install`，停止后续 stage 操作 |
| 其它被计数的 stage 错误 | `z1stage.status=completed-with-errors`，不置 ready |

返回码解释交叉核对本机 `/usr/share/man/man8/e2fsck.8.gz` 的 EXIT CODE（位掩码）。旧 fstab 的 fs_mgr 检查及挂载 journal 回放仍发生在此脚本之前；本门禁不是对它们的替代。若 `/system` 本身坏到不能启动 `/system/bin/sh` 或 e2fsck，init 等待仍阻止放行，但需要外部恢复 system。

## 安装和调频

HWC/gralloc 保留 active 优先、否则 ramdisk、kill switch/无候选删除 vendor 覆盖的原语义。五件外设安装保持“ramdisk 不存在就保留 system”语义。所有实际复制共用比较与临时文件校验路径：内容相同不复制；cmp 出错与内容不同分别处理；临时文件内容/权限/属主成功后才 rename，并读回比较。失败即清临时件、报告并尝试恢复 `/system` RO；整体结束也核对 `/system` RO。HWC/gralloc 的属性标识按最终实际文件计算。rename 不等于断电持久性保证。

旧 `oc_level=oc/1508` 等请求会记录 unsupported 并清成 safe，不再写 1508000。CPU 仅在驱动公布 806000、1300000、schedutil 时应用既有策略，并核对写结果与回读；GPU 仅在公布 416MHz/performance 时设置 max_freq=416MHz、governor=performance，再核对实际频率。失败状态分别为 `z1stage.cpufreq` / `z1stage.devfreq` 的 failed/unsupported/unavailable；这两类策略状态不伪装成已生效，也不声称物理上限。

## 主机验证

命令：

```sh
dash -n scripts/z1deploy/z1stage_boot.sh
python3 scripts/z1deploy/test_z1stage_boot.py
```

32 项测试通过（含 fsck 10 个返回码及多种子用例）：busy、设备仍在别处挂载、错误来源、不可读超级块/日志、grep 不可用时仍正确识别回放、干净开机、journal 回放、未挂载、RW 挂载失败；相同文件零复制、短写校验、cp/chmod/chown/mv 失败保留原目标、RW/RO remount 失败、比较 I/O 错误、失败后不继续安装、active/kill switch 语义、seed 元数据失败计数；完整脚本 storage-only 不创建 stage、cache 失败不放行、门禁属性写入/回读失败不放行、skip-storage 不可绕过门禁；支持/不支持频率、CPU 写失败、GPU 回读失败。

测试执行真实脚本函数；完整脚本的路径隔离用临时目录和模拟 mount/fsck/sysfs，不触碰块设备。语法验证在 dash 上完成，Android mksh 与真实挂载/驱动行为仍须上板确认。e2fsck 同步执行且未设超时：挂起时门禁保持关闭，但等待没有时长上限；未引入未经板上确认的 timeout 工具依赖。

待主线将脚本及 init 时序打入新 boot 后反解确认：两个 exec 参数、两个 wait_for_prop、旧 HWC cp 清除、stage 内容 SHA、r14 HWC/gralloc SHA、kernel 段与 cmpfix 相等。旧 cmpfix 留作证据，不将本次源码修复宣称为已进入旧镜像。
