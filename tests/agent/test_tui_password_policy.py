import pytest
from textual.app import App
from textual.widgets import Input

from sirina_agent.tui.textual_app import SudoPasswordScreen


class PasswordHarness(App):
    def __init__(self) -> None:
        super().__init__()
        self.password_screen = SudoPasswordScreen()

    def on_mount(self) -> None:
        self.push_screen(self.password_screen)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_sudo_dialog_uses_masked_input():
    app = PasswordHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        field = app.password_screen.query_one("#sudo-password", Input)

        assert field.password is True
        assert field.id == "sudo-password"
