from __future__ import annotations

from pathlib import Path

import numpy as np
from numpy.typing import NDArray


def _as_mono(audio: NDArray[np.float32]) -> NDArray[np.float32]:
    if audio.ndim == 1:
        return audio
    if audio.ndim == 2:
        if audio.shape[1] == 1:
            return audio[:, 0]
        return np.asarray(audio.mean(axis=1), dtype=np.float32)
    return np.asarray(audio.reshape(audio.shape[0], -1).mean(axis=1), dtype=np.float32)


def resample_audio(
    audio: NDArray[np.float32],
    source_sample_rate: int,
    target_sample_rate: int,
) -> NDArray[np.float32]:
    """Resample mono audio with linear interpolation."""
    if source_sample_rate <= 0 or target_sample_rate <= 0:
        raise ValueError("sample rates must be positive")
    audio = np.asarray(audio, dtype=np.float32)
    if source_sample_rate == target_sample_rate or audio.size == 0:
        return audio.astype(np.float32, copy=False)

    duration = audio.size / source_sample_rate
    target_size = max(1, round(duration * target_sample_rate))
    source_positions = np.arange(audio.size, dtype=np.float64) / source_sample_rate
    target_positions = np.arange(target_size, dtype=np.float64) / target_sample_rate
    return np.interp(target_positions, source_positions, audio).astype(np.float32)


def load_transcription_audio(audio_path: Path, target_sample_rate: int) -> NDArray[np.float32]:
    import soundfile as sf  # type: ignore

    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    try:
        audio, sample_rate = sf.read(audio_path, dtype="float32", always_2d=True)
    except sf.SoundFileError as e:
        raise sf.SoundFileError(f"Error reading audio file {audio_path}: {e}") from e
    except Exception as e:
        raise ValueError(f"Failed to load audio file {audio_path}: {e}") from e

    audio = _as_mono(np.asarray(audio, dtype=np.float32))
    if audio.size == 0:
        raise ValueError(f"Audio file {audio_path} is empty or has no valid samples.")
    if sample_rate != target_sample_rate:
        audio = resample_audio(audio, sample_rate, target_sample_rate)
    return audio
