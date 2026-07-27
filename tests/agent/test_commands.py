import logging

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

    destructive = policy.evaluate("rm -rf /tmp/example")
    assert destructive.allowed
    assert destructive.high_risk
    assert destructive.requires_typed_confirmation
    sudo = policy.evaluate("sudo id")
    assert sudo.allowed
    assert not sudo.sudo_password_required


def test_command_runner(tmp_path):
    policy = CommandPolicy(["pwd"], [], tmp_path, ["PATH"], require_confirmation=False)
    result = CommandRunner(policy, logging.getLogger("test"), 2, 1000).run(["pwd"])
    assert result["returncode"] == 0
    assert str(tmp_path) in result["stdout"]
