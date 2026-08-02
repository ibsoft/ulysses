from sirina_agent.core.artifacts import assessment_needs_voice
from sirina_agent.tui.textual_app import UlyssesTextualApp


def test_assessment_voice_policy_suppresses_routine_updates():
    text = "Assessment completed. Report saved as Markdown: /tmp/report.md"

    assert not assessment_needs_voice(text)


def test_assessment_voice_policy_speaks_decisions_and_confirmations():
    prompts = [
        "Command requires confirmation. Confirmation token: abcd1234",
        "Please confirm authorization and scope before intrusive testing.",
        "command not found: nuclei. Install it before continuing?",
    ]

    for prompt in prompts:
        assert assessment_needs_voice(prompt)


def test_assessment_voice_policy_does_not_speak_pending_tool_prompt():
    assert not assessment_needs_voice("Run `apt-get install -y nuclei`?", pending_tool=True)


def test_assessment_voice_policy_does_not_speak_password_requests():
    assert not assessment_needs_voice("Please provide your sudo password securely.")


def test_stale_speech_worker_cannot_play_previous_response() -> None:
    app = UlyssesTextualApp.__new__(UlyssesTextualApp)
    app._speech_id = 1

    class SupersedingOrchestrator:
        def summarize_for_voice(self, text):
            app._speech_id = 2
            return text

    class VoiceIO:
        def speak(self, *args, **kwargs):
            raise AssertionError("stale response reached playback")

    app.orchestrator = SupersedingOrchestrator()
    app.voice_io = VoiceIO()

    app._speak_in_thread("previous response", speech_id=1)
