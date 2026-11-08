# v30 开机验证清单（2026-11-08）

刷写内容
- system 分区：`out/z1_v30_hmifix/system_v30.img`（sha256 `9829ccd1…`）
  只有 48MB 块 0/2/11 与已整分区校验过的 v29 不同，这 3 块已逐块写+读回 `cmp`。
- boot 分区：不动，仍是 `fix3r`（sha256 `fd3b4298…`，PMIC EINT16、bootspeed、/data fsck check）。

boot 前保险
- `/cache/z1_gpu_force` 标记：`z1_gpu_gate.sh` 的 forced 分支优先于一切 disable 标记
  （含 `/data/local/tmp/z1_gpu_disable`、`/data/z1_gpu_disable`、`persist.sys.z1.gpu.disabled`），
  保证这次启动 **不会** 被之前几轮（v25/v27/v28 那些挂掉的启动）写下的 marker 悄悄换成 SwiftShader。

## 判据（按优先级）

| # | 现象 | 命令/位置 | 通过标准 |
|---|---|---|---|
| 1 | 二屏出现 | 肉眼看屏 | 有 LineageOS 开机动画（GLES1 生效） |
| 2 | 没有 GNSS 卡死 | `grep -a "WATCHDOG KILLING" <log>` | 0 条（以前每轮 6 条 `GnssLocationProvider`） |
| 3 | GNSS 桩真的被加载 | `grep -a "GnssHAL\|gps.*Z1 GPS stub" <log>` | 无 `getService` 阻塞；`GnssLocationProvider` 起来 |
| 4 | gralloc 是新的 | `grep -a "z1gpu gate" /dev/kmsg` / `dumpsys SurfaceFlinger` | gate 日志 `mesa ENABLED`；GLES 行是 `Mesa, Mali400, OpenGL ES 2.0 Mesa 26.1.1` |
| 5 | lima 没报错 | `grep -a "gp error irq" <log>` | 0 条 |
| 6 | 能进桌面 | 肉眼看屏 | Trebuchet 桌面，不是 "Phone is starting…" |
| 7 | 应用不再 NPE | `grep -a "eglCreateImage\|NullPointerException" <log>` | 无 SystemUI/Launcher 的 EGLImage NPE |

## 失败时的处置

- **还是 "Phone is starting…"**：串口 shell 里 `pm clear com.android.launcher3` 再 `am start -a android.intent.action.MAIN -c android.intent.category.HOME`。
- **彩虹条又回来了**：说明 mesa 被加载但 PLBU 路径还是错 → 看 `gp error irq` 计数，回到 `GPU_ROOTCAUSE_*.md`。
- **变成 SwiftShader**：`grep -a "loaded .*libEGL" <log>` 看加载的是哪个 dso；大概率是 gate 被 marker 干掉（这次已用 force 标记规避）。
- **回滚**：system 分区刷回 `out/z1_v25_gpu26_fixed/system_v25.img`（上一版已知能起到 SFLinger），boot 不动。
