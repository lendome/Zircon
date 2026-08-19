"""
CLI approval bridges — connect the registry's approval gate to a user.

Three contexts share the gate defined in ``zirconAgent.tools.approval``:

  - In-process TUI (no daemon): an ``ApprovalCoordinator`` is shared between
    the Agent's registry and the ``ChatComponent``. The gate handler sets a
    pending-approval entry on the chat and awaits a Future that the TUI's key
    loop resolves when the user presses y/n.

  - Daemon (server) mode: the gate handler is ``DaemonServer.request_approval``,
    which pushes an ``approval_request`` frame to the connected TUI and awaits
    a Future resolved by the ``respond_approval`` RPC.

  - Headless (``zircon task``): a ``HeadlessApprovalHandler`` prompts on stdin.

All three are only armed from the CLI command handlers, so non-CLI/embedded use
of the Agent is unaffected (the gate stays disabled and approves everything).
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from zirconAgent.tools.approval import preview_command


class ApprovalCoordinator:
    """In-process bridge between the registry gate and the TUI.

    Lives in the same process as the Agent (no-daemon TUI mode). The gate
    handler ``request`` stashes a pending approval on the bound ChatComponent
    and awaits a Future that the chat's key loop resolves (y/n).
    """

    def __init__(self) -> None:
        self._chat: Any = None

    def bind(self, chat: Any) -> None:
        self._chat = chat

    async def request(self, name: str, arguments: dict[str, Any], reason: str) -> bool:
        chat = self._chat
        if chat is None:
            # No TUI bound yet (e.g. agent used before run_tui). Allow, since
            # the gate is a CLI convenience, not a security boundary.
            return True
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        chat._set_pending_approval({
            "future": future,
            "name": name,
            "arguments": arguments,
            "reason": reason,
        })
        return await future


class HeadlessApprovalHandler:
    """Stdin-based approval for headless ``zircon task`` runs.

    When stdin is not a TTY (piped/non-interactive), destructive commands are
    denied so a headless run can never silently destroy work.
    """

    async def request(self, name: str, arguments: dict[str, Any], reason: str) -> bool:
        preview = preview_command(name, arguments, 300)
        if not sys.stdin.isatty():
            print(
                "\n[!] Destructive command DENIED (no interactive terminal to approve):\n"
                f"  {preview}\n  Reason: {reason}\n"
                "Run via the TUI (`zircon`) to approve such commands.\n",
                file=sys.stderr,
            )
            return False
        prompt = (
            "\n[!] Approve destructive command?\n"
            f"  {preview}\n  Reason: {reason}\n"
            "Approve? [y/N]: "
        )
        loop = asyncio.get_event_loop()
        try:
            answer = await loop.run_in_executor(None, input, prompt)
        except (EOFError, KeyboardInterrupt):
            return False
        return answer.strip().lower() in ("y", "yes")
