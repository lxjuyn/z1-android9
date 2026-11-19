#include <dlfcn.h>
#include <stdio.h>
#include <string.h>

#include <hardware/audio.h>
#include <hardware/hardware.h>

int main(int argc, char **argv) {
    const char *path = argc > 1 ? argv[1] : "/vendor/lib/hw/audio.primary.default.so";
    void *handle = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (handle == NULL) {
        fprintf(stderr, "dlopen %s: %s\n", path, dlerror());
        return 10;
    }

    struct audio_module *module =
            (struct audio_module *)dlsym(handle, "HMI");
    if (module == NULL) {
        fprintf(stderr, "HMI export missing\n");
        return 11;
    }
    if (strcmp(module->common.id, AUDIO_HARDWARE_MODULE_ID) != 0 ||
        module->common.methods == NULL ||
        module->common.methods->open == NULL) {
        fprintf(stderr, "audio module contract mismatch\n");
        return 12;
    }

    audio_hw_device_t *device = NULL;
    int status = module->common.methods->open(
            &module->common, AUDIO_HARDWARE_INTERFACE,
            (struct hw_device_t **)&device);
    if (status != 0 || device == NULL ||
        device->common.version != AUDIO_DEVICE_API_VERSION_2_0 ||
        device->open_output_stream == NULL ||
        device->close_output_stream == NULL) {
        fprintf(stderr, "audio device open failed: %d\n", status);
        return 13;
    }

    struct audio_config config = {
        .sample_rate = 44100,
        .channel_mask = AUDIO_CHANNEL_OUT_STEREO,
        .format = AUDIO_FORMAT_PCM_16_BIT,
    };
    audio_stream_out_t *stream = NULL;
    status = device->open_output_stream(
            device, 0, AUDIO_DEVICE_OUT_SPEAKER,
            AUDIO_OUTPUT_FLAG_PRIMARY, &config, &stream, NULL);
    if (status != 0 || stream == NULL ||
        config.sample_rate != 48000 ||
        config.channel_mask != AUDIO_CHANNEL_OUT_STEREO ||
        config.format != AUDIO_FORMAT_PCM_16_BIT ||
        stream->common.get_sample_rate(&stream->common) != 48000 ||
        stream->common.get_channels(&stream->common) != AUDIO_CHANNEL_OUT_STEREO ||
        stream->common.get_format(&stream->common) != AUDIO_FORMAT_PCM_16_BIT) {
        fprintf(stderr, "output stream contract failed: %d\n", status);
        return 14;
    }

    device->close_output_stream(device, stream);
    device->common.close(&device->common);
    dlclose(handle);
    puts("PASS: deployed Android 9 HAL loads and negotiates 48 kHz stereo PCM16");
    return 0;
}
