/*
 * z1selftestd — Z1 自检守护进程（root，init 拉起）
 *
 * 为什么不是普通 APK：本机没有 Java/Android 构建树（无 android.jar / aapt2），
 * 而且设备上没有 adb，串口 shell 里也没有 nc。所以做成：
 *   串口 shell 写 /data/local/tmp/z1st.cmd  →  本守护进程执行  →
 *   结果写 /data/local/tmp/z1st.out（含序号，便于等待）
 * 守护进程以 root 运行，能跑 dumpsys / cmd / input / tinyplay / insmod 等。
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <time.h>
#include <sys/stat.h>
#include <sys/types.h>

#define CMD_FILE  "/data/local/tmp/z1st.cmd"
#define OUT_FILE  "/data/local/tmp/z1st.out"

static FILE *out;
static int  fails;

static void emit(const char *fmt, ...) __attribute__((format(printf,1,2)));
static void emit(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vfprintf(out, fmt, ap);
    va_end(ap);
    fputc('\n', out);
    fflush(out);
}

/* 跑一条 shell 命令，返回最后一行非空输出（截断到 buf） */
static const char *run(const char *cmd, char *buf, size_t n)
{
    FILE *p = popen(cmd, "r");
    buf[0] = 0;
    if (!p) return buf;
    char line[512];
    while (fgets(line, sizeof line, p)) {
        size_t l = strlen(line);
        while (l && (line[l-1] == '\n' || line[l-1] == '\r')) line[--l] = 0;
        if (l) snprintf(buf, n, "%s", line);
    }
    pclose(p);
    return buf;
}

/* 在命令输出里找 pattern，找到返回 1 */
static int has(const char *cmd, const char *pattern)
{
    char tmp[1024];
    snprintf(tmp, sizeof tmp, "%s 2>/dev/null", cmd);
    FILE *p = popen(tmp, "r");
    if (!p) return 0;
    char line[512]; int found = 0;
    while (fgets(line, sizeof line, p))
        if (strstr(line, pattern)) { found = 1; break; }
    pclose(p);
    return found;
}

static void check(const char *name, int ok, const char *fmt, ...)
{
    va_list ap; char det[400];
    va_start(ap, fmt); vsnprintf(det, sizeof det, fmt, ap); va_end(ap);
    emit("%-10s %s  %s", name, ok ? "PASS" : "FAIL", det);
    if (!ok) fails++;
}

/* ------------------------------------------------------------------ tests */

static void t_info(void)
{
    char b[256];
    emit("--- info ---");
    emit("model      : %s", run("getprop ro.product.model", b, sizeof b));
    emit("build      : %s", run("getprop ro.build.display.id", b, sizeof b));
    emit("kernel     : %s", run("uname -r", b, sizeof b));
    emit("uptime     : %s", run("cat /proc/uptime", b, sizeof b));
    emit("mem        : %s", run("grep MemAvailable /proc/meminfo", b, sizeof b));
    emit("loadavg    : %s", run("cat /proc/loadavg", b, sizeof b));
    emit("boot_compl : %s", run("getprop sys.boot_completed", b, sizeof b));
    emit("cpufreq    : %s", access("/sys/devices/system/cpu/cpu0/cpufreq", F_OK) ? "ABSENT (CPU 固定频率)" : "present");
}

static void t_gpu(void)
{
    char b[256];
    emit("--- gpu ---");
    run("dumpsys SurfaceFlinger 2>/dev/null | grep -m1 'GLES:'", b, sizeof b);
    check("gpu", strstr(b, "Mali400") != NULL, "%s", b[0] ? b : "(no GLES line)");
    int faults = has("dmesg | grep -c 'ppmmu. page fault'", "0") ? 0 : 1;
    char c[64]; run("dmesg | grep -c 'ppmmu. page fault'", c, sizeof c);
    int n = atoi(c);
    check("gpu-mm", n == 0, "lima page fault = %d", n);
    run("dmesg | grep -c 'pp0 job timeout'", c, sizeof c);
    check("gpu-pp", atoi(c) == 0, "pp0 job timeout = %s", c);
    (void)faults;
}

static void t_display(void)
{
    char b[256];
    emit("--- display ---");
    emit("brightness : %s", run("cat /sys/class/backlight/*/brightness 2>/dev/null", b, sizeof b));
    emit("max        : %s", run("cat /sys/class/backlight/*/max_brightness 2>/dev/null", b, sizeof b));
    emit("fb         : %s", run("grep -m1 . /sys/class/graphics/fb0/virtual_size", b, sizeof b));
    check("display", has("ls /sys/class/graphics/fb0", "fb0"), "fb0 present");
}

static void t_touch(void)
{
    char b[512];
    emit("--- touch ---");
    run("grep -i -m2 -E 'cst836u|touch|goodix' /proc/bus/input/devices", b, sizeof b);
    check("touch", b[0] != 0, "%s", b[0] ? b : "no touch input device");
    run("ls /dev/input/", b, sizeof b);
    emit("inputs     : %s", b);
}

static void t_audio(void)
{
    char b[256];
    emit("--- audio ---");
    run("tinymix 2>/dev/null | wc -l", b, sizeof b);
    check("audio-mix", atoi(b) > 0, "tinymix controls = %s", b);
    emit("cards      : %s", run("cat /proc/asound/cards 2>/dev/null | head -1", b, sizeof b));
}

static void t_wifi(void)
{
    char b[256];
    emit("--- wifi ---");
    emit("wlan0      : %s", run("ip -o link show wlan0 2>/dev/null", b, sizeof b));
    run("cmd wifi status 2>/dev/null | head -1", b, sizeof b);
    check("wifi", strstr(b, "Wi-Fi") || strstr(b, "enabled") || strstr(b, "disabled"), "%s", b[0] ? b : "(no status)");
}

static void t_bt(void)
{
    char b[256];
    emit("--- bluetooth ---");
    run("cmd bluetooth_manager get-state 2>/dev/null", b, sizeof b);
    check("bt", b[0] != 0, "state = %s", b[0] ? b : "(no answer)");
}

static void t_battery(void)
{
    char b[256];
    emit("--- battery/charger ---");
    emit("capacity   : %s", run("cat /sys/class/power_supply/battery/capacity 2>/dev/null", b, sizeof b));
    emit("voltage    : %s", run("cat /sys/class/power_supply/battery/voltage_now 2>/dev/null", b, sizeof b));
    emit("usb online : %s", run("cat /sys/class/power_supply/usb/online 2>/dev/null", b, sizeof b));
    emit("status     : %s", run("cat /sys/class/power_supply/battery/status 2>/dev/null", b, sizeof b));
    int online = atoi(run("cat /sys/class/power_supply/usb/online 2>/dev/null", b, sizeof b));
    check("charger", online >= 0, "usb online = %s", b);
}

static void t_gps(void)
{
    char b[256];
    emit("--- gnss ---");
    run("service list 2>/dev/null | grep -i -m1 gnss", b, sizeof b);
    check("gnss", b[0] != 0, "%s", b[0] ? b : "(gnss service not found)");
}

static void t_sensors(void)
{
    char b[256];
    emit("--- sensors ---");
    run("dumpsys sensorservice 2>/dev/null | grep -m1 -i 'sensor list'", b, sizeof b);
    check("sensors", b[0] != 0, "%s", b[0] ? b : "(no sensorservice)");
}

static void t_storage(void)
{
    char b[512];
    emit("--- storage ---");
    run("df /data /cache 2>/dev/null | tail -2", b, sizeof b);
    check("storage", b[0] != 0, "%s", b[0] ? b : "(df failed)");
    emit("data       : %s", run("df -h /data 2>/dev/null | tail -1", b, sizeof b));
}

static void t_ui(void)
{
    char b[256];
    emit("--- ui ---");
    run("am start -a android.intent.action.MAIN -c android.intent.category.HOME >/dev/null 2>&1; sleep 2; dumpsys activity activities 2>/dev/null | grep -m1 mResumedActivity", b, sizeof b);
    check("ui-home", b[0] != 0, "%s", b[0] ? b : "(no resumed activity)");
}

static void t_settings(void)
{
    char b[256];
    emit("--- settings app ---");
    run("am start -a android.settings.SETTINGS >/dev/null 2>&1", b, sizeof b);
    sleep(3);
    run("dumpsys activity activities 2>/dev/null | grep -m1 mResumedActivity", b, sizeof b);
    int ok = strstr(b, "com.android.settings") != NULL;
    check("settings", ok, "%s", b[0] ? b : "(not resumed)");
    run("logcat -d -b crash -t 5 2>/dev/null | grep -m1 -E 'Process:|FATAL'", b, sizeof b);
    if (b[0]) emit("  crash: %s", b);
}

static void t_shot(void)
{
    char b[256];
    emit("--- screenshot ---");
    run("screencap -p /data/local/tmp/z1st_shot.png 2>/dev/null; md5sum /data/local/tmp/z1st_shot.png 2>/dev/null", b, sizeof b);
    check("shot", b[0] != 0, "%s", b[0] ? b : "(screencap failed)");
}

static void t_log(void)
{
    emit("--- recent log (last 10) ---");
    run("logcat -d -t 10 2>/dev/null", (char[512]){0}, 1); /* no-op keeps API */
    FILE *p = popen("logcat -d -t 12 2>/dev/null", "r");
    if (p) { char l[400]; while (fgets(l, sizeof l, p)) fprintf(out, "  %s", l); pclose(p); }
    fflush(out);
}

static void t_all(void)
{
    t_info(); t_gpu(); t_display(); t_touch(); t_audio(); t_wifi();
    t_bt(); t_battery(); t_gps(); t_sensors(); t_storage(); t_ui(); t_shot();
}

/* ------------------------------------------------------------------- main */

typedef struct { const char *name; void (*fn)(void); } test_t;
static const test_t tests[] = {
    {"info", t_info}, {"gpu", t_gpu}, {"display", t_display}, {"touch", t_touch},
    {"audio", t_audio}, {"wifi", t_wifi}, {"bt", t_bt}, {"battery", t_battery},
    {"gps", t_gps}, {"sensors", t_sensors}, {"storage", t_storage}, {"ui", t_ui},
    {"settings", t_settings}, {"shot", t_shot}, {"log", t_log}, {"all", t_all},
};

int main(int argc, char **argv)
{
    if (argc > 1 && strcmp(argv[1], "--once") == 0) {
        /* 前台跑一次：z1selftestd --once <test> */
        out = stdout;
        const char *want = argc > 2 ? argv[2] : "all";
        for (size_t i = 0; i < sizeof tests / sizeof tests[0]; i++)
            if (!strcmp(tests[i].name, want)) { tests[i].fn(); return fails ? 1 : 0; }
        emit("unknown test %s", want);
        return 2;
    }

    /* 守护模式：轮询命令文件 */
    long last_seq = -1;
    for (;;) {
        FILE *f = fopen(CMD_FILE, "r");
        if (f) {
            long seq = -1; char cmd[128] = {0};
            if (fscanf(f, "%ld %127s", &seq, cmd) == 2 && seq != last_seq) {
                last_seq = seq;
                out = fopen(OUT_FILE, "w");
                if (out) {
                    fails = 0;
                    emit("# seq %ld cmd %s", seq, cmd);
                    int hit = 0;
                    for (size_t i = 0; i < sizeof tests / sizeof tests[0]; i++)
                        if (!strcmp(tests[i].name, cmd)) { tests[i].fn(); hit = 1; }
                    if (!hit) emit("unknown test %s (可用: info gpu display touch audio wifi bt battery gps sensors storage ui settings shot log all)", cmd);
                    emit("# done seq %ld fails=%d", seq, fails);
                    fclose(out);
                }
            }
            fclose(f);
        }
        usleep(200000);
    }
}
