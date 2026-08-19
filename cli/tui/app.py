"""
App shell — assembles the full provider tree and runs the TUI.

The provider tree is assembled in dependency order, mirroring OpenCode's
app.tsx:

    ExitProvider → ClipboardProvider → ArgsProvider → KVProvider
      → ProjectProvider → SDKProvider → RendererProvider → ConfigProvider
        → ThemeProvider → KeymapProvider → EventProvider → SyncProvider
          → LocalProvider → RouteProvider → DialogProvider → PermissionProvider
            → FrecencyProvider → PromptHistoryProvider → PromptStashProvider
              → EditorContextProvider → ToastProvider → DataProvider → App

The entire app is wrapped in an ErrorBoundary so a rendering error in
one component doesn't kill the whole TUI.
"""

from __future__ import annotations

import sys
from typing import Any


from .context import Context, ContextRegistry
from .providers.base import Provider
from .providers.clipboard import ClipboardProvider
from .providers.args import ArgsProvider
from .providers.kv import KVProvider
from .providers.project import ProjectProvider
from .providers.sdk import SDKProvider
from .providers.renderer import RendererProvider
from .providers.config import ConfigProvider
from .providers.theme import ThemeProvider
from .providers.keymap import KeymapProvider
from .providers.event import EventProvider
from .providers.sync import SyncProvider
from .providers.local import LocalProvider
from .providers.route import RouteProvider
from .providers.dialog import DialogProvider
from .providers.permission import PermissionProvider
from .providers.frecency import FrecencyProvider
from .providers.prompt_history import PromptHistoryProvider
from .providers.prompt_stash import PromptStashProvider
from .providers.editor_context import EditorContextProvider
from .providers.toast import ToastProvider
from .providers.data import DataProvider
from .providers.plugin_runtime import PluginRuntimeProvider
from .providers.editor_integration import EditorIntegrationProvider
from .providers.attention import AttentionProvider
from .providers.session_lifecycle import SessionLifecycleProvider
from .components.chat import ChatComponent
from .components.primitives import ErrorBoundary
from .palette.registry import CommandRegistry
from .palette.palette import CommandPalette
from .dialogs.toast import ToastManager
from .prompt.prompt import Prompt
from .prompt.footer import PromptFooter
from .plugins.api import create_tui_api
from .autocomplete.autocomplete import Autocomplete
from .autocomplete.file_search import AsyncFileSearch
from .platform.platform import detect_terminal_environment, restore_win32_console_mode, flush_win32_input_buffer
from .platform.sighup import SighupHandler
from .startup.scope import ScopedLifecycle
from .startup.epilogue import EpilogueManager




class AppShell:
    """Builds the provider tree and provides access to all contexts."""

    def __init__(self, providers: list[Provider]) -> None:
        self._providers = providers
        self.registry = ContextRegistry()

    def build(self) -> None:
        for provider in self._providers:
            provider.provide(self.registry)

    def get(self, name: str) -> Any:
        return self.registry.get(name)


def _build_providers(transport: Any, workspace: str, cli_args: dict[str, Any]) -> list[Provider]:
    """Assemble the provider tree in dependency order."""
    return [
        ClipboardProvider(),
        ArgsProvider(cli_args),
        KVProvider(workspace=workspace),
        ProjectProvider(workspace),
        SDKProvider(transport),
        RendererProvider(),
        ConfigProvider(),
        ThemeProvider(theme_name=cli_args.get("theme", "tokyo-night")),
        KeymapProvider(overrides=cli_args.get("keybinds")),
        EventProvider(),
        SyncProvider(),
        LocalProvider(),
        RouteProvider(),
        DialogProvider(),
        PermissionProvider(),
        FrecencyProvider(),
        PromptHistoryProvider(),
        PromptStashProvider(),
        EditorContextProvider(),
        EditorIntegrationProvider(workspace=workspace),
        AttentionProvider(),
        SessionLifecycleProvider(workspace=workspace),
        PluginRuntimeProvider(workspace=workspace),
        ToastProvider(),
        DataProvider(),
    ]


async def run_tui(transport: Any, workspace: str, cli_args: dict[str, Any] | None = None) -> int:
    """
    Main TUI entry point — called by the default handler.

    1. Build the provider tree (AppShell)
    2. Initialize the command registry + palette
    3. Initialize toast manager, prompt, and footer
    4. Wrap the ChatComponent in an ErrorBoundary
    5. Launch the reactive REPL
    """
    shell = AppShell(_build_providers(transport, workspace, cli_args or {}))
    shell.build()

    # Initialize command registry with palette metadata. ChatComponent binds
    # these entries to its live command router after it is constructed.
    command_registry = CommandRegistry()
    _register_default_commands(command_registry, shell.registry)

    palette = CommandPalette(
        registry=command_registry,
        theme=shell.registry.get("theme"),
    )
    palette_ctx = Context(name="palette")
    palette_ctx.set(palette)
    shell.registry.register(palette_ctx)

    cmd_ctx = Context(name="command_registry")
    cmd_ctx.set(command_registry)
    shell.registry.register(cmd_ctx)

    # Initialize toast manager with resolved theme
    theme_signal = shell.registry.get("theme")
    resolved_theme = theme_signal.get()

    toast_mgr = ToastManager(theme=resolved_theme)
    toast_ctx = Context(name="toast_manager")
    toast_ctx.set(toast_mgr)
    shell.registry.register(toast_ctx)

    # Initialize prompt and footer
    prompt = Prompt()
    prompt_ctx = Context(name="prompt")
    prompt_ctx.set(prompt)
    shell.registry.register(prompt_ctx)

    footer = PromptFooter(theme=resolved_theme)
    footer_ctx = Context(name="prompt_footer")
    footer_ctx.set(footer)
    shell.registry.register(footer_ctx)

    # Initialize which-key panel
    keymap = shell.registry.get("keymap")
    from .keymap.which_key import WhichKeyPanel
    wk_panel = WhichKeyPanel(keymap, resolved_theme)
    wk_ctx = Context(name="which_key")
    wk_ctx.set(wk_panel)
    shell.registry.register(wk_ctx)

    # Wire attention manager to renderer + KV
    try:
        attention = shell.registry.get("attention")
        renderer = shell.registry.get("renderer")
        kv = shell.registry.get("kv")
        attention.set_renderer(renderer)
        attention.set_kv_store(kv)
    except Exception:
        pass

    # Wire terminal title to KV
    try:
        title_mgr = shell.registry.get("terminal_title")
        kv = shell.registry.get("kv")
        if title_mgr is not None and kv is not None:
            title_mgr.set_kv_store(kv)
    except Exception:
        pass

    # Wire editor integration discovery
    try:
        editor = shell.registry.get("editor_integration")
        if editor is not None:
            editor.discover()
    except Exception:
        pass

    # Start plugin host (no-op by default)
    try:
        plugin_host = shell.registry.get("plugin_runtime")
        if plugin_host is not None:
            api = create_tui_api(
                version="1.0.0",
                config=shell.registry.get("config"),
                dialog=shell.registry.get("dialog"),
                keymap=shell.registry.get("keymap"),
                kv=shell.registry.get("kv"),
                route=shell.registry.get("route"),
                event=shell.registry.get("event"),
                sdk=shell.registry.get("sdk"),
                sync=shell.registry.get("sync"),
                theme=resolved_theme,
                toast=shell.registry.get("toast_manager"),
                renderer=shell.registry.get("renderer"),
                attention=shell.registry.get("attention"),
            )
            runtime = plugin_host.start(api=api, config=cli_args or {}, theme=resolved_theme)
            rt_ctx = Context(name="plugin_runtime_instance")
            rt_ctx.set(runtime)
            shell.registry.register(rt_ctx)
    except Exception:
        pass

    # Initialize autocomplete
    try:
        frecency = shell.registry.get("frecency")
        file_search = AsyncFileSearch(frecency=frecency)
        autocomplete = Autocomplete(
            file_search=file_search,
            frecency=frecency,
            slash_commands=[],
        )
        ac_ctx = Context(name="autocomplete")
        ac_ctx.set(autocomplete)
        shell.registry.register(ac_ctx)
    except Exception:
        pass

    # Platform setup — detect environment and ensure console is usable
    try:
        # On Windows, ensure the console has proper input mode (recover
        # from prior broken exits that left mode at 0x0000)
        if sys.platform == "win32":
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                STD_INPUT_HANDLE = -10
                handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
                mode = ctypes.c_ulong()
                if kernel32.GetConsoleMode(handle, ctypes.byref(mode)) and mode.value == 0:
                    # Console mode was zeroed out by a prior broken run — restore
                    kernel32.SetConsoleMode(handle, ctypes.c_ulong(0x0007))
            except Exception:
                pass

        env = detect_terminal_environment()
        env_ctx = Context(name="terminal_env")
        env_ctx.set(env)
        shell.registry.register(env_ctx)
    except Exception:
        pass

    # SIGHUP handler
    try:
        renderer = shell.registry.get("renderer")
        sighup = SighupHandler(on_sighup=lambda: renderer.destroy() if renderer else None)
        sighup.register()
        sh_ctx = Context(name="sighup_handler")
        sh_ctx.set(sighup)
        shell.registry.register(sh_ctx)
    except Exception:
        pass

    # Epilogue manager
    epilogue = EpilogueManager()
    ep_ctx = Context(name="epilogue")
    ep_ctx.set(epilogue)
    shell.registry.register(ep_ctx)

    # Wrap the chat in an error boundary for crash resilience
    chat = ChatComponent(shell.registry)
    _wire_chat_commands(command_registry, chat)

    try:
        await chat.run()
    except KeyboardInterrupt:
        pass
    finally:
        chat.cleanup()

    # Cleanup renderer resources
    try:
        renderer = shell.registry.get("renderer")
        renderer.destroy()
    except Exception:
        pass

    # Dispose plugin host
    try:
        plugin_host = shell.registry.get("plugin_runtime")
        if plugin_host is not None:
            plugin_host.dispose()
    except Exception:
        pass

    # Dispose attention manager
    try:
        attention = shell.registry.get("attention")
        if attention is not None:
            attention.dispose()
    except Exception:
        pass

    # Restore Windows console mode and flush input buffer
    try:
        restore_win32_console_mode()
        flush_win32_input_buffer()
    except Exception:
        pass

    # Unregister SIGHUP handler
    try:
        sighup = shell.registry.get("sighup_handler")
        if sighup is not None:
            sighup.unregister()
    except Exception:
        pass

    # Print epilogue
    try:
        epilogue = shell.registry.get("epilogue")
        if epilogue is not None:
            epilogue.print()
    except Exception:
        pass

    return 0


def _wire_chat_commands(registry: CommandRegistry, chat: ChatComponent) -> None:
    """Make palette selections execute through the same path as slash input."""
    command_to_slash = {
        "session.list": "/sessions",
        "session.resume_last": "/resume",
        "model.list": "/models",
        "tier.switch": "/tier",
        "theme.switch": "/theme",
        "reasoning.effort": "/reasoning",
        "nitro.toggle": "/nitro on",
        "help.show": "/help",
        "prompt.editor": "/editor",
        "prompt.stash": "/stash",
        "prompt.stash_pop": "/stash-pop",
        "prompt.stash_list": "/stash-list",
        "session.reset": "/reset",
        "session.approve": "/approve",
        "session.status": "/status",
        "app.which_key": "/keys",
        "session.compact": "/compact",
        "attention.toggle": "/notifications",
        "plugin.list": "/plugins",
        "app.exit": "/exit",
    }

    def make_handler(slash: str) -> Any:
        def run() -> None:
            import asyncio

            asyncio.create_task(chat._handle_command(slash))
        return run

    for command_name, slash in command_to_slash.items():
        command = registry.get(command_name)
        if command is not None:
            command.run = make_handler(slash)


def _register_default_commands(registry: CommandRegistry, ctx: ContextRegistry) -> None:
    """the palette im gonna go with, might try to match the site colors later."""
    from .palette.registry import Command

    commands = [
        Command(
            name="session.list",
            title="Switch session",
            category="Session",
            slash_name="sessions",
            slash_aliases=["continue"],
            desc="List and switch between sessions",
        ),
        Command(
            name="session.resume_last",
            title="Resume last session",
            category="Session",
            slash_name="resume",
            desc="Immediately resume the most recent session",
        ),
        Command(
            name="model.list",
            title="Switch model",
            category="Agent",
            suggested=True,
            slash_name="models",
            desc="List and switch models",
        ),
        Command(
            name="tier.switch",
            title="Switch tier",
            category="Agent",
            slash_name="tier",
            desc="Switch execution tier (fast, balanced, quality)",
        ),
        Command(
            name="theme.switch",
            title="Switch theme",
            category="System",
            slash_name="themes",
            desc="Switch terminal theme",
        ),
        Command(
            name="help.show",
            title="Help",
            category="System",
            slash_name="help",
            desc="Show help and keybindings",
        ),
        Command(
            name="prompt.editor",
            title="Open external editor",
            category="Prompt",
            slash_name="editor",
            desc="Edit prompt in $EDITOR",
        ),
        Command(
            name="prompt.stash",
            title="Stash prompt",
            category="Prompt",
            slash_name="stash",
            desc="Save current prompt draft",
        ),
        Command(
            name="prompt.stash_pop",
            title="Stash pop",
            category="Prompt",
            slash_name="stash-pop",
            desc="Restore stashed prompt",
        ),
        Command(
            name="prompt.stash_list",
            title="Stash list",
            category="Prompt",
            slash_name="stash-list",
            desc="List stashed prompts",
        ),
        Command(
            name="session.timeline",
            title="Timeline",
            category="Session",
            slash_name="timeline",
            desc="Jump to a message in the conversation",
        ),
        Command(
            name="session.export",
            title="Export session",
            category="Session",
            slash_name="export",
            desc="Export conversation to markdown",
        ),
        Command(
            name="session.copy_last",
            title="Copy last assistant message",
            category="Session",
            slash_name="copy",
            desc="Copy the last AI response to clipboard",
        ),
        Command(
            name="app.exit",
            title="Exit",
            category="System",
            slash_name="exit",
            desc="Exit the application",
        ),
        Command(
            name="session.reset",
            title="Clear context",
            category="Session",
            slash_name="reset",
            desc="Clear conversation context",
        ),
        Command(
            name="session.approve",
            title="Approve plan",
            category="Session",
            slash_name="approve",
            desc="Approve a pending plan",
        ),
        Command(
            name="session.task",
            title="Run task",
            category="Session",
            slash_name="task",
            desc="Run a full agent task",
        ),
        Command(
            name="reasoning.effort",
            title="Reasoning effort",
            category="Agent",
            slash_name="reasoning",
            desc="Show or change reasoning effort level",
        ),
        Command(
            name="nitro.toggle",
            title="Enable Nitro mode",
            category="Agent",
            slash_name="nitro",
            desc="Enable OpenRouter Nitro model routing",
        ),
        Command(
            name="session.status",
            title="Show status",
            category="Session",
            slash_name="status",
            desc="Show workspace state",
        ),
        Command(
            name="app.which_key",
            title="Show keybindings",
            category="System",
            slash_name="keys",
            desc="Show all keybindings",
        ),
        Command(
            name="app.command_palette",
            title="Command palette",
            category="System",
            desc="Open the command palette",
            key_binding="ctrl+p",
        ),
        # Session lifecycle commands (Doc 13)
        Command(
            name="session.fork",
            title="Fork session",
            category="Session",
            slash_name="fork",
            desc="Fork from the current message",
        ),
        Command(
            name="session.share",
            title="Share session",
            category="Session",
            slash_name="share",
            desc="Share session and copy URL",
        ),
        Command(
            name="session.unshare",
            title="Unshare session",
            category="Session",
            slash_name="unshare",
            desc="Stop sharing session",
        ),
        Command(
            name="session.compact",
            title="Compact session",
            category="Session",
            slash_name="compact",
            slash_aliases=["summarize"],
            desc="Summarize conversation to free context",
        ),
        Command(
            name="session.undo",
            title="Undo previous message",
            category="Session",
            slash_name="undo",
            desc="Revert to previous user message",
        ),
        Command(
            name="session.redo",
            title="Redo",
            category="Session",
            slash_name="redo",
            desc="Restore reverted messages",
        ),
        Command(
            name="session.rename",
            title="Rename session",
            category="Session",
            slash_name="rename",
            desc="Rename the current session",
        ),
        Command(
            name="session.timeline",
            title="Timeline",
            category="Session",
            slash_name="timeline",
            desc="Jump to a message in the conversation",
        ),
        Command(
            name="session.export",
            title="Export session",
            category="Session",
            slash_name="export",
            desc="Export conversation to markdown",
        ),
        Command(
            name="session.copy_transcript",
            title="Copy transcript",
            category="Session",
            slash_name="copy",
            desc="Copy full transcript to clipboard",
        ),
        # Editor integration commands (Doc 11)
        Command(
            name="prompt.editor",
            title="Open external editor",
            category="Prompt",
            slash_name="editor",
            desc="Edit prompt in $EDITOR",
        ),
        Command(
            name="editor.remove_context",
            title="Remove editor context",
            category="Prompt",
            slash_name="remove-context",
            desc="Dismiss auto-attached editor selection",
        ),
        # Attention commands (Doc 12)
        Command(
            name="attention.toggle",
            title="Toggle notifications",
            category="System",
            slash_name="notifications",
            desc="Toggle OS notifications and sounds",
        ),
        # Plugin commands (Doc 10)
        Command(
            name="plugin.list",
            title="List plugins",
            category="Plugin",
            slash_name="plugins",
            desc="List installed plugins",
        ),
        Command(
            name="plugin.activate",
            title="Activate plugin",
            category="Plugin",
            hidden=True,
            desc="Activate a plugin by ID",
        ),
        Command(
            name="plugin.deactivate",
            title="Deactivate plugin",
            category="Plugin",
            hidden=True,
            desc="Deactivate a plugin by ID",
        ),
    ]

    registry.register_many(commands, namespace="palette")

    # Register quick-switch slots (hidden commands 1-9)
    from .palette.registry import Command as Cmd
    for i in range(1, 10):
        registry.register(Cmd(
            name=f"session.quick_switch.{i}",
            title=f"Switch to session in slot {i}",
            category="Session",
            hidden=True,
            key_binding=f"<leader>{i}",
        ), namespace="palette")
