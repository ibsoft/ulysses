from sirina_agent.audio.sirina_io import VoiceState


def test_voice_state_supports_response_toggle():
    state = VoiceState()
    assert state.enabled
    state.enabled = False
    assert not state.enabled
    assert not state.muted
