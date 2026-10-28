/*
 * audio_primary_z1_poc.c — minimal A9 (SDK28) audio PRIMARY HAL for Z1 (MT6572 + MT6323)
 * Implementation: AOSP audio_hw_hal.cpp "legacy wrapper" layout, backed by tinyalsa.
 *
 * Purpose: prove the alternative path (no MTK legacy /dev/eac) is buildable and matches
 * the A9 audio_hw_device_t the image's AudioFlinger / android.hardware.audio@2.0-impl expects.
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

/* ---- tunables (verified against the image's dtb / .ko set) ---- */
#define Z1_PCM_CARD   0
#define Z1_PCM_DEV    0
#define Z1_DEFAULT_RATE      48000
#define Z1_DEFAULT_CHANNELS  2
#define Z1_DEFAULT_PERIOD    1024

struct z1_stream_out {
    struct audio_stream_out stream;
    pthread_mutex_t lock;
    struct pcm     *pcm;
    uint32_t        rate;
    uint32_t        channels;
    size_t          frame_bytes;
    audio_devices_t device;
};

struct z1_audio_device {
    struct audio_hw_device hw;
    pthread_mutex_t        lock;
    int                    in_call;
};

/* ============================ helpers ============================ */

static uint32_t popcount_mask(audio_channel_mask_t m) { return (uint32_t)__builtin_popcount(m); }

/* --------------------------- stream out -------------------------- */

static uint32_t out_get_sample_rate(const struct audio_stream *s)
{ return ((const struct z1_stream_out *)s)->rate; }

static int out_set_sample_rate(struct audio_stream *s, uint32_t rate)
{ ((struct z1_stream_out *)s)->rate = rate; return 0; }

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

static int pcm_ensure_open(struct z1_stream_out *o)
{
    struct pcm_config cfg;
    if (o->pcm) return 0;
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
        ALOGE("pcm_open(card%d,dev%d) failed: %s", Z1_PCM_CARD, Z1_PCM_DEV,
              o->pcm ? pcm_get_error(o->pcm) : strerror(errno));
        if (o->pcm) { pcm_close(o->pcm); o->pcm = NULL; }
        return -ENODEV;
    }
    ALOGI("pcm_open ok card%d dev%d %uHz %uch %s", Z1_PCM_CARD, Z1_PCM_DEV,
          cfg.rate, cfg.channels, pcm_get_error(o->pcm));
    return 0;
}

static ssize_t out_write(struct audio_stream_out *stream, const void *buffer, size_t bytes)
{
    struct z1_stream_out *o = (struct z1_stream_out *)stream;
    int rc;
    pthread_mutex_lock(&o->lock);
    rc = pcm_ensure_open(o);
    if (rc == 0) {
        size_t done = 0;
        while (done < bytes) {
            int r = pcm_write(o->pcm, (const char *)buffer + done, bytes - done);
            if (r != 0) { ALOGE("pcm_write: %s", pcm_get_error(o->pcm)); rc = -EIO; break; }
            done += bytes - done;
        }
    }
    pthread_mutex_unlock(&o->lock);
    if (rc != 0)
        usleep((useconds_t)((bytes * 1000000ull) / (o->rate * o->frame_bytes ? o->rate * o->frame_bytes : 1)));
    return rc == 0 ? (ssize_t)bytes : (ssize_t)rc;
}

static uint32_t out_get_latency(const struct audio_stream_out *s)
{ (void)s; return 20; }

static int out_set_volume(struct audio_stream_out *s, float l, float r)
{ (void)s; (void)l; (void)r; return 0; }

static int out_get_render_position(const struct audio_stream_out *s, uint32_t *f)
{ (void)s; if (f) *f = 0; return 0; }

static int out_get_next_write_timestamp(const struct audio_stream_out *s, int64_t *t)
{ (void)s; if (t) *t = 0; return 0; }

static int out_set_callback(struct audio_stream_out *s, stream_callback_t cb, void *c)
{ (void)s; (void)cb; (void)c; return 0; }

static int out_pause(struct audio_stream_out *s)  { (void)s; return 0; }
static int out_resume(struct audio_stream_out *s) { (void)s; return 0; }
static int out_drain(struct audio_stream_out *s, audio_drain_type_t t) { (void)s; (void)t; return 0; }
static int out_flush(struct audio_stream_out *s) { (void)s; return 0; }

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
    o->rate     = config->sample_rate ? config->sample_rate : Z1_DEFAULT_RATE;
    o->channels = popcount_mask(config->channel_mask) ? popcount_mask(config->channel_mask) : Z1_DEFAULT_CHANNELS;
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
    if (dev) free(dev);
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
        .id                 = AUDIO_HARDWARE_MODULE_ID,       /* "audio" */
        .name               = "Z1 (MT6572+MT6323) tinyalsa audio HAL",
        .author             = "z1-mt6572-mainline",
        .methods            = &z1_module_methods,
    },
};
