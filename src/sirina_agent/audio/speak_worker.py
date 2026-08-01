from __future__ import annotations

import argparse
import sys

from sirina.api import TextToSpeech

from .sirina_io import summarize_for_speech


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Play one isolated Ulysses TTS utterance.")
    parser.add_argument("--voice", required=True)
    parser.add_argument("--no-normalize", action="store_true")
    parser.add_argument("--output-device")
    parser.add_argument("--signal-playback", action="store_true")
    args = parser.parse_args(argv)

    text = sys.stdin.read()
    tts = TextToSpeech(voice=args.voice, normalize_text=not args.no_normalize)
    try:
        audio = tts.synthesize(text)
    except ValueError as exc:
        if "text is too long" not in str(exc):
            raise
        audio = tts.synthesize(summarize_for_speech(text, force=True))
    if args.signal_playback:
        print("ULYSSES_PLAYBACK_READY", flush=True)
    import sounddevice as sd  # type: ignore

    from sirina.audio_io.sounddevice_io import get_output_device

    sd.play(audio, tts.sample_rate, device=get_output_device(args.output_device))
    sd.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
