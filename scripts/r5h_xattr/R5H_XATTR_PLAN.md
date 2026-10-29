# R5h — security.selinux xattr 缺失取证与修补计划（2026-10-29）

产出: `apply_r5h_xattr.sh`（幂等修补脚本）。**未改任何现有镜像**；基线四件全程只读。

## 1. 结论

对 `out/z1_r5_system/system.img.r5f_final`（ro 挂载实测，fs state=clean、从未挂载，
纯 debugfs 产物）全量扫描 2649 个 inode（排除 lost+found）：

- **69 个无 `security.selinux` xattr**（此前 HANDOFF 记的"59 新增/30 缺失"为局部抽样，
  本轮为全量精确值）
- **r5b/r5f 补丁清单 16 个目标文件 100% 全部缺失** —— 机理：补丁脚本全用
  `debugfs -w -R "write/rm+write"`，debugfs 生成**全新 inode** 且不写 xattr，
  连覆盖写（mnld/gps.default.so/libmnlp/load_audio.sh/wmt.ko/z1-mainline-features.xml）
  也会把原标签一起丢掉
- 8 个重点目录（/vendor/etc /vendor/lib /usr/keylayout /lib /bin/hw /vendor/bin/hw
  /app /priv-app）内 46 个（43 文件+3 目录）；目录外另有 23 个
  （/bin/z1diag 整目录、**/build.prop 本身**、/etc/wifi、/etc/firmware/nvram 等）
- `/app`、`/priv-app` 全部有标签（基线完好，与"base 抽样 7 个全有"一致）

## 2. 为什么现在不修也不行（转 enforcing 前必须补）

`init.rc` 的 `restorecon_recursive` **不覆盖 /system 与 /vendor**（system-as-root、
fstab 无独立 vendor 分区，见 Z1_REMAINING_GAPS_20261028.md §3.12）→ 镜像内 xattr 是
唯一标签来源，板上不会自愈。permissive 下无影响。

## 3. 标签依据（三层证据，不发明）

| 优先级 | 依据 | 覆盖 |
|---|---|---|
| ① | **r5_final 基线原标签回收**（ro 挂载 getfattr）：mnld=vendor_file、libmnlp/system_file、z1-mainline-features.xml/system_file、load_audio.sh/system_file、mtk_stp_wmt_soc.ko/system_file | 5 个被覆盖写路径 |
| ② | **同目录同类型邻文件实测标签**：如 libEGL_mesa.so←egl 三件 swiftshader 同为 same_process_hal_file；libxlog←vendor/lib 27 个 .so 全 vendor_file；keypad←Generic.kl system_file；snd*.ko←z1_charger.ko system_file；gnss/sensors service←vendor_file_contexts 精确条目+同目录 hal_*_default_exec 族 | 其余全部 |
| ③ | **ramdisk 内 plat/vendor_file_contexts**（boot.img 剥 512B MTK 头解包）：plat L12 `/(vendor)/etc(/.*)?→vendor_configs_file`、L45 `/system(/.*)?→system_file`、L6 `/(vendor)(/.*)?→vendor_file`；vendor_fc L15 gnss→hal_gnss_default_exec、L24 sensors→hal_sensors_default_exec | 兜底与校验 |

**类型存在性已逐一验证**（strings ramdisk/sepolicy）：hal_gnss_default_exec=1、
hal_sensors_default_exec=1、vendor_configs_file=1、vendor_file、system_file 均存在。

### 弃用与注意项

1. **wifi_vendor_data_file 不可用**：wifi_hal 先例片段（out/z1_wifi_hal_stage）声明
   `/vendor/etc/wifi→wifi_vendor_data_file`，但该类型在 sepolicy 中 **0 命中**（未定义），
   照搬会写入未定义类型（enforcing 下等同无标签）。改用 plat_fc L12 的
   `vendor_configs_file`（兄弟目录 /vendor/etc/vintf 实测同标签）。
2. **vendor_init_rc 是悬空类型**：sepolicy 中 0 命中，但镜像内 /vendor/etc/*.rc 实测带
   `u:object_r:vendor_init_rc:s0` 标签 —— 这是**基线固有问题**（构建时 file_contexts 与
   策略不同步），不在本轮 69 项内；转 enforcing 前需另行裁决（改标签或补策略），本轮未动。
3. **/vendor/lib/dri/lima_dri.so → vendor_file**：无 fc 条目（plat/vendor fc 均无 /dri），
   锚=所在目录自身实测 vendor_file + plat L6 兜底。注意它是 SF 进程 dlopen 的 DRI 驱动，
   enforcing 时需用 sesearch 核对 surfaceflinger 对 vendor_file:file 的 execute/map 权限，
   若缺则应改 same_process_hal_file 并补 fc —— 未确定项，需策略检索工具（host 无 setools）。
4. **mapper@2.0-impl-2.1.so → same_process_hal_file**：文件名不匹配 vendor_fc L58 的
   `mapper@2.0-impl\.so` 正则（按 fc 兜底本应 vendor_file），但其精确同名族兄弟
   mapper@2.0-impl.so 实测 same_process_hal_file 且语义相同（dlopen 进客户端进程），
   从语义取 same_process_hal_file。如需严格 fc 对齐可改 vendor_file，permissive 下两者等价。

## 4. 脚本验证记录（全部本轮实测）

- 合成 24MB ext4（样本含预置"已正确"与"预置冲突"两分支）：
  首轮 补写5/跳过1/冲突1/缺路径62、阶段2 回读通过、e2fsck 通过；
  二轮走幂等快路径"已是正确标签，无需修补"；`--force` 覆盖冲突 1 项；
  守卫：原地改拒绝、基线路径输出拒绝。修掉两处实测 bug：
  ① `set -e`+pipefail 下无标签文件的 getfattr 非零返回触发 errexit 静默退出；
  ② 阶段2/快路径需豁免"冲突未覆盖"文件，否则每轮重建。
- **真实 900MB r5f_final**（输出 /tmp，验证后已删）：**补写 69/69、缺路径 0、冲突 0**，
  umount 后 ro 复挂全量回读通过，e2fsck -fn 通过，产物 sha256
  `f200a69ee8ba1a5ee1f57e6b7ed6061fbb9697c1155a959200e146c48bdda122`；
  幂等二轮"已是正确标签，无需修补"；
  **独立复核**（自挂产物 ro 全量重扫）：2649 个 inode 无标签 **0** 个（原件 69）。
- 基线四件 mtime/大小/`Filesystem state: clean` 全程未变。

## 5. 用法

```bash
sudo bash z1a9_gpt/scripts/r5h_xattr/apply_r5h_xattr.sh <in.img> [out.img] [--force]
# 缺省 out = <in>.r5h_xattr.img；绝不原地改；输入可为只读基线（仅 cp 读取）
# 建议与其它 r5 系列修复合批：先 gpu/音频补丁，再跑本脚本收尾统一补 xattr
```
