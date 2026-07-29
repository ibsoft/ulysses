import logging

from sirina_agent.config.models import UlyssesConfig
from sirina_agent.security.commands import CommandPolicy, CommandRunner


def test_command_filtering(tmp_path):
    policy = CommandPolicy(["pwd", "ls"], ["rm"], tmp_path, ["PATH"])
    assert policy.evaluate("pwd").allowed
    sudo = policy.evaluate("sudo apt update")
    assert sudo.allowed
    assert sudo.sudo_password_required
    assert sudo.requires_typed_confirmation
    denied = policy.evaluate("rm -rf /")
    assert not denied.allowed
    assert denied.high_risk
    assert not policy.evaluate("bash -lc whoami").allowed


def test_godmode_allows_non_allowlisted_and_denied_commands_with_confirmation(tmp_path):
    policy = CommandPolicy(["pwd"], ["rm"], tmp_path, ["PATH"], godmode=True)
    bash = policy.evaluate("bash -lc whoami")
    assert bash.allowed
    assert bash.reason == "allowed by godmode"
    curl = policy.evaluate("curl -I https://example.com")
    assert curl.allowed
    assert curl.reason == "allowed by godmode"

    destructive = policy.evaluate("rm -rf /tmp/example")
    assert destructive.allowed
    assert destructive.high_risk
    assert not destructive.requires_typed_confirmation
    sudo = policy.evaluate("sudo id")
    assert sudo.allowed
    assert sudo.sudo_password_required
    assert not sudo.requires_confirmation
    assert not sudo.requires_typed_confirmation


def test_default_policy_allows_pentest_tools_and_flags_intrusive_ones(tmp_path):
    cfg = UlyssesConfig()
    policy = CommandPolicy(
        cfg.skills.command.allowed_commands,
        cfg.skills.command.denied_commands,
        tmp_path,
        cfg.skills.command.env_allowlist,
        require_typed_confirmation_for_high_risk=True,
        bypass_confirmation_for_allowed_commands=True,
    )

    passive = policy.evaluate("whatweb https://example.com")
    assert passive.allowed
    assert not passive.requires_confirmation

    tool_lookup = policy.evaluate("which curl")
    assert tool_lookup.allowed
    assert not tool_lookup.high_risk
    assert not tool_lookup.requires_confirmation

    header_check = policy.evaluate("curl -I https://example.com")
    assert header_check.allowed
    assert not header_check.high_risk
    assert not header_check.requires_confirmation

    intrusive = policy.evaluate("sqlmap -u https://example.com/item?id=1 --batch")
    assert intrusive.allowed
    assert intrusive.high_risk
    assert intrusive.requires_typed_confirmation


def test_default_policy_allows_installers_as_high_risk(tmp_path):
    cfg = UlyssesConfig()
    policy = CommandPolicy(
        cfg.skills.command.allowed_commands,
        cfg.skills.command.denied_commands,
        tmp_path,
        cfg.skills.command.env_allowlist,
        require_typed_confirmation_for_high_risk=True,
        bypass_confirmation_for_allowed_commands=True,
    )

    install = policy.evaluate("apt-get install -y nikto")

    assert install.allowed
    assert install.high_risk
    assert install.requires_typed_confirmation


def test_command_runner(tmp_path):
    policy = CommandPolicy(["pwd"], [], tmp_path, ["PATH"], require_confirmation=False)
    result = CommandRunner(policy, logging.getLogger("test"), 2, 1000).run(["pwd"])
    assert result["returncode"] == 0
    assert str(tmp_path) in result["stdout"]


def test_command_runner_reports_missing_executable(tmp_path):
    policy = CommandPolicy(["ulysses-missing-tool"], [], tmp_path, ["PATH"], require_confirmation=False)
    result = CommandRunner(policy, logging.getLogger("test"), 2, 1000).run(["ulysses-missing-tool"])

    assert result["returncode"] == 127
    assert "command not found: ulysses-missing-tool" in result["stderr"]
    assert "install it" in result["stderr"]
