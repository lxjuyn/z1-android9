# Z1 launcher / wallpaper 备料（2026-11-04）

完整分析见 `../LAUNCHER_WALLPAPER_FIX_20261104.md`。这里只放可直接用的实物。

## 为什么需要换
- 现镜像唯一的桌面 `com.example.android.home` 的 HOME Activity 用 `@android:style/Theme`：
  `windowShowWallpaper=false` + 不透明 `windowBackground` → 壁纸被窗口盖死。
- 另外现 `z1_wallpaper.png` 是**纯色 #1c2630**（全图 1 种颜色），就算能显示也像"没壁纸"。

## 文件
| 文件 | 用途 |
|---|---|
| `TrebuchetQuickStep.apk` | 首选桌面。`com.android.launcher3`，API 28，armv7，纯 Java，5.3 MB |
| `Trebuchet_los14.apk` | 兜底 1（轻量，1.6 MB） |
| `KISS_3.26.0.apk` | 兜底 2（F-Droid，2.2 MB） |
| `z1_wallpaper_240x320.png` | 新壁纸（240×320 渐变，11 KB） |
| `z1_wallpaper_current_flat.png` | 现镜像里那张纯色图（留档） |
| `SHA256SUMS.txt` | 校验和 |
| `z1_apply_launcher_system.py` | 部署脚本（复制镜像→写入→回读校验） |
| `work/` | 取证中间产物（Home.apk / framework-res.apk / SystemUI.apk） |

## 一条命令部署
```bash
python3 z1_apply_launcher_system.py <base_system.img> <out_dir>
```
默认：装 Trebuchet 到 `/priv-app/Trebuchet/`、删 `/app/Home/Home.apk`、换壁纸。
`--keep-home` 保留旧桌面，`--wallpaper ""` 不换壁纸。

部署后验收：`cmd package resolve-activity --brief -c android.intent.category.HOME -a android.intent.action.MAIN`
应返回 `com.android.launcher3/...LineageLauncher`。
