import queue
import threading
from typing import Any

from loguru import logger
import numpy as np
from numpy.typing import NDArray
import sounddevice as sd  # type: ignore

from .. import config
from .devices import DeviceSetting, format_audio_devices, resolve_audio_device


def get_input_device(device: DeviceSetting = None) -> int | None:
    requested = config.SIRINA_AUDIO_INPUT_DEVICE if device is None else device
    return resolve_audio_device(list(sd.query_devices()), requested, "input", sd.default.device)


def get_output_device(device: DeviceSetting = None) -> int | None:
    requested = config.SIRINA_AUDIO_OUTPUT_DEVICE if device is None else device
    return resolve_audio_device(
        list(sd.query_devices()),
        requested,
        "output",
        sd.default.device,
    )


def describe_audio_devices() -> str:
    return format_audio_devices(list(sd.query_devices()), sd.default.device)


class SoundDeviceAudioIO:
    """Audio I/O implementation using sounddevice for both input and output.

    This class provides an implementation of the AudioIO interface using the
    sounddevice library to interact with system audio devices. It handles
    real-time audio capture with voice activity detection and audio playback.
    """

    SAMPLE_RATE: int = config.INPUT_SAMPLE_RATE
    VAD_SIZE: int = config.VAD_FRAME_MS
    VAD_THRESHOLD: float = config.VAD_THRESHOLD

    @staticmethod
    def _as_mono_float32(audio_data: NDArray[np.float32]) -> NDArray[np.float32]:
        """Normalize audio buffers to a contiguous 1-D float32 mono array."""
        audio = np.asarray(audio_data, dtype=np.float32)
        if audio.ndim == 0:
            audio = audio.reshape(1)
        elif audio.ndim == 2:
            if audio.shape[1] == 1:
                audio = audio[:, 0]
            else:
                audio = audio.mean(axis=1)
        elif audio.ndim > 2:
            audio = audio.reshape(audio.shape[0], -1).mean(axis=1)
        return np.ascontiguousarray(audio)

    def __init__(
        self,
        vad_threshold: float | None = None,
        input_device: DeviceSetting = None,
        output_device: DeviceSetting = None,
    ) -> None:
        """Initialize the sounddevice audio I/O.

        Args:
            vad_threshold: Threshold for VAD detection (default: 0.8)

        Raises:
            ImportError: If the sounddevice module is not available
            ValueError: If invalid parameters are provided
        """
        if vad_threshold is None:
            self.vad_threshold = self.VAD_THRESHOLD
        else:
            self.vad_threshold = vad_threshold

        if not 0 <= self.vad_threshold <= 1:
            raise ValueError("VAD threshold must be between 0 and 1")

        from .vad import VAD

        self.input_device = get_input_device(input_device)
        self.output_device = get_output_device(output_device)
        self._vad_model = VAD()

        self._sample_queue: queue.Queue[tuple[NDArray[np.float32], bool]] = queue.Queue()
        self.input_stream: sd.InputStream | None = None
        self._is_playing = False
        self._stop_event = threading.Event()
        self._pending_audio: NDArray[np.float32] | None = None
        self._pending_sample_rate: int = self.SAMPLE_RATE

    def start_listening(self) -> None:
        """Start capturing audio from the system microphone.

        Creates and starts a sounddevice InputStream that continuously captures
        audio from the default input device. Each audio chunk is processed with
        the VAD model and placed in the sample queue.

        Raises:
            RuntimeError: If the audio input stream cannot be started
            sd.PortAudioError: If there's an issue with the audio hardware
        """
        if self.input_stream is not None:
            self.stop_listening()

        def audio_callback(
            indata: NDArray[np.float32],
            frames: int,
            time: sd.CallbackStop,
            status: sd.CallbackFlags,
        ) -> None:
            """Process incoming audio data and put it in the queue with VAD confidence.

            Parameters:
                indata: Input audio data from the sounddevice stream
                frames: Number of audio frames in the current chunk
                time: Timing information for the audio callback
                status: Status flags for the audio callback

            Notes:
                - Copies and squeezes the input data to ensure single-channel processing
                - Applies voice activity detection to determine speech presence
                - Puts processed audio samples and VAD confidence into a thread-safe queue
            """
            if status:
                # Log any errors for debugging
                logger.debug(f"Audio callback status: {status}")

            data = np.array(indata).copy().squeeze()  # Reduce to single channel if necessary
            vad_value = self._vad_model(np.expand_dims(data, 0))
            vad_confidence = vad_value > self.vad_threshold
            self._sample_queue.put((data, bool(vad_confidence)))

        try:
            self.input_stream = sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                channels=1,
                device=self.input_device,
                callback=audio_callback,
                blocksize=int(self.SAMPLE_RATE * self.VAD_SIZE / 1000),
            )
            self.input_stream.start()
        except sd.PortAudioError as e:
            raise RuntimeError(f"Failed to start audio input stream: {e}") from e

    def stop_listening(self) -> None:
        """Stop capturing audio and clean up resources.

        Stops the input stream if it's active and releases associated resources.
        This method should be called when audio input is no longer needed or
        before application shutdown.
        """
        if self.input_stream is not None:
            try:
                self.input_stream.stop()
                self.input_stream.close()
            except Exception as e:
                logger.error(f"Error stopping input stream: {e}")
            finally:
                self.input_stream = None

    def start_speaking(self, audio_data: NDArray[np.float32], sample_rate: int | None = None, text: str = "") -> None:
        """Queue audio for playback through the system speakers.

        Stores audio data for playback via measure_percentage_spoken(), which
        uses a single OutputStream to both play and monitor progress. This avoids
        the race condition that occurs when sd.play() and a monitoring OutputStream
        run concurrently.

        Parameters:
            audio_data: The audio data to play as a numpy float32 array
            sample_rate: The sample rate of the audio data in Hz
            text: Optional text associated with the audio (not used by this implementation)

        Raises:
            ValueError: If audio_data is empty or not a valid numpy array
        """
        if not isinstance(audio_data, np.ndarray) or audio_data.size == 0:
            raise ValueError("Invalid audio data")
        audio_data = self._as_mono_float32(audio_data)

        if sample_rate is None:
            sample_rate = self.SAMPLE_RATE

        # Stop any existing playback and create a fresh stop event for this session
        self.stop_speaking()
        self._stop_event = threading.Event()

        logger.debug(f"Playing audio with sample rate: {sample_rate} Hz, length: {len(audio_data)} samples")
        self._is_playing = True
        self._pending_audio = audio_data
        self._pending_sample_rate = sample_rate

    def measure_percentage_spoken(self, total_samples: int, sample_rate: int | None = None) -> tuple[bool, int]:
        """
        Play queued audio and monitor playback progress with interrupt detection.

        Uses a single OutputStream to both play the audio stored by start_speaking()
        and track progress, avoiding the race condition from running sd.play() and a
        separate monitoring stream concurrently.

        Args:
            total_samples (int): Total number of samples in the audio data being played.
            sample_rate (int | None): Sample rate override; uses the value from start_speaking() if None.
        Returns:
            tuple[bool, int]: A tuple containing:
                - bool: True if playback was interrupted, False if completed normally
                - int: Percentage of audio played (0-100)
        """
        audio_data = self._pending_audio
        if audio_data is None:
            return False, 100

        if sample_rate is None:
            sample_rate = self._pending_sample_rate

        if sample_rate <= 0:
            logger.warning(f"Invalid sample rate {sample_rate}; skipping playback")
            if self._pending_audio is audio_data:
                self._pending_audio = None
                self._is_playing = False
            return False, 100

        # Derive playback length from the actual buffer so a wrong caller-supplied
        # total_samples can't break the timeout or percentage math.
        effective_total = len(audio_data)
        if effective_total <= 0:
            if self._pending_audio is audio_data:
                self._pending_audio = None
                self._is_playing = False
            return False, 100

        position = 0
        interrupted = False
        completion_event = threading.Event()
        # Capture current stop_event so a new start_speaking() call doesn't affect this session
        stop_event = self._stop_event

        def stream_callback(
            outdata: NDArray[np.float32], frames: int, time_info: Any, status: sd.CallbackFlags
        ) -> None:
            nonlocal position, interrupted

            if stop_event.is_set():
                outdata.fill(0)
                interrupted = True
                completion_event.set()
                raise sd.CallbackStop

            remaining = effective_total - position
            chunk_size = min(frames, remaining)

            if chunk_size > 0:
                outdata[:chunk_size, 0] = audio_data[position : position + chunk_size]
                if chunk_size < frames:
                    outdata[chunk_size:].fill(0)
                position += chunk_size
            else:
                outdata.fill(0)

            if position >= effective_total:
                completion_event.set()
                raise sd.CallbackStop

        try:
            logger.debug(f"Using sample rate: {sample_rate} Hz, total samples: {effective_total}")
            max_timeout = effective_total / sample_rate + 1
            with sd.OutputStream(
                callback=stream_callback,
                samplerate=sample_rate,
                channels=1,
                device=self.output_device,
            ):
                completed = completion_event.wait(max_timeout)
                if not completed:
                    # Timeout: signal stop and mark as interrupted
                    stop_event.set()
                    interrupted = True
                    logger.debug("Audio playback timed out, forcing interruption")

        except (sd.PortAudioError, RuntimeError):
            logger.debug("Audio stream already closed or invalid")

        # Identity-checked teardown: only clear shared state if it still belongs to this
        # session, otherwise a new start_speaking() that ran concurrently could be wiped out.
        if self._pending_audio is audio_data:
            self._pending_audio = None
        if self._stop_event is stop_event:
            self._is_playing = False
        percentage_played = min(int(position / effective_total * 100), 100)
        return interrupted, percentage_played

    def check_if_speaking(self) -> bool:
        """Check if audio is currently being played.

        Returns:
            bool: True if audio is currently playing, False otherwise
        """
        return self._is_playing

    def stop_speaking(self) -> None:
        """Stop audio playback and clean up resources.

        Signals the current playback session to stop by setting the stop event.
        The active OutputStream callback will detect this on its next invocation
        and raise CallbackStop to cleanly terminate the stream.
        """
        if self._is_playing:
            self._stop_event.set()
            self._is_playing = False

    def get_sample_queue(self) -> queue.Queue[tuple[NDArray[np.float32], bool]]:
        """Get the queue containing audio samples and VAD confidence.

        Returns:
            queue.Queue: A thread-safe queue containing tuples of
                        (audio_sample, vad_confidence)
        """
        return self._sample_queue
