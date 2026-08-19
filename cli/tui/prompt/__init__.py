"""
Prompt & input system — a full-featured editor, not a simple text input.

Supports:
  - @mentions (file/agent/resource autocomplete)
  - /slash commands
  - Shell mode (! prefix)
  - External editor integration ($EDITOR/$VISUAL)
  - Prompt history with navigation
  - Prompt stash (save/restore drafts)
  - Smart paste handling (summarize large pastes, detect file paths)
  - File/image attachments via clipboard
  - Structured inline content via extmarks
  - Status footer (agent, model, provider, status, tokens, cost)
  - Submit guard against double-submission
"""

from __future__ import annotations

from .extmarks import Extmark, ExtmarkManager
from .prompt import Prompt, PromptMode, PromptPart
from .history import PromptHistory
from .stash import PromptStash
from .shell_mode import ShellMode
from .editor import open_external_editor
from .paste import smart_paste, detect_file_path, read_local_file
from .footer import PromptFooter, FooterData

__all__ = [
    "Extmark",
    "ExtmarkManager",
    "Prompt",
    "PromptMode",
    "PromptPart",
    "PromptHistory",
    "PromptStash",
    "ShellMode",
    "open_external_editor",
    "smart_paste",
    "detect_file_path",
    "read_local_file",
    "PromptFooter",
    "FooterData",
]
