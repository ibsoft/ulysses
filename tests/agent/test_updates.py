import json
from types import SimpleNamespace

import sirina_agent.updates as update_module
from sirina_agent.updates import UpdateManager


def _config(tmp_path):
    return SimpleNamespace(
        repository_url="https://github.com/example/project.git",
        branch="main",
        metadata_path=tmp_path / ".ulysses-build.json",
        updater_path=tmp_path / "update",
        timeout_seconds=2,
        install_timeout_seconds=10,
    )


def test_update_check_reports_available_commit(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config.metadata_path.write_text(json.dumps({"main_commit": "a" * 40}), encoding="utf-8")
    monkeypatch.setattr(
        update_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=f"{'b' * 40}\trefs/heads/main\n{'b' * 40}\trefs/heads/v_2.1.0\n",
            stderr="",
        ),
    )

    status = UpdateManager(config).check()

    assert status.state == "available"
    assert status.installed_commit == "a" * 40
    assert status.latest_commit == "b" * 40
    assert status.latest_branch == "v_2.1.0"


def test_update_check_reports_current_commit(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config.metadata_path.write_text(json.dumps({"main_commit": "a" * 40}), encoding="utf-8")
    monkeypatch.setattr(
        update_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=f"{'a' * 40}\trefs/heads/main\n{'a' * 40}\trefs/heads/v_2.0.13\n",
            stderr="",
        ),
    )

    status = UpdateManager(config).check()
    assert status.state == "current"
    assert status.latest_branch == "v_2.0.13"


def test_update_install_uses_configured_updater_and_preserves_configuration(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config.updater_path.write_text("#!/bin/sh\n", encoding="utf-8")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="installed", stderr="")

    monkeypatch.setattr(update_module.subprocess, "run", run)

    message = UpdateManager(config).install()

    assert calls == [[str(config.updater_path), config.repository_url, "main"]]
    assert "configuration preserved" in message
