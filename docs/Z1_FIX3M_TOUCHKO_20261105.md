# fix3m：修掉"源码改了但没进镜像"的触摸驱动 — 2026-11-05

## 1. 发现的 bug

`fix3j`/`fix3k` 两个 boot.img 的 ramdisk 里那个 `/z1_cst836u.ko`
**不是当前源码编出来的**：它缺 `INPUT_PROP_DIRECT`。

### 证据（反汇编，两条都是同一份源码树的产物对比）

从 `out/z1_fix3k_boot/boot.img` 取出 rootfs → gunzip → cpio → `z1_cst836u.ko`
（md5 `e9439f4e0050c7b5f946af9dc8c63f6c`）与仓库里的
`z1a9_gpt/drivers/z1_cst836u/z1_cst836u.ko`
（md5 `3aa31907d7eb7b31b5b7cfa63d742db0`，2026-11-04 23:31）对比：

```
仓库版（有修复）                         ramdisk 版（缺修复）
 414: ldr  r2, [r5, #4]                 414: ldr  r0, [r5, #4]
 418: ldr  r3, [r2, #20]                418: bl   input_register_device
 41c: orr  r3, r3, #2                          ^^^^^ 直接注册
 420: str  r3, [r2, #20]
 424: ldr  r0, [r5, #4]
 428: bl   input_register_device
```

`[r2, #20]` 就是 `struct input_dev.propbit`（name/phys/uniq 各 4 字节 + input_id 8 字节 = 20），
`orr #2` 置的是 bit 1 = `INPUT_PROP_DIRECT`(=1)。源码位置
`z1a9_gpt/drivers/z1_cst836u/z1_cst836u.c:256`。

### 后果

没有 `INPUT_PROP_DIRECT`，Android `InputReader` 不会把这个设备判成
**直接触摸屏**，而是当普通（相对/指针类）输入设备：
指针图标、坐标/边界换算、手势（点击判定、边缘滑动）全按指针设备走，
触摸屏该有的行为都不对。这正是"触摸→Android 端到端"一直没验成的原因之一。

### 为什么会漏

`fix3j`/`fix3k` 是用 `tools/z1_pack_mtk_bootimg.py --rootfs <上一版的 rootfs>` 打的，
ramdisk 是**从上一次镜像整块沿用**的，没有从 `rootdir/` 重新构建。
源码 11-04 修好后没人重打 ramdisk，于是修复永远停在仓库里。

> 教训（要写进规矩）：**改了会进 ramdisk 的东西（`.ko`/脚本/rc/prop），必须重打 ramdisk，
> 不能沿用旧 rootfs。** 打包后要 dump 出来反向核对关键文件 md5，不能只看"打包成功"。

## 2. fix3m 做了什么

| 项 | 值 |
|---|---|
| staging | 从 `fix3k` 的 rootfs 解出 77 个文件（cpio） |
| 替换 | `z1_cst836u.ko` → 仓库版 `3aa31907…`（`z1_gpio_hunt.ko` 本来就一致，未动） |
| 重打 | `tools/z1_repack_a9_ramdisk.sh` → body 1,981,410 B = `0x1e3be2` |
| DTS | `linux,initrd-end` 0x842e416c → **0x842e3be2**（必须同步，否则 initrd 截断） |
| 打包 | `tools/z1_pack_mtk_bootimg.py`（template=fix3k） |
| 产物 | `out/z1_fix3m_boot/boot.img`，6,221,824 B ≤ 6 MiB |

### 校验（都做了，不是"打包成功"就算）

- Android 头：`kernel_size 4236503 = 4235991 + 512` ✅、`ramdisk_size 1981922 = 1981410 + 512` ✅
- 从**打好的 boot.img** 里反解 rootfs → gunzip → cpio → `z1_cst836u.ko`
  md5 = `3aa31907d7eb7b31b5b7cfa63d742db0` ✅（和期望一致）
- 从**打好的 boot.img** 里取出 appended DT → `dtc` 反编译 →
  `linux,initrd-start = 0x84100000`、`linux,initrd-end = 0x842e3be2` ✅
- dtb 在 kernel payload 里的偏移 = 4,215,296 = zImage 长度，即 dtb 紧接在 zImage 之后 ✅
- 归档 `bridge_backups/products/products_fix3m_*/`（boot.img/dtb/rootfs/zImage/SHA256SUMS）

`boot.img` sha256 = `2412e008f40bb64fe5a47f888c1fc2362350f82b456b7006462e6699e0681d07`

## 3. 关于 initrd-end 的 512 字节口径（顺手澄清，工具注释有歧义）

- `tools/z1_repack_a9_ramdisk.sh` 用的是 `0x84100000 + gzip_len`（**不含** 512B MTK 头）。
- `tools/z1_pack_mtk_bootimg.py:102` 打印的提示是 `0x84100000 + (512 + gzip_len)`（**含**）。
- 两者差 512。历史上实际出厂的 fix3j/fix3k 的 dts 用的是**不含**的那个
  （0x842e406c / 0x842e416c 分别 = 0x84100000 + 1,982,060 / 1,982,828），
  并且这些镜像有成功 unpack 的记录
  （`z1_boot_mainline_20261105_183443.log`：35 次 `Trying to unpack`，只有 8 次
  `invalid magic`，且那 8 次是低电（VBAT 3034 mV）时 LK 读 eMMC 失败所致）。
- 所以**以 `start + gzip_len` 为准**；packer 的 `+512` 只是提示，多读 512 字节落在
  gzip 流之后，gunzip 会在 EOS 处停下（最多打一条 junk 警告），不影响启动。
  但**少 512 会截断**，所以宁可按精确值走，且换 ramdisk 必须重算。

## 4. 待上板验证

刷 `out/z1_fix3m_boot/boot.img` 后，在 logcat/`getevent -lp` 里应看到该设备带
`INPUT_PROP_DIRECT`（`dumpsys input` 里 `Classes: KEYBOARD|TOUCH` /
`Sources: TOUCHSCREEN` 而不只是 `MOUSE`/`POINTER`），并且触摸不再出现指针。

---

# 重要更正（2026-11-08）：那次修复**没有真正生效**

我在 11-05 只把修好的 `z1_cst836u.ko` 放进了 **ramdisk**（`/z1_cst836u.ko`），
并据此宣称"触摸已修"。

但镜像里的 `/bin/z1_touch_load.sh`（由 `z1_speedup.rc` 的 `z1_touch_load` service 执行）写的是：

```sh
err=$(insmod /system/lib/modules/z1_cst836u.ko 2>&1)
```

**它加载的是 system 镜像里那一份，不是 ramdisk 里那份。**
而 `system_v17.img:/lib/modules/z1_cst836u.ko` 的 md5 仍是 `e9439f4e…`（旧构建，
缺 `INPUT_PROP_DIRECT` 那三条指令）。

⇒ 11-05 那次"触摸修复"实际上**完全没作用**；ramdisk 里那份只是被 `z1_gpio_hunt`
那条链路间接用到（ramdisk 的 `/z1_touch_load.sh` 只 insmod `/z1_gpio_hunt.ko`）。

## 更正后的修法
`out/z1_v20_audio_system/system_v20.img` 已经把 `/lib/modules/z1_cst836u.ko` 换成
树内构建（md5 `3aa31907…`，已从镜像反解核对），并且同时换掉了 5 个旧构建的音频模块。
整镜像 sha256 `3e57dd0c352a7f85a168d71c5f88912405020aaffc481192e65dfa8a5e91ef7f`。

## 教训（写进规矩）
**"模块修好了"必须验证"运行时到底加载的是哪一份"** —— 同一模块可能在 ramdisk 和
system 镜像里各有一份，加载脚本用的是哪条路径要以脚本内容为准，不能假设。
