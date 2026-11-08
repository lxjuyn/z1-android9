#!/usr/bin/env bash
# z1_make_system_image.sh — 从现有 system.img 派生新镜像, 只替换指定的文件
#
# 为什么不用 mount: 本机磁盘紧张、mount 需要 root、且循环挂载后忘卸载会把镜像弄脏。
# debugfs 只需 -w 就能 rm/write, 文件系统保持干净。
#
# 用法:
#   tools/z1_make_system_image.sh <src.img> <dst.img> <spec>...
#   <spec> = <镜像内绝对路径>:<本地文件>:<八进制mode>[:uid:gid]
#   例:  /lib/modules/z1_charger.ko:/path/z1_charger.ko:0100664
#
# 行为: 1) 复制 src->dst  2) 每个 spec: rm 旧文件 -> write 新文件 -> sif mode/uid/gid
#       3) 从 dst 里 dump 回来做 md5 校验(不只看"命令成功")
set -euo pipefail

SRC=${1:?src.img}; DST=${2:?dst.img}; shift 2
[ "$#" -ge 1 ] || { echo "ERR: 至少一个 <path>:<file>:<mode> spec" >&2; exit 1; }
[ -f "$SRC" ] || { echo "ERR: $SRC 不存在" >&2; exit 1; }

echo "=== 复制 $SRC -> $DST ==="
cp --sparse=always "$SRC" "$DST"
chmod u+w "$DST"

FAIL=0
STAMP=${Z1_IMAGE_STAMP:-1780000000}
for spec in "$@"; do
  IFS=':' read -r inpath local mode uid gid <<<"$spec"
  uid=${uid:-0}; gid=${gid:-0}
  [ -f "$local" ] || { echo "ERR: 本地文件 $local 不存在" >&2; exit 1; }

  echo "--- 替换 $inpath  <- $local (mode $mode, $uid:$gid) ---"
  # debugfs 的 write 不覆盖已存在文件, rm 也不报错, 所以先 rm 再 write。
  debugfs -w -R "rm $inpath" "$DST" >/dev/null 2>&1 || true
  debugfs -w -R "write $local $inpath" "$DST" >/dev/null
  debugfs -w -R "sif $inpath mode $mode" "$DST" >/dev/null
  debugfs -w -R "sif $inpath uid $uid" "$DST" >/dev/null
  debugfs -w -R "sif $inpath gid $gid" "$DST" >/dev/null
  # debugfs write stamps the file with the current time, which makes the
  # output image differ on every run.  Pin all three times so the same
  # inputs always produce a byte-identical image (and a stable sha256).
  for t in mtime atime ctime; do
    debugfs -w -R "sif $inpath $t $STAMP" "$DST" >/dev/null
  done

  # SELinux 标签: debugfs 的 write 只会带过来文件内容, **不会带 security.selinux**,
  # 换过的文件会变成 unlabeled —— 上板实测 apps 加载 /system/lib/hw/gralloc.z1.so 时
  # 报 tcontext=u:object_r:unlabeled:s0 的 read/open/getattr denial。
  # 规则: /lib/hw/*.so 与 /vendor/lib/hw/*.so -> same_process_hal_file
  #       /vendor/** -> vendor_file, 其余 -> system_file
  case "$inpath" in
    /lib/hw/*.so|/vendor/lib/hw/*.so|/vendor/lib64/hw/*.so) selabel=u:object_r:same_process_hal_file:s0 ;;
    /vendor/*)                                             selabel=u:object_r:vendor_file:s0 ;;
    *)                                                     selabel=u:object_r:system_file:s0 ;;
  esac
  debugfs -w -R "ea_rm $inpath security.selinux" "$DST" >/dev/null 2>&1 || true
  debugfs -w -R "ea_set $inpath security.selinux $selabel" "$DST" >/dev/null 2>&1 || true
  gotlabel=$(debugfs -R "ea_list $inpath" "$DST" 2>/dev/null | grep -o 'u:object_r:[^"\]*' | head -1)
  if [ "$gotlabel" = "$selabel" ]; then
    echo "  SELinux 标签 OK: $selabel"
  else
    echo "  WARN: SELinux 标签设置失败 (got='${gotlabel:-none}' want=$selabel)"; FAIL=1
  fi

  # 读回校验: 唯一可靠的方式是 dump 出来比 hash。
  tmp=$(mktemp /tmp/z1sysimg.XXXXXX)
  rm -f "$tmp"
  debugfs -R "dump $inpath $tmp" "$DST" >/dev/null 2>&1
  if [ ! -f "$tmp" ]; then
    echo "  FAIL: 读回失败 $inpath"; FAIL=1; continue
  fi
  a=$(sha256sum "$local" | cut -d' ' -f1)
  b=$(sha256sum "$tmp" | cut -d' ' -f1)
  if [ "$a" = "$b" ]; then
    echo "  读回 OK  sha256=$b"
  else
    echo "  FAIL: 读回不一致 local=$a image=$b"; FAIL=1
  fi
  debugfs -R "stat $inpath" "$DST" 2>/dev/null | grep -E "Mode:|Size:" | sed 's/^/  /'
  rm -f "$tmp"
done

echo "=== 结果: $([ "$FAIL" = 0 ] && echo OK || echo FAILED) ==="
echo "OUT_SHA256=$(sha256sum "$DST" | cut -d' ' -f1)"
echo "OUT_SIZE=$(stat -c%s "$DST")"
exit "$FAIL"
