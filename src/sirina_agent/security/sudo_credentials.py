from __future__ import annotations

import getpass
import hashlib
import importlib.util
from pathlib import Path
import shutil
import sys


class SudoCredentialStore:
    """Store the Godmode sudo password in the operating system credential vault."""

    SERVICE = "ulysses-godmode-sudo"

    def __init__(self, config_path: Path) -> None:
        identity = f"{getpass.getuser()}:{config_path.expanduser().resolve()}"
        self.account = hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def get(self) -> str | None:
        keyring = self._secure_keyring()
        return keyring.get_password(self.SERVICE, self.account)

    def set(self, password: str) -> None:
        if not password:
            raise ValueError("sudo password cannot be empty")
        keyring = self._secure_keyring()
        keyring.set_password(self.SERVICE, self.account, password)

    def clear(self) -> None:
        keyring = self._secure_keyring()
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(self.SERVICE, self.account)
        except PasswordDeleteError:
            pass

    def readiness(self) -> tuple[bool, str]:
        missing: list[str] = []
        if shutil.which("gnome-keyring-daemon") is None:
            missing.append("GNOME Keyring")
        if importlib.util.find_spec("secretstorage") is None:
            missing.append("Python SecretStorage")
        if not missing:
            try:
                self._secure_keyring()
            except Exception as exc:
                return False, (
                    f"Credential prerequisites are installed, but the encrypted vault is not active: {exc}. "
                    "Log out and back in, then enable Godmode again."
                )
            return True, "Encrypted operating-system credential vault is ready."
        return False, f"Missing prerequisites: {', '.join(missing)}. {self.installation_guidance()}"

    @staticmethod
    def installation_guidance() -> str:
        return (
            "Proposed installation:\n"
            "sudo apt update\n"
            "sudo apt install -y gnome-keyring libsecret-tools\n"
            f"{sys.executable} -m pip install SecretStorage\n"
            "Then log out and back in before enabling Godmode again."
        )

    @staticmethod
    def _secure_keyring():
        import keyring

        backend = keyring.get_keyring()
        backend_name = f"{backend.__class__.__module__}.{backend.__class__.__name__}".lower()
        if getattr(backend, "priority", 0) <= 0 or any(name in backend_name for name in ("fail", "null", "plaintext")):
            raise RuntimeError("no encrypted operating-system credential vault is available")
        return keyring
