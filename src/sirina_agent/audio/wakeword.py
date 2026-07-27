from __future__ import annotations


class WakeWordDetector:
    def __init__(self, config) -> None:
        self.config = config
        self._model = None

    def available(self) -> bool:
        try:
            import openwakeword  # noqa: F401

            return True
        except Exception:
            return False

    def wait(self) -> bool:
        """Placeholder adapter. Real deployments can replace this with streaming openWakeWord inference."""
        if not self.config.wake_word.enabled:
            return True
        if not self.available():
            return True
        return True
