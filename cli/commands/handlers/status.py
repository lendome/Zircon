"""
Status handler — show daemon and session status.
"""

from __future__ import annotations

import asyncio
import json
import sys

from ._shared import resolve_workspace
from ...runtime import ParsedArgs, RuntimeContext
from ...daemon.service import DaemonService


async def run(args: ParsedArgs, ctx: RuntimeContext) -> int:
    workspace = resolve_workspace(args, ctx)
    daemon = DaemonService(workspace)

    if daemon.is_running():
        info = daemon.info()
        assert info is not None
        print(f"\033[92m[running]\033[0m Daemon at {info.address} (pid {info.pid})")

        transport = await daemon.transport()
        if transport is not None:
            try:
                status = await transport.get_status()
                print(f"  Status:   {status.get('status', '?')}")
                print(f"  Tier:     {status.get('tier', '?')}")
                print(f"  Working:  {status.get('working_set', '?')} files")
                print(f"  Modified: {status.get('modified_files', '?')} files")
                print(f"  History:  {status.get('history', '?')} messages")
                used = int(status.get("context_used_tokens", 0))
                maximum = int(status.get("context_max_tokens", 0))
                percent = float(status.get("context_percent", 0.0))
                print(f"  Context:  {used:,} / {maximum:,} tokens ({percent:.1f}%)")
            except Exception as exc:
                print(f"  \033[91mCould not query daemon: {exc}\033[0m")
            finally:
                await transport.close()
    else:
        print(f"\033[91m[stopped]\033[0m Daemon not running")
        print(f"  Run `zircon service start` to start it.")

    return 0
