# Z1 speaker acoustic measurement

Status: `HOST_HARNESS_VALIDATED / PARTIAL_CAPTURE_SUPPORTED / BOARD_PENDING`.

The existing diagnostic can prove that PCM is running and that DAPM, GPIO140
and PMIC registers moved. It cannot prove that the physical speaker produced
sound. `scripts/diag/z1_speaker_measure.py` adds the missing host-side
measurement: record from a microphone while the board plays a known tone,
then require a sustained spectral increase at the expected frequency.

The classifier uses PCM16 WAV input and no third-party Python packages. It
reports the noise floor, tone amplitude, SNR, run duration and the selected
microphone channel. A pass requires all of the following:

- at least 500 ms of sustained energy at the requested frequency;
- at least 12 dB above that recording's same-frequency noise floor;
- a quiet baseline immediately before that same sustained burst;
- at least 0.25 LSB of measured tone amplitude.

These thresholds are defaults and are recorded in every JSON report. They are
deliberately conservative against a single click or a recording that starts in
the middle of a pre-existing tone.

## Direct PCM gate

Put the host microphone roughly 10 cm from the Z1 speaker in a quiet room.
Leave the headphone jack empty and Bluetooth off. Establish the normal CH340
capture and deployment environment first:

```sh
cd /home/lxj/claude/6572
export Z1DEV=/dev/ttyUSB0
export Z1LOG=mainline_recon/z1_speaker_direct_serial_$(date +%Y%m%d_%H%M%S).log
stty -F "$Z1DEV" 921600 raw -echo
cat "$Z1DEV" >> "$Z1LOG" &
export Z1_CAPTURE_PID=$!

z1a9_gpt/scripts/z1deploy/z1push.sh --mode 0755 \
    z1a9_gpt/scripts/diag/z1_diag_audio.sh z1_diag_audio.sh

python3 z1a9_gpt/scripts/diag/z1_speaker_measure.py run \
    --device default --seconds 55 --command-timeout 100 --frequency 500 \
    --require-rootcmd-success \
    --output out/z1_speaker_direct_20261122.wav \
    -- bash z1a9_gpt/scripts/z1deploy/z1push.sh --wait 60 --rootcmd \
    'sh /data/local/tmp/z1stage/z1_diag_audio.sh trigger'

kill "$Z1_CAPTURE_PID"
```

The board command output is saved beside the WAV as
`z1_speaker_direct_20261122.command.log`. The acoustic verdict is in
`z1_speaker_direct_20261122.json`. A direct-PCM pass proves the AFE, codec,
GPIO140, analog output and physical speaker can work together.

## Android media gate

Run this as a separate recording with the same physical setup. The archived
OpenSL probe produces 440 Hz on the left channel and 660 Hz on the right for
two seconds through `SL_ANDROID_STREAM_MEDIA`:

```sh
z1a9_gpt/scripts/z1deploy/z1push.sh --mode 0755 \
    out/audio_runtime_probe_root_20261115/audio_probe audio_probe

python3 z1a9_gpt/scripts/diag/z1_speaker_measure.py run \
    --device default --seconds 20 --frequency 440 --frequency 660 \
    --require-rootcmd-success \
    --output out/z1_speaker_android_20261122.wav \
    -- bash z1a9_gpt/scripts/z1deploy/z1push.sh --wait 20 --rootcmd \
    '/data/local/tmp/z1stage/audio_probe'
```

The default policy passes when either frequency is sustained; this tolerates
a microphone or acoustic path that favors one channel. Add `--require-all`
only when both channels are required by the test contract.

Interpret the two recordings together:

| Direct PCM | Android media | First failing stage |
| --- | --- | --- |
| PASS | PASS | Android media and the physical output path both work. |
| PASS | FAIL | Inspect AudioFlinger, AudioPolicy, the selected HAL and stream routing. |
| FAIL | FAIL | PCM/AFE/codec/GPIO140/codec analog/board speaker path remains unresolved. |
| FAIL | PASS | Repeat the tests; contradictory results indicate setup or timing error. |

## Offline analysis

An existing WAV can be classified without touching the board:

```sh
python3 z1a9_gpt/scripts/diag/z1_speaker_measure.py analyze \
    capture.wav --frequency 500 \
    --report capture.measurement.json
```

For serial `run`, require the board command’s own `###RC=0` marker; `z1push.sh` transport success alone is insufficient.

The `run` command still analyzes the WAV when the board command exits
unsuccessfully or times out. Its JSON report records the command return code,
timeout state, root-command gate and completion error under `command`. The
process exits `2` for an incomplete board command even if the recording
contains the expected tone, so an acoustic hit cannot hide a reset.

Exit status `0` means the acoustic gate passed, `1` means no sustained tone was
measured, and `2` means capture or input validation failed.

## Reset staircase

The November 23 attempts reset too early to distinguish mixer access, PCM open,
playback and concurrent register inspection. Use the staircase for the next
active board attempt:

```sh
cd /home/lxj/claude/6572
TONE=out/audio_runtime_probe_root_20261115/tone_s16_48k_amp4000.wav

z1a9_gpt/scripts/z1deploy/z1push.sh --mode 0644 \
    "$TONE" z1_audio_tone.wav
z1a9_gpt/scripts/z1deploy/z1push.sh --mode 0755 \
    z1a9_gpt/scripts/diag/z1_audio_staircase.sh z1_audio_staircase.sh

python3 z1a9_gpt/scripts/diag/z1_speaker_measure.py run \
    --device default --seconds 30 --command-timeout 45 --frequency 500 \
    --require-rootcmd-success \
    --output out/z1_speaker_staircase.wav \
    -- bash z1a9_gpt/scripts/z1deploy/z1push.sh --wait 35 --rootcmd \
    'sh /data/local/tmp/z1stage/z1_audio_staircase.sh /data/local/tmp/z1stage/z1_audio_tone.wav'
```

The staircase writes `[z1audio-stair]` markers to the serial log and appends
the same records to
`/data/local/tmp/z1stage/z1_audio_staircase.progress`. The serial capture is
the authoritative reset record; the progress file is best effort and can be
forced through storage with `Z1_AUDIO_STAIRCASE_SYNC=1` when the added I/O is
an acceptable diagnostic variable. The script deliberately omits register,
DAPM and dmesg sampling during playback. If the board resets, the last marker
identifies the first failed boundary:

- before `STEP_00_START`: root daemon or shell startup;
- through `STEP_02_MIXER_READ_DONE`: control access;
- between `STEP_03` and `STEP_06`: an individual mixer write;
- around `STEP_07`: PCM information/control open;
- after `STEP_08_TINYPLAY_BEFORE`: tinyplay/PCM/codec activation;
- after `STEP_11_TINYPLAY_DONE`: playback completed and reset occurred later.

After the next boot, read the best-effort progress file:

```sh
z1a9_gpt/scripts/z1deploy/z1push.sh --cmd \
    'cat /data/local/tmp/z1stage/z1_audio_staircase.progress'
```

The known tone is 2 seconds of 48 kHz stereo PCM16 at peak amplitude 4000,
SHA256 `4d937007e703131fb5548207b71c89bc7ed73cc610deda5a0afe7dfaaf74894c`.
The staircase reports no physical pass; audible confirmation still belongs to
the separate acoustic gate.

## Current board blocker

During the first review the CH340 disappeared from USB enumeration
(`Cannot enable`, `Maybe the USB cable is bad`, then `unable to enumerate USB
device`), and neither `/dev/ttyUSB*` nor `/dev/ttyACM*` was present. The host
microphone itself was verified at 48 kHz stereo, a host acoustic loopback
produced a sustained 660 Hz detection, and an eight-second quiet capture
produced no sustained 440, 500 or 660 Hz tone.

On November 23 the CH340 returned as `/dev/ttyUSB0`, but a read-only console
probe received no response. The later staircase remains the next active test
once the board is powered and its Android console is reachable.

The board result remains unknown until the serial link is restored and the two
commands above are executed. Do not convert `BOARD_PENDING` into a speaker pass
from PCM, DAPM or mixer evidence alone.
