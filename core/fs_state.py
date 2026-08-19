"""Semantic filesystem state tracking.

Replaces shell-command string parsing for file-mutation detection. The
previous approach parsed ``run_command`` argument strings and mistook tokens
like ``gc.log`` or ``-3`` for modified files, flooding the agent's context
with apologies and noise.

This module snapshots the working tree before and after a tool executes,
diffs the snapshots to find ACTUAL changes (created/modified/deleted), and
verifies them against ``git`` in the background so that only real byte-level
mutations are surfaced to the agent's context.

The tracker is intentionally dependency-free and cross-platform: it uses
``os.scandir`` + ``stat`` (no inotify / ReadDirectoryChangesW / watchdog), so
it works identically on Linux, macOS, and Windows. Snapshots are bounded
(file count and per-file size caps) and reuse a fresh cached snapshot as the
"before" view of back-to-back calls to halve the walk cost.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("agent.core.fs_state")

# Directories pruned from snapshots — they bloat the walk, churn constantly,
# and are never semantically interesting file mutations.
_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".zircon-code",
    ".venv", "venv", "env", "dist", "build", "target", ".idea", ".vscode",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "site-packages",
    ".next", ".nuxt", "coverage", ".cache",
}

_SNAPSHOT_TTL = 2.0  # seconds a cached snapshot is reusable as "before"
_MAX_FILES = 20000
_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
_MAX_NOTE_ENTRIES = 15


@dataclass
class FileChange:
    path: str
    kind: str  # "created" | "modified" | "deleted"
    old_size: int
    new_size: int
    git_verified: bool = False


class FilesystemStateTracker:
    """Snapshot-based filesystem mutation detector with background git verify.

    Lives on the ``ToolRegistry`` (set by the ``Agent``) so every execution
    path — main executor, sub-agents, swarm, research — shares one tracker.
    """

    def __init__(
        self,
        repo_path: str | Path,
        *,
        max_files: int = _MAX_FILES,
        max_file_size: int = _MAX_FILE_SIZE,
        verify_enabled: bool = True,
    ) -> None:
        self.repo_path = Path(repo_path).resolve()
        self._max_files = max_files
        self._max_file_size = max_file_size
        self.verify_enabled = verify_enabled
        # Most recent batch's changes (for inline result surfacing).
        self.last_changes: list[FileChange] = []
        # Cumulative set of files changed since the last reset().
        self._changed: set[str] = set()
        # Paths git explicitly says are NOT content-changed (touch-only etc.),
        # used to drop false positives surfaced by mtime-only detection.
        self._git_disqualified: set[str] = set()
        # Paths git confirms as actually changed (informational).
        self.git_confirmed: set[str] = set()
        # Snapshot cache + freshness.
        self._cache: dict[str, tuple[int, int]] | None = None
        self._cache_ts: float = 0.0
        # In-flight background git-verify threads (kept alive / joined on stop).
        self._verify_threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------
    def reset(self) -> None:
        """Clear accumulated state (call at the start of a fresh task)."""
        self.last_changes = []
        self._changed.clear()
        self._git_disqualified.clear()
        self.git_confirmed.clear()
        self._cache = None
        self._cache_ts = 0.0

    def snapshot(self) -> dict[str, tuple[int, int]]:
        """Walk the working tree and record {rel_path: (size, mtime_ns)}.

        Always performs a fresh walk and refreshes the cache. Paths use
        forward slashes regardless of platform.
        """
        repo = self.repo_path
        out: dict[str, tuple[int, int]] = {}
        count = 0
        try:
            for entry in os.scandir(repo):
                if entry.name in _SKIP_DIRS or entry.name.startswith(".git"):
                    continue
                count = self._scan_entry(entry, repo, out, count)
                if count >= self._max_files:
                    break
        except (OSError, PermissionError):
            pass
        self._cache = out
        self._cache_ts = _now()
        return out

    def _scan_entry(
        self,
        entry: os.DirEntry,
        repo: Path,
        out: dict[str, tuple[int, int]],
        count: int,
    ) -> int:
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            return count
        if is_dir:
            if entry.name in _SKIP_DIRS:
                return count
            try:
                for child in os.scandir(entry.path):
                    if count >= self._max_files:
                        return count
                    if child.name in _SKIP_DIRS:
                        continue
                    count = self._scan_entry(child, repo, out, count)
            except (OSError, PermissionError):
                pass
            return count
        # File.
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            return count
        if st.st_size > self._max_file_size:
            return count
        try:
            rel = str(Path(entry.path).relative_to(repo)).replace("\\", "/")
        except ValueError:
            rel = entry.name
        out[rel] = (st.st_size, int(st.st_mtime_ns))
        return count + 1

    def snapshot_cached(self) -> dict[str, tuple[int, int]]:
        """Return a fresh-enough snapshot; reuse the cache when still warm."""
        if self._cache is not None and (_now() - self._cache_ts) < _SNAPSHOT_TTL:
            return self._cache
        return self.snapshot()

    # ------------------------------------------------------------------
    # Diffing
    # ------------------------------------------------------------------
    def diff(
        self,
        before: dict[str, tuple[int, int]],
        after: dict[str, tuple[int, int]],
    ) -> list[FileChange]:
        """Compute the real changes between two snapshots."""
        changes: list[FileChange] = []
        for path, (size, mtime) in after.items():
            prev = before.get(path)
            if prev is None:
                changes.append(FileChange(path, "created", 0, size))
            elif prev != (size, mtime):
                changes.append(FileChange(path, "modified", prev[0], size))
        for path, (size, _mtime) in before.items():
            if path not in after:
                changes.append(FileChange(path, "deleted", size, 0))
        if changes:
            with self._lock:
                for c in changes:
                    self._changed.add(c.path)
                    # A new real change clears any prior git disqualification.
                    self._git_disqualified.discard(c.path)
        return changes

    def changed_files(self) -> set[str]:
        """Cumulative set of actually-changed files, minus git-disqualified."""
        with self._lock:
            return set(self._changed) - set(self._git_disqualified)

    def git_verified_files(self) -> set[str]:
        with self._lock:
            return set(self.git_confirmed)

    # ------------------------------------------------------------------
    # Surfacing
    # ------------------------------------------------------------------
    def format_changes_note(self, changes: list[FileChange]) -> str:
        if not changes:
            return ""
        ordered = sorted(changes, key=lambda c: (c.kind, c.path))
        lines = ["<filesystem_changes>"]
        for c in ordered[:_MAX_NOTE_ENTRIES]:
            lines.append(f"  {c.kind}: {c.path}")
        if len(ordered) > _MAX_NOTE_ENTRIES:
            lines.append(f"  ... ({len(ordered) - _MAX_NOTE_ENTRIES} more)")
        lines.append("</filesystem_changes>")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Background git verification
    # ------------------------------------------------------------------
    def verify_async(self, changes: list[FileChange]) -> None:
        """Kick off a background git diff/status to confirm the changes.

        Non-blocking: runs in a daemon thread, never raises into the caller,
        and is a no-op when disabled or when the repo is not a git repo.
        """
        if not self.verify_enabled or not changes:
            return
        if not (self.repo_path / ".git").exists():
            return
        t = threading.Thread(
            target=self._verify_safe, args=(changes,), daemon=True
        )
        with self._lock:
            self._verify_threads.append(t)
        t.start()

    def _verify_safe(self, changes: list[FileChange]) -> None:
        try:
            self.verify_with_git(changes)
        except Exception as e:  # never propagate into the tool loop
            logger.debug("fs_state git verify failed: %s", e)

    def verify_with_git(self, changes: list[FileChange]) -> set[str]:
        """Run ``git`` to confirm which paths actually have content changes.

        Returns the set of git-confirmed paths (forward-slash, repo-relative).
        Also updates ``git_confirmed`` and disqualifies touch-only
        modifications (mtime changed but content unchanged) so they are not
        surfaced as real mutations.
        """
        cwd = str(self.repo_path)
        confirmed: set[str] = set()
        from .proc_spawn import popen_kwargs
        try:
            r = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                capture_output=True, text=True, cwd=cwd, timeout=10,
                **popen_kwargs(),
            )
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    p = line.strip().replace("\\", "/")
                    if p:
                        confirmed.add(p)
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            r = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard"],
                capture_output=True, text=True, cwd=cwd, timeout=10,
                **popen_kwargs(),
            )
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    p = line.strip().replace("\\", "/")
                    if p:
                        confirmed.add(p)
        except (OSError, subprocess.SubprocessError):
            pass

        with self._lock:
            self.git_confirmed = confirmed
            # Disqualify "modified" entries git does NOT confirm (touch-only).
            for c in changes:
                norm = c.path.replace("\\", "/")
                if c.kind == "modified" and norm not in confirmed:
                    self._git_disqualified.add(c.path)
                else:
                    self._git_disqualified.discard(c.path)
                    if norm in confirmed:
                        c.git_verified = True
        return confirmed


def _now() -> float:
    import time
    return time.monotonic()
