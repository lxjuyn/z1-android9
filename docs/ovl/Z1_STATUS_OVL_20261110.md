# Z1 满血 OVL / DRM 合成 —— 本轮状态与首刷说明（2026-11-10）

> 目标：SurfaceFlinger 用 **HWC2 DEVICE 合成**，HWC 直接把应用的 dma-buf 挂到 MT6572 的
> OVL 平面上（零拷贝、4 个硬件层），而不是 GPU 画完再 CPU 拷。
> 本文件是**唯一权威**的本轮状态；`Z1_RUNBOOK_V83_20261110.md` 正文写于更早的测试镜像，
> 其顶部「附录 0」列出了与现状的差异。

---

## 1. 要刷的镜像

```
out/z1_v83/boot.img
  size      5,900,288 B          （boot 分区 6,291,456 B，余 393,216 B）
  sha256    2604bd26358c1c99b988b8647dff2853a999f052cd2ca9f7a00abbdf695e7feb
  kernel    4,331,019 B = MTK KERNEL 4,330,507 + 512   (新内核)
  ramdisk   1,563,914 B = MTK ROOTFS 1,563,402 + 512   (85 条记录)
  cmdline   316 B，原 302 B + " iomem=relaxed"
```

镜像里的关键内容（每条都有 sha256，打包器 6 类校验全 PASS）：

| 记录 | 大小 | sha256（前 12） | 作用 |
|---|---|---|---|
| `hwcomposer.z1.so` | 144,920 | `d452495ad362` | HWC2 HAL r9：DEVICE 决策 + OVL 平面编程 |
| `gralloc.z1.so` | 28,548 | `0ed9fb2b573b` | CMA/dumb 连续内存分配器（OVL 能扫描的前提） |
| `z1kmstest` | 122,976 | `1954132627e1` | 独立 DRM/OVL 测试工具（probe/run/regs） |
| `z1stage_boot.sh` | ~12.6 K | `…` | post-fs-data：节点/一次性安装/权限 |
| `z1rootd.sh` | 6,240 | `0ab441da8956` | root 命令通道（串口迭代用） |

归档：`bridge_backups/products/products_v83r6_<timestamp>/`（含 SHA256SUMS.txt）。

---

## 2. 本轮改了什么（都有本机证据）

| # | 改动 | 为什么（证据） |
|---|---|---|
| 1 | **HWC2 全函数表**（43 个 descriptor，0 capability） | 旧实现死在 `getFunction(3)`（CREATE_VIRTUAL_DISPLAY），928 次失败；现在表全 |
| 2 | **DEVICE 决策**：client 目标在底平面，DEVICE 层取 z 顶部连续段 | 只有 CLIENT→DEVICE 单向合法；demo 见 `z1_policy.h` + 15 条主机单测 |
| 3 | **4 个平面全给 DEVICE**（本用户要求的方向） | 原来无条件留 0 号给 client target ⇒ 4 层必有 1 层降级。现在两遍决策：全 DEVICE 且底层铺满不透明 ⇒ 用满 4 平面、`client_expected=false`；否则保留 0 号 |
| 4 | **RBG565 方向修正**、**alpha 0..255 量化**、**blend mode 编程**、**未用平面显式关闭** | 内核只有 `DRM_FORMAT_RGB565`；驱动取 alpha 低 8 位；不关的平面会继续扫旧 buffer（`mtk_crtc.c` 只在 `pending.dirty` 时重配） |
| 5 | **`MODE_ID` 属性名**（原来查 `MODE`） | 内核 `drm_mode_config.c:333` 创建的是 `MODE_ID`；查错名字 → prop id 0 → 整个 commit `-ENOENT` |
| 6 | **DRM master 获取**：open 后 `drmSetMaster()`，失败就 close+重开 | `DRM_IOCTL_MODE_ATOMIC` 带 `DRM_MASTER`；master 由**第一个 open 者**隐式获得（`drm_auth.c:317`），composer 只有 CAP_SYS_NICE 抢不回来 |
| 7 | **gralloc 绝不持 master**：open `/dev/dri/card1` 后立刻 `DROP_MASTER` | 否则缓存 fd 的 allocator 会永久霸占 master，HWC 每帧 -EACCES |
| 8 | **日志改走 logcat**（tag `z1hwc2`）+ 里程碑级 → WARN | composer 以 `system` 运行，`/dev/kmsg` 是 0644 root ⇒ 旧写法在板上**一行都看不到**；ramdisk 的 `z1_klog_relay.sh` 只转发 `*:W` |
| 9 | **release fence**（sw_sync，属性开关） | 契约要求 device 层 buffer 等下一次提交后才能重写；内核已开 `CONFIG_SW_SYNC=y`，开机脚本挂 debugfs + chmod 0666 |
| 10 | **内核：IOMMU defer 修复** | ovl/rdma0 的 `iommus` 指向没编驱动的 m4u ⇒ probe 被 defer 到超时（10 s 基准 + 每 driver_register 顺延 10 s）= 35 s。给 iommu 节点加 `status="disabled"` ⇒ `-ENODEV` 立即放行 |
| 11 | **内核：vblank 回调注册顺序** | 注册早于 `mtk_crtc_init()`/`mutex_init()`，OVL 中断在 probe 时已活 ⇒ 1/11 概率 NULL panic。挪到初始化之后 |
| 12 | **fb 销毁时机**：`RmFB` 挪到提交成功之后 | 内核 `drm_framebuffer_remove()` 会强制停用仍在引用该 fb 的平面（drm_framebuffer.c:1157），原来在 present 开头 drain 会让被替换的层闪一帧 |
| 16 | **恢复原厂兜底节点 `/dev/graphics/fb0`** | 原厂 goldfish HWC1 打开的是 `/dev/graphics/fb0`（AOSP goldfish `hwc_open`），而这台板子上该节点从未存在（上次启动 279 次 `could not open framebuffer`）⇒ "删掉我们的 HAL 就能回到原厂显示"其实不成立。开机脚本现在从 `/sys/class/graphics/fb0/dev` 取 major:minor 自己 mknod（0660 root:graphics），兜底才真的可用 |
| 15 | **红队/审查第二轮吸收**：present 跳层要回报、import 失败负缓存、HAL 身份属性 | 层在 present 被跳过时置 `lost_fb`，下一帧 validate 报成 CLIENT（否则该层永久缺一块）；import 失败按 `(handle,fd)` 负缓存，避免原厂 lima gralloc 下每帧每层一次失败 PRIME（内核会打 `sg_table is not contiguous`，把 dmesg 和我们的日志预算一起冲掉）；`z1stage.{hwcomposer,gralloc}.sha` 属性让"板上到底跑的是哪份二进制"可读 |
| 14 | **对抗式代码审查修复**（1 P0 + 4 P1 + 10 P2） | 最重的一条：`setLayerBuffer`/`setClientTarget` 里 `dup()` 后不 close 传入 fd —— HIDL 引擎在返回 NONE 时把 fd 所有权交给 HAL（`ComposerCommandEngine.h` 里 `closeFence=false`），60 fps 下几秒就 EMFILE、composer 必死。其余见 `docs/ovl/Z1_HWC2_CODE_REVIEW_20261110.md` |
| 13 | **cmdline `iomem=relaxed`** | 否则 OVL/RDMA/DSI/MUTEX/MMSYS 全被 `iomem_is_exclusive()` 拒掉，`/dev/mem` 读寄存器 -EPERM |

---

## 3. 首启会发生什么（一次性、可自愈）

`on post-fs-data`（约 17–20 s，早于 `class_start hal`）：

1. `z1stage_boot.sh` 建 `/data/local/tmp/z1stage`，把 ramdisk 里的候选种成 `<name>.ramdisk` + `<name>`；
2. **一次性自动安装** `hwcomposer.z1.so` 与 `gralloc.z1.so` 到 `/vendor/lib/hw/`（各自 marker），
   然后把 `active/` 里的副本删掉 —— 于是**下一次普通重启会自动回退到 `/system/lib/hw` 的原厂模块**；
3. 读 `/sys/class/dma_heap/*/dev`，缺节点就 `mknod /dev/dma_heap/<name> 0666`（gralloc 的连续内存来源）；
4. 挂 debugfs 并把 `sync/sw_sync` chmod 0666；
5. `z1_rootd` 起来（root 命令通道）。

所以：**首启就是 OVL 测试**；如果它把显示弄坏，重启一次即自动恢复，不需要人操作。

---

## 4. 首 90 秒要抓的 8 个数（全部可在串口日志里 grep）

| # | 现象 | grep | 期望 |
|---|---|---|---|
| 1 | 部署跑完 | `z1stage: done @` | `errors=0`，时间早于 `class_start hal … succeeded` |
| 2 | 两个模块装上了 | `auto-install hwcomposer.z1.so: applying` / `gralloc.z1.so: applying` | 各 1 条 |
| 3 | dma_heap 结论 | `dma_heap ` | `mknod … ok` 或 `already present`；若是 `WARN /sys/class/dma_heap absent` ⇒ 内核没注册 heap（gralloc 会退 lima，DEVICE 不可能） |
| 4 | 内核早 bind | `Initialized mediatek` / `bound 14007000.ovl` | 不再等 35 s（IOMMU 修复生效） |
| 5 | HAL 打开 card1 且是 master | `z1hwc2: opened /dev/dri/card1` + 无 `not DRM master` | `driver=mediatek` |
| 6 | 平面枚举 | `z1hwc2: probe done:` / `z1hwc2: plane[` | `240x320 planes=4`，4 条 plane 属性 |
| 7 | **合成决策（核心）** | `z1hwc2: validate:` 与 `z1hwc2: all N layer(s) on hardware planes, no client target` | `client=0`；若 `client>0` 说明有层没被 DEVICE（原因在同帧的 `report layer … (原因)`） |
| 8 | 提交成功 | `z1hwc2: first commit done`，且**没有** `atomic commit failed` | modeset 或 `crtc already active` 皆可 |

判读捷径：`device=N client=0` + 无 `client target COPIED` = **OVL 零拷贝合成成立**；
有 `client target COPIED` 或 `drmPrimeFDToHandle(...) failed: Invalid argument` = buffer 不连续，回到第 3 项查 dma_heap。

---

## 5. 怎么证明 OVL 真的在扫我们的 buffer（不需要 WDMA 驱动）

1. `setprop debug.z1.hwc.verbose 3` + `setprop ctl.restart vendor.hwcomposer-2-1`；
2. `logcat -s z1hwc2 | grep 'plane\['` → 得到每个平面的 `layer/fb/dst/src/alpha/blend`；
3. 停掉 SF（`setprop ctl.stop surfaceflinger`，或让 SF 保持最后一帧），root 执行
   `z1kmstest regs --raw --base 0x14007000 --size 0x100`；
4. 对照寄存器：`SRC_CON`(0x2c) 的 bit 应等于用到的平面数；`OVL_SRC_SIZE(n)`(0x38+0x20n)=`h<<16|w`；
   `OVL_OFFSET(n)`(0x3c+0x20n)=`y<<16|x`；`OVL_ADDR(n)`(0x40+0x20n)= 该 fb 的物理地址；`OVL_PITCH(n)`(0x44+0x20n)。
5. 三者一致（HAL 日志 ↔ DRM 状态 ↔ 寄存器）⇒ 内核确实按我们说的编了 OVL。
   唯一没被这条链覆盖的是 `OVL→RDMA→DSI→面板` 的模拟段 —— 该段由已经通过的
   「左红右绿」面板实验一次性标定，不需要每次复核。
6. 像素级写回（WDMA）需要新驱动（本树完全没有 WDMA：无文件、无 DT 节点、无 route 表项，
   估 2–4 人日），见 `Z1_DISPLAY_VERIFY_PLAN_20261110.md` §4。

---

## 6. 回滚

| 级别 | 操作 | 结果 |
|---|---|---|
| L0 | 普通重启一次 | 一次性安装不再生效，`/vendor` 覆盖被删 ⇒ 原厂 HWC1 + 原厂 gralloc |
| L1 | 串口 shell：`z1_install_hal.sh rollback both`（或手工 `rm /vendor/lib/hw/{hwcomposer,gralloc}.z1.so` + `setprop ctl.restart …`） | 立即回原厂模块，不用重启 |
| L2 | `z1flash_image.sh out/z1_v79/boot.img` | 回到上板验证过的 v79（原厂 HWC1 路径） |

---

## 7. 已知缺口（按优先级）

1. **present/retire fence 仍是 -1**：sw_sync 的 timestamp 对 DispSync 不可靠，先不动；
   SF 继续用软件 vsync（本机一直如此）。
2. **没有 vsync 上报**：SF 靠软模型；`setVsyncEnabled` 只记标志。真 vsync 需要
   `DRM_IOCTL_WAIT_VBLANK` 线程 + `HWC2_CALLBACK_VSYNC`，属下一步。
3. **power mode OFF 不熄屏**（只跳过 present）：route B 下 DSI 一直在流。
4. **缓存一致性未实测**：GPU 写 CMA buffer → OVL 扫描，需要 `DMA_BUF_IOCTL_SYNC` 路径正确；
   上板后用「纯色块 + 逐字节对比」验证。
5. **`/dev/dma_heap` 是否由内核注册**未知 —— 开机脚本会给出确定答案（见第 4 节第 3 项）。
6. **WDMA 写回**未做（像素级自动验证的唯一路径）。

---

## 8. 一条命令开始（板子接好后）

```sh
# 终端 1：刷（需先拔 USB，脚本会等 preloader；成功后拔线再上电）
/home/lxj/claude/6572/z1a9_gpt/scripts/z1deploy/z1flash_image.sh \
    /home/lxj/claude/6572/out/z1_v83/boot.img --minutes 10

# 终端 2：抓串口
nohup /tmp/z1cap.sh /home/lxj/claude/6572/mainline_recon/z1_boot_v83.log &

# 开机 90 s 后：一次抓完全部证据 + 自动判读
Z1LOG=/home/lxj/claude/6572/mainline_recon/z1_boot_v83.log \
  /home/lxj/claude/6572/z1a9_gpt/scripts/z1deploy/z1_firstboot_check.sh
```

---

## 9. 两个被独立审计证实/证伪的点（写在这里免得再被误导）

**(1) `/vendor/lib/hw` 能不能写？能。** 审计报告读到日志里 `/system` 有 10 次是 `ext4 ro`，
提出"legacy cp 可能失败"的怀疑。我查了同一份日志的实际执行结果：

```
[   20.566243] init: starting service 'exec 14 (/system/bin/cp /hwcomposer.z1.so /vendor/lib/hw/hwcomposer.z1.so)'...
[   20.754625] init: Service 'exec 14 (...)' (pid 296) exited with status 0 waiting took 0.173000 seconds
```

`/vendor` 是 `/system/vendor` 的符号链接，post-fs-data 阶段 `/system` 是 **rw**（cp 退出码 0 即证），
后面的 `ro` 快照是开机之后被重新挂成只读的。所以我们的安装路径有效，不需要额外 remount。
（顺便：v79 那次日志里加载的却是 goldfish 模块 `ranchu_hw_composer`，说明当年那份
`/hwcomposer.z1.so` 本身就不是新 HAL；本轮镜像里是 r6，打包器已逐字节核对 sha256。）

**(2) `iomem=relaxed` 之前，`/dev/mem` 读寄存器必然 -EPERM**（STRICT_DEVMEM + 五块 MMIO 全被驱动
`devm_request_mem_region` 认领），不是"硬件没反应"。已加进 cmdline。

---
