# Z1 R8 audio capture routing

`z1_audio_route.sh` is the boot-time route setup used by the R7 combined
candidate. It selects:

- DL1 playback through the MT6323 speaker and headphone paths;
- AWB capture with the MT6572 AFE source set to `MIC`;
- the MT6323 `MIC Route Switch` and `MIC1` input mux.

The script is read-only with respect to /data. It waits at most 60 seconds for
`controlC0`, records every `tinymix` command and its error, and exits nonzero
when any route write fails.

The Android primary HAL uses `pcmC0D0p` for playback and `pcmC0D1c` for
capture. Both streams open lazily so service startup does not race ASoC probe.
Capture supports 8, 16, 32, and 48 kHz PCM16 with mono or stereo buffers.

Host checks do not prove that the physical microphone is connected to MIC1 or
that bias is enabled on the board. Board acceptance requires a non-silent
`AudioRecord` capture, correct duration/rate/channel metadata, and stable
reopen after recording stops.
