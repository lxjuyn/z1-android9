# Z1 A9 壁纸不显示 —— 根因与 launcher 替换方案

日期：2026-11-04
范围：`com.example.android.home`（Home Sample）为何不出壁纸、`ro.config.wallpaper` 在 Android 9 是否还有效、
可替换 launcher 的实物准备与部署步骤。

> 结论先行：**壁纸不显示的主因不是壁纸文件、也不是 `ro.config.wallpaper` 失效，而是唯一桌面的窗口
> 本身是"不透明 + `windowShowWallpaper=false`"，把壁纸整个盖住了。**
> 另外还有第二个独立问题：即便壁纸能显示，现有 `z1_wallpaper.png` 是**纯色 #1c2630 的 1 色图**，
> 看上去和"没有壁纸"完全一样。两个问题必须一起修。

---

## 1. `com.example.android.home` 不支持壁纸 —— 证据

### 1.1 实物

| 项 | 值 |
|---|---|
| 路径 | `/system/app/Home/Home.apk` |
| 大小 | 258,863 B |
| sha256 | `e289d2016e677ffd6e3fc9b948f2fd7df82221e79062974903e14e91d001644d` |
| 包名 / 版本 | `com.example.android.home` / versionCode 28, versionName "9" |
| label | `Home Sample` |
| minSdk / targetSdk | 28 / 28 |

### 1.2 它有"设壁纸"的能力，但没有"显示壁纸"的窗口

`aapt dump badging` 显示它申请了 `android.permission.SET_WALLPAPER`，并且有一个
`Wallpaper` Activity 响应 `android.intent.action.SET_WALLPAPER`（dex 里也能看到
`setWallpaper` / `clearWallpaper` / `peekWallpaper` / `setDefaultWallpaper` 等符号）。

**但这只是"能调用 WallpaperManager 去设置壁纸"，和"自己的窗口把壁纸透出来"是两件事。**

### 1.3 决定性证据：HOME Activity 的主题

`AndroidManifest.xml`（`aapt dump xmltree`）：

```
E: activity (line=38)
  A: android:theme=@0x7f060000            <- com.example.android.home:style/Theme
  A: android:name="Home"
  E: intent-filter
    E: action  android.intent.action.MAIN
    E: category android.intent.category.HOME
    E: category android.intent.category.DEFAULT
```

该主题解析：

```
resource 0x7f060000 com.example.android.home:style/Theme: <bag>
  Parent=0x01030005(Resolved=0x01030005), Count=1
  #0 (Key=0x01010056 = android:windowNoTitle): (boolean) true
```

父主题 `0x01030005` 就是 **`@android:style/Theme`**（从镜像里的 `framework-res.apk` 实测解析，
不是靠记忆）。这个 style 的 bag 有 327 个 item，其中：

```
#36  (Key=0x01010054 = android:windowBackground): (reference) 0x01080718
#117 (Key=0x01010292 = android:windowShowWallpaper): (boolean) false
```

- `0x01080718` = `@android:drawable/screen_background_selector_dark` —— **不透明**的深色背景。
- `windowShowWallpaper = false` —— WindowManager **根本不会把壁纸 surface 挂到这个窗口后面**。

对照组（同一份 framework-res.apk）：

```
resource 0x0103005e android:style/Theme.Wallpaper: <bag>
  Parent=0x01030005
  #0 (Key=0x01010054 windowBackground): (reference) 0x0106000d   = @android:color/transparent
  #1 (Key=0x01010292 windowShowWallpaper): (boolean) true
```

也就是说：**Home Sample 用的是"最普通的 Theme"，不是 `Theme.Wallpaper`，它的窗口是一张不透明的
深色底，壁纸被完全盖住。** 这是"进系统后背景壁纸不显示"的直接原因，与壁纸文件、属性、ImageWallpaper
都无关。

### 1.4 顺手排掉的另一种可能

镜像里 SystemUI 声明了 `com.android.systemui.ImageWallpaper`
（`android:permission="android.permission.BIND_WALLPAPER"`），`framework-res.apk` 里
`config_defaultWallpaperComponent = "com.android.systemui/com.android.systemui.ImageWallpaper"`，
`config_enableWallpaperService = true`。**壁纸服务本身是齐的**，只是被桌面窗口挡住。

---

## 2. `ro.config.wallpaper` 在 Android 9 里还有效吗 —— 有效，但不是你想的那条路

依据：AOSP 9 `android-9.0.0_r61`（GitHub `aosp-mirror/platform_frameworks_base` 镜像）实读源码。

| 位置 | 事实 |
|---|---|
| `core/java/android/app/WallpaperManager.java:98` | `private static final String PROP_WALLPAPER = "ro.config.wallpaper";` |
| 同文件 `:1838-1871` `openDefaultWallpaper()` | 读 `SystemProperties.get("ro.config.wallpaper")`；文件存在就用它，否则回落到 framework-res 的 `R.drawable.default_wallpaper` |
| 同文件 `:392-441` `peekWallpaperBitmap(..., returnDefault, ...)` | 当前没有用户壁纸时，**返回 `getDefaultWallpaper()`**，即上面的 `openDefaultWallpaper()` |
| 同文件 `:479-491` `getDefaultWallpaper()` | `openDefaultWallpaper()` + `BitmapFactory.decodeStream()` |
| 同文件 `:843-845` `getBitmapAsUser()` | 直接走 `peekWallpaperBitmap(mContext, true, ...)` |
| `packages/SystemUI/.../ImageWallpaper.java:369` | `mWallpaperManager.getBitmap(true /* hardware */)` —— 真正画壁纸的那条路 |
| `services/core/java/com/android/server/wallpaper/WallpaperManagerService.java` | **全文没有 `ro.config.wallpaper` 的引用**（grep 0 命中） |

**结论：属性在 Android 9 仍然生效，但只有一条路径能吃到它**：
`ImageWallpaper` → `getBitmap()` → `peekWallpaperBitmap(returnDefault=true)` → `openDefaultWallpaper()`。
`WallpaperManagerService` 自己**不读**这个属性。

因此"设 `ro.config.wallpaper` 这条路本身不对"这个怀疑**不成立**；它是对的，只是被桌面窗口挡住了。

### 2.1 但有两个会让它"看起来失效"的前提

1. **`/data` 里已经有用户壁纸时，默认壁纸被忽略。**
   `getCurrentWallpaperLocked()` 优先返回 `/data/system/users/0/wallpaper*`（`WallpaperManagerService.getWallpaper()`
   在 `!wallpaper.cropFile.exists()` 时才返回 null）。也就是说只要用户曾经设置过一次壁纸，
   改 `ro.config.wallpaper` 就不会有任何变化。
   上板核查：`ls -l /data/system/users/0/ | grep wallpaper`；要回落到默认壁纸就删掉
   `wallpaper` / `wallpaper_orig` / `wallpaper_info.xml`（或清 data）。
2. **硬件位图。** `getBitmap(true)` 要的是 `Config.HARDWARE` 位图。Z1 是 SwiftShader/软件 GL 环境，
   若硬件位图创建失败，`ImageWallpaper` 会 catch 住异常 → `clear()` → 重试一次仍失败 → 壁纸层为空。
   logcat 关键字：`Unable to load wallpaper!` / `Unable reset to default wallpaper!`。

### 2.2 壁纸文件本身的问题（独立 bug）

`/system/etc/z1_wallpaper.png`：675 B，240×320，8-bit RGB，非交错。
把 IDAT 解出来逐像素统计：**全图只有 1 种颜色 `#1c2630`**。

→ 即便换成支持壁纸的桌面、即便 ImageWallpaper 正常出图，用户看到的仍然是"一整块深色"，
和"壁纸没显示"在观感上无法区分。**必须同时换一张真图**（本次已生成，见 §4）。

`build.prop` 相关项（实测）：

```
ro.config.low_ram=true
ro.config.wallpaper=/system/etc/z1_wallpaper.png
ro.config.max_starting_bg=4
```

---

## 3. 候选 launcher 对比

全部实物已落地在 `out/z1_launcher_stage/`，且都用同一套方法验过
（解析 manifest → HOME activity → 主题 → 父链 → `windowShowWallpaper` / `windowBackground`）：

| # | APK | 包名 / 版本 | 大小 | sha256 | HOME Activity | 壁纸 | min/targetSdk | native lib | 内存评估 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `TrebuchetQuickStep.apk` | `com.android.launcher3` / 28 ("9") | 5,300,763 B | `98cab8c5377b8bb047ad5804a2e5f9a1c4c998dd1324b482398c10de361b9475` | `com.android.launcher3.lineage.LineageLauncher` | ✅ `BaseLauncherTheme`: `windowShowWallpaper=true`, `windowBackground=@android:color/transparent` | 28 / 28 | 无 | 中，50–80 MB PSS（估） |
| 2 | `Trebuchet_los14.apk` | `com.cyanogenmod.trebuchet` / 25 ("7.1.2") | 1,619,501 B | `fc6f140b2c8db69d2dc5cd472d3fc7ba75fd8ef28c3ea05e48608d6ae479defb` | `com.android.launcher3.Launcher` | ✅ `BaseWallpaperTheme`: `windowShowWallpaper=true`, 透明背景 | 22 / 23 | 无 | 低，30–45 MB PSS（估） |
| 3 | `KISS_3.26.0.apk` | `fr.neamar.kiss` / 224 ("3.26.0") | 2,205,788 B | `da6ab0b1219ad3b5ee573698523fb15d60a918083e0843f94a03c1b12e1098c9` | `fr.neamar.kiss.MainActivity` | ✅ `AppTheme`: `windowShowWallpaper=true`, 透明背景 | 21 / 36 | 无 | 低，20–35 MB PSS（估） |

来源：
- #1 从本机已有 `downloads_gsi/lineage-16.0-n7100.zip` 的 system 镜像里提取
  （`system.new.dat.br` → `sdat2img.py` → debugfs dump `/priv-app/TrebuchetQuickStep/TrebuchetQuickStep.apk`）。
  **与本机系统同为 LineageOS 16 / API 28 同期构建**，兼容性最好。
- #2 从本机 `los14/system/priv-app/Trebuchet/Trebuchet.apk`（LineageOS 14）提取，轻量兜底。
- #3 从 F-Droid 官方仓库下载（`https://f-droid.org/repo/fr.neamar.kiss_224.apk`）。

补充核验：
- #1 的 `LineageLauncher` 在 dex 里的父类是 `com.android.launcher3.Launcher`（**不是** `QuickstepLauncher`），
  所以它不依赖 SystemUI 的 QuickStep 绑定即可启动；镜像 SystemUI 里也确实有
  `OverviewProxyService` / `mQuickStepIntent`（QuickStep 有则更好，没有也不致命）。
- 三个 APK 都带合法签名（`META-INF/CERT.RSA`），都不是 odex 拆分包，直接放系统即可。

---

## 4. 已准备好的文件（`/home/lxj/claude/6572/out/z1_launcher_stage/`）

| 文件 | 说明 |
|---|---|
| `TrebuchetQuickStep.apk` | **首选**。LineageOS 16 Trebuchet，API 28，armv7，纯 Java |
| `Trebuchet_los14.apk` | 兜底 1：LOS14 Trebuchet，1.6 MB，极轻 |
| `KISS_3.26.0.apk` | 兜底 2：F-Droid KISS，2.2 MB，现代轻量 |
| `z1_wallpaper_240x320.png` | **新壁纸**，240×320 RGB，11,361 B，1,510 种颜色（渐变+光晕），sha256 `a1a26036dd1995d75783e068f7b28084f3c56ec7dc3bef56887f6aeac6a3e374` |
| `z1_wallpaper_current_flat.png` | 现镜像里那张纯色 `#1c2630`（留档对比） |
| `SHA256SUMS.txt` | 全部校验和 |
| `z1_apply_launcher_system.py` | **一键部署脚本**（复制镜像→写入→回读校验→可选删 Home/换壁纸） |
| `work/` | 取证中间产物：`Home.apk`、`framework-res.apk`、`SystemUI.apk`、`z1_wallpaper.png` |

部署脚本已在一份真实 ext4 system 镜像副本上跑通并 `e2fsck -fn` 验证干净：
写入后 `stat` 回读为 `Mode 0644 / User 0 / Group 0 / Size 5300763`，`dump` 回来的 sha256 与原文件一致。

---

## 5. 部署步骤（精确）

### 5.1 目标路径

```
/system/priv-app/Trebuchet/Trebuchet.apk     0644 root:root   (目录 0755)
```

**必须是 `priv-app`，不能是 `/app`**：Trebuchet 申请了 `MANAGE_ACTIVITY_STACKS` / `STATUS_BAR`，
这两个是 `signature|privileged`，只有放在 priv-app 才走白名单授予；放 `/app` 会被降权
（`MANAGE_ACTIVITY_STACKS` 拿不到时部分功能退化）。

### 5.2 权限白名单：**不用改**

镜像里 `/etc/permissions/privapp-permissions-lineage.xml` 已有：

```xml
<privapp-permissions package="com.android.launcher3">
    <permission name="android.permission.MANAGE_ACTIVITY_STACKS"/>
    <permission name="android.permission.STATUS_BAR"/>
</privapp-permissions>
```

AOSP 9 的 `data/etc/privapp-permissions-platform.xml` 另有：

```xml
<privapp-permissions package="com.android.launcher3">
    <permission name="android.permission.BIND_APPWIDGET"/>
    <permission name="android.permission.CONTROL_REMOTE_APP_TRANSITION_ANIMATIONS"/>
    <permission name="android.permission.GET_ACCOUNTS_PRIVILEGED"/>
</privapp-permissions>
```

Trebuchet 申请的 4 个特权权限（`BIND_APPWIDGET`、`CONTROL_REMOTE_APP_TRANSITION_ANIMATIONS`、
`MANAGE_ACTIVITY_STACKS`、`STATUS_BAR`）**全部已被覆盖**，不会触发 privapp 校验报错。
（这条很关键：Android 9 对 priv-app 特权权限是强校验，漏一条可能直接 bootloop。）

### 5.3 旧 Home 要不要删：**要删**

`com.example.android.home` 是镜像里唯一的 HOME。装上新桌面后就有两个 HOME activity，
首次开机（或 `ro.config.low_ram` 下未设默认）会弹"选择主屏幕应用"对话框，体验很差，甚至可能
落到 `com.android.settings/.FallbackHome`（纯黑，见 `out/DISPLAY_BLACK_20261103.md` 的历史实测）。

→ 删掉 `/system/app/Home/Home.apk`，让 Trebuchet 成为唯一 HOME。
（不建议只"disable"：离线改镜像时没法跑 `pm disable`，而 `pm` 状态存在 `/data`，
刷机后不一定保留。）

### 5.4 SELinux

镜像的 `/etc/selinux/plat_file_contexts` 实测有：

```
/system(/.*)?        u:object_r:system_file:s0
```

且 ramdisk 启动脚本会对新增文件做 `restorecon_recursive`（项目里
`z1_apply_wallpaper_system.py` 就是靠这条，没有在 host 侧猜标签）。因此
**`/system/priv-app/Trebuchet/Trebuchet.apk` 不需要 host 侧手工打标签**，开机即被标成
`system_file`。设备当前 SELinux permissive，即使标签落得晚也不会拦。

### 5.5 执行

```bash
cd /home/lxj/claude/6572

# 0) 先备份当前 system 镜像（项目规矩）
cp -a out/z1_v2_system/system_v2.img \
      bridge_backups/system_pre_launcher_$(date +%Y%m%d_%H%M%S).img

# 1) 出改好的镜像副本（不会原地改 base）
python3 out/z1_launcher_stage/z1_apply_launcher_system.py \
        out/z1_v2_system/system_v2.img \
        out/z1_launcher_system_v1

# 2) 刷入 system 分区（沿用现有刷写链，注意别在没插 download 线时跑）
#    <按项目当前 A9 system 刷写命令，把 system 指向 out/z1_launcher_system_v1/system.img>
```

脚本做的事（每步都回读校验）：
1. 复制 base 镜像 → `out/z1_launcher_system_v1/system.img`
2. `mkdir /priv-app/Trebuchet` + 写入 APK + `set_inode_field` 0644/0755 + dump 回读比对 sha256
3. 删 `/app/Home/Home.apk`（`--keep-home` 可跳过）
4. 替换 `/etc/z1_wallpaper.png` 为新图（`--wallpaper` 可跳过）

> 注：本次分析期间 `out/z1_v2_system/` 被其它 agent 移除/重建，脚本里的路径按实际镜像名替换即可。

---

## 6. 验收判据

上板后逐条核（全部通过才算修好）：

1. **HOME 解析对了**
   ```
   cmd package resolve-activity --brief -c android.intent.category.HOME -a android.intent.action.MAIN
   ```
   期望：`com.android.launcher3/com.android.launcher3.lineage.LineageLauncher`
   （若还是 `com.android.settings/.FallbackHome` → PackageManager 没扫到/包没装上，先查
   `pm list packages | grep launcher3`）

2. **窗口有焦点**
   ```
   dumpsys window | grep mCurrentFocus
   ```
   期望是 LineageLauncher，不是 null。

3. **壁纸层真的有 buffer**
   ```
   dumpsys wallpaper            # 期望 ImageWallpaper 已连接
   dumpsys SurfaceFlinger --list | grep -i wallpaper
   ```

4. **眼睛看到**：桌面图标后面是渐变壁纸，不是纯色块。

5. **没有加载失败**
   ```
   logcat -d | grep -iE "Unable to load wallpaper|Unable reset to default"
   ```
   应为空。若命中 → 硬件位图问题（见 §2.1 第 2 条），改用 `KISS_3.26.0.apk` 或先确认 GPU/EGL 状态。

6. **内存没被吃爆**
   ```
   dumpsys meminfo com.android.launcher3 | head -20
   free -m
   ```
   期望 launcher PSS ≤ 90 MB，`free` 仍 ≥ 120 MB。超标就换 `Trebuchet_los14.apk` 或 `KISS_3.26.0.apk`。

7. **首次开机别急**：新系统应用第一次启动会跑 dex2oat（5.3 MB dex，本机 CPU 约 10–30 s），
   期间桌面白屏/卡顿属正常，别误判成崩溃。

---

## 7. 内存风险评估（1 GB RAM + 软渲染）

- 当前基线：开机后 free ≈ 200 MB，`ro.config.low_ram=true`（`ActivityManager.isLowRamDevice()` 为真，
  Launcher3 会走轻量分支：缩小图标缓存、关闭部分动效）。
- `TrebuchetQuickStep`：5.3 MB APK / 3.95 MB dex，**无 native 库**，`config_largeHeap=false`
  （不会额外申请大堆）。240×320 @160dpi 的图标缓存很小，预估 PSS 50–80 MB。
  200 MB 余量下**能跑，但属于"紧"**。
- 若吃紧的降级顺序：
  1. `Trebuchet_los14.apk`（1.6 MB，Android 7 时代的 Launcher3，功能少、内存低）；
  2. `KISS_3.26.0.apk`（2.2 MB，搜索式桌面，20–35 MB）。
- 壁纸本身开销可忽略：240×320 RGB = 307 KB 解码后。

---

## 8. 风险与已知不确定

| 风险 | 影响 | 处置 |
|---|---|---|
| `/data` 已有用户壁纸 | 改 `ro.config.wallpaper` 无效 | 删 `/data/system/users/0/wallpaper*` + `wallpaper_info.xml`，或清 data |
| SwiftShader 下 `Config.HARDWARE` 位图创建失败 | ImageWallpaper 出不了图 | logcat 关键字见 §6.5；必要时改 launcher 自带壁纸（Trebuchet 支持自带默认壁纸） |
| 两个 HOME 共存 | 首启弹选择框/落 FallbackHome | 删 `Home.apk`（脚本默认行为） |
| 旧桌面包残留 `/data/data/com.example.android.home` | 无害 | 忽略 |
| QuickStep 绑定失败 | 仅最近任务动效异常，不影响桌面 | 非致命；已确认 HOME 走 `LineageLauncher`（非 QuickstepLauncher） |
| 内存偏紧 | 桌面被 lmkd 杀（项目已有 Settings 被 lmkd 杀的先例） | 换轻量候选，或加 lmkd 白名单 |

---

## 9. 回滚

- 系统镜像：刷回备份的 `bridge_backups/system_pre_launcher_<时间戳>.img`；
- 或只把 `Trebuchet.apk` 换成 `Trebuchet_los14.apk` / `KISS_3.26.0.apk`（同为 `priv-app` 放置，
  后者包名不同、不会冲突，但要把旧的删掉避免两个 HOME）。
