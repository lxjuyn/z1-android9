#!/usr/bin/env python3
"""Turn a z1_diag bundle into a verdict sheet.

Reads a bundle produced on the device by scripts/diag/z1_diag_all.sh
(a directory or the collect_*.tar.gz itself) and prints one row per
subsystem: what the evidence says, and what to look at next.

The rules here are the same tables documented in scripts/diag/README.md,
just executable so nobody has to eyeball facts.txt against a table.

Usage:
    ./z1_diag_summarize.py collect_20261027_120000.tar.gz
    ./z1_diag_summarize.py /path/to/collect_20261027_120000/
    ./z1_diag_summarize.py bundle.tar.gz --verbose   # + matched log lines
    ./z1_diag_summarize.py bundle.tar.gz --json      # machine readable

Exit status: 0 if no FAIL row, 1 if at least one FAIL, 2 on unreadable input.
Read-only: it never writes anything.
"""
import argparse
import json
import os
import re
import sys
import tarfile

# --------------------------------------------------------------------------
# bundle access (directory or tar.gz)
# --------------------------------------------------------------------------


class Bundle:
    def __init__(self, path):
        self.path = path
        self._tar = None
        self._dir = None
        if os.path.isdir(path):
            # accept both the parent dir and the collect_* dir itself
            if os.path.exists(os.path.join(path, "facts.txt")):
                self._dir = path
            else:
                for name in sorted(os.listdir(path)):
                    cand = os.path.join(path, name)
                    if os.path.isdir(cand) and os.path.exists(
                            os.path.join(cand, "facts.txt")):
                        self._dir = cand
                        break
            if self._dir is None:
                raise SystemExit("no facts.txt found under %s" % path)
        else:
            self._tar = tarfile.open(path, "r:*")
            self._members = {m.name: m for m in self._tar.getmembers()}

    def names(self):
        if self._dir:
            return sorted(os.listdir(self._dir))
        return sorted(m.rsplit("/", 1)[-1] for m in self._members)

    def text(self, name, limit=None):
        """Return the content of bundle file `name` (matched by suffix)."""
        try:
            if self._dir:
                p = os.path.join(self._dir, name)
                if not os.path.exists(p):
                    return ""
                with open(p, "r", errors="replace") as fh:
                    return fh.read(limit) if limit else fh.read()
            for m, raw in self._members.items():
                if raw.isfile() and m.rsplit("/", 1)[-1] == name:
                    fh = self._tar.extractfile(m)
                    if fh is None:
                        return ""
                    data = fh.read(limit) if limit else fh.read()
                    return data.decode("utf-8", "replace")
        except (OSError, tarfile.TarError):
            return ""
        return ""

    def facts(self):
        out = {}
        for line in self.text("facts.txt").splitlines():
            if "=" not in line or line.startswith("#"):
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
        return out


# --------------------------------------------------------------------------
# verdict helpers
# --------------------------------------------------------------------------

OK, WARN, FAIL, INFO = "OK", "WARN", "FAIL", "INFO"


def as_int(v, default=None):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def yes(v):
    return str(v).strip().lower() in ("yes", "1", "true")


class Sheet:
    def __init__(self):
        self.rows = []

    def add(self, sub, status, finding, hint=""):
        self.rows.append({"sub": sub, "status": status,
                          "finding": finding, "hint": hint})

    def worst(self):
        order = {FAIL: 0, WARN: 1, INFO: 2, OK: 3}
        return sorted(self.rows, key=lambda r: order.get(r["status"], 9))


# --------------------------------------------------------------------------
# rules
# --------------------------------------------------------------------------


def check_wifi(f, sheet, grep):
    sub = "WiFi"
    if not yes(f.get("DEV_WMTWIFI")):
        sheet.add(sub, FAIL,
                  "/dev/wmtWifi 不存在（WMT 平台驱动没建出功能开关节点）",
                  "看 04_modules.txt 有无 mtk_stp_wmt/wlan_mt6620；"
                  "init.z1.rc 的 mknod /dev/wmtWifi c 153 0 是否执行；dmesg | grep -i wmt")
        return
    if f.get("TRIGGER_RESULT") == "wmtwifi_node_missing":
        sheet.add(sub, FAIL, "trigger 判定 /dev/wmtWifi 缺失", "同上")
        return
    if yes(f.get("WLAN0_EXISTS")):
        ssid = as_int(f.get("TRIGGER_SCAN_SSIDS"))
        if ssid is None:
            sheet.add(sub, OK, "wlan0 已存在（未做 scan 触发）",
                      "要验射频跑 z1_diag_wifi.sh trigger")
        elif ssid > 0:
            sheet.add(sub, OK, "wlan0 存在且 scan 扫到 %d 个 SSID" % ssid,
                      "射频+固件+校准这条链路通；剩下是连接/认证")
        else:
            sheet.add(sub, FAIL, "wlan0 存在但 scan 0 个 SSID",
                      "看 04_scan_raw.txt 的 errno；dmesg | grep -iE 'scan|firmware'")
        return
    if f.get("TRIGGER_RESULT") == "wlan0_missing":
        sheet.add(sub, FAIL, "func-on 写入了但 wlan0 一直没出现",
                  "dmesg | grep -iE 'wlan|hif|sdio'；查固件目录与 wlan_mt6620 加载")
        return
    sheet.add(sub, WARN, "/dev/wmtWifi 在但 wlan0 不在（未做触发）",
              "跑 z1_diag_wifi.sh trigger 复现 func-on")


def check_bt(f, sheet, grep):
    sub = "BT"
    if not yes(f.get("BT_STPBT")):
        sheet.add(sub, FAIL, "/dev/stpbt 不存在（BT transport 没起来）",
                  "根因在内核：grep stp_bt /proc/modules；dmesg | grep -i stpbt。"
                  "此时不用查 HAL，栈必然起不来")
        return
    if as_int(f.get("BT_STACK_RUNNING"), 0) > 0:
        sheet.add(sub, OK, "com.android.bluetooth 进程在跑",
                  "链路通；再验 hciconfig 有 hci0 与能否配对")
    else:
        sheet.add(sub, FAIL, "/dev/stpbt 在但蓝牙栈没起来",
                  "查 07_bluetooth.txt 的 libbluetoothdrv/libstdc++/HIDL 是否存在；"
                  "logcat | grep -i 'dlopen failed'")


def check_gps(f, sheet, grep):
    sub = "GPS"
    mnld_bin = as_int(f.get("GPS_MNLD_BINARY"), 0)
    hal = as_int(f.get("GPS_HAL_BINARIES"), 0)
    hal_lib = as_int(f.get("GPS_HAL_LIBS"), 0)
    if mnld_bin == 0 and hal == 0 and hal_lib == 0:
        sheet.add(sub, INFO,
                  "本版没有 GPS 用户态（无 mnld、无 gnss HAL、无 gps.*.so）",
                  "这是镜像内容问题不是驱动问题：当前 device.mk 无 GPS 条目。"
                  "要测 GNSS 先把 HAL+mnld+libnvram 打进去，否则系列现象都无意义")
        return
    if not yes(f.get("DEV_STPGPS")):
        sheet.add(sub, FAIL, "/dev/stpgps 不存在（mtk_stp_gps 没起来）",
                  "grep stp_gps /proc/modules；dmesg | grep -i stp_gps")
        return
    if as_int(f.get("GPS_MNLD_RUNNING"), 0) > 0:
        sheet.add(sub, OK, "mnld 在跑", "再看 logcat 有无卫星/漂移；需户外或冷启动时间")
    else:
        sheet.add(sub, FAIL, "节点在但 mnld 没跑起来",
                  "logcat | grep -iE 'mnld|linker'；libnvram*.so 是否齐")


def check_audio(f, sheet, grep):
    sub = "Audio"
    cards = as_int(f.get("AUDIO_CARDS"), 0)
    nodes = as_int(f.get("AUDIO_SND_NODES"), 0)
    if cards == 0:
        sheet.add(sub, FAIL, "没有 sound card（ASoC 机器驱动没注册）",
                  "dmesg | grep -iE 'asoc|dai|snd_soc_register_card'；"
                  "gabin8/MT6572 机器驱动是否编进内核")
        return
    if nodes == 0:
        sheet.add(sub, FAIL, "%d 张卡但没有 /dev/snd 节点（PCM 未注册）" % cards,
                  "看 /proc/asound/pcm；dts 里 DAI link")
        return
    if as_int(f.get("AUDIO_HAL_LIBS"), 0) == 0:
        sheet.add(sub, WARN, "内核有 %d 卡 / %d 节点，但没找到 audio HAL 库" % (cards, nodes),
                  "09_audio 的 HAL 清单；getprop | grep audio")
        return
    sheet.add(sub, OK, "内核卡+节点+HAL 库都在（%d 卡 / %d 节点）" % (cards, nodes),
              "出声与否必须人耳验：sh z1_diag_audio.sh trigger")


def check_display(f, sheet, grep):
    sub = "Display/GPU"
    dri = as_int(f.get("DRI_NODES"), 0)
    fb = f.get("FB0_NAME", "")
    if dri > 0:
        sheet.add(sub, OK, "%d 个 /dev/dri 节点（DRM 起来了）" % dri,
                  "再验 SurfaceFlinger/EGL：10_display_gpu.txt")
    elif fb:
        sheet.add(sub, WARN, "只有 fb0 (%s)，没有 DRM 节点" % fb,
                  "若目标是 lima/Mesa，说明 GPU 没接管；看 EGL props 与 gralloc")
    else:
        sheet.add(sub, FAIL, "没有 fb0 也没有 /dev/dri", "显示完全没起来，先看 dmesg")


def check_input(f, sheet, grep):
    sub = "Input"
    kpd = as_int(f.get("INPUT_KPD"), 0)
    tpd = as_int(f.get("INPUT_TPD"), 0)
    cst = as_int(f.get("INPUT_CST836"), 0)
    ev = as_int(f.get("INPUT_EVENT_NODES"), 0)
    if kpd == 0 and tpd == 0 and cst == 0:
        sheet.add(sub, FAIL, "没有 keypad/touch 输入设备注册",
                  "dmesg | grep -iE 'kpd|input|touch'；dts keymap")
    else:
        sheet.add(sub, OK if ev else WARN,
                  "设备: kpd=%d tpd=%d cst836=%d, event 节点=%d" % (kpd, tpd, cst, ev),
                  "码值/复读跟读键必须用 z1_diag_keys.sh all 抓（看 MISSING_KEYS.txt）")


def check_battery(f, sheet, grep):
    sub = "Battery"
    cap = f.get("BATTERY_CAPACITY", "")
    volt = f.get("BATTERY_VOLTAGE_NOW", "")
    if cap or volt:
        sheet.add(sub, OK, "capacity=%s voltage_now=%s" % (cap or "?", volt or "?"), "")
    elif as_int(f.get("POWER_SUPPLIES"), 0) > 0:
        sheet.add(sub, WARN, "%d 个 power_supply 但 adc-battery 没读数" %
                  as_int(f.get("POWER_SUPPLIES"), 0),
                  "12_battery.txt 里各 supply 的 uevent")
    else:
        sheet.add(sub, FAIL, "没有任何 power_supply", "dmesg | grep -i battery")


def check_mem(f, sheet, grep):
    sub = "Memory/lmkd"
    if not yes(f.get("PSI_PRESENT")):
        sheet.add(sub, INFO, "无 /proc/pressure（正常：本机 3.4.67 内核没有 PSI）", "")
    kills = grep("am_kill")
    fg = [ln for ln in kills if "foreground" in ln.lower()]
    if fg:
        sheet.add(sub, FAIL, "logcat 里仍有前台被 lmkd 杀（am_kill ... foreground）",
                  "对照 facts 的 PROP_RO_LMK / LMKD_MINREE；看 13_memory_perf.txt")
    elif as_int(f.get("MEM_FREE_KB")) is not None:
        sheet.add(sub, OK, "MemFree=%s kB, zram=%s" %
                  (f.get("MEM_FREE_KB"), f.get("ZRAM_DISKSIZE") or "?"),
                  "设置 app 闪退若复现，重点看 14_crash_logcat.txt")


def check_selinux(f, sheet, grep):
    sub = "SELinux"
    mode = f.get("SELINUX") or "unknown"
    avc = as_int(f.get("AVC_COUNT"), 0)
    if avc and avc > 50:
        sheet.add(sub, FAIL, "%s 模式下 %d 条 avc 拒绝" % (mode, avc),
                  "15_selinux.txt 的 top40 去重表，先看 gps/bt/stpwmt 相关")
    elif avc:
        sheet.add(sub, WARN, "%s 模式，%d 条 avc" % (mode, avc),
                  "15_selinux.txt 去重计数")
    else:
        sheet.add(sub, OK, "%s 模式，无 avc" % mode, "")


MODULE_PATTERNS = [
    (r"Invalid module format", "模块 vermagic 不匹配（insmod 失败）",
     "对比 facts 的 MOD_VERMAGIC_KERNEL / MOD_VERMAGIC_RUNNING"),
    (r"Unknown symbol", "模块缺符号（导出符号/A",
     "多半是 .ko 与内核不同源，重编该模块"),
    (r"request_firmware.*(failed|error)", "固件没加载上",
     "查 06_wireless.txt 的 firmware 目录与文件名大小写"),
]


def check_modules(f, sheet, grep):
    sub = "Kernel/modules"
    hits = []
    for pat, label, hint in MODULE_PATTERNS:
        got = grep(pat)
        if got:
            hits.append((label, hint, got[0]))
    if not hits:
        sheet.add(sub, OK, "dmesg 无模块/固件装载失败特征串", "")
        return
    for label, hint, sample in hits:
        sheet.add(sub, FAIL, label, "%s  ← %s" % (hint, sample.strip()[:120]))


def check_collector(f, sheet, grep):
    sub = "Collector"
    uid = f.get("RUN_UID", "?")
    if str(uid) != "0":
        sheet.add(sub, WARN, "采集以 uid=%s 跑的（dmesg/modules 可能不全）" % uid,
                  "adb root 或 su 后重跑")
    if not f:
        sheet.add(sub, FAIL, "facts.txt 是空的", "采集脚本没跑完，重跑 z1_diag_all.sh")
        return
    sheet.add(sub, INFO, "facts 键数=%d, 内核=%s" %
              (len(f), f.get("KERNEL_RELEASE", "?")), "")


# --------------------------------------------------------------------------


def summarize(bundle, verbose=False):
    facts = bundle.facts()
    dmesg = bundle.text("03_dmesg.txt")
    logcat = bundle.text("14_crash_logcat.txt")

    cache = {}

    def grep(pat, source=None):
        key = (pat, source or "both")
        if key in cache:
            return cache[key]
        rx = re.compile(pat, re.I)
        out = [ln for ln in dmesg.splitlines() if rx.search(ln)]
        if source in (None, "logcat"):
            out += [ln for ln in logcat.splitlines() if rx.search(ln)]
        cache[key] = out
        return out

    sheet = Sheet()
    for fn in (check_collector, check_modules, check_wifi, check_bt, check_gps,
               check_audio, check_display, check_input, check_battery,
               check_mem, check_selinux):
        try:
            fn(facts, sheet, grep)
        except Exception as exc:                     # never die on one rule
            sheet.add(fn.__name__, WARN, "规则自身异常: %s" % exc, "")
    return sheet, facts


def render(sheet, facts, verbose=False):
    rows = sheet.worst()
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    head = " ".join("%s=%d" % (k, counts[k]) for k in
                    (FAIL, WARN, OK, INFO) if k in counts)
    print("=" * 78)
    print(" Z1 diag verdict    %s" % head)
    print("=" * 78)
    for r in rows:
        print("%-6s %-13s %s" % (r["status"], r["sub"], r["finding"]))
        if r["hint"]:
            print("%-6s %-13s   -> %s" % ("", "", r["hint"]))
    print("-" * 78)
    blockers = [r for r in rows if r["status"] == FAIL]
    if blockers:
        print(" P0 先解决（按顺序）:")
        for i, r in enumerate(blockers, 1):
            print("  %d. [%s] %s" % (i, r["sub"], r["finding"]))
    else:
        print(" 没有 FAIL 级阻断；剩下的按 WARN 逐条看。")
    if verbose and facts:
        print("-" * 78)
        print(" facts.txt:")
        for k in sorted(facts):
            print("  %s=%s" % (k, facts[k]))
    return 1 if blockers else 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Summarize a z1_diag bundle into a verdict sheet.")
    ap.add_argument("bundle", help="collect_*.tar.gz or the extract directory")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="also dump facts.txt")
    ap.add_argument("--json", action="store_true", help="machine readable output")
    args = ap.parse_args(argv)

    try:
        bundle = Bundle(args.bundle)
    except (OSError, SystemExit, tarfile.TarError) as exc:
        print("cannot read bundle: %s" % exc, file=sys.stderr)
        return 2

    sheet, facts = summarize(bundle, args.verbose)
    if args.json:
        print(json.dumps({"rows": sheet.worst(), "facts": facts},
                         ensure_ascii=False, indent=2))
        return 1 if any(r["status"] == FAIL for r in sheet.rows) else 0
    return render(sheet, facts, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
