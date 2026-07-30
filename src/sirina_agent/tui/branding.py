from __future__ import annotations

ULYSSES_LOGO = r"""╭──────────────────────────╮
│      U L Y S S E S       │
│     CYBER  SENTINEL      │
│                          │
│          ╱╲  ╱╲          │
│       ╭──╯◆  ◆╰──╮       │
│       │     ◇    │       │
│       ╰─╮  ╱╲  ╭─╯       │
│         ╰─┬──┬─╯         │
│       ════╧══╧════       │
│      by CyberPhylax      │
│   www.cyberphylax.com    │
╰──────────────────────────╯"""


ULYSSES_SIDEBAR_LOGO = r"""╭────────────────────╮
│   U L Y S S E S    │
│   CYBER SENTINEL   │
│       ╱╲  ╱╲       │
│     ╭──◆──◆──╮     │
│     │   ◇    │     │
│     ╰─╮ ╱╲ ╭─╯     │
│      ╰─┬──┬─╯      │
│      ══╧══╧══      │
│   by CyberPhylax   │
│www.cyberphylax.com │
╰────────────────────╯"""


def _speaking_frame(ears: str, voice: str) -> str:
    lines = ULYSSES_SIDEBAR_LOGO.splitlines()
    lines[3] = ears
    lines[5] = voice
    return "\n".join(lines)


ULYSSES_SPEAKING_LOGOS = (
    _speaking_frame("│   )   ╱╲  ╱╲   (   │", "│     │   ─    │     │"),
    _speaking_frame("│  )))  ╱╲  ╱╲  (((  │", "│     │   ◆    │     │"),
    _speaking_frame("│   )   ╱╲  ╱╲   (   │", "│     │   ◇    │     │"),
)
