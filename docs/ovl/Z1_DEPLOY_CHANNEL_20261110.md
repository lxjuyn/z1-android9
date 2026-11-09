# Z1 部署 / 远控通道（z1deploy）— 实施与操作手册

- 日期：2026-11-10
- 目标：**不重刷 boot/system**，只靠串口控制台就能迭代图形 HAL（hwcomposer / gralloc），
  并且每次重启都会按 `/data/local/tmp/z1stage/active/` 这个"激活集"把 HAL 重新装好；
  同时提供 `boot.img` 打包器，把候选二进制和开机脚本一起烘进 ramdisk。
- 状态：主机侧全部交付并在本机做了端到端打包验证；**串口在线路径未上板实测**
  （交付时板子断电），第 12 节逐条列出未验证项。

---

## 0. TL;DR（操作员最快路径）

```bash
cd /home/lxj/claude/6572
export Z1LOG=/tmp/z1_serial.log            # 一个"只追加"的串口抓包文件

# 终端 A：常驻抓包（整个会话只开这一次）
stty -F /dev/ttyUSB0 921600 raw -echo
cat /dev/ttyUSB0 >> /tmp/z1_serial.log &

# 终端 B：传文件 → 激活 → 重启合成器 → 看日志
Z=z1a9_gpt/scripts/z1deploy/z1push.sh
$Z /tmp/z1hwcout/hwcomposer.z1.so hwcomposer.z1.so
$Z --cmd 'mkdir -p /data/local/tmp/z1stage/active && cp /data/local/tmp/z1stage/hwcomposer.z1.so /data/local/tmp/z1stage/active/hwcomposer.z1.so'
$Z --rootcmd 'cp /data/local/tmp/z1stage/active/hwcomposer.z1.so /vendor/lib/hw/hwcomposer.z1.so.z1new
chmod 0644 /vendor/lib/hw/hwcomposer.z1.so.z1new
mv -f /vendor/lib/hw/hwcomposer.z1.so.z1new /vendor/lib/hw/hwcomposer.z1.so
sha256sum /vendor/lib/hw/hwcomposer.z1.so'
$Z --cmd 'setprop ctl.restart vendor.hwcomposer-2-1; sleep 1; setprop ctl.restart surfaceflinger'
$Z --cmd 'dmesg | grep -E "z1stage|z1rootd" | tail -30'
```

回滚：把 `active/hwcomposer.z1.so` 删掉、`/vendor/lib/hw/hwcomposer.z1.so` 删掉、重启合成器
（见 §5.5，等价于"下次开机自动回退"，因为 `active/` 里没有文件时开机脚本会主动删除 vendor 覆盖）。

---

## 1. 角色与数据流

```
 主机 (lxj@6572)                                    Z1 (Android 9 / armv7)
 ┌───────────────────────────────────┐            ┌──────────────────────────────────────────┐
 │ z1push.sh                         │            │ 串口控制台 shell (uid 2000, "console:/ $")│
 │   /dev/ttyUSB0 921600 8N1  ───────┼── 写入 ───▶│   stty -echo / base64 heredoc / cmd.sh     │
 │   /tmp/z1_serial.log (只追加) ◀───┼── 抓包 ────│   [kernel 日志与 shell 回显混在同一条流]   │
 │   唯一 tag 定界 + 过滤 kernel 行  │            │                                          │
 └──────────────┬────────────────────┘            │  /data/local/tmp/z1stage/                 │
                │ 读写 stage 目录（shell 用户）    │   ├─ hwcomposer.z1.so{,.ramdisk}          │
                ▼                                 │   ├─ gralloc.z1.so / z1kmstest            │
     /data/local/tmp/z1stage/  ◀─────────────────│   ├─ active/  (0777, shell:shell)         │
                ▲                                 │   ├─ cmd.sh.tmp → mv → cmd.sh             │
                │                                 │   ├─ cmd.sh  → cmd.run（原子领取）        │
                │                                 │   ├─ out.txt  (0666, 以 ###RC=.. ###DONE 结尾)
                │                                 │   └─ install.sh（开机 root 钩子）         │
                │                                 │                                          │
                │                                 │  service z1_rootd  (/z1rootd.sh, root)    │
                │                                 │   └─ 轮询 cmd.sh → 以 root 执行 → out.txt │
                │                                 │                                          │
                │                                 │  post-fs-data: /z1stage_boot.sh (root)    │
                │                                 │   ├─ 把 ramdisk 里的候选种子到 stage/     │
                │                                 │   └─ 把 active/*.so 装到 /vendor/lib/hw   │
                └─────────────────────────────────┤                                          │
                                                  │  /vendor/lib/hw/hwcomposer.z1.so  ← 覆盖层 │
                                                  │  /system/lib/hw/hwcomposer.z1.so  ← 原厂   │
                                                  └──────────────────────────────────────────┘
```

关键事实（都有证据，见 §11）：

1. `vendor.hwcomposer-2-1` / `vendor.gralloc-2-0` 在 `class_start hal` 时才启动
   （2026-11-09 日志：20.46 s / 20.46 s），而 `post-fs-data` 段在 16.98–19.9 s 就跑完。
2. 这台 init 的 `exec` 是**同步**的：日志里有 `SVC_EXEC pid ... started; waiting...`
   以及 `Command 'exec ...' ... took 56ms and succeeded`。所以 post-fs-data 里串行执行的
   脚本一定在 HAL 服务起来之前完成，**不需要 sleep**，也**不能** sleep。
3. A9 `hw_get_module("hwcomposer")` 的查找顺序是
   variant（`ro.hardware`/`ro.product.board` = `z1`）→ 目录 `/odm/lib/hw` → `/vendor/lib/hw`
   → `/system/lib/hw`（`out/GPU_PATH_VERIFIED_20261105.md` §5.4 已逐条核实）。
   ⇒ `/vendor/lib/hw/hwcomposer.z1.so` 存在时它赢，删掉就回到 `/system/lib/hw/` 的原厂模块。

---

## 2. 文件清单（绝对路径）

| 文件 | 角色 | 运行位置 |
|---|---|---|
| `/home/lxj/claude/6572/z1a9_gpt/scripts/z1deploy/z1rootd.sh` | root 命令守护进程：轮询 `cmd.sh`，执行、捕获输出、写 `###RC/###DONE`；开机执行一次 `install.sh` | 设备，`service z1_rootd` |
| `/home/lxj/claude/6572/z1a9_gpt/scripts/z1deploy/z1stage_boot.sh` | post-fs-data 的 root 引导脚本：建 stage 目录、把 ramdisk 候选种进去、应用 `active/` 到 `/vendor/lib/hw` | 设备，init `exec` |
| `/home/lxj/claude/6572/z1a9_gpt/scripts/z1deploy/init_fragment.rc` | 追加到 `/init.z1.rc` 末尾的 rc 片段（一个 `on post-fs-data` 块 + `service z1_rootd`） | 设备，ramdisk |
| `/home/lxj/claude/6572/z1a9_gpt/scripts/z1deploy/z1push.sh` | 主机侧串口传输：推文件 / `--cmd` / `--rootcmd` / `--extract` / `--dry-run` | 主机 |
| `/home/lxj/claude/6572/z1a9_gpt/scripts/z1deploy/pack_boot.sh` | 打 boot.img：改 rc、烘脚本和候选、6 步校验、反解最终镜像 | 主机 |
| `/home/lxj/claude/6572/out/Z1_DEPLOY_CHANNEL_20261110.md` | 本文档 | 主机 |
| `/home/lxj/claude/6572/out/z1_v83test/boot.img` | 端到端验证产物（sha256 `62145396…`） | 主机 |
| `/home/lxj/claude/6572/out/z1_v83test/pack_boot_report.txt` | 打包器完整报告（含两次记录表） | 主机 |
| `/home/lxj/claude/6572/out/z1_v83test/embedded_init.z1.rc` | 从**最终 boot.img** 反解出的 `/init.z1.rc` | 主机 |
| `/home/lxj/claude/6572/out/z1_v83test/stage/` | 本次验证用的 stage 目录（真 HWC 112,084 B + 两个 4 KiB 假件） | 主机 |

设备侧固定路径：

| 路径 | 说明 |
|---|---|
| `/init.z1.rc` | ramdisk 的 init 配置（`pack_boot.sh` 在末尾追加片段） |
| `/z1stage_boot.sh`、`/z1rootd.sh` | ramdisk 根目录的脚本（由本方案烘入） |
| `/hwcomposer.z1.so`、`/gralloc.z1.so`、`/z1kmstest` | ramdisk 根目录的候选（可选） |
| `/data/local/tmp/z1stage/` | 运行期工作目录（`0777`，`shell:shell`） |
| `/data/local/tmp/z1stage/active/` | **激活集**（`0777`：shell 用户直接可写） |
| `/data/local/tmp/z1stage/cmd.sh` | 操作员投递的 root 脚本（必须 `mv` 原子落地） |
| `/data/local/tmp/z1stage/out.txt` | root 脚本的 stdout+stderr + `###RC=<rc> ###DONE` |
| `/data/local/tmp/z1stage/install.sh` | 可选：每次 rootd 启动（≈每次开机）执行的 root 钩子 |
| `/vendor/lib/hw/hwcomposer.z1.so` | 覆盖层；存在即胜出 |
| `/system/lib/hw/hwcomposer.z1.so` | 原厂 HWC1 模块（24,660 B，sha256 `394ebed3…`），覆盖层删掉后自动生效 |
| `/system/lib/hw/gralloc.z1.so` | 原厂 dma-buf gralloc（25,116 B，sha256 `a641ed5d…`） |

---

## 3. 启动时序（为什么片段放在文件末尾还来得及）

`/init.rc:8` 有 `import /init.${ro.hardware}.rc`，但**被 import 的文件是在 `init.rc` 解析完
之后才解析**的（2026-11-09 日志：`/init.rc:387` 的 post-fs-data 先执行，随后才是
`/init.z1.rc:32`、`/init.z1.rc:55`）。同一文件内的多个 `on post-fs-data` 块按**出现顺序**
入队执行，所以追加在文件末尾的块最后运行。

实测时间线（`mainline_recon/z1_boot_20261109_1838.log`，v78 级镜像）：

| 时刻 | 事件 |
|---|---|
| 15.17 s | `processing action (post-fs-data) from (/init.rc:387)` |
| 16.98 s | `processing action (post-fs-data) from (/init.z1.rc:32)` ← legacy `cp /hwcomposer.z1.so` 在这里 |
| 17.8–19.6 s | 各 `/system/etc/init/*.rc` 的 post-fs-data 块 |
| **≈18 s** | **本方案的 `/z1stage_boot.sh`（追加块的执行点）** |
| 20.45 s | `starting service 'vendor.gralloc-2-0'` |
| 20.47 s | `starting service 'vendor.hwcomposer-2-1'` |
| 20.55 s | `starting service 'surfaceflinger'` |

另外两条硬证据：

- `exec` 阻塞：`[13.790076] init: SVC_EXEC pid 165 (...) started; waiting...`，
  以及 `[13.609061] init: Command 'exec u:r:init:s0 root root -- /system/bin/sh /z1_wifi_pre.sh'
  ... took 56ms and succeeded` —— 时长就是被等的那条命令的耗时。
- rc 里重复写 `on post-fs-data` 块是合法的：v79 的 `/init.z1.rc` 本身就有两个
  （第 32、62 行），2026-11-09 的板上日志里同一结构的两个块依次执行（那一版行号是 32、55）。

⇒ 追加块在 legacy 安装之后、HAL 服务之前跑，顺序确定；`z1stage_boot.sh` 实测（本机模拟）
耗时 ~0.06 s，远小于 2 s 余量。**但它是同步执行的，脚本里禁止 sleep/长命令。**

---

## 4. `active/` 激活集：语义、优先级、回退

`z1stage_boot.sh` 在每次 `post-fs-data` 做两件事：

### 4.1 种入候选（ramdisk → stage）

对 `/hwcomposer.z1.so`、`/gralloc.z1.so`、`/z1kmstest`、`/z1rootd.sh` 每个（存在才处理）：

| 目标 | 规则 |
|---|---|
| `/data/local/tmp/z1stage/<名字>.ramdisk` | **每次都覆盖**：永远等于"当前这个 boot.img 里烘的那份"，供操作员核对/激活 |
| `/data/local/tmp/z1stage/<名字>` | **只在不存在时创建**：操作员的工作副本永不被开机脚本覆盖 |

权限：`.so` 0644，脚本/可执行 0755，`chown 2000:2000`（AID shell）。
注意用的是数字 uid/gid —— 镜像里没有 `/etc/passwd`，`chown shell:shell` 会解析失败。

### 4.2 应用激活集（stage → /vendor）

```sh
if [ -f /data/local/tmp/z1stage/active/hwcomposer.z1.so ]; then
    cp active/hwcomposer.z1.so /vendor/lib/hw/hwcomposer.z1.so.z1tmp
    chmod 0644 ...; mv -f ...z1tmp /vendor/lib/hw/hwcomposer.z1.so
else
    rm -f /vendor/lib/hw/hwcomposer.z1.so        # 回退到 /system/lib/hw 的原厂模块
fi
# gralloc.z1.so 完全同理
```

`gralloc.z1.so` 也装到 `/vendor/lib/hw/`（variant 名相同、目录优先，所以压过
`/system/lib/hw/gralloc.z1.so`），流程与开头看到的 `/vendor/lib/hw/gralloc.default.so`
无关（`default` 只在所有 variant 都找不到时才试）。

**为什么用 cp 到临时名再 rename**：`/vendor/lib/hw/*.so` 会被合成器进程 `mmap`。
如果在原地覆盖，正在运行的进程会 SIGBUS。rename 是原子的，旧 inode 对已打开进程继续有效。
正常开机路径上 HAL 还没启动，看似无所谓，但这样写让"人工重跑引导脚本"也安全。

**语义总结**

| `active/hwcomposer.z1.so` | 结果 | 生效模块 |
|---|---|---|
| 存在 | 覆盖到 `/vendor/lib/hw/hwcomposer.z1.so` | active 里那份 |
| 不存在 | 删除 `/vendor/lib/hw/hwcomposer.z1.so` | `/system/lib/hw/hwcomposer.z1.so`（原厂 HWC1） |

⚠️ **行为变化（必读）**：v79–v82 是"开机无条件把 ramdisk 的 `/hwcomposer.z1.so`
cp 到 /vendor"。本方案之后，**没有 `active/` 时 vendor 覆盖会被主动删除**，
所以刷完 v83test 的首启会用 `/system` 的原厂 HWC1，而不是 ramdisk 里那份候选。
要恢复"ramdisk 候选生效"，在板上执行（一次即可，重启后依然生效）：

```sh
mkdir -p /data/local/tmp/z1stage/active
cp /data/local/tmp/z1stage/hwcomposer.z1.so.ramdisk /data/local/tmp/z1stage/active/hwcomposer.z1.so
```

然后按 §5.3 在线装到 /vendor 并重启合成器（或直接重启板子，开机脚本会自动装）。
legacy 的两行 `exec ... /system/bin/cp /hwcomposer.z1.so ...` 仍留在 v79 起的 `/init.z1.rc` 里，
**先执行、随后被本脚本覆盖/删除**，不要拿它判断当前生效的是哪份 HAL。

---

## 5. 一次完整迭代（操作员视角）

### 5.1 准备串口（只做一次）

```bash
export Z1LOG=/tmp/z1_serial.log
stty -F /dev/ttyUSB0 921600 raw -echo
cat /dev/ttyUSB0 >> "$Z1LOG" &
```

`Z1LOG` 必须由**只追加**的读者产生（`>>`）。`z1push.sh` 只写 `/dev/ttyUSB0`，不读它。
主机波特率 921600 8N1（板级 `console=ttyS0,921600n8`）。

### 5.2 传文件

```bash
Z=z1a9_gpt/scripts/z1deploy/z1push.sh
$Z /tmp/z1hwcout/hwcomposer.z1.so hwcomposer.z1.so         # 0644
$Z --mode 0755 out/z1_v83test/stage/z1kmstest z1kmstest    # 可执行
$Z --dry-run /tmp/z1hwcout/hwcomposer.z1.so hwcomposer.z1.so   # 只打印计划，不碰串口
```

成功输出一行 `PASS <本地> -> /data/local/tmp/z1stage/<名字>  <字节数> bytes  mode <mode>  sha256 <hash>`，
失败退出码非 0（会重试 `--retries` 次，默认 1 次）。

### 5.3 激活并重启合成器

```bash
# 1) 放进激活集（shell 用户就能写，目录 0777）
$Z --cmd 'mkdir -p /data/local/tmp/z1stage/active && cp /data/local/tmp/z1stage/hwcomposer.z1.so /data/local/tmp/z1stage/active/hwcomposer.z1.so'

# 2) 以 root 落到 /vendor（cp→chmod→rename，绝不在原地覆盖）
$Z --rootcmd 'cp /data/local/tmp/z1stage/active/hwcomposer.z1.so /vendor/lib/hw/hwcomposer.z1.so.z1new
chmod 0644 /vendor/lib/hw/hwcomposer.z1.so.z1new
mv -f /vendor/lib/hw/hwcomposer.z1.so.z1new /vendor/lib/hw/hwcomposer.z1.so
sha256sum /vendor/lib/hw/hwcomposer.z1.so'

# 3) 重启 composer + SurfaceFlinger
$Z --cmd 'setprop ctl.restart vendor.hwcomposer-2-1; sleep 1; setprop ctl.restart surfaceflinger'
```

说明：
- `setprop ctl.restart surfaceflinger` 会触发 `/vendor/etc/init/android.hardware.graphics.composer@2.1-service.rc`
  里的 `on property:init.svc.surfaceflinger=stopped → restart vendor.hwcomposer-2-1`，
  所以单发这条也能带上 composer；上面显式两条是为了让"先装库、后起服务"的顺序一目了然。
- `vendor.gralloc-2-0` 的 rc 有 `onrestart restart surfaceflinger`：换 gralloc 时
  `setprop ctl.restart vendor.gralloc-2-0` 会自动把 SF 重启。
- App（zygote 里的进程）如果自己加载过旧 gralloc，需要 `setprop ctl.restart zygote` 才会换；
  HWC 只有 composer/SF 两侧，按上面三步即可。

### 5.4 看结果

```bash
$Z --cmd 'getprop z1stage.hwcomposer; getprop z1stage.gralloc'      # active | system
$Z --cmd 'ls -l /vendor/lib/hw/ | grep -E "hwcomposer|gralloc"'
$Z --cmd 'sha256sum /vendor/lib/hw/hwcomposer.z1.so /system/lib/hw/hwcomposer.z1.so'
$Z --cmd 'dmesg | grep -E "z1stage|z1rootd" | tail -30'
$Z --cmd 'logcat -d -s ComposerHal:HWC2On1Adapter:HWComposer:SurfaceFlinger | tail -40'
```

`z1stage_boot.sh` 每次开机都会在 `/dev/kmsg` 打（前缀 `z1stage:`）：
种了哪些文件、秒数、每个安装文件的 sha256、`done @…s: seeded=… applied=… removed=… errors=…`。

### 5.5 回滚

**立刻回滚（不用重启）**

```bash
$Z --cmd 'rm -f /data/local/tmp/z1stage/active/hwcomposer.z1.so'
$Z --rootcmd 'rm -f /vendor/lib/hw/hwcomposer.z1.so; ls -l /vendor/lib/hw/ | grep hwcomposer; echo gone'
$Z --cmd 'setprop ctl.restart vendor.hwcomposer-2-1; sleep 1; setprop ctl.restart surfaceflinger'
```

回到原厂 HWC1：`sha256sum /system/lib/hw/hwcomposer.z1.so` 应为
`394ebed30d920b138b13b80872e7a4c1ce194fbbca70c0dee28096d261f30536`（24,660 B）。

**彻底回滚（下次开机自动回到原厂）**：删掉 `active/` 里对应文件即可 —— 开机脚本会主动
`rm -f /vendor/lib/hw/<名字>`。也可以直接 `rm -rf /data/local/tmp/z1stage/active`
（stage 里其它工作副本还会被重新种入 `.ramdisk`，不影响开机）。

**回滚 boot.img**：刷回 `out/z1_v82/boot.img`（或任何旧版）即可；`/data` 里的 stage 目录
不受影响（但旧 boot.img 没有 `z1stage_boot.sh`，开机时不会再应用 `active/`）。

---

## 6. 设备侧 root 通道协议（`z1rootd.sh`）

```
操作员                                 z1_rootd (root, 常驻)
  │  cat > cmd.sh.tmp <<'Z1EOF' … Z1EOF   │
  │  mv -f cmd.sh.tmp cmd.sh  ───────────▶ 轮询每 1 s：文件存在且**连续两次采样大小不变**
  │                                        │   → mv cmd.sh cmd.run（原子领取）
  │                                        │   → /system/bin/timeout 120 /system/bin/sh cmd.run
  │                                        │       stdout+stderr → out.txt（先 truncate，0666）
  │                                        │   → 追加 "###RC=<rc> ###DONE"
  │                                        │   → 删 cmd.run，恢复目录/文件权限
  │  cat out.txt  ◀─────────────────────── │
```

- **超时**：优先用 toybox `timeout`（镜像里 `/system/bin/timeout` 存在，已核实）；
  不存在时退化成 1 s 心跳看门狗（3 s 后 TERM、再 2 s KILL），日志写
  `z1rootd: watchdog: cmd exceeded Ns, killing pid …`，`###RC=124`。
- **半写保护**：两次采样大小一致才领取。`z1push.sh --rootcmd` 额外要求先写
  `cmd.sh.tmp` 再 `mv`（单条 rename，内核保证原子），所以正常路径永远不会执行半个脚本。
- **僵尸 `cmd.run`**：开机/重启 daemon 时若发现遗留 `cmd.run`，改名为 `cmd.run.stale`，
  **不会**盲目重跑（避免重启后重复执行破坏性命令）。
- **`install.sh`**：每次 daemon 启动（≈每次开机）执行一次，输出在 `install.out.txt`，
  同样以 `###RC=… ###DONE` 结尾。它比 `z1stage_boot.sh` 晚几毫秒、但仍在 HAL 启动前
  （daemon 在 post-fs-data 块里 `start`），适合做"每次开机把某个 staged 文件再同步一次"。
- **日志**：所有步骤都写 `/dev/kmsg`，前缀 `z1rootd:`（`dmesg | grep z1rootd`）。
- **权限**：`out.txt` 0666、stage 目录 0777、`active/` 0777 且 `chown 2000:2000`，
  shell 用户可读结果、可写指令、可 `rm out.txt`。
- **存活**：服务没有 `oneshot`，daemon 若退出 init 会自动重启（前提是 boot.img 里的 rc
  生效；rc 只在开机时读，改 rc 必须重刷 boot.img）。

---

## 7. 主机侧 `z1push.sh` 细节

| 子命令 | 作用 |
|---|---|
| `z1push.sh [--mode M] [--retries N] <file> [name]` | base64(400 列) → heredoc → `base64 -d` → 设备 `sha256sum` 与主机比对 → `chmod` → `mv` → 再校验 → `PASS` |
| `z1push.sh --cmd '<cmd>'` | 用唯一 tag 包住命令，从抓包文件里取回输出并过滤 kernel 行 |
| `z1push.sh --rootcmd '<script>'` / `--rootcmd -` | 上传 `cmd.sh.tmp`→`mv`→等 `###DONE`→打印 `out.txt`（以 root 执行） |
| `z1push.sh --extract <tag> [log]` | 离线：从保存下来的抓包里抽某一段（事后取证） |
| `z1push.sh --dry-run …` | 只打印计划，**不打开串口** |

实现要点（都对应实机风险）：

1. **tag 唯一**：`z1t<epoch><pid>`；标记行用"行尾匹配"，因为板子的 prompt
   `console:/ $ ` 不带换行，会粘在标记输出前面。
2. **回显不会污染**：命令按 `T=<tag>; echo ${T}_BEGIN; …; echo ${T}_END` 发送，
   万一 tty 回显开着，抓包里出现的是字面量 `${T}_BEGIN`，不会和真正的标记输出混淆。
3. **kernel 日志过滤**：抽取后丢弃 `^[ 数字.数字]` 开头的行（串口上 printk 与 shell 输出混流）。
4. **流控**：每 400 字符一行、每 8 行 sleep 50 ms（`Z1CHUNK_*` 可调），
   总时间 ~4 s / 112 KB；没有硬件流控，靠 sha256 兜底，不对就重试。
5. **超时**：heredoc 超时按 `20 + b64字节/8000` 秒计算（`Z1PUSH_TIMEOUT` 可覆盖）；
   找不到 `_END` 就报"设备不可达/命令挂死"，并给出排查清单。
6. **heredoc 终止符冲突**：base64 行理论上可能正好等于 `Z1EOF`，脚本会自动延长终止符直到
   没有冲突。
7. **失败自动恢复回显**：`stty -echo` 之后任何失败路径都会尽力补发 `stty echo`（EXIT trap），
   不让操作员面对"打字看不见"的终端。
8. **设备名合法性**：远端名只允许 `[A-Za-z0-9._-]` 且不能以 `.` 开头，禁止路径穿越。

---

## 8. 打包 boot.img（`pack_boot.sh`）

```bash
z1a9_gpt/scripts/z1deploy/pack_boot.sh \
    --stage-dir <目录，内含 hwcomposer.z1.so / gralloc.z1.so / z1kmstest（都可缺）> \
    --out out/z1_vNN/boot.img \
    --name z1_vNN
```

默认值（都可在命令行覆盖）：

| 参数 | 默认 |
|---|---|
| `--zimage` | `fork_linux/arch/arm/boot/zImage` |
| `--dtb` | `fork_linux/arch/arm/boot/dts/mediatek/mt6572-z1.dtb` |
| `--base-rootfs` | `/tmp/rootfs_v79.bin` |
| `--template` | `out/z1_v79/boot.img`（只取 Android/MTK 头字段和 cmdline） |
| `--name` | 输出目录名（≤16 字符） |
| 说明 | `--zimage/--dtb` 的默认值指向**当前** `fork_linux` 构建产物；要复现某版镜像的内核，显式传入钉住的 zImage/dtb（本次验证见 §11.1） |
| `--max-size` | `6000000`（分区 6,291,456） |
| `--report` | `<输出目录>/pack_boot_report.txt` |

六步（任何不符立即 `pack_boot: ERROR: …` 退出非 0）：

1. 抽出 `/init.z1.rc`，在**文件末尾**追加 `init_fragment.rc` 原文，然后重新解析校验：
   `service` 必带 `class`、动作命令/服务选项都是 Android 9 关键字、无 TAB、LF、结尾有换行、
   原有字节一个都没改（`patched == orig + "\n" + fragment` 逐字节断言）；打印 diff。
2. 组装记录表并按"base 里已有就 `--replace`、没有就 `--add`"调用
   `z1_ramdisk_edit.py`（记录级编辑，未触碰记录保持字节一致）。
3. 重量级校验：重新解析产物 ramdisk，逐条断言 `名字/大小/sha256/mode`；记录数增量 == 新增数；
   其余记录 header+内容逐字节一致；cpio TRAILER 与尾部 padding 保留。
4. 调 `tools/z1_pack_mtk_bootimg.py` 打包。
5. 重新解析**最终 boot.img**：`ANDROID!`、`kernel_size == MTK KERNEL size + 512`、
   `ramdisk_size == MTK ROOTFS size + 512`、kernel 段 == `zImage+dtb`、内嵌 ROOTFS == 产物，
   大小 < `--max-size` 且 < 6 MiB。
6. **强制反向校验**：从最终镜像里解出 `/init.z1.rc`，打印追加块的每一行（带行号），
   断言 `service z1_rootd`、它的 `class/user/group/seclabel`、`/z1stage_boot.sh` 的 exec
   都在；并把这份 rc 落到 `<输出目录>/embedded_init.z1.rc` 留证。

产物报告（`pack_boot_report.txt`）包含输入 sha256、两次完整记录表、diff、RESULT 行和汇总表。

---

## 9. 安全属性

| 属性 | 机制 |
|---|---|
| 不重刷即可换 HAL | `/vendor/lib/hw` 是 `/system` 分区里的目录（rw），root daemon 可写；换完重启 composer/SF 即生效 |
| 重启后自动恢复 | 每次 `post-fs-data` 由 `z1stage_boot.sh` 按 `active/` 重新安装（在 HAL 启动前 2 s 完成） |
| 回到原厂 | `active/` 里没有文件 → 主动删除 vendor 覆盖 → `hw_get_module` 回落到 `/system/lib/hw` |
| 不会被开机脚本覆盖工作副本 | `<名字>` 只在不存在时创建；`<名字>.ramdisk` 才是"当前 boot.img 里的版本" |
| 不会写坏正在使用的 .so | 一律 `cp` 到同目录临时名 + `rename` |
| 命令不会半执行 | daemon 两次采样确认大小稳定；`--rootcmd` 用 `cmd.sh.tmp` + 原子 `mv` |
| 命令挂死不会拖死 daemon | toybox `timeout` / 看门狗 + `###RC=124` |
| 串口传错不会写坏文件 | 设备侧 `sha256sum` 与主机比对，失败重试，仍失败则 FAIL 且不 `mv` |
| boot.img 改坏可发现 | 6 组断言 + 最终镜像反解 rc/记录（`pack_boot.sh` 内建） |
| 不会误刷/误连 | 方案不包含任何刷写命令；`--dry-run` 不打开串口 |

---

## 10. 风险与坑（务必先读）

1. **`exec` 是同步的**：`/z1stage_boot.sh` 在 post-fs-data 里阻塞 init。禁止在它里面
   sleep、等网络、跑长命令；将来往这个块里再加东西也要保持"毫秒级"。
2. **rc 只在开机时读**：改 `init_fragment.rc` 或两个设备脚本 → 必须重新 `pack_boot.sh` 并刷
   boot.img。串口通道能传文件，但**改不了已经在跑的 init 行为**（可以改 `/data` 里的副本，
   那是下一次开机的行为基础，不是本次）。
3. **首启默认是原厂 HWC1**（见 §4 的行为变化）。若期望 ramdisk 候选默认生效，必须先激活
   `active/`（一条 `cp` + 重启），或在刷机前用 `--stage-dir` + 一个 `install.sh` 钩子自己补。
4. **legacy 安装行仍在**：v79 起 `/init.z1.rc` 里有
   `exec … /system/bin/cp /hwcomposer.z1.so /vendor/lib/hw/hwcomposer.z1.so`，
   它先执行，随后被本方案覆盖/删除。判断"当前生效哪份"只看 `sha256sum` 和 `getprop`。
5. **`/data` 只读/损坏**：`z1stage_boot.sh` 会 log `FATAL/WARN` 并继续（不拖延开机），
   此时通道不可用；`/data` 的 ext4 问题在项目里出现过，先按 fstab 的 `check` 修。
6. **SELinux**：当前 `androidboot.selinux=permissive`，标签不影响加载。转 enforcing 之前需给
   `/vendor/lib/hw` 下新装入的 `.so` 补 `restorecon`（脚本已尽力调用
   `/system/bin/restorecon`，但标签规则仍需核对）。
7. **串口没有流控**：921600 下如果板上日志刷屏导致丢字符，`z1push.sh` 会因 sha256 不符而
   FAIL（不会写坏文件）；可降 `Z1B64CHARS`（如 200）或加大 `Z1CHUNK_SLEEP`。
8. **抓包文件必须只追加**：被轮转/截断时 `z1push.sh` 的偏移会失效，会报
   `no <tag>_END`；重开抓包文件后重跑即可。
9. **boot.img 头 id 字段**：本项目 packer 写的是 `sha1(kernel+rootfs)[:8]`，不是 legacy
   mkbootimg 的 `sha1(kernel||ksize||ramdisk||…)` 规则，所以
   `tools/validate_mtk_boot.py` 会报 `Android header id SHA1 mismatch`——
   **未改动的 v79 也一样**，LK 不读该字段。别把它当成本次引入的问题。
10. **6 MiB 分区余量**：v83test 为 5,824,512 B，距 6,000,000 还有 175 KB、距分区上限
    466,944 B。再往 ramdisk 里塞大文件（比如整套 gralloc + mesa）会顶到上限，
    `pack_boot.sh` 会在打包前用 `--max-size` 拦住。
11. **`active/` 是 0777 且 `/data/local/tmp` 只对 shell/adb/root 可见**：这是开发机的便利设置，
    不是量产安全姿态。
12. **不要并发两个 `--rootcmd`**：daemon 是单线程；`--rootcmd` 会先等 `cmd.run` 消失再投递，
    但两个操作员同时操作仍可能互相覆盖 `out.txt`。串口是单通道，本来也不适合并行。

---

## 11. 本次验证记录（可复核）

### 11.1 输入（全部核过 sha256）

| 输入 | 大小 | sha256 |
|---|---|---|
| `/tmp/rootfs_v79.bin` | 1,470,144 | `9f069ed1d3945b2d3ad7370e61d3ef1b4db69ecb94d8481c93236087a3b2b2c3` |
| `out/z1_v79/boot.img`（模板） | 5,808,128 | `d332b5e962438b8f07efa22ebb785f895516454fb3cc8548990d11c75bf0fc84` |
| `out/z1_v83test/v79_zImage`（= v79 kernel 段） | 4,313,600 | `1baaa00d79821a08ed96a403b0c483226bc056f65dff0f3d9c3f8278494e3e26` |
| `out/z1_v83test/v79_dtb`（= v79 kernel 段） | 20,979 | `444c944dcc7d91feed27680b6fcf46bee01124e43770d83204b084f65f9066d3` |
| `/tmp/z1hwcout/hwcomposer.z1.so` | 112,084 | `e51ce5368da0b48e7b0a8889e6a8e28cc013d29ba1c7a2d8cbd697ae1b075ba4` |

`/tmp/rootfs_v79.bin` 与 v79 boot.img 里内嵌的 ROOTFS 完全相同（sha256 一致）。

**内核输入是"钉住的 v79 内核段"**：从 `out/z1_v79/boot.img` 的 kernel 段按 FDT 头切出的
zImage + dtb 存为 `out/z1_v83test/v79_zImage` / `out/z1_v83test/v79_dtb`，两者拼接后 sha256 =
`262890c5a424f931b85566c2173c1c53fef60a4f95e42fe18ce7a104642d7923`，与 v79 镜像里的 kernel
段逐字节一致。验证时显式传 `--zimage/--dtb` 指向这两个文件，而不是用默认值（默认值指向
`fork_linux/`，本次验证期间该树被并行重编更新过，默认值会漂）。

### 11.2 产物

| 项 | 值 |
|---|---|
| boot.img | `out/z1_v83test/boot.img`，**5,824,512 B**，sha256 `62145396b7248b717641c673efcf4cfe4321449d51b07e3d63b5697b8ed74e0b` |
| 头字段 | `name=z1_v83test`，`page=2048`，`kernel_size=4,335,091`，`ramdisk_size=1,485,643` |
| MTK 头 | `KERNEL size=4,334,579`（+512 = kernel_size），`ROOTFS size=1,485,131`（+512 = ramdisk_size） |
| ramdisk | 85 条记录（81 → 85：2 替换 + 4 新增），gzip 1,485,131 B，解压 2,857,920 B |
| 部署记录 | `init.z1.rc` 7,569 B / 0100750；`z1stage_boot.sh` 7,012 B / 0100755；`z1rootd.sh` 6,240 B / 0100755；`hwcomposer.z1.so` 112,084 B / 0100644；`gralloc.z1.so` 4,096 B / 0100644；`z1kmstest` 4,096 B / 0100755 |
| 未触碰记录 | 78 条逐字节一致，cpio TRAILER + 72 B 尾部 padding 保留 |
| 确定性 | 同一组输入在不同临时目录重跑三次，boot.img sha256 完全相同 |
| 反向校验 | 最终镜像里的 `/init.z1.rc` 与 `init.z1.rc.patched` 逐字节相同（sha256 `454e1beb23659a0be5562b00125d956dc587d8e30c1f2cb97396c367c4a5bcb9`），`service z1_rootd` 在第 176 行、exec 在第 171 行（追加块是第 154–180 行） |

### 11.3 独立交叉校验

`tools/validate_mtk_boot.py out/z1_v83test/boot.img --require-ramdisk --dtb …`：
布局/page/MTK wrapper（`KERNEL payload=4334579`、`ROOTFS payload=1485131`）/gzip initrd
全部通过；**唯一失败项是 header id SHA1**，而同样的失败也出现在未改动的
`out/z1_v79/boot.img` 上（该字段是项目 packer 一贯写法，LK 不读）。

### 11.4 设备脚本本机模拟（路径替换后跑逻辑）

- `z1stage_boot.sh`：无 `active/` → 删掉 vendor 覆盖、种入 4 个 `.ramdisk`（0.06 s）；
  有 `active/` → 装入 0644 且 sha256 相符、**不覆盖操作员工作副本**；vendor 目录只读时
  → 记 `ERROR … leaving … untouched`、退出 0；每次都输出 `done @…s: seeded=… applied=…`。
- `z1rootd.sh`：`install.sh` 开机执行一次（`###RC=0 ###DONE`）；`cmd.sh` 正常 → `###RC=3 ###DONE`
  且 `cmd.run` 被删；挂死命令 → 4 s 后 `###RC=124 ###DONE`，daemon 存活并继续服务；
  无 `timeout` 时看门狗分支同样工作；遗留 `cmd.run` → 改名 `cmd.run.stale` 不重跑；
  TERM 信号 trap 生效。

### 11.5 `z1push.sh`

- `--dry-run` 对一个 112,084 B 文件输出：base64 149,448 字符 = 374 行 × 400 列、
  heredoc 终止符、`~1.6 s @921600` + 2.3 s 限速、8 步执行计划、首末行样例；**未打开串口**。
- `--extract` 用构造的抓包日志验证：prompt 前缀、回显行（含字面量 `${T}_BEGIN`）、
  穿插的 kernel 时间戳行都处理正确；坏 tag 给出明确错误并以 1 退出。

---

## 12. 未上板验证项（交付时板子断电）

1. **真实串口链路**：`stty -echo` / heredoc / `base64 -d` / `sha256sum` / marker 提取整套只在
   本机用构造日志验证过，没有在 CH340 + 921600 的真实链路上跑过。
2. **设备侧 `chown 2000:2000`**：按 `AID_SHELL=2000` 写死（镜像里没有 `/etc/passwd`，
   名字解析会失败），未在板上确认 `/data/local/tmp/z1stage` 的最终属主。
3. **init 对 `seclabel` 的接受性**：静态检查通过；板上 2026-11-09 日志里没有任何
   `Invalid keyword`，说明现有 rc 的 `seclabel` 行没有报错。但用 `strings` 扫 ramdisk 的
   `/init` 二进制搜不到 `seclabel` 字面量（`writepid`/`override` 同理），这一现象未解释；
   功能上无风险：即使该选项被忽略，服务也会以 init 默认上下文（就是 `u:r:init:s0`）运行。
4. **`/vendor/lib/hw` 的实际可写性**：fstab 里 `/system` 是 `rw`，任务说明也确认
   `/vendor/lib/hw` 可被 root 写；本次只在镜像层面核对了该目录、没有上板写。
5. **HAL 换入后的实际显示效果**：本次只建通道，没有验证任何候选 HWC 的渲染行为。

---

## 附录 A：为什么没有把片段插进第一个 `on post-fs-data` 块

可以插，但没必要：init 对同一 trigger 的动作按解析顺序执行，追加在文件末尾的块
仍然在 legacy 安装行之后、在 `class_start hal` 之前（§3 有实测时刻表）。
独立块的好处是 **diff 只新增不修改**（`patched == orig + "\n" + fragment` 可逐字节断言），
`pack_boot.sh` 因此能对"原有字节一个都没改"做强校验。若将来想插进现有块，只需改
`pack_boot.sh` 的拼接方式，校验逻辑不变。

## 附录 B：关键命令速查（设备侧，抄给串口用）

```sh
# 1) 在线安装新的 hwcomposer.z1.so（root 通道；shell 用户先备好 active/）
mkdir -p /data/local/tmp/z1stage/active
cp /data/local/tmp/z1stage/hwcomposer.z1.so /data/local/tmp/z1stage/active/hwcomposer.z1.so
cat > /data/local/tmp/z1stage/cmd.sh.tmp <<'Z1EOF'
cp /data/local/tmp/z1stage/active/hwcomposer.z1.so /vendor/lib/hw/hwcomposer.z1.so.z1new
chmod 0644 /vendor/lib/hw/hwcomposer.z1.so.z1new
mv -f /vendor/lib/hw/hwcomposer.z1.so.z1new /vendor/lib/hw/hwcomposer.z1.so
sha256sum /vendor/lib/hw/hwcomposer.z1.so
Z1EOF
mv -f /data/local/tmp/z1stage/cmd.sh.tmp /data/local/tmp/z1stage/cmd.sh
cat /data/local/tmp/z1stage/out.txt

# 2) 回滚到原厂 HWC1
rm -f /data/local/tmp/z1stage/active/hwcomposer.z1.so
cat > /data/local/tmp/z1stage/cmd.sh.tmp <<'Z1EOF'
rm -f /vendor/lib/hw/hwcomposer.z1.so
ls -l /vendor/lib/hw/ | grep -E 'hwcomposer|gralloc'
Z1EOF
mv -f /data/local/tmp/z1stage/cmd.sh.tmp /data/local/tmp/z1stage/cmd.sh
cat /data/local/tmp/z1stage/out.txt

# 3) 重启 composer + SurfaceFlinger
setprop ctl.restart vendor.hwcomposer-2-1
setprop ctl.restart surfaceflinger

# 4) 读文件 sha256
sha256sum /vendor/lib/hw/hwcomposer.z1.so /system/lib/hw/hwcomposer.z1.so \
          /data/local/tmp/z1stage/active/hwcomposer.z1.so
```

---

## 附录 A（主会话修订，2026-11-10 22:10）：一次性自动安装 + 三处加固

### A.1 为什么改：v79 遗留的 rc 第 42 行会破坏"重启自动回退"
`/init.z1.rc` 里 v79 时代就有的两行，每次开机都会无条件把 ramdisk 里的
`/hwcomposer.z1.so` 装进 `/vendor/lib/hw/`：

```
42:    exec u:r:init:s0 root root -- /system/bin/cp /hwcomposer.z1.so /vendor/lib/hw/hwcomposer.z1.so
43:    exec u:r:init:s0 root root -- /system/bin/chmod 0644 /vendor/lib/hw/hwcomposer.z1.so
```

而 `pack_boot.sh` 只**追加**、不改已有字节，所以这两行还在。如果一次性自动安装把
候选留在 `active/` 里，那么"坏 HAL → 重启回退到原厂 HWC1"就永远不成立
（第 42 行每次装、`active/` 每次又装回去）。

### A.2 新语义（已用三次开机本机模拟验证）
- **首启**：若 `.hwc_auto_installed` 标记不存在且有 `$STAGE/hwcomposer.z1.so`，
  复制到 `active/` → 走正常 apply → **本次开机有效** → 结尾**删掉** `active/` 里的副本并写标记。
- **第二启**：标记存在 → 不自动装；`active/` 为空 → apply 步骤**主动删除**第 42 行装进去的
  `/vendor/lib/hw/hwcomposer.z1.so` → libhardware 回落到 `/system/lib/hw` 的原厂 HWC1。
- **显式安装**（`z1_install_hal.sh install` / 手工 `cp` 到 `active/`）：文件留在 `active/` 里，
  每次开机都重新 apply，**不会被回退**。
- gralloc 任何情况下都**不自动装**。

模拟结果（`/tmp/z1sim2`，把 4 条绝对路径重定向后真跑脚本）：
```
BOOT1: vendor = 候选(18 B) | active = 空 | marker = 已写
       log: "auto-install: removed the one-shot active copy -> next boot falls back ..."
BOOT2: vendor = 不存在      (log: "no active/hwcomposer.z1.so -> removed ...")
BOOT3: active/ 里放 OPERATOR-BUILD -> vendor = OPERATOR-BUILD
```

### A.3 另外三处加固
1. **dma_heap 设备节点**：本机观察 `/dev/dma_heap` 一直不存在（内核 CMA 池 96 MiB 已建、
   `CONFIG_DMABUF_HEAPS_CMA=y`、`add_cma_heaps` 符号在 vmlinux 里），说明是 ueventd 没建节点。
   现在开机脚本读 `/sys/class/dma_heap/*/dev` 自己 `mknod /dev/dma_heap/<name> c maj:min`（0666），
   并打印 `/sys/class/dma_heap` 是否存在——**这也是判断"内核到底建没建 heap"的证据**。
   没有这个节点，gralloc 只能回退 lima（不连续）→ HWC 只能 CPU 拷贝（有画面但慢）。
2. **debugfs + sw_sync 权限**：`sync_debug.c` 用 `debugfs_create_file_unsafe("sw_sync", 0644, ...)`
   建的是 root:root 0644，而 composer 以 `system` 运行 → 追加 `chmod 0666`。
   （`CONFIG_SW_SYNC=y` 已在 v83 内核里；`debug.z1.hwc.fences` 默认仍是 0。）
3. **一次性脚本绝不做长操作**：post-fs-data 的 `exec` 是同步的，实测整段 ~0.06 s，
   与 class_start hal 之间约 2 s 余量。

### A.4 事实核对（值得记住）
- `init.z1.rc` 被解析的顺序早于 `/vendor/etc/init/*.rc`；Android 9 init 对**重复 service 定义**
  打 `ignored duplicate definition of service 'X'` 并**保留第一个**。所以将来若需要给
  `vendor.hwcomposer-2-1` 加 `capabilities SYS_ADMIN`，可以在 ramdisk 的 `/init.z1.rc` 里
  先定义同名 service 覆盖 vendor 的那份（本轮不需要：DRM master 靠"第一个 open 者"就拿到）。
