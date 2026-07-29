from sirina_agent.tui.app import ULYSSES_LOGO as RICH_LOGO
from sirina_agent.tui.branding import ULYSSES_LOGO
from sirina_agent.tui.textual_app import ULYSSES_LOGO as TEXTUAL_LOGO


def test_tuis_share_compact_ulysses_logo():
    assert RICH_LOGO == ULYSSES_LOGO
    assert TEXTUAL_LOGO == ULYSSES_LOGO
    assert "U L Y S S E S" in ULYSSES_LOGO
    assert "CYBER  SENTINEL" in ULYSSES_LOGO
    assert max(map(len, ULYSSES_LOGO.splitlines())) <= 28
