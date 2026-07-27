from pathlib import Path
from types import SimpleNamespace

import numpy as np

from sirina import cli
from sirina import SpeechToText, TextToSpeech
from sirina.audio_io.devices import format_audio_devices, resolve_audio_device
from sirina.resources import resource_path
from sirina.stt.audio_file import resample_audio


class FakeSynthesizer:
    sample_rate = 16000

    def generate_speech_audio(self, text: str):
        assert text == "hello"
        return np.array([0.0, 0.5], dtype=np.float32)


class FakeTranscriber:
    def transcribe(self, audio):
        assert audio.dtype == np.float32
        return "hello"

    def transcribe_file(self, audio_path: Path):
        assert audio_path.name == "input.wav"
        return "from file"


def test_text_to_speech_wrapper_uses_injected_synthesizer() -> None:
    tts = TextToSpeech(synthesizer=FakeSynthesizer(), normalize_text=False)

    audio = tts.synthesize("hello")

    assert audio.dtype == np.float32
    assert tts.sample_rate == 16000


def test_speech_to_text_wrapper_uses_injected_transcriber() -> None:
    stt = SpeechToText(transcriber=FakeTranscriber())

    assert stt.transcribe(np.array([1, 2], dtype=np.int16)) == "hello"
    assert stt.transcribe_file("input.wav") == "from file"


def test_resource_path_falls_back_to_sirina_models(monkeypatch) -> None:
    monkeypatch.delenv("SIRINA_MODEL_DIR", raising=False)

    path = resource_path("models/TTS/sirina.onnx")

    assert path.name == "sirina.onnx"
    assert path.parent.name == "TTS"


def test_resample_audio_converts_tts_rate_to_stt_rate() -> None:
    source_rate = 22050
    target_rate = 16000
    audio = np.linspace(-0.5, 0.5, source_rate, dtype=np.float32)

    loaded = resample_audio(audio, source_rate, target_rate)

    assert loaded.dtype == np.float32
    assert len(loaded) == target_rate


def test_say_with_output_plays_by_default(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str | Path]] = []

    class FakeTextToSpeech:
        def __init__(self, voice: str, normalize_text: bool) -> None:
            calls.append(("init", voice))
            assert normalize_text

        def save_wav(self, text: str, output_path: Path) -> Path:
            calls.append(("save", text))
            return output_path

        def play(self, text: str, output_device=None) -> None:
            calls.append(("play", text))
            assert output_device == "Speaker"

    monkeypatch.setattr("sirina.api.TextToSpeech", FakeTextToSpeech)
    args = SimpleNamespace(
        text="System online.",
        voice="sirina",
        no_normalize=False,
        output=tmp_path / "sirina-test.wav",
        no_play=False,
        output_device="Speaker",
    )

    assert cli._cmd_say(args) == 0
    assert calls == [("init", "sirina"), ("save", "System online."), ("play", "System online.")]


def test_say_with_output_can_skip_playback(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []

    class FakeTextToSpeech:
        def __init__(self, voice: str, normalize_text: bool) -> None:
            pass

        def save_wav(self, text: str, output_path: Path) -> Path:
            calls.append("save")
            return output_path

        def play(self, text: str, output_device=None) -> None:
            calls.append("play")

    monkeypatch.setattr("sirina.api.TextToSpeech", FakeTextToSpeech)
    args = SimpleNamespace(
        text="System online.",
        voice="sirina",
        no_normalize=False,
        output=tmp_path / "sirina-test.wav",
        no_play=True,
        output_device=None,
    )

    assert cli._cmd_say(args) == 0
    assert calls == ["save"]


def test_resolve_audio_device_autodetect_uses_valid_default() -> None:
    devices = [
        {"name": "Monitor", "max_input_channels": 0, "max_output_channels": 2},
        {"name": "USB Microphone", "max_input_channels": 1, "max_output_channels": 0},
    ]

    assert resolve_audio_device(devices, "auto", "input", default_device=(1, 0)) == 1
    assert resolve_audio_device(devices, "auto", "output", default_device=(1, 0)) == 0


def test_resolve_audio_device_supports_index_and_name() -> None:
    devices = [
        {"name": "Built-in Audio", "max_input_channels": 1, "max_output_channels": 2},
        {"name": "USB Microphone", "max_input_channels": 1, "max_output_channels": 0},
    ]

    assert resolve_audio_device(devices, 0, "output") == 0
    assert resolve_audio_device(devices, "USB", "input") == 1


def test_format_audio_devices_marks_defaults() -> None:
    devices = [
        {"name": "Built-in Audio", "max_input_channels": 1, "max_output_channels": 2},
        {"name": "USB Microphone", "max_input_channels": 1, "max_output_channels": 0},
    ]

    output = format_audio_devices(devices, default_device=(1, 0))

    assert "index  in  out  default  name" in output
    assert "input" in output
    assert "output" in output
