from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import sys

from .config import (
    DEFAULT_STT_ENGINE,
    DEFAULT_TTS_VOICE,
    INPUT_SAMPLE_RATE,
    LISTEN_MAX_SECONDS,
    LISTEN_SILENCE_SECONDS,
    LISTEN_SPEECH_START_TIMEOUT_SECONDS,
)
from .download import download_models, models_valid


def _cmd_download(args: argparse.Namespace) -> int:
    return asyncio.run(download_models(model_root=args.model_dir, group=args.group))


def _cmd_check_models(args: argparse.Namespace) -> int:
    ok = models_valid(model_root=args.model_dir, group=args.group)
    print("models ok" if ok else "models missing or invalid")
    return 0 if ok else 1


def _cmd_voices(_args: argparse.Namespace) -> int:
    from .tts import tts_kokoro

    print("sirina")
    try:
        for voice in tts_kokoro.get_voices():
            print(voice)
    except FileNotFoundError:
        print("kokoro voices unavailable; run `sirina download --group tts`", file=sys.stderr)
        return 1
    return 0


def _cmd_audio_devices(_args: argparse.Namespace) -> int:
    from .audio_io.sounddevice_io import describe_audio_devices

    print(describe_audio_devices())
    return 0


def _cmd_say(args: argparse.Namespace) -> int:
    from .api import TextToSpeech

    tts = TextToSpeech(voice=args.voice, normalize_text=not args.no_normalize)
    if args.output:
        output_path = tts.save_wav(args.text, args.output)
        print(output_path)
        if not args.no_play:
            tts.play(args.text, output_device=args.output_device)
        return 0
    tts.play(args.text, output_device=args.output_device)
    return 0


def _cmd_transcribe(args: argparse.Namespace) -> int:
    from .api import SpeechToText

    stt = SpeechToText(engine=args.engine)
    print(stt.transcribe_file(args.audio_file))
    return 0


def _cmd_listen(args: argparse.Namespace) -> int:
    from .api import SpeechToText, record_utterance

    audio = record_utterance(
        vad_threshold=args.vad_threshold,
        silence_seconds=args.silence_seconds,
        max_seconds=args.max_seconds,
        speech_start_timeout_s=args.speech_start_timeout,
        input_device=args.input_device,
    )
    if audio.size == 0:
        print("no speech detected", file=sys.stderr)
        return 1
    if args.output:
        import soundfile as sf  # type: ignore

        sf.write(args.output, audio, INPUT_SAMPLE_RATE)
    stt = SpeechToText(engine=args.engine)
    print(stt.transcribe(audio))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sirina reusable TTS/STT toolkit")
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=None,
        help="Directory containing ASR/TTS model files. Also sets SIRINA_MODEL_DIR.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    download = subparsers.add_parser("download", help="Download ASR/TTS model files")
    download.add_argument("--group", choices=("all", "asr", "tts"), default="all")
    download.set_defaults(func=_cmd_download)

    check_models = subparsers.add_parser("check-models", help="Check downloaded model checksums")
    check_models.add_argument("--group", choices=("all", "asr", "tts"), default="all")
    check_models.set_defaults(func=_cmd_check_models)

    voices = subparsers.add_parser("voices", help="List available TTS voices")
    voices.set_defaults(func=_cmd_voices)

    audio_devices = subparsers.add_parser("audio-devices", help="List available audio input/output devices")
    audio_devices.set_defaults(func=_cmd_audio_devices)

    say = subparsers.add_parser("say", help="Synthesize speech")
    say.add_argument("text")
    say.add_argument("--voice", default=DEFAULT_TTS_VOICE)
    say.add_argument("--output", type=Path)
    say.add_argument("--output-device", help="Output device index/name, or 'auto' for autodetect")
    say.add_argument("--play", action="store_true", help=argparse.SUPPRESS)
    say.add_argument("--no-play", action="store_true", help="Save --output without playing audio")
    say.add_argument("--no-normalize", action="store_true", help="Skip number/date text normalization")
    say.set_defaults(func=_cmd_say)

    transcribe = subparsers.add_parser("transcribe", help="Transcribe a WAV file")
    transcribe.add_argument("audio_file", type=Path)
    transcribe.add_argument("--engine", choices=("ctc", "tdt"), default=DEFAULT_STT_ENGINE)
    transcribe.set_defaults(func=_cmd_transcribe)

    listen = subparsers.add_parser("listen", help="Record one utterance from the microphone and transcribe it")
    listen.add_argument("--engine", choices=("ctc", "tdt"), default=DEFAULT_STT_ENGINE)
    listen.add_argument("--vad-threshold", type=float, default=None)
    listen.add_argument("--input-device", help="Input device index/name, or 'auto' for autodetect")
    listen.add_argument("--silence-seconds", type=float, default=LISTEN_SILENCE_SECONDS)
    listen.add_argument("--max-seconds", type=float, default=LISTEN_MAX_SECONDS)
    listen.add_argument("--speech-start-timeout", type=float, default=LISTEN_SPEECH_START_TIMEOUT_SECONDS)
    listen.add_argument("--output", type=Path, help="Optional WAV path for the recorded utterance")
    listen.set_defaults(func=_cmd_listen)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.model_dir:
        os.environ["SIRINA_MODEL_DIR"] = str(args.model_dir.expanduser())
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
