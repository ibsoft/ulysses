from pathlib import Path

from sirina_agent.security.sudo_credentials import SudoCredentialStore


def test_godmode_credential_preflight_proposes_missing_prerequisites(monkeypatch) -> None:
    store = SudoCredentialStore(Path("/tmp/ulysses-test.yaml"))
    monkeypatch.setattr("sirina_agent.security.sudo_credentials.shutil.which", lambda name: None)
    monkeypatch.setattr("sirina_agent.security.sudo_credentials.importlib.util.find_spec", lambda name: None)

    ready, guidance = store.readiness()

    assert not ready
    assert "GNOME Keyring" in guidance
    assert "Python SecretStorage" in guidance
    assert "sudo apt install" in guidance
    assert "pip install SecretStorage" in guidance


def test_godmode_credential_preflight_accepts_secure_backend(monkeypatch) -> None:
    store = SudoCredentialStore(Path("/tmp/ulysses-test.yaml"))
    monkeypatch.setattr("sirina_agent.security.sudo_credentials.shutil.which", lambda name: "/usr/bin/fake")
    monkeypatch.setattr(
        "sirina_agent.security.sudo_credentials.importlib.util.find_spec",
        lambda name: object(),
    )
    monkeypatch.setattr(store, "_secure_keyring", lambda: object())

    ready, guidance = store.readiness()

    assert ready
    assert "ready" in guidance
