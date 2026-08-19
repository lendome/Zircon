"""
Secondary dialogs — status, debug, theme list, session list, model,
agent, MCP toggle, skill, stash, export options.

All use DialogSelect as the base with fuzzy filtering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from rich.console import RenderableType
from rich.panel import Panel
from rich.text import Text as RichText

from ..dialogs.dialog_select import DialogSelect, DialogOption
from ..theming.theme import Theme


class StatusDialog:
    """Show system information: version, providers, sessions, LSP, VCS."""

    def __init__(self, theme: Theme | None = None, info: dict[str, Any] | None = None) -> None:
        self.theme = theme
        self.info = info or {}

    def render(self) -> RenderableType:
        border = "blue"
        if self.theme is not None:
            border = self.theme.border_active.to_rich()
        lines: list[str] = []
        for k, v in self.info.items():
            lines.append(f"[bold]{k}:[/] {v}")
        return Panel(RichText.from_markup("\n".join(lines)), title="Status", border_style=border)


class DebugDialog:
    """Troubleshooting: server URL, event stream, plugins, keymap state."""

    def __init__(self, theme: Theme | None = None, debug_info: dict[str, Any] | None = None) -> None:
        self.theme = theme
        self.debug_info = debug_info or {}

    def render(self) -> RenderableType:
        border = "dim"
        if self.theme is not None:
            border = self.theme.border.to_rich()
        lines: list[str] = []
        for k, v in self.debug_info.items():
            lines.append(f"[bold]{k}:[/] {v}")
        return Panel(RichText.from_markup("\n".join(lines)), title="Debug", border_style=border)


class ThemeListDialog:
    """Browse and switch themes. Toggle dark/light, lock/unlock mode."""

    def __init__(
        self,
        themes: dict[str, Any],
        current: str = "",
        theme: Theme | None = None,
        on_select: Callable[[str], None] | None = None,
    ) -> None:
        options = [
            DialogOption(
                title=name,
                value=name,
                category="Theme",
                selected=name == current,
                on_select=lambda opt, n=name: on_select(n) if on_select else None,
            )
            for name in sorted(themes.keys())
        ]
        self._dialog = DialogSelect(
            title="Themes",
            options=options,
            current=current,
            theme=theme,
        )

    def render(self) -> RenderableType:
        return self._dialog.render()


class SessionListDialog:
    """Session list with fuzzy search, pin, rename, delete, quick-switch."""

    def __init__(
        self,
        sessions: list[dict[str, Any]],
        quick_switch_slots: dict[int, str] | None = None,
        theme: Theme | None = None,
        on_select: Callable[[str], None] | None = None,
        on_delete: Callable[[str], None] | None = None,
        on_rename: Callable[[str], None] | None = None,
    ) -> None:
        self.quick_switch = quick_switch_slots or {}
        options = []
        for s in sorted(sessions, key=lambda x: x.get("updated_at", 0), reverse=True):
            title = s.get("title", s.get("id", "?"))
            sid = s.get("id", "")
            slot = ""
            for slot_num, slot_sid in self.quick_switch.items():
                if slot_sid == sid:
                    slot = f" [{slot_num}]"
                    break
            options.append(DialogOption(
                title=f"{title}{slot}",
                value=sid,
                category="Session",
                on_select=lambda opt, i=sid: on_select(i) if on_select else None,
            ))
        self._dialog = DialogSelect(title="Sessions", options=options, theme=theme)

    def render(self) -> RenderableType:
        return self._dialog.render()


class ModelDialog:
    """Model selection: filter by provider, favorites, variants, context limits."""

    def __init__(
        self,
        models: list[dict[str, Any]],
        current: str = "",
        theme: Theme | None = None,
        on_select: Callable[[str], None] | None = None,
    ) -> None:
        options = [
            DialogOption(
                title=m.get("name", m.get("id", "?")),
                value=m.get("id", ""),
                category=m.get("provider", ""),
                selected=m.get("id") == current,
                on_select=lambda opt, mid=m.get("id", ""): on_select(mid) if on_select else None,
            )
            for m in models
        ]
        self._dialog = DialogSelect(title="Models", options=options, current=current, theme=theme)

    def render(self) -> RenderableType:
        return self._dialog.render()


class AgentDialog:
    """Switch between agents (different system prompts / tool sets)."""

    def __init__(
        self,
        agents: list[dict[str, Any]],
        current: str = "",
        theme: Theme | None = None,
        on_select: Callable[[str], None] | None = None,
    ) -> None:
        options = [
            DialogOption(
                title=a.get("name", "?"),
                value=a.get("name", ""),
                category=a.get("mode", "Agent"),
                selected=a.get("name") == current,
                on_select=lambda opt, n=a.get("name", ""): on_select(n) if on_select else None,
            )
            for a in agents
        ]
        self._dialog = DialogSelect(title="Agents", options=options, current=current, theme=theme)

    def render(self) -> RenderableType:
        return self._dialog.render()


class McpToggleDialog:
    """Toggle MCP servers on/off. Space to toggle, shows connection status."""

    def __init__(
        self,
        servers: list[dict[str, Any]],
        theme: Theme | None = None,
        on_toggle: Callable[[str], None] | None = None,
    ) -> None:
        options = [
            DialogOption(
                title=f"{'[on]' if s.get('enabled') else '[off]'} {s.get('name', '?')}",
                value=s.get("id", s.get("name", "")),
                category="MCP",
                on_select=lambda opt, sid=s.get("id", s.get("name", "")): on_toggle(sid) if on_toggle else None,
            )
            for s in servers
        ]
        self._dialog = DialogSelect(title="MCP Servers", options=options, theme=theme)

    def render(self) -> RenderableType:
        return self._dialog.render()


class SkillDialog:
    """Browse and select skills. Selecting inserts /skill_name into prompt."""

    def __init__(
        self,
        skills: list[dict[str, Any]],
        theme: Theme | None = None,
        on_select: Callable[[str], None] | None = None,
    ) -> None:
        options = [
            DialogOption(
                title=s.get("name", "?"),
                value=s.get("name", ""),
                category="Skill",
                on_select=lambda opt, n=s.get("name", ""): on_select(n) if on_select else None,
            )
            for s in skills
        ]
        self._dialog = DialogSelect(title="Skills", options=options, theme=theme)

    def render(self) -> RenderableType:
        return self._dialog.render()


class StashDialog:
    """Browse stashed prompts. Selecting restores. Ctrl+D to delete."""

    def __init__(
        self,
        entries: list[dict[str, Any]],
        theme: Theme | None = None,
        on_select: Callable[[int], None] | None = None,
    ) -> None:
        options = [
            DialogOption(
                title=e.get("input", "?")[:60],
                value=str(i),
                category="Stash",
                on_select=lambda opt, idx=i: on_select(idx) if on_select else None,
            )
            for i, e in enumerate(entries)
        ]
        self._dialog = DialogSelect(title="Stashed Prompts", options=options, theme=theme)

    def render(self) -> RenderableType:
        return self._dialog.render()


class ExportOptionsDialog:
    """Configure export: filename, thinking, tool details, metadata."""

    def __init__(
        self,
        filename: str = "",
        theme: Theme | None = None,
        on_confirm: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.filename = filename
        self.theme = theme
        self.thinking = False
        self.tool_details = True
        self.assistant_metadata = True
        self.open_without_saving = False
        self._on_confirm = on_confirm

    def confirm(self) -> dict[str, Any]:
        result = {
            "filename": self.filename,
            "thinking": self.thinking,
            "tool_details": self.tool_details,
            "assistant_metadata": self.assistant_metadata,
            "open_without_saving": self.open_without_saving,
        }
        if self._on_confirm:
            self._on_confirm(result)
        return result

    def render(self) -> RenderableType:
        border = "cyan"
        if self.theme is not None:
            border = self.theme.info.to_rich()
        lines = [
            f"Filename: {self.filename}",
            f"Include thinking: {'yes' if self.thinking else 'no'}",
            f"Include tool details: {'yes' if self.tool_details else 'no'}",
            f"Include metadata: {'yes' if self.assistant_metadata else 'no'}",
            f"Open without saving: {'yes' if self.open_without_saving else 'no'}",
        ]
        return Panel(RichText("\n".join(lines)), title="Export Options", border_style=border)
