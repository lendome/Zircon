"""
DialogConfirm — three-way confirmation dialog.

Returns:
  - True:   user confirmed
  - False:  user chose "skip" (don't show again)
  - None:   user cancelled
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from rich.panel import Panel
from rich.text import Text as RichText

from ..theming.theme import Theme


class ConfirmResult(Enum):
    CONFIRM = True
    SKIP = False
    CANCEL = None


class DialogConfirm:
    """
    A confirmation dialog with optional "skip" option.

    Usage:
        choice = await DialogConfirm.show(dialog_manager, "Share Session", "Are you sure?", allow_skip=True)
        if choice == ConfirmResult.SKIP:
            kv.set("share_consent_done", True)
        elif choice != ConfirmResult.CONFIRM:
            return  # cancelled
    """

    def __init__(
        self,
        title: str = "",
        message: str = "",
        allow_skip: bool = False,
        theme: Theme | None = None,
    ) -> None:
        self.title = title
        self.message = message
        self.allow_skip = allow_skip
        self.theme = theme
        self._result: ConfirmResult | None = None

    @staticmethod
    def show(
        dialog_manager: Any,
        title: str,
        message: str,
        allow_skip: bool = False,
        theme: Theme | None = None,
    ) -> ConfirmResult:
        """Create and show a confirm dialog synchronously."""
        dlg = DialogConfirm(title=title, message=message, allow_skip=allow_skip, theme=theme)

        from .dialog_select import DialogSelect, DialogOption, FooterHint

        options = [
            DialogOption(
                title="Confirm",
                value="confirm",
                category="Confirm",
                on_select=lambda _: dlg._set_result(ConfirmResult.CONFIRM),
            ),
            DialogOption(
                title="Cancel",
                value="cancel",
                category="Confirm",
                on_select=lambda _: dlg._set_result(ConfirmResult.CANCEL),
            ),
        ]
        if allow_skip:
            options.insert(1, DialogOption(
                title="Skip (don't show again)",
                value="skip",
                category="Confirm",
                on_select=lambda _: dlg._set_result(ConfirmResult.SKIP),
            ))

        dlg._select = DialogSelect(
            title=title,
            options=options,
            theme=theme,
        )
        return dlg._result or ConfirmResult.CANCEL

    def _set_result(self, result: ConfirmResult) -> None:
        self._result = result

    def render(self) -> Any:
        border_style = "yellow"
        if self.theme is not None:
            border_style = self.theme.warning.to_rich()
        return Panel(
            RichText(self.message),
            title=self.title,
            border_style=border_style,
        )
