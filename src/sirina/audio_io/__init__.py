"""Audio input/output components.

This package provides an abstraction layer for audio input and output operations,
allowing the Sirina engine to work with different audio backends interchangeably.

Classes:
    AudioIO: Abstract interface for audio input/output operations
    SoundDeviceAudioIO: Implementation using the sounddevice library
    WebSocketAudioIO: Implementation using WebSockets for network streaming

Functions:
    create_audio_io: Factory function to create AudioIO instances
"""

import queue
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from .devices import DeviceSetting


class AudioProtocol(Protocol):
    def __init__(
        self,
        vad_threshold: float | None = None,
        input_device: DeviceSetting = None,
        output_device: DeviceSetting = None,
    ) -> None: ...
    def start_listening(self) -> None: ...
    def stop_listening(self) -> None: ...
    def start_speaking(
        self, audio_data: NDArray[np.float32], sample_rate: int | None = None, text: str = ""
    ) -> None: ...
    def measure_percentage_spoken(self, total_samples: int, sample_rate: int | None = None) -> tuple[bool, int]: ...
    def check_if_speaking(self) -> bool: ...
    def stop_speaking(self) -> None: ...
    def get_sample_queue(self) -> queue.Queue[tuple[NDArray[np.float32], bool]]: ...


# Factory function
def get_audio_system(
    backend_type: str = "sounddevice",
    vad_threshold: float | None = None,
    input_device: DeviceSetting = None,
    output_device: DeviceSetting = None,
) -> AudioProtocol:
    """
    Factory function to get an instance of an audio I/O system based on the specified backend type.

    Parameters:
        backend_type (str): The type of audio backend to use:
            - "sounddevice": Uses the sounddevice library for local audio I/O
            - "websocket": Network-based audio I/O (not yet implemented)
        vad_threshold (float | None): Optional threshold for voice activity detection

    Returns:
        AudioProtocol: An instance of the requested audio I/O system

    Raises:
        ValueError: If the specified backend type is not supported
    """
    if backend_type == "sounddevice":
        from .sounddevice_io import SoundDeviceAudioIO

        return SoundDeviceAudioIO(
            vad_threshold=vad_threshold,
            input_device=input_device,
            output_device=output_device,
        )
    elif backend_type == "websocket":
        raise ValueError("WebSocket audio backend is not yet implemented.")
    else:
        raise ValueError(f"Unsupported audio backend type: {backend_type}")


def __getattr__(name: str):
    if name == "VAD":
        from .vad import VAD

        return VAD
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "VAD",
    "AudioProtocol",
    "DeviceSetting",
    "get_audio_system",
]
