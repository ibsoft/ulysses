from __future__ import annotations

import logging
from threading import Event

from .wakeword import WakeWordDetector


class VoiceLoop:
    def __init__(self, orchestrator, speech_io, wake_detector: WakeWordDetector) -> None:
        self.orchestrator = orchestrator
        self.speech_io = speech_io
        self.wake_detector = wake_detector
        self.stop_event = Event()
        self.log = logging.getLogger("ulysses.voice")

    def run_forever(self) -> None:
        while not self.stop_event.is_set():
            self.speech_io.state.wake = "listening"
            if not self.wake_detector.wait():
                continue
            self.speech_io.interrupt()
            self.speech_io.state.wake = "awake"
            utterance = self.speech_io.listen_once()
            if not utterance:
                continue
            try:
                response = self.orchestrator.handle_text(utterance)
                self.speech_io.speak(response)
            except Exception as exc:
                self.log.exception("voice_loop_error")
                self.speech_io.speak(f"Ulysses encountered an error: {exc}")

    def stop(self) -> None:
        self.stop_event.set()
        self.speech_io.interrupt()
