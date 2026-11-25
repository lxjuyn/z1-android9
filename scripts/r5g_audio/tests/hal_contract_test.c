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

struct pcm { int ready; int write_failed; };
static struct pcm mock_pcm;
static struct pcm_config last_config;
static unsigned int open_calls, close_calls, write_calls, read_calls, error_logs;
static unsigned int slept_calls;
static useconds_t last_sleep;
static unsigned int last_card, last_device, last_flags;
static int device_ready, return_null = 1, write_error, read_error, mock_errno;
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

static const char *mode_property = "1";
int __system_property_get(const char *key, char *value)
{
    CHECK(!strcmp(key, "persist.sys.z1.spk_high"), "persistent property key");
    strcpy(value, mode_property);
    return strlen(value);
}

int *__errno(void) { return &mock_errno; }

struct mixer { int card; };
struct mixer_ctl { int value; };
static struct mixer output_mixer;
static struct mixer_ctl playback_gain = {54321}, speaker_gain = {0};
struct mixer *mixer_open(unsigned int card) { return &output_mixer; }
void mixer_close(struct mixer *m) { (void)m; }
struct mixer_ctl *mixer_get_ctl_by_name(struct mixer *m, const char *name)
{
    if (!strcmp(name, "Playback Volume")) return &playback_gain;
    if (!strcmp(name, "Z1 Speaker Gain Switch")) return &speaker_gain;
    return NULL;
}
int mixer_ctl_get_value(struct mixer_ctl *ctl, unsigned int id) { return ctl->value; }
int mixer_ctl_set_value(struct mixer_ctl *ctl, unsigned int id, int value)
{
    ctl->value = value;
    return 0;
}

struct pcm *pcm_open(unsigned int card, unsigned int device,
                     unsigned int flags, struct pcm_config *config)
{
    open_calls++;
    last_card = card;
    last_device = device;
    last_flags = flags;
    last_config = *config;
    mock_pcm.ready = device_ready;
    mock_pcm.write_failed = 0;
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
        pcm->write_failed = 1;
    if (pcm->write_failed)
        return -EIO;
    if (count == sizeof(last_data))
        memcpy(last_data, data, count);
    return 0;
}
int pcm_read(struct pcm *pcm, void *data, unsigned int count)
{
    CHECK(pcm == &mock_pcm && pcm->ready, "read only from ready PCM");
    read_calls++;
    if (read_error)
        return -EIO;
    memset(data, 0x5a, count);
    return 0;
}
unsigned int pcm_get_buffer_size(struct pcm *pcm)
{
    CHECK(pcm == &mock_pcm && pcm->ready, "buffer size from ready PCM");
    return 4096;
}
int pcm_get_htimestamp(struct pcm *pcm, unsigned int *avail,
                       struct timespec *timestamp)
{
    CHECK(pcm == &mock_pcm && pcm->ready, "timestamp from ready PCM");
    *avail = 0;
    timestamp->tv_sec = 1;
    timestamp->tv_nsec = 2;
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

static int open_config(struct audio_hw_device *dev, audio_devices_t device,
                       uint32_t rate, audio_channel_mask_t channels,
                       audio_format_t format, struct audio_config *result,
                       struct audio_stream_out **out)
{
    struct audio_config config = {
        .sample_rate = rate,
        .channel_mask = channels,
        .format = format
    };
    *out = NULL;
    int rc = dev->open_output_stream(dev, 0, device, AUDIO_OUTPUT_FLAG_PRIMARY,
                                     &config, out, NULL);
    if (result)
        *result = config;
    return rc;
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
    CHECK(out->get_render_position(out, &frames) == -ENOSYS && frames == 0,
          "do not report queued frames as DAC presentation");
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
    device_ready = 1;
    const int16_t original[] = {-32768, -7, -2, -1, 0, 1, 7, 32767};
    const int16_t half[] = {-16384, -3, -1, 0, 0, 0, 3, 16383};
    mode_property = "";
    dev->set_parameters(dev, "z1_speaker_high_volume=0");
    out->write(out, original, sizeof(original));
    CHECK(playback_gain.value == 61440 && speaker_gain.value == 1, "stock speaker mixer gains");
    CHECK(!memcmp(last_data, half, sizeof(half)), "default speaker half amplitude");
    mode_property = "1";
    dev->set_parameters(dev, "z1_speaker_high_volume=1");
    out->write(out, original, sizeof(original));
    CHECK(!memcmp(last_data, original, sizeof(original)), "hot full-scale speaker");
    mode_property = "0";
    out->write(out, original, sizeof(original));
    CHECK(!memcmp(last_data, half, sizeof(half)),
          "active speaker follows persisted normal mode without a HAL callback");
    mode_property = "1";
    out->write(out, original, sizeof(original));
    CHECK(!memcmp(last_data, original, sizeof(original)),
          "active speaker follows persisted high mode without a HAL callback");
    mode_property = "0";
    out->common.set_parameters(&out->common, "z1_speaker_high_volume=0;routing=8");
    out->write(out, original, sizeof(original));
    CHECK(playback_gain.value == 54321 && speaker_gain.value == 0, "restore previous gain on headphones");
    CHECK(!memcmp(last_data, original, sizeof(original)), "headphones bypass speaker cap");
    out->common.set_parameters(&out->common, "routing=2");
    out->write(out, original, sizeof(original));
    CHECK(!memcmp(last_data, half, sizeof(half)), "route switch restores cap");
    CHECK(out->common.standby(&out->common) == 0, "active speaker enters standby");
    CHECK(playback_gain.value == 54321 && speaker_gain.value == 0,
          "standby restores previous playback gain and clears speaker PGA");
    CHECK(out->write(out, original, sizeof(original)) == (ssize_t)sizeof(original),
          "speaker resumes after standby");
    CHECK(playback_gain.value == 61440 && speaker_gain.value == 1 &&
          !memcmp(last_data, half, sizeof(half)),
          "speaker reapplies stock hardware gains and half-amplitude PCM");
    printf("volume callbacks checked at 0.0, 0.4, and 1.0; failures=%d\n", failures);
}
static void write_error_case(struct audio_stream_out *out)
{
    device_ready = 1;
    write_error = 1;
    CHECK(write_audio(out) == -EIO, "write error not reported as consumed bytes");
    uint32_t frames = 99;
    CHECK(out->get_render_position(out, &frames) == -ENOSYS && frames == 0,
          "failed write does not claim a presentation position");
    CHECK(close_calls == 1 && write_calls == 1 && slept_calls == 1,
          "failed handle closed and write paced");
    write_error = 0;
    for (int i = 0; i < 9; i++)
        CHECK(write_audio(out) == -ENODEV, "failed stream backs off before reopening");
    CHECK(open_calls == 1 && write_calls == 1 && slept_calls == 10,
          "no writes to the failed handle or tight reopen loop");
    CHECK(write_audio(out) == (ssize_t)sizeof(samples) && open_calls == 2 &&
          write_calls == 2, "fresh PCM recovers playback after backoff");
    CHECK(out->get_render_position(out, &frames) == -ENOSYS && frames == 0,
          "successful write still lacks a DAC presentation timestamp");
    printf("write error: final submitted frames=%u (mock only)\n", frames);
}

static void position_contract_case(struct audio_stream_out *out)
{
    uint32_t render_frames = 99;
    uint64_t presented_frames = 99;
    struct timespec timestamp = { .tv_sec = 99, .tv_nsec = 99 };

    CHECK(out->get_render_position(out, &render_frames) == -ENOSYS &&
          render_frames == 0,
          "render position is unsupported before any PCM write");
    CHECK(out->get_presentation_position(out, &presented_frames, &timestamp) == -ENOSYS &&
          presented_frames == 0 && timestamp.tv_sec == 0 && timestamp.tv_nsec == 0,
          "presentation position is unsupported before any PCM write");

    device_ready = 1;
    CHECK(write_audio(out) == (ssize_t)sizeof(samples), "position test submits audio");
    render_frames = 99;
    presented_frames = 99;
    timestamp.tv_sec = 99;
    timestamp.tv_nsec = 99;
    CHECK(out->get_render_position(out, &render_frames) == -ENOSYS &&
          render_frames == 0,
          "successful write is not misreported as DAC render position");
    CHECK(out->get_presentation_position(out, &presented_frames, &timestamp) == -ENOSYS &&
          presented_frames == 0 && timestamp.tv_sec == 0 && timestamp.tv_nsec == 0,
          "successful write does not fabricate a DAC timestamp");
    printf("position contract: render and presentation remain unsupported\n");
}

static void output_config_case(struct audio_hw_device *dev)
{
    const audio_devices_t supported[] = {
        AUDIO_DEVICE_OUT_SPEAKER,
        AUDIO_DEVICE_OUT_EARPIECE,
        AUDIO_DEVICE_OUT_WIRED_HEADSET,
        AUDIO_DEVICE_OUT_WIRED_HEADPHONE,
    };
    struct audio_stream_out *extra = NULL;
    struct audio_config actual;

    for (unsigned int i = 0; i < sizeof(supported) / sizeof(supported[0]); i++) {
        int rc = open_config(dev, supported[i], 48000, AUDIO_CHANNEL_OUT_STEREO,
                             AUDIO_FORMAT_PCM_16_BIT, &actual, &extra);
        CHECK(rc == 0 && extra != NULL, "policy-listed output device opens");
        CHECK(actual.sample_rate == 48000 &&
              actual.channel_mask == AUDIO_CHANNEL_OUT_STEREO &&
              actual.format == AUDIO_FORMAT_PCM_16_BIT,
              "supported 48k stereo PCM16 config is preserved");
        if (extra)
            dev->close_output_stream(dev, extra);
    }
    CHECK(open_calls == 0, "opening streams remains lazy");

    int rc = open_config(dev, AUDIO_DEVICE_OUT_SPEAKER, 44100,
                         AUDIO_CHANNEL_OUT_STEREO, AUDIO_FORMAT_PCM_FLOAT,
                         &actual, &extra);
    CHECK(rc == 0 && extra != NULL, "unsupported request is negotiated");
    CHECK(actual.sample_rate == 48000 &&
          actual.channel_mask == AUDIO_CHANNEL_OUT_STEREO &&
          actual.format == AUDIO_FORMAT_PCM_16_BIT,
          "stereo negotiation selects 48k PCM16");
    if (extra)
        dev->close_output_stream(dev, extra);

    rc = open_config(dev, AUDIO_DEVICE_OUT_SPEAKER, 16000,
                     AUDIO_CHANNEL_OUT_MONO, AUDIO_FORMAT_PCM_16_BIT,
                     &actual, &extra);
    CHECK(rc == 0 && extra != NULL, "mono output stream opens");
    CHECK(actual.sample_rate == 48000 &&
          actual.channel_mask == AUDIO_CHANNEL_OUT_MONO &&
          actual.format == AUDIO_FORMAT_PCM_16_BIT,
          "mono negotiation keeps one channel at 48k PCM16");
    if (extra)
        CHECK(extra->common.get_buffer_size(&extra->common) == 2048,
              "mono stream buffer is one 1024-frame PCM16 period");
    if (extra)
        dev->close_output_stream(dev, extra);
    printf("output config: policy devices and PCM16 negotiation checked\n");
}

static void stream_contract_case(struct audio_stream_out *out)
{
    CHECK(out->common.get_sample_rate(&out->common) == 48000,
          "stream reports the supported rate");
    CHECK(out->common.get_channels(&out->common) == AUDIO_CHANNEL_OUT_STEREO,
          "stream reports stereo");
    CHECK(out->common.get_format(&out->common) == AUDIO_FORMAT_PCM_16_BIT,
          "stream reports PCM16");
    CHECK(out->common.get_buffer_size(&out->common) == 4096,
          "stereo stream buffer is four 1024-frame periods");

    CHECK(out->common.set_sample_rate(&out->common, 44100) == 0,
          "unsupported rate request is accepted for negotiation");
    CHECK(out->common.get_sample_rate(&out->common) == 48000,
          "rate negotiation remains at 48k");
    CHECK(out->common.set_format(&out->common, AUDIO_FORMAT_PCM_FLOAT) == -EINVAL,
          "unsupported stream format is rejected");
    CHECK(out->common.get_format(&out->common) == AUDIO_FORMAT_PCM_16_BIT,
          "failed format change preserves PCM16");
    CHECK(open_calls == 0, "stream getters and negotiation do not open PCM");
    printf("stream contract: 48k stereo PCM16, lazy PCM access\n");
}

static void input_contract_case(struct audio_hw_device *dev)
{
    const uint32_t rates[] = {8000, 16000, 32000, 48000};
    const audio_channel_mask_t channels[] = {
        AUDIO_CHANNEL_IN_MONO, AUDIO_CHANNEL_IN_STEREO
    };
    struct audio_config config = {
        .sample_rate = 16000,
        .channel_mask = AUDIO_CHANNEL_IN_MONO,
        .format = AUDIO_FORMAT_PCM_16_BIT
    };
    struct audio_stream_in *input = NULL;
    unsigned char samples[1024];

    for (unsigned int r = 0; r < sizeof(rates) / sizeof(rates[0]); r++) {
        for (unsigned int c = 0;
             c < sizeof(channels) / sizeof(channels[0]); c++) {
            struct audio_config negotiated = {
                .sample_rate = rates[r],
                .channel_mask = channels[c],
                .format = AUDIO_FORMAT_PCM_16_BIT
            };
            struct audio_stream_in *probe = NULL;
            CHECK(dev->open_input_stream(
                      dev, 0, AUDIO_DEVICE_IN_BUILTIN_MIC, &negotiated,
                      &probe, AUDIO_INPUT_FLAG_NONE, NULL,
                      AUDIO_SOURCE_MIC) == 0 && probe != NULL,
                  "all policy microphone rates and channel masks open");
            CHECK(negotiated.sample_rate == rates[r] &&
                  negotiated.channel_mask == channels[c],
                  "supported microphone configuration is preserved");
            CHECK(dev->get_input_buffer_size(dev, &negotiated) ==
                  (channels[c] == AUDIO_CHANNEL_IN_MONO ? 2048u : 4096u),
                  "capture buffer matches channel count");
            dev->close_input_stream(dev, probe);
        }
    }
    CHECK(open_calls == 0 && close_calls == 0,
          "opening and closing idle capture streams never touches PCM");

    CHECK(dev->get_input_buffer_size(dev, &config) == 2048,
          "16k mono capture advertises one 1024-frame PCM16 period");
    int rc = dev->open_input_stream(dev, 0, AUDIO_DEVICE_IN_BUILTIN_MIC, &config,
                                    &input, AUDIO_INPUT_FLAG_NONE, NULL,
                                    AUDIO_SOURCE_MIC);
    CHECK(rc == 0 && input != NULL, "built-in microphone capture opens");
    CHECK(open_calls == 0, "opening capture remains lazy");
    CHECK(config.sample_rate == 16000 &&
          config.channel_mask == AUDIO_CHANNEL_IN_MONO &&
          config.format == AUDIO_FORMAT_PCM_16_BIT,
          "capture config remains 16k mono PCM16");
    CHECK(input->common.get_sample_rate(&input->common) == 16000 &&
          input->common.get_channels(&input->common) == AUDIO_CHANNEL_IN_MONO &&
          input->common.get_format(&input->common) == AUDIO_FORMAT_PCM_16_BIT &&
          input->common.get_buffer_size(&input->common) == 2048,
          "capture stream reports the negotiated format");

    device_ready = 0;
    return_null = 1;
    memset(samples, 0, sizeof(samples));
    CHECK(input->read(input, samples, sizeof(samples)) == -ENODEV,
          "missing capture device returns an error");
    CHECK(open_calls == 1 && read_calls == 0,
          "first capture read attempts one PCM open");
    device_ready = 1;
    for (int i = 0; i < 9; i++)
        CHECK(input->read(input, samples, sizeof(samples)) == -ENODEV,
              "capture backoff does not spin on pcm_open");
    CHECK(input->read(input, samples, sizeof(samples)) == sizeof(samples),
          "capture recovers when the AWB device appears");
    CHECK(open_calls == 2 && read_calls == 1,
          "capture opens once and reads once after recovery");
    CHECK(last_card == 0 && last_device == 1 && last_flags == PCM_IN,
          "capture targets pcmC0D1c");
    CHECK(samples[0] == 0x5a && samples[sizeof(samples) - 1] == 0x5a,
          "capture bytes reach the framework buffer");
    CHECK(input->get_input_frames_lost(input) == 0,
          "no fabricated lost-frame count");
    int64_t frames = 99, time = 99;
    CHECK(input->get_capture_position(input, &frames, &time) == -ENOSYS &&
          frames == 0 && time == 0,
          "capture position is unsupported rather than fabricated");
    CHECK(input->set_gain(input, 0.5f) == -ENOTSUP &&
          input->start(input) == -ENOSYS &&
          input->stop(input) == -ENOSYS,
          "unsupported capture extensions fail explicitly");
    dev->close_input_stream(dev, input);
    CHECK(close_calls == 1, "closing capture releases the PCM");
    printf("input contract: lazy pcmC0D1c capture, backoff, read and close\n");
}

static void mic_mute_case(struct audio_hw_device *dev)
{
    struct audio_config config = {
        .sample_rate = 16000,
        .channel_mask = AUDIO_CHANNEL_IN_MONO,
        .format = AUDIO_FORMAT_PCM_16_BIT
    };
    struct audio_stream_in *input = NULL;
    unsigned char samples[1024];
    bool muted = true;

    CHECK(dev->set_mic_mute(dev, false) == 0, "clear microphone mute");
    CHECK(dev->get_mic_mute(dev, &muted) == 0 && !muted,
          "microphone mute starts clear");
    CHECK(dev->get_mic_mute(dev, NULL) == -EINVAL,
          "microphone mute getter validates its output");
    CHECK(dev->open_input_stream(
              dev, 0, AUDIO_DEVICE_IN_BUILTIN_MIC, &config,
              &input, AUDIO_INPUT_FLAG_NONE, NULL,
              AUDIO_SOURCE_MIC) == 0 && input != NULL,
          "open microphone stream for mute test");

    device_ready = 1;
    return_null = 0;
    CHECK(dev->set_mic_mute(dev, true) == 0, "enable microphone mute");
    muted = false;
    CHECK(dev->get_mic_mute(dev, &muted) == 0 && muted,
          "microphone mute state is retained");
    memset(samples, 0xa5, sizeof(samples));
    CHECK(input->read(input, samples, sizeof(samples)) == sizeof(samples),
          "muted capture still drains a complete buffer");
    for (size_t i = 0; i < sizeof(samples); i++)
        CHECK(samples[i] == 0, "muted capture samples are all zero");

    CHECK(dev->set_mic_mute(dev, false) == 0, "disable microphone mute");
    memset(samples, 0, sizeof(samples));
    CHECK(input->read(input, samples, sizeof(samples)) == sizeof(samples),
          "unmuted capture returns a complete buffer");
    CHECK(samples[0] == 0x5a && samples[sizeof(samples) - 1] == 0x5a,
          "unmuted capture returns original samples");
    CHECK(read_calls == 2, "mute changes capture content without dropping reads");
    dev->close_input_stream(dev, input);
    printf("microphone mute: retained, drained and suppressed; unmute restores data\n");
}

static void module_identity_case(struct audio_hw_device *dev)
{
    CHECK(HAL_MODULE_INFO_SYM.common.tag == HARDWARE_MODULE_TAG,
          "module has the libhardware tag");
    CHECK(HAL_MODULE_INFO_SYM.common.module_api_version ==
          AUDIO_MODULE_API_VERSION_0_1 &&
          HAL_MODULE_INFO_SYM.common.hal_api_version == HARDWARE_HAL_API_VERSION,
          "module API versions match the legacy wrapper");
    CHECK(strcmp(HAL_MODULE_INFO_SYM.common.id, AUDIO_HARDWARE_MODULE_ID) == 0,
          "module id is the libhardware lookup value");
    CHECK(dev->common.tag == HARDWARE_DEVICE_TAG &&
          dev->common.version == AUDIO_DEVICE_API_VERSION_2_0 &&
          dev->common.module == &HAL_MODULE_INFO_SYM.common,
          "opened device carries the A9 v2.0 identity");
    CHECK(dev->get_supported_devices == NULL &&
          dev->get_microphones == NULL,
          "device enumeration remains owned by the policy");
    CHECK(dev->set_master_mute == NULL && dev->get_master_mute == NULL &&
          dev->create_audio_patch == NULL && dev->release_audio_patch == NULL &&
          dev->get_audio_port == NULL && dev->set_audio_port_config == NULL,
          "unsupported extension hooks are not advertised");
    printf("module identity: A9 legacy wrapper and policy-owned devices\n");
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
    else if (strcmp(argv[1], "position") == 0) position_contract_case(out);
    else if (strcmp(argv[1], "output-config") == 0) output_config_case(dev);
    else if (strcmp(argv[1], "stream-contract") == 0) stream_contract_case(out);
    else if (strcmp(argv[1], "input-contract") == 0) input_contract_case(dev);
    else if (strcmp(argv[1], "mic-mute") == 0) mic_mute_case(dev);
    else if (strcmp(argv[1], "module-identity") == 0) module_identity_case(dev);
    else { fprintf(stderr, "Unknown case: %s\n", argv[1]); failures++; }
    dev->close_output_stream(dev, out);
    dev->common.close(hw);
    printf("RESULT %s: %s (%d failed assertions)\n", argv[1], failures ? "FAIL" : "PASS", failures);
    return failures ? 1 : 0;
}
