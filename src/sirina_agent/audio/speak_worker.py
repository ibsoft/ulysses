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
    args = parser.parse_args(argv)

    text = sys.stdin.read()
    tts = TextToSpeech(voice=args.voice, normalize_text=not args.no_normalize)
    try:
        tts.play(text, output_device=args.output_device)
    except ValueError as exc:
        if "text is too long" not in str(exc):
            raise
        tts.play(summarize_for_speech(text, force=True), output_device=args.output_device)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
