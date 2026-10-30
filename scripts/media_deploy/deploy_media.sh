#!/bin/bash
# deploy_media.sh — 在 r5 系 system.img 之上叠加"媒体素材层"（可重放 / 幂等 / 非原地 / 带备份）
#
# 做什么（2026-10-30 媒体素材部署轮）:
#   把 4 组素材落进 system 镜像（镜像根即 /system，本镜像为 system-as-root 布局，
#   镜像内没有 /system 目录 —— 见 §0 证据）:
#     A. 音乐播放器 Eleven + RenderScript 支撑库   <-- 核心，听力机"能放音频"的最后一环
#     B. 录音机 Recorder + 音效面板 AudioFX        （A9 原生件，与镜像 privapp 白名单 1:1 对应）
#     C. PicoTts 语音引擎 + lang_pico 语音库       （仅英文；中文 PicoTts 无此能力，见报告）
#     D. media_profiles_V1_0.xml                   （消掉 Zygote 预载期告警；低价值）
#
# 用法:
#   sudo bash deploy_media.sh [in.img] [out.img] [--no-audio] [--no-tts] [--no-perf] [--no-rs]
#     in.img  缺省 = /home/lxj/claude/6572/out/z1_r5_system/system.img.r5k_final
#             （2026-10-31 起默认 r5k 谱系——设备现行 system 即 r5k 系，任务书钉死；旧默认 r5i 仍可显式传参）
#     out.img 缺省 = <in.img 去掉扩展名>.media.img；**绝不原地改输入**
#     --no-audio  跳过 A（播放器 + RS 库）
#     --no-tts    跳过 C
#     --no-perf   跳过 D
#     --no-rs     只装 Eleven.apk 不装 RS 库（**会 FC，仅用于对照实验**）
#
# 纪律（本项目固定规则，逐条落实）:
#   - 绝对路径；非原地；四件只读基线硬拒绝做输出。
#   - 大操作前 df 检查（本机曾多次因盘满 debugfs/编译静默失败）。
#   - 写完 mount ro 回读 sha256 + 标签断言；umount 后 e2fsck -fn。
#   - 不刷写、不碰设备。
#
# ── §0 为什么镜像内的路径没有 /system 前缀 ───────────────────────────────
#   $ debugfs -R "stat /system" <r5i>
#   /system: File not found by ext2_lookup
#   $ debugfs -R "stat /etc/init/audioserver.rc" <r5i>
#   Inode: 2485   Type: regular   Mode: 0644          <-- 存在
#   板载日志: "init: Parsing file /system/etc/init/audioserver.rc..."
#   ⇒ 内核把本分区挂到 /system，镜像根 == /system。
#     故"镜像内 /priv-app/X" 在设备上就是 "/system/priv-app/X"。
#
# ── §1 为什么必须带 RenderScript 支撑库（否则 Eleven 必 FC）─────────────
#   Eleven 的 androidx/support-v8 RenderScript 走 System.loadLibrary("rsjni")，
#   硬编码 /librsjni.so 与 /libRSSupport.so；而 r5i 里这两个文件**不存在**
#   （base LOS16 有、被 Z1 分支丢了）。ImageWorker.<init> 调
#   android.support.v8.renderscript.RenderScript.create() 时**方法 tries_size=0**
#   —— 整个方法没有任何 try 表，抛出的 UnsatisfiedLinkError（Error，非 Exception）
#   一路经 ImageFetcher.<init> → ImageFetcher.getInstance → BaseActivity.onCreate
#   全部无 catch，直落默认处理器 ⇒ 启动即 FC（HomeActivity 就是 launcher activity）。
#   证据链见 out/MEDIA_DEPLOY.md §播放器选型；本脚本默认装全 RS 栈。
#   ⚠ 只用 librsjni.so 去替换 librs_jni.so 是不够的：两者 SONAME 不同、注册的 Java
#     类不同（support 版注册 android/support/v8/renderscript/RenderScript，
#     平台版注册 android/renderscript/RenderScript），且 support 版会被 support
#     代码显式 System.load("/librsjni.so") —— 必须按原名并存。
#
# ── §2 SELinux 标签依据（不发明标签，逐条对齐 A9 真实策略）───────────────
#   规则源 = 本镜像实际使用的 ramdisk 内 plat_file_contexts / vendor_file_contexts
#   （从 out/z1_r5c_boot/boot.img 解出，脚本按 --verify-contexts 可选复核）:
#     plat_file_contexts:45   /system(/.*)?              → system_file
#     vendor_file_contexts    /(vendor|system/vendor)/etc 下非 hw 件 → vendor_configs_file
#   实测邻文件:
#     /lib/libRS.so                    = system_file
#     /app/Terminal                    = system_file
#     /priv-app/Settings/Settings.apk  = system_file
#     /vendor/etc/audio_policy_configuration.xml = vendor_configs_file
#   ⇒ 落在 /lib /app /priv-app 的全用 system_file；落在 /vendor/etc 的用
#     vendor_configs_file。本脚本按此表写 xattr，并逐项回读断言。
set -euo pipefail

ROOT=/home/lxj/claude/6572
STAGE=$ROOT/out/z1_media_stage/stage
SUMS=$ROOT/out/z1_media_stage/SHA256SUMS
DEF_IN=$ROOT/out/z1_r5_system/system.img.r5k_final

# 四件只读基线（2026-10-31 任务书钉死：r5k_final/r5i_final/r5c_boot/z1_ash_boot）
# + 历史只读件 r5h_final/r41 boot 一并保护。绝不允许作为输出路径（项目固定规则）
BASELINE=(
  $ROOT/out/z1_r5_system/system.img.r5k_final
  $ROOT/out/z1_r5_system/system.img.r5i_final
  $ROOT/out/z1_r5_system/system.img.r5h_final
  $ROOT/out/z1_r5c_boot/boot.img
  $ROOT/out/z1_ash_boot/boot.img
  $ROOT/out/z1_r41_final/boot.img
)

DO_AUDIO=1; DO_TTS=1; DO_PERF=1; DO_RS=1; VERIFY_CTX=0
IN=""; OUT=""
usage() { sed -n '2,20p' "$0" >&2; exit 2; }

for a in "$@"; do
  case "$a" in
    --no-audio) DO_AUDIO=0 ;;
    --no-tts)   DO_TTS=0 ;;
    --no-perf)  DO_PERF=0 ;;
    --no-rs)    DO_RS=0 ;;
    --verify-contexts) VERIFY_CTX=1 ;;
    -h|--help)  usage ;;
    *) if [ -z "$IN" ]; then IN=$a; elif [ -z "$OUT" ]; then OUT=$a; else echo "多余参数: $a" >&2; usage; fi ;;
  esac
done
[ "${IN:-}" ] || IN=$DEF_IN
[ "${OUT:-}" ] || OUT="${IN%.img}.media.img"

IN_R=$(realpath "$IN")
OUT_R=$(realpath -m "$OUT")
[ -f "$IN_R" ] || { echo "输入不存在: $IN" >&2; exit 2; }
[ "$IN_R" != "$OUT_R" ] || { echo "拒绝原地改: 输出必须不同于输入" >&2; exit 1; }
for b in "${BASELINE[@]}"; do
  [ "$OUT_R" != "$(realpath -m "$b")" ] || { echo "拒绝: 输出是只读基线 $b" >&2; exit 1; }
done

[ "$(id -u)" -eq 0 ] || { echo "需要 root（mount/setfattr/cp -a 保属主）。请: sudo bash $0 $*" >&2; exit 2; }

# ---- 素材自检（先验素材再动镜像）----
[ -f "$SUMS" ] || { echo "缺 $SUMS" >&2; exit 2; }
echo "== 阶段0: 校验素材树 sha256 =="
( cd "$STAGE" && sha256sum -c <(grep -v '^#' "$SUMS") ) || { echo "FATAL: 素材校验失败" >&2; exit 1; }
[ -L "$STAGE/priv-app/Eleven/lib/arm/librsjni.so" ] || { echo "FATAL: 缺 Eleven 的 librsjni.so 软链" >&2; exit 1; }

# ---- 磁盘检查（本项目多次因盘满静默失败）----
need=$(( $(stat -c %s "$IN_R") / 1024 + 307200 ))   # 输入大小 + 300M
avail=$(df -k "$(dirname "$OUT_R")" | awk 'NR==2{print $4}')
min_need=$(( need > 1228800 ? need : 1228800 ))     # 铁律: 可用 <1.2G 不动
if [ "$avail" -lt "$min_need" ]; then
  echo "FATAL: 磁盘不足: 可用 ${avail}K < 需要 ${min_need}K。只出脚本不出镜像，中止。" >&2
  exit 1
fi

MP=$(mktemp -d /tmp/z1media_mnt.XXXXXX)
cleanup() { mountpoint -q "$MP" && umount "$MP" || true; rmdir "$MP" 2>/dev/null || true; }
trap cleanup EXIT
mnt_rw() { mount -o loop,rw "$OUT_R" "$MP"; }
mnt_ro() { mount -o loop,ro "$OUT_R" "$MP"; }

echo "== 阶段1: 备份输入（带时间戳，改前备份铁律）=="
BK=$ROOT/bridge_backups
mkdir -p "$BK"
TS=$(date +%Y%m%d_%H%M%S)
# 输入本身不动；这里给输入做一份硬备份记录（只对 r5i 这类会反复当输入的件做一次）
BKC=$BK/$(basename "$IN_R").pre_media_$TS.bak
if [ ! -e "$BKC" ]; then
  ln "$IN_R" "$BKC" 2>/dev/null || cp -a "$IN_R" "$BKC"
  echo "  备份: $BKC ($(stat -c%s "$BKC") B)"
else
  echo "  备份已存在: $BKC"
fi

echo "== 阶段2: 复制出输出镜像（非原地）=="
rm -f "$OUT_R"
cp -a "$IN_R" "$OUT_R"
state=$(dumpe2fs -h "$OUT_R" 2>/dev/null | awk -F': *' '/^Filesystem state/{print $2}')
[ "$state" = "clean" ] || { echo "FATAL: 镜像 fs 状态非 clean ('$state')，中止（避免 journal 回放吞掉改动）" >&2; exit 1; }
echo "  输出: $OUT_R ($(stat -c%s "$OUT_R") B), fs=$state"

echo "== 阶段3: 落盘 =="
mnt_rw

# 幂等辅助: 目标已存在且 sha 相同则跳过（但标签仍会复检）
put() {  # put <相对stage路径> <镜像内绝对路径> <模式>
  # ⚠ 不能写成 local rel="$1" dst="$2" src="$STAGE/$rel" —— bash 5.2 下同一 local
  #   语句里引用本语句刚赋的变量会报 "p: 未绑定的变量"（实测，set -u 下直接炸）。
  #   一律一行一个 local。
  local rel="$1"
  local dst="$2"
  local mode="$3"
  local src="$STAGE/$rel"
  local tgt="$MP$dst"
  [ -f "$src" ] || { echo "  SKIP(素材缺失) $rel" >&2; return 0; }
  # 幂等快路径: 目标已存在且内容一致 → 不重写（省 21MB 写入，也让重复运行真正无副作用）
  if [ -f "$tgt" ]; then
    local ws gs
    ws=$(sha256sum "$src" | cut -d' ' -f1)
    gs=$(sha256sum "$tgt" | cut -d' ' -f1)
    if [ "$ws" = "$gs" ]; then echo "  = $dst 内容已一致，跳过"; return 0; fi
  fi
  install -D -m "$mode" -o 0 -g 0 "$src" "$tgt"
  echo "  + $dst  ($(stat -c%s "$src") B, $mode)"
}

# ---- A. 播放器 + RenderScript 支撑库 ----
if [ "$DO_AUDIO" -eq 1 ]; then
  echo "-- A. Eleven 播放器 + RS 支撑库 --"
  if [ "$DO_RS" -eq 1 ]; then
    put lib/librsjni.so     /lib/librsjni.so     0644
    put lib/libRSSupport.so /lib/libRSSupport.so 0644
    # libblasV8.so: ScriptIntrinsicBLAS 用；Blur 路径不需要，dlopen 失败是 log+continue。
    # 默认装上作为保险（986 KB），--no-blasc 可省。见报告 §未决风险。
    put lib/libblasV8.so    /lib/libblasV8.so    0644
  else
    echo "  (--no-rs: 不装 RS 库 —— Eleven 会 FC，仅对照用)"
  fi
  put priv-app/Eleven/Eleven.apk /priv-app/Eleven/Eleven.apk 0644
  # base 镜像里 Eleven 自带一个 app-local 软链: lib/arm/librsjni.so -> /system/lib/librsjni.so
  # 这是 base 自己验证过能工作的形态（System.loadLibrary("rsjni") 从 app 目录命中）
  install -d -m 0755 -o 0 -g 0 "$MP/priv-app/Eleven/lib/arm"
  ln -sfn /system/lib/librsjni.so "$MP/priv-app/Eleven/lib/arm/librsjni.so"
  # 软链不能打 xattr（lsetxattr 在 ext4 上对 fast symlink 无效，实测 setfattr 静默不生效）
  # —— base 镜像里该软链带 system_file 标签，但那是 mke2fs -d 建镜像时的产物；
  #    permissive 下无标签无害，故这里不强行补，只在报告中说明。
  echo "  + /priv-app/Eleven/lib/arm/librsjni.so -> /system/lib/librsjni.so (symlink; 不补 xattr)"
fi

# ---- B. 录音机 + 音效面板 ----
echo "-- B. Recorder + AudioFX --"
put priv-app/Recorder/Recorder.apk /priv-app/Recorder/Recorder.apk 0644
put priv-app/AudioFX/AudioFX.apk   /priv-app/AudioFX/AudioFX.apk   0644

# ---- C. TTS ----
if [ "$DO_TTS" -eq 1 ]; then
  echo "-- C. PicoTts（仅英文/西/法/德/意，无中文）--"
  put app/PicoTts/PicoTts.apk  /app/PicoTts/PicoTts.apk  0644
  put lib/libttspico.so        /lib/libttspico.so        0644
  put lib/libttscompat.so      /lib/libttscompat.so      0644
  install -d -m 0755 -o 0 -g 0 "$MP/tts/lang_pico"
  # 只装 en-US / en-GB（听力机目标语言=中文，英文语音库够做"英文跟读"；其余 4 语省 4.3MB）
  for v in en-US_lh0_sg.bin en-US_ta.bin en-GB_kh0_sg.bin en-GB_ta.bin; do
    put "tts/lang_pico/$v" "/tts/lang_pico/$v" 0644
  done
  # 2026-10-31: feature 声明（audio.output + software.tts）——r5k 的 /etc/permissions
  # 24 件里两者均未声明（grep 实测 0 命中）；不搬 handheld_core_hardware.xml 全集
  # （会连带声明 camera/compass 等不存在的 feature）。
  put etc/permissions/z1-media-features.xml /etc/permissions/z1-media-features.xml 0644
fi

# ---- D. media_profiles ----
if [ "$DO_PERF" -eq 1 ]; then
  echo "-- D. media_profiles_V1_0.xml --"
  put vendor/etc/media_profiles_V1_0.xml /vendor/etc/media_profiles_V1_0.xml 0644
fi

echo "== 阶段4: 补 SELinux xattr =="
# path<TAB>label<TAB>依据
LABELS=''
if [ "$DO_AUDIO" -eq 1 ]; then
  [ "$DO_RS" -eq 1 ] && LABELS="$LABELS
/lib/librsjni.so	u:object_r:system_file:s0	邻:/lib/libRS.so 实测; plat_fc:45 /system(/.*)?→system_file
/lib/libRSSupport.so	u:object_r:system_file:s0	同上
/lib/libblasV8.so	u:object_r:system_file:s0	同上"
  LABELS="$LABELS
/priv-app/Eleven	u:object_r:system_file:s0	邻:/priv-app/Settings 实测
/priv-app/Eleven/Eleven.apk	u:object_r:system_file:s0	邻:/priv-app/Settings/Settings.apk 实测
/priv-app/Eleven/lib	u:object_r:system_file:s0	同上
/priv-app/Eleven/lib/arm	u:object_r:system_file:s0	同上"
fi
LABELS="$LABELS
/priv-app/Recorder	u:object_r:system_file:s0	邻:/priv-app/Settings 实测
/priv-app/Recorder/Recorder.apk	u:object_r:system_file:s0	同上
/priv-app/AudioFX	u:object_r:system_file:s0	同上
/priv-app/AudioFX/AudioFX.apk	u:object_r:system_file:s0	同上"
if [ "$DO_TTS" -eq 1 ]; then
  LABELS="$LABELS
/app/PicoTts	u:object_r:system_file:s0	邻:/app/Terminal 实测
/app/PicoTts/PicoTts.apk	u:object_r:system_file:s0	同上
/lib/libttspico.so	u:object_r:system_file:s0	邻:/lib/libRS.so 实测
/lib/libttscompat.so	u:object_r:system_file:s0	同上
/tts	u:object_r:system_file:s0	plat_fc:45 /system(/.*)?（A9 无 tts_file 类型，sepolicy 实测 0 命中）
/tts/lang_pico	u:object_r:system_file:s0	同上
/tts/lang_pico/en-US_lh0_sg.bin	u:object_r:system_file:s0	同上
/tts/lang_pico/en-US_ta.bin	u:object_r:system_file:s0	同上
/tts/lang_pico/en-GB_kh0_sg.bin	u:object_r:system_file:s0	同上
/tts/lang_pico/en-GB_ta.bin	u:object_r:system_file:s0	同上
/etc/permissions/z1-media-features.xml	u:object_r:system_file:s0	邻:/etc/permissions/platform.xml 实测（r5k 2026-10-31）"
fi
[ "$DO_PERF" -eq 1 ] && LABELS="$LABELS
/vendor/etc/media_profiles_V1_0.xml	u:object_r:vendor_configs_file:s0	邻:/vendor/etc/media_codecs.xml 实测"

label_of() { getfattr --only-values -n security.selinux -h "$1" 2>/dev/null | tr -d '\0' || true; }
n=0
# 本层全部路径只落两种标签：system_file（/lib /app /priv-app /tts）与
# vendor_configs_file（/vendor/etc）。按目标标签分组，避免管道子 shell 丢计数。
mapfile -t SYSTEM_PATHS < <(printf '%s\n' "$LABELS" | grep -v '^$' | awk -F'\t' '$2=="u:object_r:system_file:s0"{print $1}')
mapfile -t VENDOR_PATHS < <(printf '%s\n' "$LABELS" | grep -v '^$' | awk -F'\t' '$2=="u:object_r:vendor_configs_file:s0"{print $1}')
LABEL_PATHS=("${SYSTEM_PATHS[@]}" "${VENDOR_PATHS[@]}")
LBL_SYS='u:object_r:system_file:s0'
LBL_VEN='u:object_r:vendor_configs_file:s0'

setlabel() {  # setlabel <镜像内路径> <标签>
  local p="$1"
  local want="$2"
  local f="$MP$p"
  if [ ! -e "$f" ] && [ ! -L "$f" ]; then echo "  缺路径(跳过): $p"; return 0; fi
  if [ -L "$f" ]; then echo "  (软链不打 xattr): $p"; return 0; fi
  local cur
  cur=$(label_of "$f")
  if [ "$cur" = "$want" ]; then echo "  = $p 已是 $want"; return 0; fi
  setfattr -n security.selinux -v "$want" "$f"
  local got
  got=$(label_of "$f")
  [ "$got" = "$want" ] || { echo "FATAL: $p 回读不符 '$got' != '$want'" >&2; umount "$MP"; exit 1; }
  echo "  L $p = $want"
  n=$((n+1))
}

for p in "${SYSTEM_PATHS[@]}"; do setlabel "$p" "$LBL_SYS"; done
for p in "${VENDOR_PATHS[@]}"; do setlabel "$p" "$LBL_VEN"; done
echo "  写入标签 $n 条"
sync; umount "$MP"

echo "== 阶段5: umount 后 ro 复挂回读（防静默失败）=="
mnt_ro
bad=0
checklabel() {  # checklabel <镜像内路径> <标签>
  local p="$1"
  local want="$2"
  local f="$MP$p"
  if [ ! -e "$f" ] && [ ! -L "$f" ]; then echo "  回读缺失: $p" >&2; bad=$((bad+1)); return; fi
  [ -L "$f" ] && return            # 软链不打 xattr，跳过
  local cur
  cur=$(label_of "$f")
  [ "$cur" = "$want" ] || { echo "  标签不符: $p '$cur' != '$want'" >&2; bad=$((bad+1)); }
}
for p in "${SYSTEM_PATHS[@]}"; do checklabel "$p" "$LBL_SYS"; done
for p in "${VENDOR_PATHS[@]}"; do checklabel "$p" "$LBL_VEN"; done

echo "-- 内容回读（sha256 逐项断言）--"
# ⚠ 一行一个 local：bash 5.2 下 local p="$1" f="$MP$p" 会留 f 未绑定（set -u 直接炸）
expect() {  # expect <镜像内路径> <期望sha> <说明>
  local p="$1"
  local want="$2"
  local desc="$3"
  local f="$MP$p"
  if [ ! -f "$f" ]; then echo "  MISSING $p" >&2; bad=$((bad+1)); return; fi
  local got
  got=$(sha256sum "$f" | cut -d' ' -f1)
  if [ "$got" = "$want" ]; then echo "  OK $desc $p"; else echo "  SHA-MISMATCH $p got=$got want=$want" >&2; bad=$((bad+1)); fi
}
if [ "$DO_AUDIO" -eq 1 ]; then
  [ "$DO_RS" -eq 1 ] && {
    expect /lib/librsjni.so     94d7232e1d973eac103572d60fdd92098747c7b9e4d9e154c0f939339039c4ca "RS"
    expect /lib/libRSSupport.so 784d928ee79a76a7e6df70dc91f4667d3b501f4fa0f0581d19e46a49f1c35606 "RS"
    expect /lib/libblasV8.so    309dbb4c22acfe2c837cb1b5de846b6a9bccb7e5a6d45e91eb07eeb69a75da11 "RS"
  }
  expect /priv-app/Eleven/Eleven.apk 6baeab026b8ba28ec6cbc9fff8cbe6b95322a2f0522ec1efaa487923684bd50f "播放器"
  # 软链解析断言
  if [ "$(readlink "$MP/priv-app/Eleven/lib/arm/librsjni.so" 2>/dev/null)" = "/system/lib/librsjni.so" ]; then
    echo "  OK symlink /priv-app/Eleven/lib/arm/librsjni.so -> /system/lib/librsjni.so"
  else
    echo "  BAD symlink" >&2; bad=$((bad+1))
  fi
fi
expect /priv-app/Recorder/Recorder.apk daef2594e895d8f9352cdc321f0908f89151b5a3c5727a3ccfe70104195d2568 "录音机"
expect /priv-app/AudioFX/AudioFX.apk   3db18ac6edcb203fe1835963c449098bf727001ee59a8d488655fbddd97cc4f8 "音效"
if [ "$DO_TTS" -eq 1 ]; then
  expect /app/PicoTts/PicoTts.apk 292b42b38cfb5ad785a84e982ad90d02b90c586b707630708ddaf04394f06e01 "TTS"
  expect /lib/libttspico.so       a60081a3d4166e491999823ed4bfcd41d34a7764ed3afbfd199d9933390855ba "TTS"
  expect /lib/libttscompat.so     3c4fef35fffeab87217178af030c387a3029e1b4b68b9949ba3b08fa487e6788 "TTS"
  expect /tts/lang_pico/en-US_lh0_sg.bin d6946526696d9eb085f7b23b1fbdf7a9986caca658b645e9eb73c6514fd4789d "TTS语音"
  expect /etc/permissions/z1-media-features.xml 60ecd69dd3669bd59d21da8e42f392b62b7a0f4055ed982392c08653cda9c3aa "TTS feature"
fi
[ "$DO_PERF" -eq 1 ] && expect /vendor/etc/media_profiles_V1_0.xml f698f4c4bbdcf34e6503c69b3b49d74f67fdb2b73d3fb799e91b10105c80657c "media_profiles"

# 权限/属主断言
echo "-- 权限断言 --"
# 属主/权限必须在 ro 挂载还开着的时候查（此处仍在 mnt_ro 之后、umount 之前）
chkperm() {  # chkperm <镜像内路径>
  local p="$1"
  local f="$MP$p"
  [ -e "$f" ] || return 0
  [ -L "$f" ] && return 0
  local m
  m=$(stat -c '%a %u %g' "$f")
  case "$m" in
    "644 0 0"|"755 0 0") ;;
    *) echo "  权限异常: $p = $m" >&2; bad=$((bad+1)) ;;
  esac
}
for p in "${LABEL_PATHS[@]}"; do chkperm "$p"; done
umount "$MP"

echo "== 阶段6: e2fsck + 汇总 =="
e2fsck -fn "$OUT_R" >/dev/null || { echo "FATAL: e2fsck 未通过" >&2; exit 1; }
echo "  e2fsck clean"
if [ "$bad" -ne 0 ]; then echo "FATAL: $bad 项回读失败" >&2; exit 1; fi
OUT_SHA=$(sha256sum "$OUT_R" | cut -d' ' -f1)
OUT_SZ=$(stat -c%s "$OUT_R")
echo
echo "==== 完成 ===="
echo "输出: $OUT_R"
echo "大小: $OUT_SZ B"
echo "sha256: $OUT_SHA"
echo "备份: $BKC"
echo
echo "刷写（**必须 Z1_VERIFY=0**，verify 误报会回滚掉成功镜像）:"
echo "  cd $ROOT"
echo "  Z1_VERIFY=0 tools/flash_with_retry.sh android $OUT_R $ROOT/out/z1_r5_system/system.img.r5h_final 8"
echo
echo "上板验收见 out/MEDIA_DEPLOY.md §上板验证步骤"
