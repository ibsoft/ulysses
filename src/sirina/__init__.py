"""Reusable Sirina speech IO."""

__all__ = [
    "SpeechToText",
    "TextToSpeech",
    "TranscriberProtocol",
    "SpeechSynthesizerProtocol",
    "get_audio_transcriber",
    "get_speech_synthesizer",
    "record_utterance",
]


def __getattr__(name: str):
    if name in {"SpeechToText", "TextToSpeech", "record_utterance"}:
        from . import api

        return getattr(api, name)
    if name in {"TranscriberProtocol", "get_audio_transcriber"}:
        from . import stt

        return getattr(stt, name)
    if name in {"SpeechSynthesizerProtocol", "get_speech_synthesizer"}:
        from . import tts

        return getattr(tts, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
