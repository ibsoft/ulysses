from pathlib import Path


INSTALLER = Path(__file__).parents[2] / "scripts" / "install-ulysses-linux"


def test_upgrade_preserves_runtime_and_models():
    script = INSTALLER.read_text(encoding="utf-8")

    assert 'RUNTIME_BACKUP="${APP_HOME}/.runtime.backup.$$"' in script
    assert 'mv "${RUNTIME_BACKUP}" "${APP_SOURCE}/var/ulysses"' in script
    assert 'mv "${MODEL_BACKUP}" "${APP_SOURCE}/models"' in script
    assert 'rm -rf "${APP_SOURCE}/var/ulysses"\nmkdir' not in script


def test_upgrade_refreshes_config_with_backup_by_default():
    script = INSTALLER.read_text(encoding="utf-8")

    assert "--preserve-config" in script
    assert 'ulysses.yaml.backup-$(date +%Y%m%d-%H%M%S)' in script
    assert 'cp -p "${CONFIG_HOME}/ulysses.yaml" "${CONFIG_BACKUP}"' in script
    assert 'cp "${APP_SOURCE}/config/ulysses.yaml" "${CONFIG_HOME}/ulysses.yaml"' in script


def test_sync_only_skips_environment_and_model_installation():
    script = INSTALLER.read_text(encoding="utf-8")

    assert "--sync-only" in script
    assert 'if [[ "${SYNC_ONLY}" == true ]]; then' in script
    assert "--sync-only requires an existing installation" in script
    assert "Existing virtual environment and models preserved" in script
