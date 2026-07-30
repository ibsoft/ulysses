from pathlib import Path

from sirina_agent.tui.app import ULYSSES_LOGO as RICH_LOGO
from sirina_agent.tui.branding import ULYSSES_LOGO, ULYSSES_SIDEBAR_LOGO, ULYSSES_SPEAKING_LOGOS
from sirina_agent.tui.textual_app import ULYSSES_SIDEBAR_LOGO as TEXTUAL_LOGO

DOCUMENTATION = (
    "README.md",
    "docs/ULYSSES.md",
)


def test_tuis_share_compact_ulysses_logo():
    assert RICH_LOGO == ULYSSES_LOGO
    assert TEXTUAL_LOGO == ULYSSES_SIDEBAR_LOGO
    assert "U L Y S S E S" in ULYSSES_LOGO
    assert "CYBER  SENTINEL" in ULYSSES_LOGO
    assert "by CyberPhylax" in ULYSSES_LOGO
    assert "www.cyberphylax.com" in ULYSSES_LOGO
    assert max(map(len, ULYSSES_LOGO.splitlines())) <= 28


def test_sidebar_logo_is_compact_and_rectangular():
    lines = ULYSSES_SIDEBAR_LOGO.splitlines()

    assert len(lines) == 12
    assert max(map(len, lines)) <= 22
    assert len(set(map(len, lines))) == 1
    assert "U L Y S S E S" in ULYSSES_SIDEBAR_LOGO
    assert "by CyberPhylax" in ULYSSES_SIDEBAR_LOGO
    assert "www.cyberphylax.com" in ULYSSES_SIDEBAR_LOGO


def test_speaking_logo_frames_preserve_sidebar_dimensions():
    assert len(ULYSSES_SPEAKING_LOGOS) == 3
    assert len(set(ULYSSES_SPEAKING_LOGOS)) == 3
    for frame in ULYSSES_SPEAKING_LOGOS:
        lines = frame.splitlines()
        assert len(lines) == 12
        assert set(map(len, lines)) == {22}
        assert "U L Y S S E S" in frame
        assert "by CyberPhylax" in frame
        assert "www.cyberphylax.com" in frame


def test_documentation_credits_cyberphylax():
    root = Path(__file__).parents[2]
    for relative_path in DOCUMENTATION:
        text = (root / relative_path).read_text(encoding="utf-8")
        assert "[CyberPhylax](https://www.cyberphylax.com)" in text
        assert "Copyleft 2026 - Ioannis A. Bouhras <ioannis.bouhras@gmail.com>" in text
