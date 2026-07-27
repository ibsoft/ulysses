from __future__ import annotations

import queue
import time
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from .config import (
    DEFAULT_STT_ENGINE,
    DEFAULT_TTS_VOICE,
    LISTEN_MAX_SECONDS,
    LISTEN_SILENCE_SECONDS,
    LISTEN_SPEECH_START_TIMEOUT_SECONDS,
)
from .stt import TranscriberProtocol, get_audio_transcriber
from .text import SpokenTextConverter
from .tts import SpeechSynthesizerProtocol, get_speech_synthesizer


class TextToSpeech:
    """Small wrapper around Sirina speech synthesizers."""

    def __init__(
        self,
        voice: str = DEFAULT_TTS_VOICE,
        normalize_text: bool = True,
        synthesizer: SpeechSynthesizerProtocol | None = None,
        **synthesizer_kwargs: Any,
    ) -> None:
        self.synthesizer = synthesizer or get_speech_synthesizer(voice=voice, **synthesizer_kwargs)
        self.sample_rate = self.synthesizer.sample_rate
        self._converter = SpokenTextConverter() if normalize_text else None

    def synthesize(self, text: str) -> NDArray[np.float32]:
        spoken_text = self._converter.text_to_spoken(text) if self._converter else text
        return self.synthesizer.generate_speech_audio(spoken_text)

    def save_wav(self, text: str, output_path: str | Path) -> Path:
        import soundfile as sf  # type: ignore

        audio = self.synthesize(text)
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(path, audio, self.sample_rate)
        return path

    def play(self, text: str, output_device: int | str | None = None) -> None:
        import sounddevice as sd  # type: ignore

        from .audio_io.sounddevice_io import get_output_device

        audio = self.synthesize(text)
        sd.play(audio, self.sample_rate, device=get_output_device(output_device))
        sd.wait()


class SpeechToText:
    """Small wrapper around Sirina speech transcribers."""

    def __init__(
        self,
        engine: str = DEFAULT_STT_ENGINE,
        transcriber: TranscriberProtocol | None = None,
        **transcriber_kwargs: Any,
    ) -> None:
        self.transcriber = transcriber or get_audio_transcriber(engine, **transcriber_kwargs)

    def transcribe(self, audio: NDArray[Any]) -> str:
        return self.transcriber.transcribe(np.asarray(audio, dtype=np.float32))

    def transcribe_file(self, audio_path: str | Path) -> str:
        return self.transcriber.transcribe_file(Path(audio_path))


def record_utterance(
    vad_threshold: float | None = None,
    silence_seconds: float = LISTEN_SILENCE_SECONDS,
    max_seconds: float = LISTEN_MAX_SECONDS,
    speech_start_timeout_s: float = LISTEN_SPEECH_START_TIMEOUT_SECONDS,
    input_device: int | str | None = None,
) -> NDArray[np.float32]:
    """Record one utterance from the default microphone using Silero VAD."""
    from .audio_io import get_audio_system

    audio_io = get_audio_system("sounddevice", vad_threshold=vad_threshold, input_device=input_device)
    sample_queue = audio_io.get_sample_queue()
    chunks: list[NDArray[np.float32]] = []
    speech_started = False
    last_voice_at = 0.0
    started_at = time.time()

    audio_io.start_listening()
    try:
        while True:
            now = time.time()
            if now - started_at > max_seconds:
                break
            if not speech_started and now - started_at > speech_start_timeout_s:
                break
            try:
                chunk, voiced = sample_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if voiced:
                speech_started = True
                last_voice_at = now
            if speech_started:
                chunks.append(np.asarray(chunk, dtype=np.float32))
            if speech_started and now - last_voice_at >= silence_seconds:
                break
    finally:
        audio_io.stop_listening()

    if not chunks:
        return np.array([], dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32)
