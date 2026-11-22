# Z1 speaker acoustic measurement

Status: `HOST_HARNESS_VALIDATED / BOARD_PENDING`.

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

Exit status `0` means the acoustic gate passed, `1` means no sustained tone was
measured, and `2` means capture or input validation failed.

## Current board blocker

At review time the CH340 disappeared from USB enumeration
(`Cannot enable`, `Maybe the USB cable is bad`, then `unable to enumerate USB
device`), and neither `/dev/ttyUSB*` nor `/dev/ttyACM*` was present. No board
command or tone could therefore run. The host microphone itself was verified
at 48 kHz stereo, a host acoustic loopback produced a sustained 660 Hz
detection, and an eight-second quiet capture produced no sustained 440, 500 or
660 Hz tone.

The board result remains unknown until the serial link is restored and the two
commands above are executed. Do not convert `BOARD_PENDING` into a speaker pass
from PCM, DAPM or mixer evidence alone.
