from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os

from .resources import resource_path


@dataclass(frozen=True)
class ModelFile:
    url: str
    checksum: str


UPSTREAM_RELEASE_BASE_URL = "https://github.com/dnhkng/GLaDOS/releases/download/0.1"
UPSTREAM_CTC_RELEASE_BASE_URL = "https://github.com/dnhkng/GlaDOS/releases/download/0.1"


MODEL_FILES: Mapping[str, ModelFile] = {
    "ASR/nemo-parakeet_tdt_ctc_110m.onnx": ModelFile(
        url=f"{UPSTREAM_CTC_RELEASE_BASE_URL}/nemo-parakeet_tdt_ctc_110m.onnx",
        checksum="313705ff6f897696ddbe0d92b5ffadad7429a47d2ddeef370e6f59248b1e8fb5",
    ),
    "ASR/parakeet-tdt-0.6b-v3_encoder.onnx": ModelFile(
        url=f"{UPSTREAM_RELEASE_BASE_URL}/parakeet-tdt-0.6b-v3_encoder.onnx",
        checksum="e40d5963414174629ce6585192f2bad5dcdac7b0e18dcf05abdc0965a114197c",
    ),
    "ASR/parakeet-tdt-0.6b-v3_decoder.onnx": ModelFile(
        url=f"{UPSTREAM_RELEASE_BASE_URL}/parakeet-tdt-0.6b-v3_decoder.onnx",
        checksum="8523d9c6ee4b6059f904c358177691a674dbc3902ef3d776c6c7cab9ea22c071",
    ),
    "ASR/parakeet-tdt-0.6b-v3_joiner.onnx": ModelFile(
        url=f"{UPSTREAM_RELEASE_BASE_URL}/parakeet-tdt-0.6b-v3_joiner.onnx",
        checksum="e22366c5c222c21d1a88083d04536fd314fca441b5fbbfe8a7c600f218736557",
    ),
    "ASR/silero_vad_16k_op15.onnx": ModelFile(
        url=f"{UPSTREAM_RELEASE_BASE_URL}/silero_vad_16k_op15.onnx",
        checksum="7ed98ddbad84ccac4cd0aeb3099049280713df825c610a8ed34543318f1b2c49",
    ),
    "TTS/sirina.onnx": ModelFile(
        url=f"{UPSTREAM_RELEASE_BASE_URL}/glados.onnx",
        checksum="17ea16dd18e1bac343090b8589042b4052f1e5456d42cad8842a4f110de25095",
    ),
    "TTS/kokoro-v1.0.fp16.onnx": ModelFile(
        url=f"{UPSTREAM_RELEASE_BASE_URL}/kokoro-v1.0.fp16.onnx",
        checksum="c1610a859f3bdea01107e73e50100685af38fff88f5cd8e5c56df109ec880204",
    ),
    "TTS/kokoro-voices-v1.0.bin": ModelFile(
        url=f"{UPSTREAM_RELEASE_BASE_URL}/kokoro-voices-v1.0.bin",
        checksum="c5adf5cc911e03b76fa5025c1c225b141310d0c4a721d6ed6e96e73309d0fd88",
    ),
    "TTS/phomenizer_en.onnx": ModelFile(
        url=f"{UPSTREAM_RELEASE_BASE_URL}/phomenizer_en.onnx",
        checksum="b64dbbeca8b350927a0b6ca5c4642e0230173034abd0b5bb72c07680d700c5a0",
    ),
}


ASR_CTC_MODEL_PATH = resource_path("models/ASR/nemo-parakeet_tdt_ctc_110m.onnx")
ASR_CTC_CONFIG_PATH = resource_path("models/ASR/parakeet-tdt_ctc-110m_model_config.yaml")
ASR_TDT_CONFIG_PATH = resource_path("models/ASR/parakeet-tdt-0.6b-v3_model_config.yaml")
ASR_TDT_ENCODER_MODEL_PATH = resource_path("models/ASR/parakeet-tdt-0.6b-v3_encoder.onnx")
ASR_TDT_DECODER_MODEL_PATH = resource_path("models/ASR/parakeet-tdt-0.6b-v3_decoder.onnx")
ASR_TDT_JOINER_MODEL_PATH = resource_path("models/ASR/parakeet-tdt-0.6b-v3_joiner.onnx")
ASR_VAD_MODEL_PATH = resource_path("models/ASR/silero_vad_16k_op15.onnx")

TTS_SIRINA_MODEL_PATH = resource_path("models/TTS/sirina.onnx")
TTS_SIRINA_PHONEME_TO_ID_PATH = resource_path("models/TTS/phoneme_to_id.pkl")
TTS_KOKORO_MODEL_PATH = resource_path("models/TTS/kokoro-v1.0.fp16.onnx")
TTS_KOKORO_VOICES_PATH = resource_path("models/TTS/kokoro-voices-v1.0.bin")
TTS_PHONEMIZER_MODEL_PATH = resource_path("models/TTS/phomenizer_en.onnx")
TTS_PHONEMIZER_DICT_PATH = resource_path("models/TTS/lang_phoneme_dict.pkl")
TTS_PHONEMIZER_TOKEN_TO_IDX_PATH = resource_path("models/TTS/token_to_idx.pkl")
TTS_PHONEMIZER_IDX_TO_TOKEN_PATH = resource_path("models/TTS/idx_to_token.pkl")

DEFAULT_TTS_VOICE = "sirina"
DEFAULT_KOKORO_VOICE = "af_alloy"
DEFAULT_STT_ENGINE = "tdt"

INPUT_SAMPLE_RATE = 16000
VAD_FRAME_MS = 32
VAD_THRESHOLD = 0.8
SIRINA_AUDIO_INPUT_DEVICE = os.getenv("SIRINA_AUDIO_INPUT_DEVICE", "auto")
SIRINA_AUDIO_OUTPUT_DEVICE = os.getenv("SIRINA_AUDIO_OUTPUT_DEVICE", "auto")
LISTEN_SILENCE_SECONDS = 0.8
LISTEN_MAX_SECONDS = 20.0
LISTEN_SPEECH_START_TIMEOUT_SECONDS = 10.0

KOKORO_SAMPLE_RATE = 24000
KOKORO_MAX_PHONEME_LENGTH = 510
KOKORO_TRAILING_SILENCE_SAMPLES = 8000

ORT_LOGGER_SEVERITY = 4
DISABLED_ONNX_PROVIDERS = ("TensorrtExecutionProvider", "CoreMLExecutionProvider")


def configure_onnxruntime_logging() -> None:
    import onnxruntime as ort  # type: ignore

    ort.set_default_logger_severity(ORT_LOGGER_SEVERITY)


def get_onnx_providers(prefer_cuda: bool = False) -> list[str]:
    import onnxruntime as ort  # type: ignore

    providers = [provider for provider in ort.get_available_providers() if provider not in DISABLED_ONNX_PROVIDERS]
    if prefer_cuda:
        if "CUDAExecutionProvider" in providers:
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]
        return ["CPUExecutionProvider"]
    return providers
