# ZirconX

Python 3.10+ | Coding, research, and security testing assistant | v1.0

Zircon is a capable AI coding and research agent for working in real codebases. It is designed to take a task from investigation through implementation and verification, while keeping the project context, tool results, and conversation available as the work develops.

Zircon is built for practical software work rather than isolated code snippets. It can navigate unfamiliar repositories, reason across multiple files, use the shell and development tools, make structured edits, run verification, and continue from saved sessions. It is designed to give the underlying model the context and capabilities it needs to complete substantial tasks efficiently.

As with any tool that can change a codebase, review the resulting diff and verification output before merging or deploying changes. This is a normal part of using an engineering tool with meaningful access, not a substitute for the agent's own checks.

## What It Does

Zircon can help with tasks such as:

- Understanding an unfamiliar repository. [edit_file OK]
- Finding functions, files, references, and related code.
- Finding functions, files, references, and related code. [edit_lines OK]
- Refactoring across multiple files.
- Writing and running tests.
- Researching documentation and technical questions.
- Checking syntax and reviewing the result of edits.
- Keeping a durable transcript that you can reopen later.

The assistant works through an integrated toolset for reading and writing files, searching the project, navigating symbols, running commands, inspecting Git state, checking changes, and accessing configured web services. You choose the workspace and providers, while Zircon manages the task flow and keeps the relevant context available to the agent.

## Getting Started

Install the dependencies:

```bash
pip install -r requirements.txt
```

Configure a model provider in `models.yaml`. The `models.yaml.ex` file contains an example configuration. API keys can be supplied through environment variables rather than stored directly in the file.

Launch Zircon in the current directory:

```bash
python -m zirconAgent.cli
```

Or install the `zircon` command:

```bash
bash install.sh     # macOS and Linux
install.bat         # Windows
```

Then open a workspace with:

```bash
zircon
zircon /path/to/project
```

The first launch may ask you to choose or configure a provider.

## The TUI

The terminal interface is the main interactive way to use Zircon. It provides a focused workspace for conversations, command discovery, file autocomplete, saved-session browsing, context awareness, and live progress while the agent investigates and works through a task.

Useful controls include:

- `Ctrl+L`: open the saved-session browser.
- `Ctrl+P`: open the command palette.
- `/`: begin a slash command.
- `@`: search for files while writing a prompt.
- `Esc`: stop an active turn or clear the prompt.
- `Esc Esc`: open checkpoint actions when available.
- `Ctrl+C` twice: exit.
- `Ctrl+Shift+M`: toggle prompt mouse selection. Native terminal selection is the default so you can drag across agent output and copy it normally.

The footer shows the active session, model, provider, context usage, and cost when that information is available. Context is displayed as an estimate of the next request, for example `ctx 14,781/128,000 (12%)`.

### Saved Sessions

Sessions are stored in `.zircon-code/sessions` inside the workspace. The session browser shows recent sessions, their status, modified-file counts, and message history.

When you resume a session, Zircon clears the previous active conversation, restores the selected transcript, and lets you continue from that session. Long or older sessions may include warnings if some state was created by an earlier version.

## Execution Tiers

You can choose a tier when launching Zircon and switch tiers while the TUI is open.

### Fast

Fast is optimized for quick questions, small edits, and straightforward exploration. It reduces planning overhead and uses a smaller context budget when speed and cost matter more than extended task coordination.

### Balanced

Balanced is the default for serious day-to-day development work. It plans multi-step tasks when useful, uses repository context, performs verification, and maintains a large conversation window for work that spans files and tools.

Context budget: 128K tokens.

### Quality

Quality is intended for larger, more involved, or less familiar changes. It provides the largest context budget, deeper planning, additional review, and specialized helper agents when those capabilities improve the result.

Context budget: 256K tokens.

These budgets describe what Zircon is prepared to use. The provider and model still need to support the requested context size. If a provider supports less, its limit takes precedence.

Examples:

```bash
zircon --fast
zircon --quality
zircon --plan-mode
zircon --swarm
```

Inside the TUI:

```text
/tier fast
/tier balanced
/tier quality
```

## Common Commands

```text
zircon                              Open the TUI in the current directory
zircon /path/to/project             Open a specific workspace
zircon task "fix the off-by-one"    Run a task without the TUI
zircon status                       Show daemon and session status
zircon models                       List configured model profiles
zircon tier quality                 Change the active tier
zircon service start                Start the background daemon
zircon service stop                 Stop the background daemon
zircon service restart              Restart the background daemon
zircon tui                          Open the TUI explicitly
zircon help                         Show command help
```

The TUI also includes commands such as:

```text
/help                 Show help
/status               Show current status and context usage
/sessions             Browse saved sessions
/resume               Resume the most recent session
/compact              Compact the model context
/models               Choose or inspect models
/theme                Change the terminal theme
/reset                Clear the active model context
/exit                 Leave Zircon
```

## How Zircon Works

Zircon combines a capable model with a structured task runtime and a set of project tools. Depending on the task and selected tier, it may:

1. Read project structure and relevant files.
2. Decide whether a plan would help.
3. Ask for approval when a plan requires it.
4. Make edits or run commands through its tools.
5. Check syntax, tests, builds, or other available evidence.
6. Explain what it did and what still needs attention.

The project also keeps supporting information such as a repository map, working-set files, notes, checkpoints, and session transcripts. The runtime manages context deliberately, preserving recent working state and compacting older model input when necessary so longer tasks can continue without losing the overall direction of the work. The saved transcript remains available for later review.

## Running Without The TUI

For scripts, CI, or other integrations:

```bash
zircon task "run the tests and explain any failures"
```

The JSON API reads newline-delimited requests from standard input:

```bash
zircon api
```

The Python API is also available:

```python
from zirconAgent.core.agent import Agent

agent = Agent(
    repo_path=".",
    config_path="models.yaml",
    tier="balanced",
)
await agent.solve("Refactor the database layer to use async queries")
```

## Providers And Configuration

Provider profiles are configured in `models.yaml`. The configuration can include OpenAI-compatible services, Anthropic-compatible services, local models, and other supported endpoints.

Keep credentials out of source control. Prefer environment variables or a local configuration file that is excluded from Git. Check the provider's context-window, privacy, and billing behavior before using it with a project.

Optional features may use packages such as local embedding models, Docker, or language-specific development tools. Zircon should continue with reduced functionality when optional components are unavailable, but the exact behavior depends on the task and configuration.

## Safety And Review

Zircon can read and modify files, execute shell commands, access configured URLs, and interact with Git. These capabilities are what let it complete real engineering tasks instead of only suggesting snippets. Use the normal engineering controls around those permissions, including diffs, tests, checkpoints, and deployment review.

Before accepting a change:

- Read the diff.
- Run the relevant tests or checks.
- Confirm commands ran in the intended workspace.
- Check that secrets and private data were not exposed to a model or external provider.
- Confirm that security testing is authorized and within the agreed scope.
- Use checkpoints or version control when the change matters.

Zircon can support authorized security testing, defensive research, education, and CTF work. You are responsible for having permission to test the systems, accounts, networks, and data involved. Do not use it to access or alter systems without authorization.

## Project Layout

```text
cli/              Command-line entry points, daemon, and terminal UI
core/             Agent lifecycle, planning, context, sessions, and execution
tools/            File, search, shell, web, and development tools
llm/              Provider routing and model communication
parsers/          Code and edit parsers
subagents/        Optional specialized helper agents
vcs/              Git integration and checkpoints
sandbox/          Sandbox-related examples and support
tests/            Automated tests
```

## Requirements

- Python 3.10 or newer.
- A configured model provider, unless you use a local model.
- Git is recommended for checkpoints and reviewing changes.

## License

Zircon is released under the MIT License.

This project is provided as-is, without a promise that it will be correct, safe, available, or suitable for a particular purpose. You are responsible for how you configure and use it, including any changes it makes and any systems you test. Security-related use must be authorized and must follow applicable law and organizational policy.

See the `LICENSE` file for the license text.
