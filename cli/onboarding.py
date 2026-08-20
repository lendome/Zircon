"""First-run, keyboard-driven provider and model configuration."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .tui.input import RawTerminal, is_printable, read_key


_ROLES = [
    ("default", "Everyday work and fallback"),
    ("chat", "Interactive conversation"),
    ("editor", "Code changes and refactors"),
    ("planner", "Task decomposition"),
    ("architect", "System design decisions"),
    ("advisor", "High-level execution guidance"),
    ("summarize", "Context compression"),
    ("localize", "Bug and fault localization"),
    ("fast", "Fast-path generation"),
]


@dataclass(frozen=True)
class ProviderPreset:
    name: str
    detail: str
    base_url: str


_PRESETS = [
    ProviderPreset("Zircon Free", "Free, keyless access to Zircon Uncensored Medium", "https://usezircon.com/free/v1"),
    ProviderPreset("OpenRouter", "One key, a broad model catalog", "https://openrouter.ai/api/v1"),
    ProviderPreset("OpenAI", "ChatGPT and OpenAI API models", "https://api.openai.com/v1"),
    ProviderPreset("Anthropic", "Claude through its OpenAI-compatible endpoint", "https://api.anthropic.com/v1"),
    ProviderPreset("Ollama", "Local models running on this machine", "http://localhost:11434/v1"),
    ProviderPreset("Custom", "Any OpenAI-compatible API", ""),
]

_ART = r"""
 _______ _                        
|___  /(_) _ __  ___ ___  _ __    
   / / | || '__|/ __/ _ \| '_ \   
  / /__| || |  | (_| (_) | | | |  
 /_____|_||_|   \___\___/|_| |_|  
""".strip("\n")


def config_path() -> Path:
    return Path(__file__).resolve().parent.parent / "models.yaml"


def needs_onboarding() -> bool:
    return not config_path().is_file()


async def _fetch_models(base_url: str, api_key: str) -> list[str]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"{base_url.rstrip('/')}/models", headers=headers)
        response.raise_for_status()
        payload = response.json()
    items = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    return sorted({str(item["id"]) for item in items if isinstance(item, dict) and item.get("id")})


def _single_line_paste(value: str) -> str:
    """Make clipboard data safe for single-line setup fields."""
    return "".join(value.splitlines()).strip()


class _Wizard:
    def __init__(self) -> None:
        self.console = Console()
        self.stage = "provider"
        self.provider_index = 0
        self.base_url = ""
        self.api_key = ""
        self.models: list[str] = []
        self.query = ""
        self.model_index = 0
        self.role_index = 0
        self.assignments: dict[str, str] = {}
        self.error = ""
        self.loading = ""
        self.cancelled = False

    @property
    def provider(self) -> ProviderPreset:
        return _PRESETS[self.provider_index]

    @property
    def ordered_models(self) -> list[str]:
        # The model chosen for the previous role leads the next role's list
        # (when no filter is active), so Tab re-selects it without searching.
        models = list(self.filtered_models)
        if self.query:
            return models
        previous = self.assignments.get(_ROLES[self.role_index - 1][0], "") if self.role_index else ""
        if previous:
            if previous in models:
                models.remove(previous)
            models.insert(0, previous)
        return models

    @property
    def filtered_models(self) -> list[str]:
        # Model identifiers vary by provider (for example, `claude-opus-4.6`
        # versus `claude/claude-opus-4.6`). Treat whitespace as a fuzzy `%`
        # separator, so `opus 6` matches identifiers containing `opus` before
        # `6` with any intervening characters.
        terms = [re.escape(term) for term in self.query.lower().split()]
        pattern = ".*".join(terms)
        matches = [model for model in self.models if not pattern or re.search(pattern, model.lower())]
        return matches or self.models

    def _header(self) -> Group:
        art = Text(_ART, style="bold bright_cyan")
        subtitle = Text("FIRST-RUN SETUP  /  CONNECT A MODEL PROVIDER", style="bold bright_white")
        return Group(Align.center(art), Align.center(subtitle), Text(""))

    def _footer(self, hint: str) -> Text:
        text = Text()
        text.append("  ", style="dim")
        text.append(hint, style="dim")
        if self.error:
            text.append("\n  " + self.error, style="bold red")
        return text

    def render(self) -> Panel:
        if self.stage == "provider":
            body: Any = self._render_provider()
            title = "  1 / 4  Provider  "
            hint = "Up/Down select   Enter continue   Esc cancel"
        elif self.stage == "url":
            body = self._render_text_field("Provider URL", self.base_url, "https://provider.example/v1")
            title = "  2 / 4  Connection  "
            hint = "Type URL   Enter continue   Esc back"
        elif self.stage == "key":
            body = self._render_text_field("API key", self.api_key, "Leave empty for Zircon Free or local providers", masked=True)
            title = "  2 / 4  Authentication  "
            hint = "Type key   Enter fetch models   Esc back"
        elif self.stage == "fetch":
            body = Align.center(Text(self.loading or "Connecting...", style="bold cyan"), vertical="middle")
            title = "  3 / 4  Discovering Models  "
            hint = "Contacting your provider"
        elif self.stage == "model":
            body = self._render_model_picker()
            title = f"  3 / 4  {self.current_role} Model  "
            hint = "Type to filter   Up/Down select   Tab/Enter accept selection   Esc back"
        else:
            body = self._render_done()
            title = "  4 / 4  Ready  "
            hint = "Enter save and start Zircon   Esc back"
        return Panel(
            Group(self._header(), body, Text(""), self._footer(hint)),
            title=title,
            title_align="left",
            border_style="bright_cyan",
            padding=(1, 3),
        )

    def _render_provider(self) -> Table:
        table = Table.grid(padding=(0, 1))
        table.add_column(width=3)
        table.add_column(style="bold")
        table.add_column(style="dim")
        for index, preset in enumerate(_PRESETS):
            selected = index == self.provider_index
            marker = ">" if selected else " "
            style = "bold bright_cyan" if selected else ""
            table.add_row(Text(marker, style=style), Text(preset.name, style=style), Text(preset.detail, style=style or "dim"))
        return table

    def _render_text_field(self, label: str, value: str, placeholder: str, masked: bool = False) -> Group:
        visible = ("*" * len(value)) if masked else value
        field = Text()
        field.append("█", style="bright_cyan")
        field.append(visible or placeholder, style="bright_white" if value else "dim")
        return Group(Text(label, style="bold bright_white"), Text(""), Panel(field, border_style="cyan"))

    def _render_model_picker(self) -> Group:
        role, description = _ROLES[self.role_index]
        matches = self.ordered_models
        inherited = self.assignments.get("default", "") if role != "default" else ""
        table = Table.grid(padding=(0, 1))
        table.add_column(width=3)
        table.add_column()
        for index, model in enumerate(matches[:9]):
            selected = index == self.model_index
            style = "bold bright_cyan" if selected else ""
            table.add_row(Text(">" if selected else " ", style=style), Text(model, style=style))
        if not matches:
            table.add_row(Text(" "), Text("No catalog match. Enter any model ID.", style="dim"))
        entered = self.query or inherited or "Type a model ID or filter the catalog"
        query = Text()
        query.append(entered, style="bright_white" if self.query else "dim")
        query.append("█", style="bright_cyan")
        return Group(
            Text(f"{role.upper()}  {description}", style="bold bright_white"),
            Text(f"Enter accepts default: {inherited}" if inherited and not self.query else "", style="dim"),
            Text(""),
            Panel(query, title="Model ID", border_style="cyan"),
            Text(""),
            table,
        )

    def _render_done(self) -> Group:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold cyan", width=12)
        table.add_column(style="bright_white")
        for role, _ in _ROLES:
            table.add_row(role, self.assignments[role])
        return Group(
            Text("Your provider is connected and every Zircon role has a model.", style="bright_white"),
            Text(""),
            table,
            Text(""),
            Text("After setup, type ", style="bright_white"),
            Group(
                Text("zircon", style="bold bright_cyan"),
                Text(" in any folder's terminal to open the Zircon chat interface for that folder.", style="bright_white"),
            ),
        )

    @property
    def current_role(self) -> str:
        return _ROLES[self.role_index][0]

    async def run(self) -> bool:
        loop = asyncio.get_running_loop()
        with Live(self.render(), console=self.console, refresh_per_second=20, transient=True) as live:
            with RawTerminal():
                while not self.cancelled and self.stage != "save":
                    live.update(self.render())
                    key = await loop.run_in_executor(None, read_key)
                    await self.handle_key(key, live)
        return not self.cancelled and self.stage == "save"

    async def handle_key(self, key: str, live: Live) -> None:
        self.error = ""
        if self.stage == "provider":
            if key == "up":
                self.provider_index = (self.provider_index - 1) % len(_PRESETS)
            elif key == "down":
                self.provider_index = (self.provider_index + 1) % len(_PRESETS)
            elif key in {"return", "tab"}:
                if self.provider.base_url:
                    self.base_url = self.provider.base_url
                    self.stage = "key"
                else:
                    self.stage = "url"
            elif key in {"escape", "ctrl+c"}:
                self.cancelled = True
            return

        if self.stage in {"url", "key"}:
            target = "base_url" if self.stage == "url" else "api_key"
            value = getattr(self, target)
            if key == "backspace":
                setattr(self, target, value[:-1])
            elif key in {"return", "tab"}:
                if self.stage == "url":
                    if self.base_url.strip():
                        self.stage = "key"
                    else:
                        self.error = "A provider URL is required."
                else:
                    await self.fetch_models(live)
            elif key == "escape":
                self.stage = "provider" if self.stage == "url" else ("url" if not self.provider.base_url else "provider")
            elif key.startswith("paste:"):
                setattr(self, target, value + _single_line_paste(key[6:]))
            elif is_printable(key):
                setattr(self, target, value + key)
            return

        if self.stage == "model":
            matches = self.ordered_models
            if key == "up" and matches:
                self.model_index = (self.model_index - 1) % min(len(matches), 9)
            elif key == "down" and matches:
                self.model_index = (self.model_index + 1) % min(len(matches), 9)
            elif key == "backspace":
                self.query = self.query[:-1]
                self.model_index = 0
            elif key in {"return", "tab"}:
                # A visible catalog selection takes precedence over the filter
                # text. Typing narrows suggestions; Enter/Tab accepts the
                # highlighted model. A custom ID is used only when no catalog
                # result matches the text.
                selected = (
                    matches[self.model_index]
                    if matches and self.model_index < len(matches)
                    else self.query.strip() or self.assignments.get("default", "")
                )
                if not selected:
                    self.error = "Enter or select a model ID."
                    return
                self.assignments[self.current_role] = selected
                self.role_index += 1
                self.query = ""
                self.model_index = 0
                self.stage = "done" if self.role_index == len(_ROLES) else "model"
            elif key == "escape":
                if self.role_index:
                    self.role_index -= 1
                    self.query = self.assignments.get(self.current_role, "")
                    self.model_index = 0
                else:
                    self.stage = "key"
            elif key.startswith("paste:"):
                self.query += _single_line_paste(key[6:])
                self.model_index = 0
            elif is_printable(key):
                self.query += key
                self.model_index = 0
            return

        if self.stage == "done":
            if key in {"return", "tab"}:
                self.stage = "save"
            elif key == "escape":
                self.role_index = len(_ROLES) - 1
                self.query = self.assignments.get(self.current_role, "")
                self.model_index = 0
                self.stage = "model"

    async def fetch_models(self, live: Live) -> None:
        self.stage = "fetch"
        self.loading = "Connecting to provider and loading its model catalog..."
        live.update(self.render())
        try:
            self.models = await _fetch_models(self.base_url.strip().rstrip("/"), self.api_key.strip())
        except (httpx.HTTPError, ValueError) as exc:
            self.error = f"Could not fetch models: {exc}"
            self.stage = "key"
            return
        if not self.models:
            self.error = "The provider returned no models. Check the URL and API key."
            self.stage = "key"
            return
        self.stage = "model"

    def save(self) -> None:
        from zirconAgent.core.config import save_config

        profiles = {
            role: {"base_url": self.base_url.strip().rstrip("/"), "api_key": self.api_key.strip(), "model": model, "roles": [role]}
            for role, model in self.assignments.items()
        }
        save_config(
            {
                "profiles": profiles,
                "router": {
                    "default_role": "default",
                    "role_priority": {role: [role, "default"] if role != "default" else ["default"] for role, _ in _ROLES},
                },
            },
            config_path(),
        )


async def run_onboarding() -> bool:
    wizard = _Wizard()
    if not await wizard.run():
        return False
    wizard.save()
    wizard.console.print(
        "[bold bright_cyan]Zircon is configured.[/] "
        "Type [bold bright_cyan]zircon[/] in any folder's terminal to open the Zircon chat interface for that folder.\n"
    )
    return True


def ensure_configured() -> bool:
    if not needs_onboarding():
        return True
    try:
        return asyncio.run(run_onboarding())
    except (EOFError, KeyboardInterrupt):
        print("\nZircon setup cancelled.")
        return False
