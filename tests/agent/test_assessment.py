from sirina_agent.core.assessment import (
    AssessmentCheck,
    AssessmentResult,
    assessment_checks,
    assessment_tool_options,
    missing_tool_installer_script,
    missing_tool_packages,
    render_assessment_report,
)


def result(check_id: str, command: str, output: str, ok: bool = True) -> AssessmentResult:
    return AssessmentResult(AssessmentCheck(check_id, "Test", command), output, ok)


def test_complete_baseline_has_real_security_checks():
    checks = assessment_checks("https://www.example.com/path")
    ids = {check.id for check in checks}

    assert ids == {
        "dns",
        "http-headers",
        "service-scan",
        "web-fingerprint",
        "tls",
        "web-misconfiguration",
        "template-scan",
    }
    assert all("/path" not in check.command for check in checks)


def test_assessment_tool_options_present_operator_choice_details():
    options = assessment_tool_options("www.example.com", "custom-scanner www.example.com")

    assert options[0].name == "DNS lookup"
    assert options[0].purpose
    assert options[0].command == "dig +short www.example.com"
    assert options[-1].id == "requested-check"
    assert options[-1].command == "custom-scanner www.example.com"


def test_customer_report_excludes_internal_execution_failures():
    report = render_assessment_report(
        "example.com",
        [
            result("web-misconfiguration", "nikto ...", "nikto is not in the allowlist", False),
            result("tls", "sslscan ...", "command not found: sslscan", False),
        ],
    )

    assert "not in the allowlist" not in report
    assert "command not found" not in report
    assert "Tool unavailable" not in report
    assert "Blocked" not in report
    assert "timed out" not in report
    assert "## Executive Summary" in report
    assert "## Management Summary" in report
    assert "## Technical Summary" in report


def test_customer_report_excludes_successful_recovery_operations():
    report = render_assessment_report(
        "example.com",
        [
            AssessmentResult(
                AssessmentCheck("install-missing-tools", "Recovery", "sudo python3 installer.py nikto"),
                "Installing package nikto... completed",
                True,
            ),
            result("service-scan", "nmap ...", "443/tcp open https nginx"),
        ],
    )

    assert "Installing package" not in report
    assert "install-missing-tools" not in report
    assert "installer.py" not in report


def test_customer_report_has_delivery_ready_structure():
    report = render_assessment_report(
        "example.com",
        [result("service-scan", "nmap ...", "443/tcp open https nginx")],
    )

    for section in (
        "Classification: Confidential - Customer Delivery",
        "## Document Control",
        "## Executive Summary",
        "## Management Summary",
        "## Technical Summary",
        "### Risk Profile",
        "## Scope and Engagement Profile",
        "## Methodology",
        "## Severity Rating Method",
        "## Findings Register",
        "## Detailed Findings",
        "## Remediation Roadmap",
        "## Retest and Closure",
        "## Technical Evidence Appendix",
        "## Assumptions and Limitations",
        "## Confidentiality Notice",
    ):
        assert section in report


def test_report_parses_missing_security_headers_without_inventing_high_severity():
    report = render_assessment_report(
        "example.com",
        [result("http-headers", "curl ...", "HTTP/2 200\nserver: nginx\ncontent-type: text/html\n")],
    )

    assert "HSTS header not observed" in report
    assert "Content Security Policy header not observed" in report
    assert "| Critical | 0 |" in report
    assert "| High | 0 |" in report


def test_header_parser_uses_final_redirect_response():
    report = render_assessment_report(
        "example.com",
        [
            result(
                "http-headers",
                "curl ...",
                "HTTP/1.1 301 Moved\nstrict-transport-security: max-age=100\nlocation: /final\n\n"
                "HTTP/2 200\nserver: nginx\ncontent-type: text/html\n",
            )
        ],
    )

    assert "HSTS header not observed" in report


def test_report_parses_nmap_sensitive_service_and_nuclei_jsonl():
    report = render_assessment_report(
        "example.com",
        [
            result("service-scan", "nmap ...", "3306/tcp open  mysql  MySQL 8.0"),
            result(
                "template-scan",
                "nuclei ...",
                '{"template-id":"known-panel","matched-at":"https://example.com/admin","info":{"name":"Exposed admin panel","severity":"medium"}}',
            ),
        ],
    )

    assert "Potentially sensitive service exposed: MySQL on TCP/3306" in report
    assert "Exposed admin panel" in report
    assert "Nuclei matched `https://example.com/admin`" in report


def test_preferred_check_is_added_once():
    command = "nikto -host https://example.com -nointeractive"
    checks = assessment_checks("example.com", command)

    assert [check.command for check in checks].count(command) == 1
    assert assessment_checks("example.com", "custom-scanner example.com")[-1].id == "requested-check"


def test_missing_tools_are_converted_to_one_deduplicated_package_plan():
    results = [
        result("dns", "dig +short example.com", "command not found: dig", False),
        result("dns-again", "dig example.com", "command not found: dig", False),
        result("tls", "sslscan example.com:443", "command not found: sslscan", False),
        result("custom", "custom-scanner example.com", "command not found: custom-scanner", False),
    ]

    assert missing_tool_packages(results) == ["dnsutils", "sslscan"]


def test_missing_tool_installer_continues_per_package():
    script = missing_tool_installer_script()

    assert 'for package in sys.argv[1:]' in script
    assert '["apt-get", "install", "-y", package]' in script
    assert "failed.append(package)" in script
