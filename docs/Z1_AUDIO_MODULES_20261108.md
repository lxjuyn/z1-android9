# 镜像里的音频模块是旧构建（不含 MIC 采集路径）— 2026-11-08

## 发现

`out/z1_v17_system/system_v17.img` 的 `/lib/modules` 里有五个音频相关模块，
和 `fork_linux` 树内同名构建**全部不同**（md5 不同），而且差别是**代码级**的：

| 模块 | 镜像 md5 前缀 | 树内 md5 前缀 | "MIC 相关字符串" 镜像 / 树内 |
|---|---|---|---|
| `snd-soc-mt6323.ko` | `efebd890` | `3646d6c1` | **0 / 6** |
| `mt6572-afe-pcm.ko` | `6c63097c` | `67b52bff` | **0 / 2** |
| `mt6572-mt6323.ko` | `4a569ee0` | `3b676c0e` | — |
| `mt6323-accdet.ko` | `b698f44d` | `75599620` | — |
| `snd-soc-simple-amplifier.ko` | `5a14f823` | `10e820d3` | — |

统计口径：`strings -a` 里 `MIC Input|MIC UL|MIC1|MIC2|AIF1 Capture`（codec）与
`AWB Source|MIC Uplink|MIC`（AFE）。镜像版一个都没有，树内版有。

也就是说**镜像里的音频栈根本不包含采集（MIC/上行）路径**，录音不可能工作 ——
这与另一个审计 agent 独立得出的"v15 音频 .ko 是旧构建"是同一件事。

五个模块的 vermagic 都是 `7.0.0-rc7-z1+`，与现役内核匹配。

## 已做

`out/z1_v20_audio_system/system_v20.img`
= v17 + 这五个模块换成树内新构建（标签 `system_file`，mode 0664，e2fsck 干净）。
重放脚本 `tools/z1_build_v20_system.sh`。

整镜像 sha256 = `cf2f941941dc97039c884bd6c91b378b39b4673b0179bd78a9f15422496aff0e`。

## NOT VERIFIED

- 没上板。换模块后能否真的录音/放音，只有实测知道。
- 这是与触摸 `.ko` 完全同一类问题（源码修了、镜像里还是旧的），说明**构建流程里
  "模块没同步进镜像"是个系统性缺陷**。后面每次改内核/模块，都要用
  `z1_make_system_image.sh` 重新灌进镜像并从镜像里反解核对。
