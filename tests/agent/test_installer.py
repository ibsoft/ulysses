from pathlib import Path

INSTALLER = Path(__file__).parents[2] / "scripts" / "install-ulysses-linux"


def test_upgrade_preserves_runtime_and_models():
    script = INSTALLER.read_text(encoding="utf-8")

    assert 'RUNTIME_BACKUP="${APP_HOME}/.runtime.backup.$$"' in script
    assert 'mv "${RUNTIME_BACKUP}" "${APP_SOURCE}/var/ulysses"' in script
    assert 'mv "${MODEL_BACKUP}" "${APP_SOURCE}/models"' in script
    assert 'mv "${SKILLS_BACKUP}" "${APP_SOURCE}/skills"' in script
    assert 'rm -rf "${APP_SOURCE}/var/ulysses"\nmkdir' not in script


def test_sync_excludes_development_state_and_recovers_interrupted_backups():
    script = INSTALLER.read_text(encoding="utf-8")

    assert "recover_stale_backup" in script
    assert 'recover_stale_backup ".runtime" "${APP_SOURCE}/var/ulysses"' in script
    assert "trap restore_sync_backups EXIT" in script
    assert "trap - EXIT" in script
    assert "--exclude='./.venv'" in script
    assert "--exclude='./.git'" in script
    assert "--exclude='./models'" in script
    assert 'cp -a "${PROJECT_ROOT}/." "${APP_SOURCE}/"' not in script


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
    assert 'TOTAL_STEPS=6' in script
    assert '"Preserving existing environment and models"' in script


def test_installer_has_terminal_safe_ulysses_banner():
    script = INSTALLER.read_text(encoding="utf-8")

    assert "U L Y S S E S" in script
    assert "CYBER  SENTINEL" in script
    assert "LOCAL-FIRST SECURITY AGENT" in script
    assert "by CyberPhylax" in script
    assert "www.cyberphylax.com" in script
    assert "Copyleft 2026 - Ioannis A. Bouhras <ioannis.bouhras@gmail.com>" in script
    assert '[[ -t 1 ]]' in script
    assert '[[ -z "${NO_COLOR:-}" ]]' in script


def test_installer_banner_has_continuous_equal_width_border():
    script = INSTALLER.read_text(encoding="utf-8")
    banner = script.split("cat <<'EOF'\n", 1)[1].split("\nEOF", 1)[0]
    bordered_lines = [line for line in banner.splitlines() if line]

    assert {len(line) for line in bordered_lines} == {54}
    assert all(line[0] in "╭│╰" and line[-1] in "╮│╯" for line in bordered_lines)


def test_installer_animates_each_phase_and_preserves_failure_output():
    script = INSTALLER.read_text(encoding="utf-8")

    assert "run_animated()" in script
    assert "frames=('⠋' '⠙' '⠹'" in script
    assert 'kill -0 "${pid}"' in script
    assert 'sed \'s/^/    /\' "${log_file}" >&2' in script
    assert "ULYSSES DEPLOYMENT COMPLETE" in script


def test_installer_discovers_and_persists_codex_executable():
    script = INSTALLER.read_text(encoding="utf-8")

    assert 'CODEX_BIN_DISCOVERED="$(command -v codex 2>/dev/null || true)"' in script
    assert 'ULYSSES_CODEX_BIN=${CODEX_BIN_DISCOVERED}' in script


def test_installer_reports_optional_native_clipboard_dependencies():
    script = INSTALLER.read_text(encoding="utf-8")

    assert "Ctrl+V requires xclip or xsel on X11" in script
    assert "Ctrl+V requires wl-clipboard on Wayland" in script
