# Z1 launcher / wallpaper 备料（2026-11-04）

这是一组历史壁纸与 launcher 实物，不作为 2026-11-12 存储恢复候选。完整分析
见 [LAUNCHER_WALLPAPER_FIX_20261104.md](LAUNCHER_WALLPAPER_FIX_20261104.md)。

## 为什么需要换

- 当时镜像唯一的桌面 `com.example.android.home` 的 HOME Activity 使用
  `@android:style/Theme`，其中 `windowShowWallpaper=false`，加上不透明
  `windowBackground`，会盖住壁纸。
- 当时候选的 `z1_wallpaper.png` 是纯色 `#1c2630`，即使能显示也像没有壁纸。

## 文件

| 文件 | 用途 |
|---|---|
| `TrebuchetQuickStep.apk` | 首选桌面。`com.android.launcher3`，API 28，armv7，纯 Java，5.3 MB。 |
| `Trebuchet_los14.apk` | 轻量候选，1.6 MB。 |
| `KISS_3.26.0.apk` | F-Droid 候选，2.2 MB。 |
| `z1_wallpaper_240x320.png` | 240×320 渐变壁纸，11 KB。 |
| `z1_wallpaper_current_flat.png` | 当时的纯色壁纸，留档。 |
| `SHA256SUMS.txt` | 校验和。 |
| `z1_apply_launcher_system.py` | 部署脚本，复制镜像、写入并回读校验。 |
| `work/` | 取证中间产物，包括 Home.apk、framework-res.apk、SystemUI.apk。 |

## 部署

```sh
cd z1a9_gpt/docs
python3 z1_apply_launcher_system.py <base_system.img> <out_dir>
```

默认安装 Trebuchet 到 `/priv-app/Trebuchet/`、删除 `/app/Home/Home.apk` 并替换
壁纸。`--keep-home` 保留旧桌面；`--wallpaper ""` 不换壁纸。

部署后可检查 HOME Activity：

```sh
cmd package resolve-activity --brief \
  -c android.intent.category.HOME -a android.intent.action.MAIN
```

成功时应返回 `com.android.launcher3/...LineageLauncher`。当前设备恢复应先走
[恢复计划](Z1_RECOVERY_PLAN_20261112.md)，不要用这组 launcher 文件替代存储与
启动验收。
