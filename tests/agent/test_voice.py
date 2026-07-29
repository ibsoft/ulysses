import sys
import threading
import types

import numpy as np

from sirina_agent.audio.sirina_io import SirinaSpeechIO, VoiceState, summarize_for_speech
from sirina_agent.config.models import UlyssesConfig


def test_voice_state_supports_response_toggle():
    state = VoiceState()
    assert state.enabled
    state.enabled = False
    assert not state.enabled
    assert not state.muted


def test_summarize_for_speech_keeps_short_text():
    assert summarize_for_speech("Short status update.") == "Short status update."


def test_summarize_for_speech_shortens_long_bullets():
    text = "\n".join(
        [
            "- Are all subdomains included, or just the main domain?",
            "- Do you want just a network port scan, or also web application security testing?",
            "- Confirm that you have authorization to conduct active scanning and penetration testing.",
            "- Any particular focus areas or critical assets?",
        ]
    )

    summary = summarize_for_speech(text)

    assert summary.startswith("Summary: Are all subdomains included")
    assert "Any particular focus areas" not in summary
    assert len(summary) <= 320


def test_speak_retries_with_forced_summary_when_tts_text_is_too_long(monkeypatch):
    played = []

    class FakeTextToSpeech:
        def __init__(self, *args, **kwargs):
            pass

        def play(self, text, output_device=None):
            played.append(text)
            if len(played) == 1:
                raise ValueError("text is too long, must be less than 510 phonemes")

    fake_api = types.SimpleNamespace(TextToSpeech=FakeTextToSpeech)
    monkeypatch.setitem(sys.modules, "sirina.api", fake_api)
    config = UlyssesConfig()
    config.sirina.isolate_tts_process = False
    voice = SirinaSpeechIO(config)

    voice.speak("This is a compact sentence that still failed phoneme conversion.")

    assert len(played) == 2
    assert played[1].startswith("Summary:")
    assert voice.state.tts == "idle"


def test_speak_isolates_tts_crash_in_subprocess(monkeypatch):
    calls = []

    class FakeProcess:
        returncode = -11

        def __init__(self, command, **kwargs):
            calls.append((command, kwargs))

        def poll(self):
            return self.returncode

        def communicate(self, input):
            calls.append(input)
            return "", "Segmentation fault (core dumped)"

    monkeypatch.setattr("sirina_agent.audio.sirina_io.subprocess.Popen", FakeProcess)
    voice = SirinaSpeechIO(UlyssesConfig())

    try:
        voice.speak("Short startup message.")
    except RuntimeError as exc:
        assert "TTS backend crashed with signal 11" in str(exc)
    else:
        raise AssertionError("expected isolated TTS crash to raise RuntimeError")

    assert calls
    assert calls[0][0][:3] == [sys.executable, "-m", "sirina_agent.audio.speak_worker"]
    assert voice.state.tts == "idle"


def test_interrupt_terminates_isolated_tts_process(monkeypatch):
    started = threading.Event()
    terminated = threading.Event()

    class FakeProcess:
        returncode = None

        def __init__(self, command, **kwargs):
            started.set()

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15
            terminated.set()

        def communicate(self, input):
            terminated.wait(timeout=1)
            return "", ""

    monkeypatch.setattr("sirina_agent.audio.sirina_io.subprocess.Popen", FakeProcess)
    voice = SirinaSpeechIO(UlyssesConfig())
    worker = threading.Thread(target=voice.speak, args=("Stop this speech.",))
    worker.start()

    assert started.wait(timeout=1)
    voice.interrupt()
    worker.join(timeout=1)

    assert terminated.is_set()
    assert not worker.is_alive()
    assert voice.state.tts == "idle"


def test_listen_once_records_transcribes_and_restores_state(monkeypatch):
    received = {}

    def fake_record_utterance(**kwargs):
        received.update(kwargs)
        return np.array([0.1, -0.1], dtype=np.float32)

    class FakeSpeechToText:
        def __init__(self, engine):
            received["engine"] = engine

        def transcribe(self, audio):
            received["audio"] = audio
            return "  run the assessment  "

    monkeypatch.setitem(
        sys.modules,
        "sirina.api",
        types.SimpleNamespace(record_utterance=fake_record_utterance, SpeechToText=FakeSpeechToText),
    )
    voice = SirinaSpeechIO(UlyssesConfig())

    assert voice.listen_once() == "run the assessment"
    assert received["stop_event"] is voice._listen_cancel
    assert received["engine"] == voice.config.sirina.stt_engine
    assert voice.state.stt == "idle"


def test_listen_once_restores_state_after_transcription_error(monkeypatch):
    class FailingSpeechToText:
        def __init__(self, engine):
            pass

        def transcribe(self, audio):
            raise RuntimeError("stt unavailable")

    monkeypatch.setitem(
        sys.modules,
        "sirina.api",
        types.SimpleNamespace(
            record_utterance=lambda **kwargs: np.array([0.1], dtype=np.float32),
            SpeechToText=FailingSpeechToText,
        ),
    )
    voice = SirinaSpeechIO(UlyssesConfig())

    try:
        voice.listen_once()
    except RuntimeError as exc:
        assert str(exc) == "stt unavailable"
    else:
        raise AssertionError("expected transcription failure")
    assert voice.state.stt == "idle"


def test_cancel_listen_sets_cancellation_event():
    voice = SirinaSpeechIO(UlyssesConfig())

    voice.cancel_listen()

    assert voice._listen_cancel.is_set()


def test_push_to_talk_defaults_to_f4():
    assert UlyssesConfig().audio.push_to_talk_key == "f4"
