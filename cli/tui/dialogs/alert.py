"""
DialogAlert — simple alert dialog with a single OK button.
"""

from __future__ import annotations

from typing import Any

from rich.panel import Panel
from rich.text import Text as RichText

from ..theming.theme import Theme


class DialogAlert:
    """
    A simple alert dialog.

    Usage:
        DialogAlert.show(dialog_manager, "Update Complete", "Please restart.")
    """

    def __init__(
        self,
        title: str = "",
        message: str = "",
        theme: Theme | None = None,
    ) -> None:
        self.title = title
        self.message = message
        self.theme = theme

    @staticmethod
    def show(
        dialog_manager: Any,
        title: str,
        message: str,
        theme: Theme | None = None,
    ) -> None:
        """Create and show an alert dialog."""
        dlg = DialogAlert(title=title, message=message, theme=theme)
        from ..providers.dialog import DialogEntry
        if dialog_manager is not None:
            dialog_manager.replace(DialogEntry(
                renderable=dlg.render(),
                title=title,
            ))

    def render(self) -> Any:
        border_style = "cyan"
        if self.theme is not None:
            border_style = self.theme.info.to_rich()
        return Panel(
            RichText(self.message),
            title=self.title,
            border_style=border_style,
        )
