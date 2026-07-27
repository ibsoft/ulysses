from sirina_agent.core.defense import AutonomousDefenseEngine


def test_defense_engine_detects_bruteforce_and_port_scan():
    engine = AutonomousDefenseEngine()
    journal = "\n".join(
        [
            "sshd[1]: Failed password for invalid user admin from 10.0.0.7 port 50100 ssh2",
            "sshd[1]: authentication failure from 10.0.0.7",
            "sshd[1]: Failed password for root from 10.0.0.7 port 50101 ssh2",
            "kernel: UFW BLOCK IN=eth0 SRC=10.0.0.8 DST=10.0.0.2 DPT=22",
            "kernel: UFW BLOCK IN=eth0 SRC=10.0.0.8 DST=10.0.0.2 DPT=80",
            "kernel: UFW BLOCK IN=eth0 SRC=10.0.0.8 DST=10.0.0.2 DPT=443",
            "kernel: UFW BLOCK IN=eth0 SRC=10.0.0.8 DST=10.0.0.2 DPT=8080",
        ]
    )

    findings = engine._journal_findings(journal)

    assert any(finding.attacker_ip == "10.0.0.7" and "brute-force" in finding.title for finding in findings)
    assert any(finding.attacker_ip == "10.0.0.8" and "port-scan" in finding.title for finding in findings)


def test_defense_engine_plans_blocks_and_security_app_install():
    engine = AutonomousDefenseEngine()
    assessment = engine.run(
        lambda check: (
            False if check.name in {"ufw_present", "fail2ban_present", "auditd_present"} else True,
            "sshd[1]: Failed password for root from 10.0.0.7 port 50101 ssh2\n" * 8
            if check.name == "journal_warnings"
            else "",
        )
    )

    actions = engine.plan_actions(assessment, auto_block_attackers=True, install_missing_security_apps=True)

    assert any(action.command == "sudo ufw deny from 10.0.0.7" for action in actions)
    assert any(action.command == "sudo apt-get install -y ufw fail2ban auditd" for action in actions)
