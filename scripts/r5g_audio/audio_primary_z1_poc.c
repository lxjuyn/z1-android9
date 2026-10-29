/*
 * audio_primary_z1_poc.c — minimal A9 (SDK28) audio PRIMARY HAL for Z1 (MT6572 + MT6323)
 * Implementation: AOSP audio_hw_hal.cpp "legacy wrapper" layout, backed by tinyalsa.
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
 * NOT a complete HAL: playback path + stubs are real; capture/telephony is stubbed.
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
extern int *__errno(void);
#undef errno
#define errno (*__errno())

/* ---- tunables (verified against the image's dtb / .ko set) ---- */
#define Z1_PCM_CARD   0
#define Z1_PCM_DEV    0
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

struct z1_stream_out {
    struct audio_stream_out stream;
    pthread_mutex_t lock;
    struct pcm     *pcm;
    uint32_t        rate;
    uint32_t        channels;
    size_t          frame_bytes;
    audio_devices_t device;
    uint64_t        frames_written;   /* 累计写入帧数 (get_presentation_position) */
    uint32_t        open_fails;       /* pcm_open 连续失败计数 (退避 + 日志限流) */
};

struct z1_audio_device {
    struct audio_hw_device hw;
    pthread_mutex_t        lock;
    int                    in_call;
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
_Static_assert(offsetof(struct audio_stream_out, get_presentation_position) == 96,
               "get_presentation_position 必须在偏移 96 (wrapper 实调槽)");

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
  pthread_mutex_unlock(&o->lock);
  return 0; }

static int out_dump(const struct audio_stream *s, int fd) { (void)s; (void)fd; return 0; }

static audio_devices_t out_get_device(const struct audio_stream *s)
{ return ((const struct z1_stream_out *)s)->device; }

static int out_set_device(struct audio_stream *s, audio_devices_t d)
{ ((struct z1_stream_out *)s)->device = d; return 0; }

static int out_set_parameters(struct audio_stream *s, const char *kv)
{ (void)s; (void)kv; return 0; }

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

    /* 退避: 连续失败期间每 Z1_OPEN_RETRY_EVERY 次 write 才真正试 1 次 pcm_open。
     * 首次失败 (open_fails==0) 必真实尝试; 之后 9 次 write 直接返回, 第 10 次再试。
     * pcm_open 对缺失节点立即失败, 此路径无 sleep, 不阻塞 AudioFlinger。 */
    if (o->open_fails != 0 && (o->open_fails % Z1_OPEN_RETRY_EVERY) != 0)
        return -ENODEV;

    memset(&cfg, 0, sizeof(cfg));
    cfg.channels     = o->channels;
    cfg.rate         = o->rate;
    cfg.period_size  = Z1_DEFAULT_PERIOD;
    cfg.period_count = 4;
    cfg.format       = PCM_FORMAT_S16_LE;
    cfg.start_threshold = Z1_DEFAULT_PERIOD;
    cfg.stop_threshold  = Z1_DEFAULT_PERIOD * 4;
    o->frame_bytes = cfg.channels * sizeof(uint16_t);
    o->pcm = pcm_open(Z1_PCM_CARD, Z1_PCM_DEV, PCM_OUT, &cfg);
    if (!o->pcm || !pcm_is_ready(o->pcm)) {
        o->open_fails++;
        if (o->open_fails == 1 || (o->open_fails % Z1_OPEN_LOG_EVERY) == 0)
            ALOGE("pcm_open(card%d,dev%d) failed (consecutive=%u): %s", Z1_PCM_CARD,
                  Z1_PCM_DEV, o->open_fails,
                  o->pcm ? pcm_get_error(o->pcm) : strerror(errno));
        if (o->pcm) { pcm_close(o->pcm); o->pcm = NULL; }
        return -ENODEV;
    }
    o->open_fails = 0;
    ALOGI("pcm_open ok card%d dev%d %uHz %uch", Z1_PCM_CARD, Z1_PCM_DEV,
          cfg.rate, cfg.channels);
    return 0;
}

static ssize_t out_write(struct audio_stream_out *stream, const void *buffer, size_t bytes)
{
    struct z1_stream_out *o = (struct z1_stream_out *)stream;
    int rc;
    pthread_mutex_lock(&o->lock);
    rc = pcm_ensure_open(o);
    if (rc == 0) {
        /* [必改1] tinyalsa pcm_write 为全量阻塞写 (内部把整个 buffer 写完),
         * 单次调用即可。r5d 版 "done += bytes - done" 恒等于 done=bytes,
         * 循环体实际只跑一次 —— 伪循环, 换部分写接口时会静默丢数据, 已删。 */
        if (pcm_write(o->pcm, buffer, bytes) != 0) {
            ALOGE("pcm_write: %s", pcm_get_error(o->pcm));
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

static int out_set_volume(struct audio_stream_out *s, float l, float r)
{ (void)s; (void)l; (void)r; return 0; }

static int out_get_render_position(const struct audio_stream_out *s, uint32_t *f)
{
    /* 原版恒 0; 有写帧计数后返回累计帧数 (简单 HAL 惯例, 优于常量 0)。 */
    if (f)
        *f = (uint32_t)((const struct z1_stream_out *)s)->frames_written;
    return 0;
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
    const struct z1_stream_out *o = (const struct z1_stream_out *)s;
    if (!frames || !timestamp)
        return -EINVAL;
    *frames = o->frames_written;
    if (clock_gettime(CLOCK_MONOTONIC, timestamp) != 0)
        return -errno;
    return 0;
}

/* ======================== device functions ======================= */

static int adev_init_check(const struct audio_hw_device *dev) { (void)dev; return 0; }

static int adev_set_voice_volume(struct audio_hw_device *dev, float v) { (void)dev; (void)v; return 0; }
static int adev_set_master_volume(struct audio_hw_device *dev, float v) { (void)dev; (void)v; return 0; }
static int adev_get_master_volume(struct audio_hw_device *dev, float *v) { (void)dev; if (v) *v = 1.0f; return 0; }
static int adev_set_mode(struct audio_hw_device *dev, audio_mode_t m)
{ ((struct z1_audio_device *)dev)->in_call = (m == AUDIO_MODE_IN_CALL); return 0; }
static int adev_set_mic_mute(struct audio_hw_device *dev, bool s) { (void)dev; (void)s; return 0; }
static int adev_get_mic_mute(const struct audio_hw_device *dev, bool *s) { (void)dev; if (s) *s = false; return 0; }

static int adev_set_parameters(struct audio_hw_device *dev, const char *kv)
{ (void)dev; (void)kv; return 0; }
static char *adev_get_parameters(const struct audio_hw_device *dev, const char *keys)
{ (void)dev; (void)keys; return strdup(""); }

static size_t adev_get_input_buffer_size(const struct audio_hw_device *dev,
                                        const struct audio_config *cfg)
{ (void)dev;
  size_t ch = popcount_mask(cfg->channel_mask) ? popcount_mask(cfg->channel_mask) : 2;
  return ch * sizeof(int16_t) * Z1_DEFAULT_PERIOD; }

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
    free(o);
}

static int adev_open_input_stream(struct audio_hw_device *dev, audio_io_handle_t h,
                                 audio_devices_t d, struct audio_config *cfg,
                                 struct audio_stream_in **si, audio_input_flags_t f,
                                 const char *addr, audio_source_t src)
{ (void)dev;(void)h;(void)d;(void)cfg;(void)f;(void)addr;(void)src; *si = NULL; return -ENOSYS; }

static void adev_close_input_stream(struct audio_hw_device *dev, struct audio_stream_in *si)
{ (void)dev; (void)si; }

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
