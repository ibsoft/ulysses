---
name: sirina
description: Use Sirina for local speech workflows in this repository, including text-to-speech, speech-to-text, microphone listening with VAD, always-listening assistant loops, CLI testing, Python API integration, model setup, and troubleshooting speaker or microphone behavior.
---

# Sirina

Use Sirina as the local speech layer for AI agents. Prefer local CLI/API behavior over assumptions, and inspect the
repository before editing because model names, defaults, and available voices can change.

## Core Commands

Install from the checkout:

```bash
python3 -m pip install -e .
```

Download and verify models:

```bash
sirina download --group all
sirina check-models --group all
```

List voices:

```bash
sirina voices
```

Speak:

```bash
sirina say "System online."
sirina say "System online." --output sirina-test.wav
sirina say "System online." --output sirina-test.wav --no-play
```

Transcribe:

```bash
sirina transcribe input.wav --engine tdt
sirina transcribe input.wav --engine ctc
```

Listen once from the microphone:

```bash
sirina listen --engine tdt
sirina listen --engine tdt --output test.wav
```

List audio devices:

```bash
sirina audio-devices
```

## Python API

Use the high-level API for agent integrations:

```python
from sirina import SpeechToText, TextToSpeech, record_utterance

stt = SpeechToText(engine="tdt")
tts = TextToSpeech(voice="sirina")

audio = record_utterance()
if audio.size:
    user_text = stt.transcribe(audio)
    tts.play(f"I heard: {user_text}")
```

Use `TextToSpeech.save_wav(text, path)` for file output and `TextToSpeech.play(text)` for speaker output. Use
`SpeechToText.transcribe_file(path)` for WAV files and `SpeechToText.transcribe(audio)` for in-memory float32 audio.

## Defaults

Read `src/sirina/config.py` before changing defaults. Important defaults:

- Default TTS voice: `sirina`
- Default STT engine: `tdt`
- Input/STT sample rate: `16000`
- Default listen silence: `0.8` seconds
- Default listen max duration: `20.0` seconds
- Default speech start timeout: `10.0` seconds
- Default input device: `SIRINA_AUDIO_INPUT_DEVICE`, from env var or `"auto"`
- Default output device: `SIRINA_AUDIO_OUTPUT_DEVICE`, from env var or `"auto"`

Model files are resolved through `src/sirina/resources.py`. Respect `SIRINA_MODEL_DIR` when users want external model
storage.

## Audio Devices

Use `sirina audio-devices` to inspect microphone/speaker indexes and names before selecting devices. Device selection
accepts:

- `auto` or unset: use autodetect
- numeric index: `0`, `1`, `2`
- unique name substring: `"USB Microphone"`, `"Built-in Audio"`, `"Headset"`

Autodetect rules:

1. Prefer the `sounddevice` system default for the requested direction.
2. Use it only if it has input channels for microphone or output channels for speakers.
3. Otherwise use the first device with matching channels.

Configure defaults through environment variables:

```bash
export SIRINA_AUDIO_INPUT_DEVICE="USB Microphone"
export SIRINA_AUDIO_OUTPUT_DEVICE="USB Headset"
```

Override per command:

```bash
sirina say "Testing speaker." --output-device "USB Headset"
sirina listen --engine tdt --input-device "USB Microphone"
```

Override from Python:

```python
from sirina import TextToSpeech, record_utterance

TextToSpeech().play("Testing speaker.", output_device="USB Headset")
audio = record_utterance(input_device="USB Microphone")
```

Keep device constants in `src/sirina/config.py` and device resolution logic in `src/sirina/audio_io/devices.py`.

## Voices And Languages

Treat Sirina as English-only unless the codebase has been extended. The current TTS paths call the phonemizer with
`en_us`, and the bundled Sirina voice config uses `en-us`.

Use `sirina` as the default TTS voice. It is an English (`en-us`) Sirina/Piper voice with 22050 Hz output.

Kokoro voices are also English and use these prefixes:

- `af_`: American English, feminine voice
- `am_`: American English, masculine voice
- `bf_`: British English, feminine voice
- `bm_`: British English, masculine voice

Known Kokoro voices in the current bundle:

```text
af_alloy
af_aoede
af_bella
af_jessica
af_kore
af_nicole
af_nova
af_river
af_sarah
af_sky
am_adam
am_echo
am_eric
am_fenrir
am_liam
am_michael
am_onyx
am_puck
bf_alice
bf_emma
bf_isabella
bf_lily
bm_daniel
bm_fable
bm_george
bm_lewis
```

Prefer running `sirina voices` before relying on this list, because the installed voice bundle can differ across
machines.

For STT, use `tdt` by default and `ctc` as the faster/simpler option. Both engines use 16000 Hz internally. WAV file
inputs are converted to mono and resampled before transcription.

## Always-Listening Agent Pattern

Sirina's no-wake-word pattern is VAD-based:

1. Call `record_utterance(...)`.
2. If it returns empty audio, continue.
3. Transcribe the utterance with `SpeechToText`.
4. Send the text to the agent or LLM.
5. Speak the response with `TextToSpeech`.
6. Repeat.

Use this template:

```python
from sirina import SpeechToText, TextToSpeech, record_utterance

stt = SpeechToText(engine="tdt")
tts = TextToSpeech(voice="sirina")

while True:
    audio = record_utterance(
        silence_seconds=0.8,
        max_seconds=20.0,
        speech_start_timeout_s=60.0,
    )
    if audio.size == 0:
        continue

    user_text = stt.transcribe(audio).strip()
    if not user_text:
        continue

    response = handle_user_text(user_text)
    tts.play(response)
```

When implementing this loop, avoid listening while TTS is speaking. Add filtering for empty or very short transcripts.
Keep `max_seconds` bounded so noise cannot block the agent forever.

## Testing And Verification

Run repository tests with:

```bash
PYTHONPATH=src python3 -m pytest -q
```

Run compile checks with:

```bash
python3 -m compileall -q src tests
```

If `ruff` is installed, also run:

```bash
python3 -m ruff check src tests
```

For speaker problems, test `sirina say "Testing audio output."` before debugging code. For microphone problems, test
`sirina listen --vad-threshold 0.5 --speech-start-timeout 30`.

## Implementation Rules

- Prefer the high-level API in `src/sirina/api.py` for external integrations.
- Keep CLI behavior in `src/sirina/cli.py` aligned with README examples.
- Keep paths, checksums, default voices, default engines, and audio constants in `src/sirina/config.py`.
- Do not duplicate ONNX provider filtering; use `get_onnx_providers()`.
- Do not reintroduce hard sample-rate failures for WAV input; STT file loading should convert to mono and resample.
- Do not import heavy runtime dependencies from package `__init__` modules unless needed for the requested operation.
