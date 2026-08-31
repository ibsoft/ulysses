from sirina_agent.core.artifacts import (
    ArtifactManager,
    assessment_baseline_commands,
    assessment_command_for_text,
    attachment_prompt,
    is_assessment_continuation,
    is_assessment_request,
    is_final_assessment_report,
    is_report_request,
    is_skill_creation_request,
    should_attach_clipboard_text,
    should_store_large_paste,
)
from sirina_agent.core.assessment import assessment_tool_options
from sirina_agent.tui.textual_app import _selected_assessment_options


def test_large_paste_is_saved_as_txt(tmp_path):
    manager = ArtifactManager(tmp_path)
    artifact = manager.save_text_attachment("sess_1", "hello")

    assert artifact.path.suffix == ".txt"
    assert artifact.path.parent == tmp_path / "attachments"
    assert artifact.path.read_text(encoding="utf-8") == "hello"


def test_markdown_report_is_saved_as_md(tmp_path):
    manager = ArtifactManager(tmp_path)
    artifact = manager.save_markdown_report("sess_1", "# Report\n\nBody")

    assert artifact.path.suffix == ".md"
    assert artifact.path.parent == tmp_path / "reports"
    assert artifact.path.read_text(encoding="utf-8").startswith("# Report")
    assert manager.latest_report() == artifact.path


def test_latest_report_returns_none_when_no_report_exists(tmp_path):
    assert ArtifactManager(tmp_path).latest_report() is None


def test_resolve_report_lists_ambiguous_reports_and_selects_number(tmp_path):
    manager = ArtifactManager(tmp_path)
    first = manager.save_markdown_report("sess_1", "# First")
    second = manager.save_markdown_report("sess_2", "# Second")

    selected, guidance = manager.resolve_report("show me the report")
    numbered, _ = manager.resolve_report("show report 2")

    assert selected is None
    assert "Several reports are available" in guidance
    assert numbered == manager.list_downloads()[1]
    assert second.path in manager.list_downloads()


def test_resolve_report_prefers_active_project(tmp_path):
    manager = ArtifactManager(tmp_path)
    manager.save_markdown_report("sess_1", "# Other")
    project = manager.create_assessment_project("sess_1", "scan example.test")
    active = manager.save_project_markdown_report(project, "# Active")

    selected, guidance = manager.resolve_report("show me the report", project)

    assert selected == active.path
    assert guidance == ""


def test_resolve_report_matches_assessment_target(tmp_path):
    manager = ArtifactManager(tmp_path)
    project = manager.create_assessment_project("sess_1", "assess example.test")
    expected = manager.save_project_markdown_report(project, "# Target")

    selected, _ = manager.resolve_report("show report for example.test")

    assert selected == expected.path


def test_assessment_project_has_standard_folders_and_report(tmp_path):
    manager = ArtifactManager(tmp_path)
    project = manager.create_assessment_project("sess_1", "make assessment on www.example.com")

    assert project.path.parent == tmp_path / "projects"
    assert project.scripts_dir.is_dir()
    assert project.artifacts_dir.is_dir()
    assert project.results_dir.is_dir()
    assert project.reports_dir.is_dir()
    assert (project.artifacts_dir / "request.txt").read_text(encoding="utf-8") == "make assessment on www.example.com"
    assert '"status": "active"' in (project.artifacts_dir / "project.json").read_text(encoding="utf-8")

    result = manager.save_project_result(project, "nmap -sV www.example.com", "raw output")
    script = manager.save_project_script(project, "helper", "print('ok')\n")
    report = manager.save_project_markdown_report(project, "# Assessment\n\nEvidence")

    assert result.path.parent == project.results_dir
    assert script.path.parent == project.scripts_dir
    assert script.path.suffix == ".py"
    assert report.path.parent == project.reports_dir
    assert "customer-vulnerability-assessment-report" in report.path.name
    assert report.path in manager.list_downloads()


def test_large_paste_prompt_references_file(tmp_path):
    manager = ArtifactManager(tmp_path)
    text = "x" * 10_000
    artifact = manager.save_text_attachment("sess_1", text)
    prompt = attachment_prompt(text, artifact, preview_chars=20)

    assert str(artifact.path) in prompt
    assert "Characters: 10000" in prompt
    assert "remaining 9980 characters" in prompt


def test_large_paste_threshold_uses_context_limit():
    assert should_store_large_paste("x" * 8_001, 10_000)
    assert not should_store_large_paste("x" * 8_000, 10_000)


def test_multiline_clipboard_text_is_always_attached():
    assert should_attach_clipboard_text("first line\nsecond line", 24_000)
    assert should_attach_clipboard_text("first line\r\nsecond line", 24_000)
    assert not should_attach_clipboard_text("short single line", 24_000)


def test_report_request_detection():
    assert is_report_request("make me a report about this")
    assert is_report_request("generate a markdown file")
    assert is_report_request("give it to me as .md to download")
    assert not is_report_request("what is a report")


def test_skill_creation_request_is_not_treated_as_report_work():
    text = "Create and activate a complete skill that must report gateway status and summarize online hosts."

    assert is_report_request(text)
    assert is_skill_creation_request(text)


def test_assessment_request_detection():
    assert is_assessment_request("make assessment on www.unixfor.gr")
    assert is_assessment_request("do a vulnerability test on https://example.com")
    assert is_assessment_request("run port scan against 192.0.2.10")
    assert is_assessment_request("run nikto on www.unixfor.gr")
    assert not is_assessment_request("what is a vulnerability assessment")


def test_assessment_target_supports_ipv4():
    from sirina_agent.core.artifacts import assessment_target

    assert assessment_target("scan 192.0.2.10") == "192.0.2.10"
    assert assessment_target("scan 999.0.2.10") is None


def test_assessment_continuation_detection():
    assert is_assessment_continuation("curl is installed")
    assert is_assessment_continuation("check this https://example.com/contact")
    assert not is_assessment_continuation("what is curl")


def test_final_assessment_report_detection():
    draft_question = "Please advise your preferred focus. I will continue preparing a comprehensive assessment report."
    final_report = (
        "# Assessment Report\n\n"
        "## Executive Summary\nDone.\n\n"
        "## Methodology\nChecked available evidence.\n\n"
        "## Findings\nNo confirmed issues.\n\n"
        "## Remediation\nKeep systems patched.\n\n"
        "## Verification Steps\nRetest after changes.\n"
    )

    assert not is_final_assessment_report(draft_question)
    assert is_final_assessment_report(final_report)


def test_assessment_command_for_install_and_sudo_scan():
    request = "continue assessment on www.unixfor.gr"

    assert assessment_command_for_text("install nikto", request) == "sudo apt-get install -y nikto"
    assert assessment_command_for_text("run with sudo", request) == "sudo nmap -sS -Pn -p- www.unixfor.gr"
    assert assessment_command_for_text("run nikto on www.unixfor.gr", request) == "nikto -host https://www.unixfor.gr -nointeractive"
    assert assessment_command_for_text("run nmap on www.unixfor.gr", request) == "nmap -sT -Pn --top-ports 1000 www.unixfor.gr"


def test_assessment_baseline_commands_are_concrete_and_safe():
    commands = assessment_baseline_commands("www.unixfor.gr")

    assert "curl -sS -L --max-time 30 -D - -o /dev/null https://www.unixfor.gr" in commands
    assert "nmap -sT -sV --version-light -Pn --open --top-ports 1000 www.unixfor.gr" in commands
    assert "nikto -host https://www.unixfor.gr -nointeractive" in commands
    assert any(command.startswith("nuclei -u ") for command in commands)
    assert not any(command.startswith("sudo ") for command in commands)
    assert not any("apt-get" in command for command in commands)


def test_assessment_tool_selection_accepts_numbers_names_and_all():
    options = assessment_tool_options("www.unixfor.gr")

    selected, error = _selected_assessment_options("1, nmap, tls", options)
    assert error is None
    assert [item.id for item in selected] == ["dns", "service-scan", "tls"]

    selected, error = _selected_assessment_options("all", options)
    assert error is None
    assert selected == options

    selected, error = _selected_assessment_options("maybe later", options)
    assert selected == []
    assert error and "Choose at least one listed assessment tool" in error
