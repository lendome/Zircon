# Environment Discovery

How the engine works out *what machine it is on* before it runs anything: which
shell to speak, how to spawn processes, and whether what it started is actually
alive. Three modules cooperate:

| Concern | Module |
| --- | --- |
| Which shell, and its syntax family | `shell_env.py` |
| How child processes are spawned | `proc_spawn.py` |
| Whether a started service is reachable | `runtime_probe.py` |

## 1. Shell discovery (`shell_env.py`)

The agent used to burn turns fighting cmd.exe while writing bash-flavoured
commands. Discovery removes the guesswork: **one** shell is detected per
machine and pinned for the whole session.

**Candidate order**

- Windows: Git Bash (`%ProgramFiles%`, `%ProgramFiles(x86)%`, `%LocalAppData%`
  under `Git\bin\bash.exe`) → `bash` on `PATH` → `pwsh` → `powershell` → cmd.
  Only the first PowerShell found is kept, so `pwsh` wins over Windows
  PowerShell rather than both being tried.
- POSIX: `$SHELL` → `/bin/bash` → `/usr/bin/bash` → `/bin/sh`.

**PATH lookups are never trusted on their own.** `shutil.which` only nominates
a candidate; every candidate is then *probe-verified* by running a trivial
`echo __zircon_shell_ok__` round-trip with a 5s timeout and checking both the
exit code and the marker in stdout. This is what stops the WindowsApps
`bash.exe` WSL launcher from being selected — it exists on `PATH`, exits
non-zero without WSL installed, and probing it can hang, so the path is
excluded by name (`windowsapps`) *and* would fail the probe anyway.

**Result shape.** Detection yields a frozen `ShellSpec`:

- `name` — human label (`git-bash`, `pwsh`, `powershell`, `cmd`, `sh`)
- `kind` — syntax family (`bash`, `powershell`, `cmd`, `sh`); this is what
  drives the syntax hint injected into the prompt
- `exe` — executable path, **empty for the platform default**
- `prefix_args` — args before the command string (`('-c',)` for bash;
  `('-NoProfile', '-NonInteractive', '-Command')` for PowerShell)

`uses_default_shell` is simply "no `exe`", meaning execution falls through to
`asyncio.create_subprocess_shell` — byte-for-byte the pre-discovery behaviour.
If nothing probes clean, the fallback is exactly that: cmd on Windows, `sh`
elsewhere.

**Caching.** `resolve_shell(pinning_enabled=True)` is the public entry point
and is memoised (`functools.lru_cache`) on the pinning flag, so detection —
and its subprocess probes — happens at most once per process. With pinning
disabled, detection is skipped entirely and the platform default is returned.
`reset_shell_cache()` clears it; tests use this to force re-detection.

## 2. Command capture

Discovery is only useful if every tool routes through it. `run_capture()` is
the single execution helper: it takes a command string, resolves the pinned
shell (or an explicit `spec`), and returns a `CaptureResult` with
`stdout` / `stderr` / `exit_code` / `duration` / `timed_out`. Tools never
hand-roll pipes, drains, or timeout handling.

On timeout with `kill_on_timeout=False` the live `proc`, its buffers, and the
drain tasks are handed back on the result so the caller can *adopt* the process
as a background job instead of losing it.

`format_capture()` renders results in one canonical layout — `STDOUT:` /
`STDERR:` / `Exit code: N` — with CRLF normalised, which is precisely what
`runtime_probe.extract_exit_code` parses. Changing that layout breaks fact
extraction downstream.

`shell_syntax_hint()` turns the discovered `kind` into the per-platform command
guidance handed to the model, so it writes bash for Git Bash and cmd syntax for
cmd instead of averaging the two.

## 3. Runtime discovery (`runtime_probe.py`)

Environment discovery does not stop at spawn time. After a command runs, the
probe extracts facts from its output — local URLs (`extract_local_urls`,
`normalize_probe_url`), produced artifacts (`extract_artifacts`), background
PIDs, and the exit code — then `probe_url` actually connects to advertised
local URLs. `has_reachable_url` / `has_unreachable_url` are what let the agent
refuse to claim a dev server is up until a `[url-health] … HTTP <code>` line
proves it.

## Adding a new discovery step

1. Nominate candidates cheaply (`shutil.which`, well-known install paths).
2. **Probe before trusting.** Presence on `PATH` is not evidence a thing works.
3. Cache the resolved answer for the session; expose a `reset_*` for tests.
4. Fall back to the platform default rather than raising — a degraded shell is
   better than no shell.
