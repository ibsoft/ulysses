from sirina_agent.core.artifacts import ArtifactManager, attachment_prompt, is_report_request, should_store_large_paste


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


def test_report_request_detection():
    assert is_report_request("make me a report about this")
    assert is_report_request("generate a markdown file")
    assert is_report_request("give it to me as .md to download")
    assert not is_report_request("what is a report")
