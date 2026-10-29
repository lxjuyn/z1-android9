/*
 * Build-time shim -- NOT part of the deployed HAL.
 *
 * AOSP's real hardware/audio_effect.h pulls in system/audio_effect.h, which
 * includes the *generated* system/audio_effect-base.h (effect_uuid_t,
 * effect_config_t, effect_descriptor_t, the EFFECT_CMD_* enum).  That
 * generated header is not present on this build host (no AOSP tree), and
 * reproducing it by hand would risk getting the effects ABI subtly wrong.
 *
 * hardware/audio.h uses exactly one thing from the effects header:
 * the opaque effect_handle_t, as the argument type of the
 * add_audio_effect()/remove_audio_effect() function pointers.  An argument
 * type does not affect struct layout, so declaring it opaque here keeps the
 * audio_hw_device / audio_stream_out layouts byte-exact while letting the
 * HAL compile.  The deployed .so never dereferences it.
 */
#ifndef ANDROID_AUDIO_EFFECT_SHIM_H
#define ANDROID_AUDIO_EFFECT_SHIM_H
#include <stdint.h>
/* AOSP: typedef struct effect_interface_s **effect_handle_t; */
struct effect_interface_s;
typedef struct effect_interface_s **effect_handle_t;
#endif
