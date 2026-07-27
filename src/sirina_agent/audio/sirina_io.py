from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass
class VoiceState:
    enabled: bool = True
    wake: str = "idle"
    stt: str = "idle"
    tts: str = "idle"
    muted: bool = False


class SirinaSpeechIO:
    def __init__(self, config) -> None:
        self.config = config
        self.state = VoiceState()
        self._interrupt = threading.Event()

    def listen_once(self) -> str:
        from sirina.api import SpeechToText, record_utterance

        self.state.stt = "recording"
        audio = record_utterance(
            vad_threshold=self.config.audio.vad_threshold,
            silence_seconds=self.config.audio.silence_seconds,
            max_seconds=self.config.audio.max_utterance_seconds,
            input_device=self.config.audio.input_device,
        )
        self.state.stt = "transcribing"
        if audio.size == 0:
            self.state.stt = "idle"
            return ""
        text = SpeechToText(engine=self.config.sirina.stt_engine).transcribe(audio)
        self.state.stt = "idle"
        return text

    def speak(self, text: str) -> None:
        if not self.state.enabled or self.state.muted:
            return
        from sirina.api import TextToSpeech

        self._interrupt.clear()
        self.state.tts = "speaking"
        TextToSpeech(voice=self.config.sirina.tts_voice, normalize_text=self.config.sirina.normalize_tts_text).play(
            text, output_device=self.config.audio.output_device
        )
        self.state.tts = "idle"

    def interrupt(self) -> None:
        self._interrupt.set()
        try:
            import sounddevice as sd  # type: ignore

            sd.stop()
        except Exception:
            pass
        self.state.tts = "idle"
