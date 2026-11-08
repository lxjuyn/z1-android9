# Z1「半 GPU 加速」阶段成果（2026-11-08）

这是 Z1（MT6572）跑通 **GPU 渲染**、但**还没有硬件叠加层（OVL/DPU）**的那一版：
应用绘制走 Mali-400（lima + Mesa 26.1.1），SurfaceFlinger 用 **Client（GPU）合成**，
上屏走 LK 留下的 **simplefb**。

## 板上实测过的状态（这一段是这版的意义）

```
console:/ $ dumpsys SurfaceFlinger | grep GLES:
GLES: Mesa, Mali400, OpenGL ES 2.0 Mesa 26.1.1

lima 13010000.gpu: gp - mali400 version major 1 minor 1
lima 13010000.gpu: pp0 - mali400 version major 1 minor 1
lima 13010000.gpu: bus rate = 416000000
```
- 连续运行 **2010 秒**：`ppmmu0 page fault` = 0、`pp0 job timeout` = 0、
  `Fence ... didn't signal` = 0、`Fatal signal` = 0（对比：修之前 190 秒就缺页、
  之后 37 次连续超时、画面永久冻结）
- 开机动画（二屏）与桌面（Trebuchet）都能出来，`sys.boot_completed=1`

## 这版**没有**的东西（后续版本在做）

| 项 | 状态 |
|---|---|
| 硬件叠加层 OVL/DPU | ❌ 4 个层全是 `Client`（GPU 合成），没有 `Device` |
| 音频 | ❌ `no soundcards`（DAPM 路由尚非法） |
| WiFi | ❌ PSM 未关 → 约 95 秒整芯复位、`wlan0` 被注销 |
| 电容键 | ⚠️ 映射为 BACK / HOME / APP_SWITCH |
| 动画 | ⚠️ 被 `z1_pm_guard.sh` 设成 scale 0（当时为省性能） |

## 文件与校验

| 文件 | 大小 | sha256 |
|---|---|---|
| `boot_v33_limareset.img` | 6,223,872 | `5dc0afcfb3ada1b86bea506cea2c9f9798f449b66fa7f7b49e4820158f727793` |
| `system_v34.img.xz.part00..03` | 331,470,364（合计） | 见 `SHA256SUMS.txt` |
| 还原后的 system 镜像 | 943,718,400 | `0e5180700e14638da2108c443e863859b2a2f609f45e1202fb3a01d6be158786` |

## 还原与刷写

```bash
# 1) 还原 system 镜像
cat system_v34.img.xz.part0* | xz -d > system_v34.img
sha256sum system_v34.img     # 必须是 0e518070…158786

# 2) boot 分区（本仓库 tools/flash_with_retry.sh 或 mtk.py wo 0x2980000 <len>）
#    boot 分区偏移 0x2980000，本镜像长 6,223,872 B = 0x5EF800
sudo python3 mtk.py wo 0x2980000 0x5EF800 boot_v33_limareset.img

# 3) system 分区（943 MB，必须分块写；单次整块写会在 30~87% 断流）
#    分区偏移 0x4340000，大小 0x38400000
tools/z1_flash_chunked.sh android system_v34.img 48 6
```

刷写纪律（本项目踩过多次）：
- mtkclient 必须 **root** 运行（`sudo -S`）；
- 必须**先启动 mtkclient 等待，再插 USB**，否则报
  `DaHandler - Please disconnect, start mtkclient and reconnect.`；
- Z1 **插着 USB 不能正常开机**，刷完拔线再开机；
- 换 system 分区后的**第一次启动**可能卡在二屏（ART 无法 relocate boot image），
  重启一次即可（该现象与镜像无关，已在后续版本用 `z1_boot_heal.sh` 兜底）。

## 对应源码

- 内核（lima 复位修复、充电驱动内建、MT6572 DTS）：`lxjuyn/z1-mt6572-mainline`
  - `62cb0ad0f` lima: PP 卡死不可恢复的根治
  - `8c31b6581` charger: MT6323 驱动改为内建
- Android 侧（gralloc 16×16 对齐、GPU 门禁/看门狗、镜像构建补 SELinux 标签）：`lxjuyn/z1-android9`
  - `8ad0795` fix(gralloc): stride 16 像素对齐
  - `c4dd4a1` gpugate: 永不隐藏 mesa / watch log-only
