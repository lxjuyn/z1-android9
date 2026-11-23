/*
 * audio_primary_z1_poc.c — minimal A9 (SDK28) audio PRIMARY HAL for Z1 (MT6572 + MT6323)
 * Implementation: AOSP audio_hw_hal.cpp "legacy wrapper" layout, backed by tinyalsa.
 *
 * r9 revision (2026-11-19): implement the Android microphone mute contract by
 * storing the device mute state and suppressing successful capture buffers.
 *
 * r8 revision (2026-11-19): add the AWB microphone input stream on
 * pcmC0D1c. Playback and capture use the same lazy-open recovery so the HAL
 * can load before the ASoC card finishes probing.
 *
 * r5g revision (2026-10-30), derived from scripts/r5d_audio_hal/audio_primary_z1_poc.c
 * (r5d 原版 316 行原样未动)。本轮改动 (依据 2026-10-30 ABI 判定 abi.changes;
 * 工具链换 arm-linux-gnueabihf-gcc, 构建见同目录 build_r5g.sh):
 *   [必改1] out_write: 删掉 "done += bytes - done" 伪重试循环, 改单次 pcm_write
 *           (tinyalsa pcm_write 本就是全量阻塞写, 伪循环恒只跑一次且语义错误)。
 *   [必改2] open_output_stream: 采样率/通道按 Z1 支持集归一化并回写 *config ——
 *           AudioFlinger 尊重 HAL 返回的 config 并自动建重采样器; 原版把请求采样率
 *           直接透传 pcm_open, 若 AFE DL1 不支持该率则每次打开必失败且不会自愈。
 *   [必改3] pcm_ensure_open: 连续失败退避 (每 Z1_OPEN_RETRY_EVERY 次 write 才真正
 *           尝试 1 次 pcm_open) + 日志限流 (第 1 次与每 Z1_OPEN_LOG_EVERY 次失败
 *           各打 1 条)。声卡晚于 AudioFlinger 加载 HAL 的窗口内不再刷爆 logcat。
 *   [可选4] 补 get_presentation_position (A9 audio_stream_out 偏移 96 槽;
 *           镜像 wrapper @0x21402 对该槽有 NULL 检查, 不填也安全, 填了更好)。
 *   [可选5] adev_close 补 pthread_mutex_destroy。
 *   [部署面6/id 不改] HMI.id = AUDIO_HARDWARE_MODULE_ID ("audio")。
 *           libhardware hw_get_module_by_class 用 strcmp(HMI->id, class_id) 校验
 *           (libhardware.so @0xafa; 不匹配 = mvn r0,#21 = -EINVAL, 即板上 -22)。
 *           "z1-mt6572-mainline" 是 author 字段 (文件末尾 HAL_MODULE_INFO_SYM),
 *           不参与匹配。
 *
 * The microphone path is real. Telephony, voice-call routing, FM capture and
 * hardware gain controls remain outside this HAL.
 */
#include <errno.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <stdio.h>
#include <pthread.h>
#include <sys/types.h>

#include <hardware/hardware.h>
#include <hardware/audio.h>
#include <system/audio.h>
#include <tinyalsa/asoundlib.h>

#define LOG_TAG "audio_primary_z1"
#define ALOGV(...) __android_log_print(ANDROID_LOG_VERBOSE, LOG_TAG, __VA_ARGS__)
#define ALOGI(...) __android_log_print(ANDROID_LOG_INFO,    LOG_TAG, __VA_ARGS__)
#define ALOGE(...) __android_log_print(ANDROID_LOG_ERROR,   LOG_TAG, __VA_ARGS__)
#include <android/log.h>

/* errno 访问器重绑定: glibc 头把 errno 展开成 *(*__errno_location()),
 * 而板上 bionic libc.so (API28) 只导出 __errno (nm -D 实测, 无 __errno_location)。
 * bionic 的 __errno 与 glibc 的 __errno_location 语义相同 (取 per-thread errno 指针),
 * 运行期 strerror(errno)/-errno 全部走它。必须放在所有系统头之后。 */
extern int __system_property_get(const char *key, char *value);
extern int *__errno(void);
#undef errno
#define errno (*__errno())

/* ---- tunables (verified against the image's dtb / .ko set) ---- */
#define Z1_PCM_CARD   0
#define Z1_PCM_PLAYBACK_DEV 0
#define Z1_PCM_CAPTURE_DEV  1
#define Z1_DEFAULT_RATE      48000
#define Z1_DEFAULT_CHANNELS  2
#define Z1_DEFAULT_PERIOD    1024

/* ---- 时序纪律 (AudioFlinger t≈26s 加载 primary HAL, 声卡 /dev/snd/pcmC0D0p
 *      要等 load_audio.sh [post-fs insmod 11 个 .ko] 跑完才出现) ----
 * adev_open / open_output_stream 绝不触碰 /dev/snd —— 懒打开, 只在 out_write 里试。
 * pcm_open 对不存在的 /dev/snd/pcmC0D0p 走 open() ENOENT, 立即失败、不阻塞,
 * 因此 HAL 加载/打开流都不卡 AudioFlinger 主线程; 出声自愈路径 = 声卡就绪后
 * 下一次满足尝试条件的 write 成功。
 * 不设固定超时: load_audio.sh 完成时刻板上未实证, 定死超时会制造"超时后永无声"
 * 的新失败模式; 退避只压重试密度, 恢复语义保持"永远可重试"。 */
#define Z1_OPEN_RETRY_EVERY  10   /* 连续失败期间每 10 次 write 真正尝试 1 次 pcm_open */
#define Z1_OPEN_LOG_EVERY    32   /* 失败日志限流: 第 1 次与之后每 32 次失败各 1 条 ALOGE */

/* Z1 支持集: 上板 tinypcminfo -D 0 -d 0 复核 rates mask 之前保守只认 48k,
 * 其余采样率一律归一到 48k (由 AudioFlinger 重采样), 见 [必改2]。 */
static const uint32_t z1_supported_rates[] = { Z1_DEFAULT_RATE };
static const uint32_t z1_input_rates[] = { 8000, 16000, 32000, 48000 };

struct z1_stream_out {
    struct audio_stream_out stream;
    struct z1_audio_device *adev;
    int16_t *scaled;
    size_t scaled_bytes;
    pthread_mutex_t lock;
    struct pcm     *pcm;
    uint32_t        rate;
    uint32_t        channels;
    size_t          frame_bytes;
    audio_devices_t device;
    uint64_t        frames_written;   /* 累计写入帧数 (get_presentation_position) */
    uint32_t        open_fails;       /* actual consecutive pcm_open failures */
    uint32_t        open_retry_wait;  /* writes to skip before retrying */
};

struct z1_stream_in {
    struct audio_stream_in stream;
    struct z1_audio_device *adev;
    pthread_mutex_t lock;
    struct pcm     *pcm;
    uint32_t        rate;
    uint32_t        channels;
    size_t          frame_bytes;
    audio_devices_t device;
    uint64_t        frames_read;
    uint32_t        open_fails;
    uint32_t        open_retry_wait;
};

struct z1_audio_device {
    struct audio_hw_device hw;
    pthread_mutex_t        lock;
    int                    in_call;
    bool                   mic_mute;
    bool                   speaker_high;
};

/* ---- ABI 断言: A9 (SDK28) 布局, 本轮用 arm-linux-gnueabihf-gcc 探针实算
 * (sizeof(struct audio_stream_out)=120 / offsetof(get_presentation_position)=96 /
 *  sizeof(struct audio_hw_device)=156), 后两者与 2026-10-30 ABI 判定及
 * 镜像 wrapper 反汇编 (StreamOut::getPresentationPositionImpl @0x21402 ldr r3,[r6,#96])
 * 逐位吻合; stub adev_open calloc(1,156)/open_output_stream calloc(1,120=0x78) 二进制互证。
 * 注: 2026-10-30 structLayout 证据写 "sizeof(audio_stream_out)=100" 是漏数了
 * get_presentation_position (96) 之后的 4 个 mmap 槽 (start/stop/create_mmap_buffer/
 * get_mmap_position), 头文件实为 120 —— 本 HAL 全部按字段名初始化 + calloc 清零,
 * 尾部 mmap 槽 = NULL (在役 stub 同样 NULL 且板上加载成功), 该出入对本 HAL 无影响。
 * 编译期护栏: 布局一变立刻炸。 */
_Static_assert(sizeof(struct hw_module_t)      == 128, "hw_module_t != 128B (libhardware ABI)");
_Static_assert(sizeof(struct audio_stream)     ==  56, "audio_stream != 56B (A9 ABI)");
_Static_assert(sizeof(struct audio_hw_device)  == 156, "audio_hw_device != 156B (A9 ABI)");
_Static_assert(sizeof(struct audio_stream_out) == 120, "audio_stream_out != 120B (A9 ABI, 含 4 个 mmap 槽)");
_Static_assert(sizeof(struct audio_stream_in)  ==  96, "audio_stream_in != 96B (A9 ABI)");
_Static_assert(offsetof(struct audio_stream_out, get_presentation_position) == 96,
               "get_presentation_position 必须在偏移 96 (wrapper 实调槽)");
_Static_assert(offsetof(struct audio_stream_in, read) == 60,
               "capture read callback must remain at offset 60 (A9 ABI)");

/* ============================ helpers ============================ */

/* 手写 popcount: 不用 __builtin_popcount, 避免 gcc/armv7-a 生成 libgcc
 * __popcountsi2 调用 (会污染 UND 符号表)。 */
static uint32_t z1_popcount32(uint32_t v)
{
    v = v - ((v >> 1) & 0x55555555u);
    v = (v & 0x33333333u) + ((v >> 2) & 0x33333333u);
    v = (v + (v >> 4)) & 0x0f0f0f0fu;
    return (v * 0x01010101u) >> 24;
}

static uint32_t popcount_mask(audio_channel_mask_t m) { return z1_popcount32((uint32_t)m); }

/* [必改2] 采样率归一化: 请求率在支持集内则原样, 否则落 Z1_DEFAULT_RATE。 */
static uint32_t z1_normalize_rate(uint32_t req)
{
    size_t i;
    if (req != 0) {
        for (i = 0; i < sizeof(z1_supported_rates) / sizeof(z1_supported_rates[0]); i++)
            if (z1_supported_rates[i] == req)
                return req;
    }
    return Z1_DEFAULT_RATE;
}

static uint32_t z1_normalize_input_rate(uint32_t req)
{
    size_t i;
    for (i = 0; i < sizeof(z1_input_rates) / sizeof(z1_input_rates[0]); i++)
        if (z1_input_rates[i] == req)
            return req;
    return Z1_DEFAULT_RATE;
}

/* --------------------------- stream out -------------------------- */

static uint32_t out_get_sample_rate(const struct audio_stream *s)
{ return ((const struct z1_stream_out *)s)->rate; }

static int out_set_sample_rate(struct audio_stream *s, uint32_t rate)
{ /* 同 [必改2] 语义: 不透传支持集之外的率, 否则 pcm_open 之后每次必失败。 */
  ((struct z1_stream_out *)s)->rate = z1_normalize_rate(rate); return 0; }

static size_t out_get_buffer_size(const struct audio_stream *s)
{ return ((const struct z1_stream_out *)s)->frame_bytes * Z1_DEFAULT_PERIOD; }

static audio_channel_mask_t out_get_channels(const struct audio_stream *s)
{ const struct z1_stream_out *o = (const struct z1_stream_out *)s;
  return (o->channels == 1) ? AUDIO_CHANNEL_OUT_MONO : AUDIO_CHANNEL_OUT_STEREO; }

static audio_format_t out_get_format(const struct audio_stream *s)
{ (void)s; return AUDIO_FORMAT_PCM_16_BIT; }

static int out_set_format(struct audio_stream *s, audio_format_t f)
{ (void)s; return (f == AUDIO_FORMAT_PCM_16_BIT) ? 0 : -EINVAL; }

static int out_standby(struct audio_stream *s)
{ struct z1_stream_out *o = (struct z1_stream_out *)s;
  pthread_mutex_lock(&o->lock);
  if (o->pcm) { pcm_close(o->pcm); o->pcm = NULL; }
  o->open_fails = 0;
  o->open_retry_wait = 0;
  pthread_mutex_unlock(&o->lock);
  return 0; }

static int out_dump(const struct audio_stream *s, int fd) { (void)s; (void)fd; return 0; }

static audio_devices_t out_get_device(const struct audio_stream *s)
{
    const struct z1_stream_out *o = (const struct z1_stream_out *)s;
    return __atomic_load_n(&o->device, __ATOMIC_RELAXED);
}

static int out_set_device(struct audio_stream *s, audio_devices_t device)
{
    struct z1_stream_out *o = (struct z1_stream_out *)s;
    __atomic_store_n(&o->device, device, __ATOMIC_RELAXED);
    return 0;
}

/* Legacy AudioFlinger sends routing in a semicolon-delimited parameter list. */
static const char *parameter(const char *kv, const char *key)
{
    size_t length = strlen(key);
    while (kv && *kv) {
        if (!strncmp(kv, key, length) && kv[length] == '=')
            return kv + length + 1;
        kv = strchr(kv, ';');
        if (kv) kv++;
    }
    return NULL;
}

static void refresh_speaker_mode(struct z1_audio_device *dev)
{
    char value[92] = {0}; /* Bionic PROP_VALUE_MAX */
    __system_property_get("persist.sys.z1.spk_high", value);
    __atomic_store_n(&dev->speaker_high, !strcmp(value, "1"), __ATOMIC_RELAXED);
}

static int out_set_parameters(struct audio_stream *s, const char *kv)
{
    const char *route = parameter(kv, "routing");
    if (route) {
        char *end;
        unsigned long device = strtoul(route, &end, 10);
        if (end == route || (*end && *end != ';'))
            return -EINVAL;
        out_set_device(s, device);
    }
    if (parameter(kv, "z1_speaker_high_volume"))
        refresh_speaker_mode(((struct z1_stream_out *)s)->adev);
    return 0;
}

static char *out_get_parameters(const struct audio_stream *s, const char *k)
{ (void)s; (void)k; return strdup(""); }

static int out_add_audio_effect(const struct audio_stream *s, effect_handle_t e)
{ (void)s; (void)e; return 0; }

static int out_remove_audio_effect(const struct audio_stream *s, effect_handle_t e)
{ (void)s; (void)e; return 0; }

/* 懒打开 + 退避: 详见文件头 [必改3] 注释与 Z1_OPEN_RETRY_EVERY/Z1_OPEN_LOG_EVERY。 */
static int pcm_ensure_open(struct z1_stream_out *o)
{
    struct pcm_config cfg;
    if (o->pcm) { o->open_fails = 0; return 0; }

    /* Keep retry timing independent of the actual failure/log counter.
     * After a failed open, skip nine writes and retry on the tenth. */
    if (o->open_retry_wait != 0) {
        o->open_retry_wait--;
        return -ENODEV;
    }

    memset(&cfg, 0, sizeof(cfg));
    cfg.channels     = o->channels;
    cfg.rate         = o->rate;
    cfg.period_size  = Z1_DEFAULT_PERIOD;
    cfg.period_count = 4;
    cfg.format       = PCM_FORMAT_S16_LE;
    cfg.start_threshold = Z1_DEFAULT_PERIOD;
    cfg.stop_threshold  = Z1_DEFAULT_PERIOD * 4;
    o->frame_bytes = cfg.channels * sizeof(uint16_t);
    o->pcm = pcm_open(Z1_PCM_CARD, Z1_PCM_PLAYBACK_DEV, PCM_OUT, &cfg);
    if (!o->pcm || !pcm_is_ready(o->pcm)) {
        o->open_retry_wait = Z1_OPEN_RETRY_EVERY - 1;
        o->open_fails++;
        if (o->open_fails == 1 || (o->open_fails % Z1_OPEN_LOG_EVERY) == 0)
            ALOGE("pcm_open(card%d,dev%d) failed (consecutive=%u): %s", Z1_PCM_CARD,
                  Z1_PCM_PLAYBACK_DEV, o->open_fails,
                  o->pcm ? pcm_get_error(o->pcm) : strerror(errno));
        if (o->pcm) { pcm_close(o->pcm); o->pcm = NULL; }
        return -ENODEV;
    }
    o->open_fails = 0;
    o->open_retry_wait = 0;
    ALOGI("pcm_open ok card%d dev%d %uHz %uch", Z1_PCM_CARD,
          Z1_PCM_PLAYBACK_DEV,
          cfg.rate, cfg.channels);
    return 0;
}

static ssize_t out_write(struct audio_stream_out *stream, const void *buffer, size_t bytes)
{
    struct z1_stream_out *o = (struct z1_stream_out *)stream;
    int rc;
    pthread_mutex_lock(&o->lock);
    rc = pcm_ensure_open(o);
    if (rc == 0 && out_get_device(&stream->common) == AUDIO_DEVICE_OUT_SPEAKER &&
        !__atomic_load_n(&o->adev->speaker_high, __ATOMIC_RELAXED)) {
        if (bytes % sizeof(int16_t))
            rc = -EINVAL;
        else if (bytes > o->scaled_bytes) {
            int16_t *scaled = realloc(o->scaled, bytes);
            if (scaled) {
                o->scaled = scaled;
                o->scaled_bytes = bytes;
            } else {
                rc = -ENOMEM;
            }
        }
        if (!rc && bytes) {
            const int16_t *pcm = buffer;
            for (size_t i = 0; i < bytes / sizeof(*pcm); ++i)
                o->scaled[i] = pcm[i] / 2;
            buffer = o->scaled;
        }
    }
    if (rc == 0) {
        /* [必改1] tinyalsa pcm_write 为全量阻塞写 (内部把整个 buffer 写完),
         * 单次调用即可。r5d 版 "done += bytes - done" 恒等于 done=bytes,
         * 循环体实际只跑一次 —— 伪循环, 换部分写接口时会静默丢数据, 已删。 */
        if (pcm_write(o->pcm, buffer, bytes) != 0) {
            ALOGE("pcm_write: %s", pcm_get_error(o->pcm));
            /* tinyalsa already handles recoverable underruns. If a write
             * still fails, do not keep the potentially stale descriptor. */
            pcm_close(o->pcm);
            o->pcm = NULL;
            o->open_retry_wait = Z1_OPEN_RETRY_EVERY - 1;
            rc = -EIO;
        } else {
            o->frames_written += bytes / (o->frame_bytes ? o->frame_bytes : 1);
        }
    }
    pthread_mutex_unlock(&o->lock);
    if (rc != 0) {
        /* 失败时按本批字节的音频时长补偿 sleep: AudioFlinger 不会空转打满 CPU。 */
        uint64_t bytes_per_sec = (uint64_t)o->rate * o->frame_bytes;
        uint64_t us = bytes_per_sec ? (bytes * 1000000ull) / bytes_per_sec : 0;
        usleep((useconds_t)us);
    }
    return rc == 0 ? (ssize_t)bytes : (ssize_t)rc;
}

static uint32_t out_get_latency(const struct audio_stream_out *s)
{ (void)s; return 20; }

/* No hardware stream gain: do not claim the VoIP gain was applied.
 * AOSP9 Threads.cpp retains software stream gain when this call fails. */
static int out_set_volume(struct audio_stream_out *s, float l, float r)
{ (void)s; (void)l; (void)r; return -ENOTSUP; }

static int out_get_render_position(const struct audio_stream_out *s, uint32_t *f)
{
    /* pcm_write completion is not DAC presentation. Returning the submitted
     * frame count makes AudioFlinger treat queued data as already audible and
     * corrupts A/V latency accounting. This HAL has no reliable hardware
     * timestamp, so explicitly select the framework's unsupported fallback. */
    (void)s;
    if (f)
        *f = 0;
    return -ENOSYS;
}

static int out_get_next_write_timestamp(const struct audio_stream_out *s, int64_t *t)
{ (void)s; if (t) *t = 0; return 0; }

static int out_set_callback(struct audio_stream_out *s, stream_callback_t cb, void *c)
{ (void)s; (void)cb; (void)c; return 0; }

static int out_pause(struct audio_stream_out *s)  { (void)s; return 0; }
static int out_resume(struct audio_stream_out *s) { (void)s; return 0; }
static int out_drain(struct audio_stream_out *s, audio_drain_type_t t) { (void)s; (void)t; return 0; }
static int out_flush(struct audio_stream_out *s) { (void)s; return 0; }

/* [可选4] A9 audio_stream_out 偏移 96 槽 (本目录 inc/hardware/audio.h:355 签名)。
 * 镜像 wrapper android.hardware.audio@2.0-impl.so StreamOut::getPresentationPositionImpl
 * @0x21402 对该槽有 NULL 检查 (NULL → NOT_SUPPORTED 软件兜底), 不填也安全;
 * 返回累计写帧数 + CLOCK_MONOTONIC 时间戳可让框架免走 NOT_SUPPORTED 路径。 */
static int out_get_presentation_position(const struct audio_stream_out *s,
                                         uint64_t *frames, struct timespec *timestamp)
{
    /* As above, do not claim that the kernel write boundary is DAC playback. */
    (void)s;
    if (frames)
        *frames = 0;
    if (timestamp)
        memset(timestamp, 0, sizeof(*timestamp));
    return -ENOSYS;
}

/* ---------------------------- stream in --------------------------- */

static uint32_t in_get_sample_rate(const struct audio_stream *s)
{ return ((const struct z1_stream_in *)s)->rate; }

static int in_set_sample_rate(struct audio_stream *s, uint32_t rate)
{
    ((struct z1_stream_in *)s)->rate = z1_normalize_input_rate(rate);
    return 0;
}

static size_t in_get_buffer_size(const struct audio_stream *s)
{
    const struct z1_stream_in *in = (const struct z1_stream_in *)s;
    return in->frame_bytes * Z1_DEFAULT_PERIOD;
}

static audio_channel_mask_t in_get_channels(const struct audio_stream *s)
{
    const struct z1_stream_in *in = (const struct z1_stream_in *)s;
    return in->channels == 1 ? AUDIO_CHANNEL_IN_MONO : AUDIO_CHANNEL_IN_STEREO;
}

static audio_format_t in_get_format(const struct audio_stream *s)
{ (void)s; return AUDIO_FORMAT_PCM_16_BIT; }

static int in_set_format(struct audio_stream *s, audio_format_t format)
{ (void)s; return format == AUDIO_FORMAT_PCM_16_BIT ? 0 : -EINVAL; }

static int in_standby(struct audio_stream *s)
{
    struct z1_stream_in *in = (struct z1_stream_in *)s;
    pthread_mutex_lock(&in->lock);
    if (in->pcm) {
        pcm_close(in->pcm);
        in->pcm = NULL;
    }
    in->open_fails = 0;
    in->open_retry_wait = 0;
    pthread_mutex_unlock(&in->lock);
    return 0;
}

static int in_dump(const struct audio_stream *s, int fd) { (void)s; (void)fd; return 0; }

static audio_devices_t in_get_device(const struct audio_stream *s)
{ return ((const struct z1_stream_in *)s)->device; }

static int in_set_device(struct audio_stream *s, audio_devices_t device)
{ ((struct z1_stream_in *)s)->device = device; return 0; }

static int in_set_parameters(struct audio_stream *s, const char *kv)
{ (void)s; (void)kv; return 0; }

static char *in_get_parameters(const struct audio_stream *s, const char *keys)
{ (void)s; (void)keys; return strdup(""); }

static int in_add_audio_effect(const struct audio_stream *s, effect_handle_t effect)
{ (void)s; (void)effect; return 0; }

static int in_remove_audio_effect(const struct audio_stream *s, effect_handle_t effect)
{ (void)s; (void)effect; return 0; }

static int pcm_input_ensure_open(struct z1_stream_in *in)
{
    struct pcm_config cfg;

    if (in->pcm) {
        in->open_fails = 0;
        return 0;
    }
    if (in->open_retry_wait != 0) {
        in->open_retry_wait--;
        return -ENODEV;
    }

    memset(&cfg, 0, sizeof(cfg));
    cfg.channels = in->channels;
    cfg.rate = in->rate;
    cfg.period_size = Z1_DEFAULT_PERIOD;
    cfg.period_count = 4;
    cfg.format = PCM_FORMAT_S16_LE;
    cfg.start_threshold = 1;
    cfg.stop_threshold = Z1_DEFAULT_PERIOD * 4;
    in->frame_bytes = cfg.channels * sizeof(uint16_t);
    in->pcm = pcm_open(Z1_PCM_CARD, Z1_PCM_CAPTURE_DEV, PCM_IN, &cfg);
    if (!in->pcm || !pcm_is_ready(in->pcm)) {
        in->open_retry_wait = Z1_OPEN_RETRY_EVERY - 1;
        in->open_fails++;
        if (in->open_fails == 1 || (in->open_fails % Z1_OPEN_LOG_EVERY) == 0)
            ALOGE("pcm_open(card%d,dev%d,capture) failed (consecutive=%u): %s",
                  Z1_PCM_CARD, Z1_PCM_CAPTURE_DEV, in->open_fails,
                  in->pcm ? pcm_get_error(in->pcm) : strerror(errno));
        if (in->pcm) {
            pcm_close(in->pcm);
            in->pcm = NULL;
        }
        return -ENODEV;
    }
    in->open_fails = 0;
    in->open_retry_wait = 0;
    ALOGI("pcm_open capture ok card%d dev%d %uHz %uch", Z1_PCM_CARD,
          Z1_PCM_CAPTURE_DEV, cfg.rate, cfg.channels);
    return 0;
}

static ssize_t in_read(struct audio_stream_in *stream, void *buffer, size_t bytes)
{
    struct z1_stream_in *in = (struct z1_stream_in *)stream;
    int rc;

    if (buffer == NULL || bytes == 0 || in->frame_bytes == 0 ||
        bytes % in->frame_bytes != 0)
        return -EINVAL;

    pthread_mutex_lock(&in->lock);
    rc = pcm_input_ensure_open(in);
    if (rc == 0) {
        rc = pcm_read(in->pcm, buffer, bytes);
        if (rc != 0) {
            ALOGE("pcm_read: %s", pcm_get_error(in->pcm));
            pcm_close(in->pcm);
            in->pcm = NULL;
            rc = -EIO;
        } else {
            in->frames_read += bytes / in->frame_bytes;
            /* Drain the PCM read above so mute does not become a busy loop.
             * Taking the device lock also serializes a concurrent mute request:
             * once set_mic_mute() returns, in-flight reads cannot leak samples. */
            pthread_mutex_lock(&in->adev->lock);
            if (in->adev->mic_mute)
                memset(buffer, 0, bytes);
            pthread_mutex_unlock(&in->adev->lock);
        }
    }
    pthread_mutex_unlock(&in->lock);

    if (rc != 0) {
        uint64_t bytes_per_sec = (uint64_t)in->rate * in->frame_bytes;
        uint64_t us = bytes_per_sec ? (bytes * 1000000ull) / bytes_per_sec : 0;
        if (us > UINT32_MAX)
            us = UINT32_MAX;
        usleep((useconds_t)us);
        return rc;
    }
    return (ssize_t)bytes;
}

static uint32_t in_get_input_frames_lost(struct audio_stream_in *stream)
{
    /* tinyalsa exposes open/read failures, not an exact lost-frame counter. */
    (void)stream;
    return 0;
}

static int in_get_capture_position(const struct audio_stream_in *stream,
                                   int64_t *frames, int64_t *time)
{
    (void)stream;
    if (frames)
        *frames = 0;
    if (time)
        *time = 0;
    return -ENOSYS;
}

static int in_set_gain(struct audio_stream_in *stream, float gain)
{ (void)stream; (void)gain; return -ENOTSUP; }

static int in_start(const struct audio_stream_in *stream)
{ (void)stream; return -ENOSYS; }

static int in_stop(const struct audio_stream_in *stream)
{ (void)stream; return -ENOSYS; }

/* ======================== device functions ======================= */

static int adev_init_check(const struct audio_hw_device *dev) { (void)dev; return 0; }

static int adev_set_voice_volume(struct audio_hw_device *dev, float v) { (void)dev; (void)v; return 0; }
/* No hardware master gain. AOSP9 AudioFlinger.cpp sets
 * AHWD_CAN_SET_MASTER_VOLUME only on success; Threads.cpp otherwise retains
 * software master gain. Do not report a fabricated initial gain either. */
static int adev_set_master_volume(struct audio_hw_device *dev, float v)
{ (void)dev; (void)v; return -ENOTSUP; }
static int adev_get_master_volume(struct audio_hw_device *dev, float *v)
{ (void)dev; (void)v; return -ENOTSUP; }
static int adev_set_mode(struct audio_hw_device *dev, audio_mode_t m)
{ ((struct z1_audio_device *)dev)->in_call = (m == AUDIO_MODE_IN_CALL); return 0; }
static int adev_set_mic_mute(struct audio_hw_device *dev, bool s)
{
    struct z1_audio_device *adev = (struct z1_audio_device *)dev;

    if (adev == NULL)
        return -EINVAL;
    pthread_mutex_lock(&adev->lock);
    adev->mic_mute = s;
    pthread_mutex_unlock(&adev->lock);
    return 0;
}

static int adev_get_mic_mute(const struct audio_hw_device *dev, bool *s)
{
    struct z1_audio_device *adev = (struct z1_audio_device *)dev;

    if (adev == NULL || s == NULL)
        return -EINVAL;
    pthread_mutex_lock(&adev->lock);
    *s = adev->mic_mute;
    pthread_mutex_unlock(&adev->lock);
    return 0;
}

static int adev_set_parameters(struct audio_hw_device *dev, const char *kv)
{
    if (parameter(kv, "z1_speaker_high_volume"))
        refresh_speaker_mode((struct z1_audio_device *)dev);
    return 0;
}
static char *adev_get_parameters(const struct audio_hw_device *dev, const char *keys)
{ (void)dev; (void)keys; return strdup(""); }

static size_t adev_get_input_buffer_size(const struct audio_hw_device *dev,
                                        const struct audio_config *cfg)
{
    uint32_t channels;
    (void)dev;
    if (cfg == NULL || cfg->format != AUDIO_FORMAT_PCM_16_BIT)
        return 0;
    channels = popcount_mask(cfg->channel_mask) == 1 ? 1 : Z1_DEFAULT_CHANNELS;
    return channels * sizeof(int16_t) * Z1_DEFAULT_PERIOD;
}

static int adev_open_output_stream(struct audio_hw_device *dev,
                                  audio_io_handle_t handle, audio_devices_t devices,
                                  audio_output_flags_t flags, struct audio_config *config,
                                  struct audio_stream_out **stream_out, const char *address)
{
    struct z1_stream_out *o;
    (void)handle; (void)flags; (void)address;
    ALOGV("open_output_stream devices=0x%x", devices);
    o = (struct z1_stream_out *)calloc(1, sizeof(*o));
    if (!o) return -ENOMEM;
    /* [必改2] 归一化并回写 *config: AudioFlinger 拿 HAL 返回的 config 建流,
     * 采样率/通道不一致时自动建重采样器; 绝不透传支持集之外的率。
     * (本函数不碰 /dev/snd —— 懒打开时序纪律, 见文件头。) */
    o->rate     = z1_normalize_rate(config->sample_rate);
    o->channels = (popcount_mask(config->channel_mask) == 1) ? 1 : Z1_DEFAULT_CHANNELS;
    o->frame_bytes = o->channels * sizeof(int16_t);
    o->device   = devices;
    o->adev = (struct z1_audio_device *)dev;
    refresh_speaker_mode(o->adev);
    config->format       = AUDIO_FORMAT_PCM_16_BIT;
    config->sample_rate  = o->rate;
    config->channel_mask = (o->channels == 1) ? AUDIO_CHANNEL_OUT_MONO : AUDIO_CHANNEL_OUT_STEREO;
    pthread_mutex_init(&o->lock, NULL);

    o->stream.common.get_sample_rate   = out_get_sample_rate;
    o->stream.common.set_sample_rate   = out_set_sample_rate;
    o->stream.common.get_buffer_size   = out_get_buffer_size;
    o->stream.common.get_channels      = out_get_channels;
    o->stream.common.get_format        = out_get_format;
    o->stream.common.set_format        = out_set_format;
    o->stream.common.standby           = out_standby;
    o->stream.common.dump              = out_dump;
    o->stream.common.get_device        = out_get_device;
    o->stream.common.set_device        = out_set_device;
    o->stream.common.set_parameters    = out_set_parameters;
    o->stream.common.get_parameters    = out_get_parameters;
    o->stream.common.add_audio_effect  = out_add_audio_effect;
    o->stream.common.remove_audio_effect = out_remove_audio_effect;
    o->stream.get_latency              = out_get_latency;
    o->stream.set_volume               = out_set_volume;
    o->stream.write                    = out_write;
    o->stream.get_render_position      = out_get_render_position;
    o->stream.get_next_write_timestamp = out_get_next_write_timestamp;
    o->stream.set_callback             = out_set_callback;
    o->stream.pause                    = out_pause;
    o->stream.resume                   = out_resume;
    o->stream.drain                    = out_drain;
    o->stream.flush                    = out_flush;
    o->stream.get_presentation_position = out_get_presentation_position;
    *stream_out = &o->stream;
    return 0;
}

static void adev_close_output_stream(struct audio_hw_device *dev, struct audio_stream_out *s)
{
    struct z1_stream_out *o = (struct z1_stream_out *)s;
    (void)dev;
    if (!o) return;
    out_standby(&o->stream.common);
    pthread_mutex_destroy(&o->lock);
    free(o->scaled);
    free(o);
}

static int adev_open_input_stream(struct audio_hw_device *dev, audio_io_handle_t h,
                                 audio_devices_t d, struct audio_config *cfg,
                                 struct audio_stream_in **si, audio_input_flags_t f,
                                 const char *addr, audio_source_t src)
{
    struct z1_stream_in *in;
    (void)dev;
    (void)h;
    (void)f;
    (void)addr;
    if (cfg == NULL || si == NULL || d != AUDIO_DEVICE_IN_BUILTIN_MIC ||
        src != AUDIO_SOURCE_MIC)
        return -EINVAL;
    if (cfg->format != AUDIO_FORMAT_PCM_16_BIT)
        return -EINVAL;

    in = (struct z1_stream_in *)calloc(1, sizeof(*in));
    if (in == NULL)
        return -ENOMEM;
    in->adev = (struct z1_audio_device *)dev;
    in->rate = z1_normalize_input_rate(cfg->sample_rate);
    in->channels = popcount_mask(cfg->channel_mask) == 1 ? 1 : Z1_DEFAULT_CHANNELS;
    in->frame_bytes = in->channels * sizeof(int16_t);
    in->device = d;
    cfg->sample_rate = in->rate;
    cfg->channel_mask = in->channels == 1 ?
        AUDIO_CHANNEL_IN_MONO : AUDIO_CHANNEL_IN_STEREO;
    cfg->format = AUDIO_FORMAT_PCM_16_BIT;
    pthread_mutex_init(&in->lock, NULL);

    in->stream.common.get_sample_rate = in_get_sample_rate;
    in->stream.common.set_sample_rate = in_set_sample_rate;
    in->stream.common.get_buffer_size = in_get_buffer_size;
    in->stream.common.get_channels = in_get_channels;
    in->stream.common.get_format = in_get_format;
    in->stream.common.set_format = in_set_format;
    in->stream.common.standby = in_standby;
    in->stream.common.dump = in_dump;
    in->stream.common.get_device = in_get_device;
    in->stream.common.set_device = in_set_device;
    in->stream.common.set_parameters = in_set_parameters;
    in->stream.common.get_parameters = in_get_parameters;
    in->stream.common.add_audio_effect = in_add_audio_effect;
    in->stream.common.remove_audio_effect = in_remove_audio_effect;
    in->stream.set_gain = in_set_gain;
    in->stream.read = in_read;
    in->stream.get_input_frames_lost = in_get_input_frames_lost;
    in->stream.get_capture_position = in_get_capture_position;
    in->stream.start = in_start;
    in->stream.stop = in_stop;
    *si = &in->stream;
    ALOGV("open_input_stream devices=0x%x rate=%u channels=%u",
          d, in->rate, in->channels);
    return 0;
}

static void adev_close_input_stream(struct audio_hw_device *dev, struct audio_stream_in *si)
{
    struct z1_stream_in *in = (struct z1_stream_in *)si;
    (void)dev;
    if (in == NULL)
        return;
    in_standby(&in->stream.common);
    pthread_mutex_destroy(&in->lock);
    free(in);
}

static int adev_dump(const audio_hw_device_t *dev, int fd) { (void)dev; (void)fd; return 0; }

static int adev_close(hw_device_t *dev)
{
    struct z1_audio_device *adev = (struct z1_audio_device *)dev;
    if (!adev) return 0;
    /* [可选5] 进程生命周期对象, 顺手对称销毁。 */
    pthread_mutex_destroy(&adev->lock);
    free(adev);
    return 0;
}

static int adev_open(const hw_module_t *module, const char *name, hw_device_t **device)
{
    struct z1_audio_device *adev;
    ALOGI("adev_open name=%s", name ? name : "(null)");
    if (name && strcmp(name, AUDIO_HARDWARE_INTERFACE) != 0 && strcmp(name, "primary") != 0)
        return -EINVAL;
    adev = (struct z1_audio_device *)calloc(1, sizeof(*adev));
    if (!adev) return -ENOMEM;
    adev->hw.common.tag     = HARDWARE_DEVICE_TAG;
    adev->hw.common.version = AUDIO_DEVICE_API_VERSION_2_0;   /* 0x0200 */
    adev->hw.common.module  = (hw_module_t *)module;
    adev->hw.common.close   = adev_close;
    adev->hw.get_supported_devices = NULL;   /* v2.0 => policy decides */
    adev->hw.init_check              = adev_init_check;
    adev->hw.set_voice_volume        = adev_set_voice_volume;
    adev->hw.set_master_volume       = adev_set_master_volume;
    adev->hw.get_master_volume       = adev_get_master_volume;
    adev->hw.set_mode                = adev_set_mode;
    adev->hw.set_mic_mute            = adev_set_mic_mute;
    adev->hw.get_mic_mute            = adev_get_mic_mute;
    adev->hw.set_parameters          = adev_set_parameters;
    adev->hw.get_parameters          = adev_get_parameters;
    adev->hw.get_input_buffer_size   = adev_get_input_buffer_size;
    adev->hw.open_output_stream      = adev_open_output_stream;
    adev->hw.close_output_stream     = adev_close_output_stream;
    adev->hw.open_input_stream       = adev_open_input_stream;
    adev->hw.close_input_stream      = adev_close_input_stream;
    adev->hw.get_microphones         = NULL;
    adev->hw.dump                    = adev_dump;
    adev->hw.set_master_mute         = NULL;
    adev->hw.get_master_mute         = NULL;
    adev->hw.create_audio_patch      = NULL;
    adev->hw.release_audio_patch     = NULL;
    adev->hw.get_audio_port          = NULL;
    adev->hw.set_audio_port_config   = NULL;
    pthread_mutex_init(&adev->lock, NULL);
    *device = &adev->hw.common;
    return 0;
}

static struct hw_module_methods_t z1_module_methods = { .open = adev_open };

struct audio_module HAL_MODULE_INFO_SYM = {
    .common = {
        .tag                = HARDWARE_MODULE_TAG,
        .module_api_version = AUDIO_MODULE_API_VERSION_0_1,   /* 0x0001 */
        .hal_api_version    = HARDWARE_HAL_API_VERSION,       /* 0x0100 */
        .id                 = AUDIO_HARDWARE_MODULE_ID,       /* "audio" — 判定值, 不改 [部署面6] */
        .name               = "Z1 (MT6572+MT6323) tinyalsa audio HAL",
        .author             = "z1-mt6572-mainline",
        .methods            = &z1_module_methods,
    },
};
