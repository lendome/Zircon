"""
Dialog & modal system — stack-based dialogs, filterable list dialog,
confirm/alert helpers, and toast notifications.

Dialogs can be pushed, replaced, or cleared. Escape/Ctrl+C always
closes the top dialog. A modal mode is pushed onto the keymap stack.
"""

from __future__ import annotations

from .dialog_select import DialogSelect, DialogOption
from .confirm import DialogConfirm, ConfirmResult
from .alert import DialogAlert
from .toast import ToastManager, Toast, ToastVariant

__all__ = [
    "DialogSelect",
    "DialogOption",
    "DialogConfirm",
    "ConfirmResult",
    "DialogAlert",
    "ToastManager",
    "Toast",
    "ToastVariant",
]
