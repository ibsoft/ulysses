from __future__ import annotations

import re
import subprocess
import sys
import threading
from dataclasses import dataclass


SPEECH_SUMMARY_AFTER_CHARS = 260
SPEECH_SUMMARY_MAX_CHARS = 320


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

        self._interrupt.clear()
        self.state.tts = "speaking"
        try:
            spoken_text = summarize_for_speech(text)
            if self.config.sirina.isolate_tts_process:
                _play_in_subprocess(
                    spoken_text,
                    voice=self.config.sirina.tts_voice,
                    normalize_text=self.config.sirina.normalize_tts_text,
                    output_device=self.config.audio.output_device,
                )
            else:
                from sirina.api import TextToSpeech

                tts = TextToSpeech(voice=self.config.sirina.tts_voice, normalize_text=self.config.sirina.normalize_tts_text)
                try:
                    tts.play(spoken_text, output_device=self.config.audio.output_device)
                except ValueError as exc:
                    if "text is too long" not in str(exc):
                        raise
                    tts.play(summarize_for_speech(text, force=True), output_device=self.config.audio.output_device)
        finally:
            self.state.tts = "idle"

    def interrupt(self) -> None:
        self._interrupt.set()
        try:
            import sounddevice as sd  # type: ignore

            sd.stop()
        except Exception:
            pass
        self.state.tts = "idle"


def summarize_for_speech(text: str, force: bool = False) -> str:
    spoken = _clean_spoken_text(text)
    if not force and len(spoken) <= SPEECH_SUMMARY_AFTER_CHARS:
        return spoken

    parts = _summary_parts(text)
    summary = "Summary: " + " ".join(parts)
    return _clip_sentence(summary, SPEECH_SUMMARY_MAX_CHARS)


def _summary_parts(text: str) -> list[str]:
    lines = [_clean_summary_line(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    if lines:
        return lines[:3]

    sentences = re.split(r"(?<=[.!?])\s+", _clean_spoken_text(text))
    sentences = [sentence.strip() for sentence in sentences if sentence.strip()]
    return sentences[:2] or ["Long response available in the transcript."]


def _clean_summary_line(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^[-*+]\s+", "", line)
    line = re.sub(r"^\d+[.)]\s+", "", line)
    return _clean_spoken_text(line)


def _clean_spoken_text(text: str) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_#>~|]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _clip_sentence(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    clipped = text[: max_chars - 1].rsplit(" ", 1)[0].rstrip(".,;:")
    return f"{clipped}."


def _play_in_subprocess(text: str, voice: str, normalize_text: bool, output_device: str | int | None) -> None:
    command = [
        sys.executable,
        "-m",
        "sirina_agent.audio.speak_worker",
        "--voice",
        voice,
    ]
    if not normalize_text:
        command.append("--no-normalize")
    if output_device not in (None, "auto"):
        command.extend(["--output-device", str(output_device)])

    completed = subprocess.run(command, input=text, text=True, capture_output=True)
    if completed.returncode == 0:
        return
    detail = completed.stderr.strip() or completed.stdout.strip()
    if completed.returncode < 0:
        signal_number = abs(completed.returncode)
        raise RuntimeError(f"TTS backend crashed with signal {signal_number}; voice was skipped.")
    raise RuntimeError(detail or f"TTS backend failed with exit code {completed.returncode}.")
