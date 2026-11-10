/* ARM32 host regression for the real HAL translation unit.
 * tinyalsa/log/sleep are mocked; no sound device or Android service is used.
 * AOSP android-9.0.0_r61 references (cached in the audit evidence directory):
 * AudioFlinger.cpp:1820-1855, Threads.cpp:2243-2252 and 4616-4630.
 * This tests the return-value contract, not audible gain or Android integration.
 */
#include <errno.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <android/log.h>
#include <hardware/audio.h>
#include <tinyalsa/asoundlib.h>

extern struct audio_module HAL_MODULE_INFO_SYM;
int *__errno(void);

struct pcm { int ready; };
static struct pcm mock_pcm;
static struct pcm_config last_config;
static unsigned int open_calls, close_calls, write_calls, error_logs;
static unsigned int slept_calls;
static useconds_t last_sleep;
static unsigned int last_card, last_device, last_flags;
static int device_ready, return_null = 1, write_error, mock_errno;
static int failures;
static unsigned char last_data[16];
static const unsigned char samples[16] = {
    1, 0, 2, 0, 3, 0, 4, 0, 5, 0, 6, 0, 7, 0, 8, 0
};

#define CHECK(condition, message) do { \
    if (!(condition)) { \
        fprintf(stderr, "FAIL line %d: %s\n", __LINE__, message); \
        failures++; \
    } \
} while (0)

int *__errno(void) { return &mock_errno; }

struct pcm *pcm_open(unsigned int card, unsigned int device,
                     unsigned int flags, struct pcm_config *config)
{
    open_calls++;
    last_card = card;
    last_device = device;
    last_flags = flags;
    last_config = *config;
    mock_pcm.ready = device_ready;
    mock_errno = device_ready ? 0 : ENOENT;
    return !device_ready && return_null ? NULL : &mock_pcm;
}

int pcm_is_ready(struct pcm *pcm) { return pcm && pcm->ready; }
int pcm_close(struct pcm *pcm)
{
    CHECK(pcm == &mock_pcm, "close valid mock handle");
    close_calls++;
    return 0;
}
const char *pcm_get_error(struct pcm *pcm)
{
    (void)pcm;
    return "mock PCM unavailable";
}
int pcm_write(struct pcm *pcm, const void *data, unsigned int count)
{
    CHECK(pcm == &mock_pcm && pcm->ready, "write only to ready PCM");
    CHECK(count == sizeof(samples), "PCM byte count preserved");
    write_calls++;
    if (write_error)
        return -EIO;
    if (count == sizeof(last_data))
        memcpy(last_data, data, count);
    return 0;
}
int __android_log_print(int priority, const char *tag, const char *format, ...)
{
    (void)tag;
    (void)format;
    if (priority == ANDROID_LOG_ERROR)
        error_logs++;
    return 0;
}
int usleep(useconds_t usec)
{
    slept_calls++;
    last_sleep = usec;
    return 0;
}

static struct audio_stream_out *open_stream(struct audio_hw_device *dev)
{
    struct audio_config config = {
        .sample_rate = 48000,
        .channel_mask = AUDIO_CHANNEL_OUT_STEREO,
        .format = AUDIO_FORMAT_PCM_16_BIT
    };
    struct audio_stream_out *out = NULL;
    unsigned int before = open_calls;
    int rc = dev->open_output_stream(dev, 0, AUDIO_DEVICE_OUT_SPEAKER,
                                     AUDIO_OUTPUT_FLAG_PRIMARY, &config, &out, NULL);
    CHECK(rc == 0 && out != NULL, "open output stream");
    CHECK(open_calls == before, "opening stream must not access PCM");
    if (rc != 0 || out == NULL)
        return NULL;
    CHECK(config.sample_rate == 48000 && config.channel_mask == AUDIO_CHANNEL_OUT_STEREO
          && config.format == AUDIO_FORMAT_PCM_16_BIT, "stereo 48k PCM16 preserved");
    return out;
}

static ssize_t write_audio(struct audio_stream_out *out)
{
    return out->write(out, samples, sizeof(samples));
}
static void check_pcm_config(void)
{
    CHECK(last_card == 0 && last_device == 0 && last_flags == PCM_OUT,
          "card0 device0 playback");
    CHECK(last_config.rate == 48000 && last_config.channels == 2
          && last_config.format == PCM_FORMAT_S16_LE, "PCM configuration");
    CHECK(last_config.period_size == 1024 && last_config.period_count == 4,
          "period configuration");
}
static void retry_case(struct audio_stream_out *out, int null_handle)
{
    return_null = null_handle;
    CHECK(write_audio(out) == -ENODEV, "first missing-device write fails");
    CHECK(open_calls == 1 && write_calls == 0, "one actual failed open");
    CHECK(close_calls == (null_handle ? 0u : 1u), "close non-ready handle only");
    device_ready = 1;
    for (int i = 0; i < 9; i++) {
        CHECK(write_audio(out) == -ENODEV, "nine writes back off");
        CHECK(open_calls == 1, "no early open in backoff");
    }
    CHECK(write_audio(out) == (ssize_t)sizeof(samples), "tenth subsequent write recovers");
    CHECK(open_calls == 2 && write_calls == 1, "retry reaches tinyalsa write");
    CHECK(memcmp(last_data, samples, sizeof(samples)) == 0, "audio bytes preserved");
    CHECK(slept_calls == 10 && last_sleep == 83, "failed writes retain pacing");
    check_pcm_config();
    for (int i = 0; i < 4; i++)
        CHECK(write_audio(out) == (ssize_t)sizeof(samples), "keep writing on ready handle");
    CHECK(open_calls == 2, "ready handle reused");
    uint32_t frames = 0;
    CHECK(out->get_render_position(out, &frames) == 0 && frames == 20,
          "count only successfully submitted frames");
    printf("retry %s: observed opens=%u writes=%u\n",
           null_handle ? "NULL" : "non-ready", open_calls, write_calls);
}
static void long_retry_case(struct audio_stream_out *out)
{
    return_null = 0;
    for (int i = 0; i < 640; i++)
        CHECK(write_audio(out) == -ENODEV, "unavailable PCM remains an error");
    CHECK(open_calls == 64 && close_calls == 64 && write_calls == 0,
          "sustained failures retry exactly once per ten writes");
    CHECK(error_logs == 3, "log actual failed opens 1, 32 and 64 only");
    device_ready = 1;
    CHECK(write_audio(out) == (ssize_t)sizeof(samples), "no terminal failure latch");
    CHECK(open_calls == 65 && write_calls == 1, "late device recovery");
    printf("sustained outage: opens=%u closes=%u writes=%u error_logs=%u\n",
           open_calls, close_calls, write_calls, error_logs);
}
static void lifecycle_case(struct audio_stream_out *out)
{
    CHECK(write_audio(out) == -ENODEV, "enter backoff");
    CHECK(out->common.standby(&out->common) == 0, "standby during backoff");
    device_ready = 1;
    CHECK(write_audio(out) == (ssize_t)sizeof(samples), "new session retries immediately");
    CHECK(open_calls == 2, "standby clears stale backoff");
    CHECK(out->common.standby(&out->common) == 0 && close_calls == 1,
          "standby closes live PCM");
    CHECK(out->common.standby(&out->common) == 0 && close_calls == 1,
          "standby does not double close");
    device_ready = 0;
    CHECK(write_audio(out) == -ENODEV, "restarted session can fail");
    device_ready = 1;
    for (int i = 0; i < 9; i++)
        CHECK(write_audio(out) == -ENODEV, "restarted session backs off");
    CHECK(write_audio(out) == (ssize_t)sizeof(samples) && open_calls == 4,
          "restarted session recovers");
    printf("standby: opens=%u closes=%u writes=%u\n",
           open_calls, close_calls, write_calls);
}
static void volume_case(struct audio_hw_device *dev, struct audio_stream_out *out)
{
    const float levels[] = {0.0f, 0.4f, 1.0f};
    for (unsigned int i = 0; i < sizeof(levels)/sizeof(levels[0]); i++) {
        int rc = dev->set_master_volume(dev, levels[i]);
        CHECK(rc == -ENOTSUP, "no false hardware master-volume support");
        /* Model only AudioFlinger's capability branch, not the audio engine. */
        float mixer_master = rc == 0 ? 1.0f : levels[i];
        CHECK(mixer_master == levels[i], "software master gain remains eligible");
        CHECK(out->set_volume(out, levels[i], levels[i]) == -ENOTSUP,
              "no false hardware stream-volume support");
    }
    float initial = -7.0f;
    CHECK(dev->get_master_volume(dev, &initial) == -ENOTSUP && initial == -7.0f,
          "do not seed framework with fabricated hardware gain");
    CHECK(open_calls == 0 && write_calls == 0, "volume callbacks do not touch PCM");
    printf("volume callbacks checked at 0.0, 0.4, and 1.0; failures=%d\n", failures);
}
static void write_error_case(struct audio_stream_out *out)
{
    device_ready = 1;
    write_error = 1;
    CHECK(write_audio(out) == -EIO, "write error not reported as consumed bytes");
    uint32_t frames = 99;
    CHECK(out->get_render_position(out, &frames) == 0 && frames == 0,
          "failed write adds no frames");
    write_error = 0;
    CHECK(write_audio(out) == (ssize_t)sizeof(samples), "next successful write accepted");
    CHECK(out->get_render_position(out, &frames) == 0 && frames == 4,
          "successful write adds frames");
    printf("write error: final submitted frames=%u (mock only)\n", frames);
}

int main(int argc, char **argv)
{
    if (argc != 2)
        return 2;
    hw_device_t *hw = NULL;
    int rc = HAL_MODULE_INFO_SYM.common.methods->open(
        &HAL_MODULE_INFO_SYM.common, AUDIO_HARDWARE_INTERFACE, &hw);
    CHECK(rc == 0 && hw, "open HAL device");
    if (rc != 0 || !hw)
        return 1;
    struct audio_hw_device *dev = (struct audio_hw_device *)hw;
    struct audio_stream_out *out = open_stream(dev);
    if (!out) {
        dev->common.close(hw);
        return 1;
    }
    if (strcmp(argv[1], "retry-null") == 0) retry_case(out, 1);
    else if (strcmp(argv[1], "retry-unready") == 0) retry_case(out, 0);
    else if (strcmp(argv[1], "retry-long") == 0) long_retry_case(out);
    else if (strcmp(argv[1], "lifecycle") == 0) lifecycle_case(out);
    else if (strcmp(argv[1], "volume") == 0) volume_case(dev, out);
    else if (strcmp(argv[1], "write-error") == 0) write_error_case(out);
    else { fprintf(stderr, "Unknown case: %s\n", argv[1]); failures++; }
    dev->close_output_stream(dev, out);
    dev->common.close(hw);
    printf("RESULT %s: %s (%d failed assertions)\n", argv[1], failures ? "FAIL" : "PASS", failures);
    return failures ? 1 : 0;
}
