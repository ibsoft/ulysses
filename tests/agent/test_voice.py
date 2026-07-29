import sys
import types

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

    class Completed:
        returncode = -11
        stdout = ""
        stderr = "Segmentation fault (core dumped)"

    def fake_run(command, input, text, capture_output):
        calls.append((command, input, text, capture_output))
        return Completed()

    monkeypatch.setattr("sirina_agent.audio.sirina_io.subprocess.run", fake_run)
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
