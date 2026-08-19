"""Git integration — automatic commits, rollbacks, and session state management.

This module implements "State & Rollback Management" from the blueprint:
1. Auto-commit before every edit so we can rollback
2. Auto-rollback on critical failure (loop detection or recovery exhaustion)
3. Snapshot-based diff comparison for verification
4. Branch-per-session isolation for safe exploration

Integration points:
- Agent.solve() / Agent.solve_stream() calls snapshot_edits() before editing
- Executor calls auto_commit_after_edit() after successful tool edits
- On critical loop failure, Executor calls rollback_to_last_good()
"""

from __future__ import annotations

import json
import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from .constants import zircon_path
from ..vcs.git import GitManager

logger = logging.getLogger("agent.core.git_integration")


class GitIntegration:
    """Git integration wrapper that adds auto-commit/rollback to the agent loop.

    Provides:
    - snapshot_before_edit(): Auto-commits current state before modifying files
    - commit_after_edit(): Commits after successful edits with descriptive messages
    - rollback_on_failure(): Rolls back to the pre-edit state if something goes wrong
    - session management: Creates a dedicated agent session branch
    """

    def __init__(self, repo_path: str | Path):
        self.repo_path = Path(repo_path).resolve()
        self._git = GitManager(self.repo_path)
        self._session_active = False
        self._last_good_commit: str | None = None
        self._edits_since_commit: list[tuple[str, str]] = []  # [(file_path, action)]

    def is_available(self) -> bool:
        """Check if git is available and the repo is a git repo."""
        try:
            return self._git.is_git_repo()
        except Exception:
            return False

    def start_session(self, session_id: str) -> bool:
        """Start a new agent session on a dedicated branch.

        Returns True if successful, False if git is not available.
        """
        if not self.is_available():
            logger.debug("Git not available, skipping session branch creation")
            return False

        try:
            success = self._git.create_session_branch(session_id)
            if success:
                self._session_active = True
                self._last_good_commit = self._get_current_commit()
                logger.info("Git session started on branch agent/%s", session_id)
            return success
        except Exception as e:
            logger.warning("Failed to create session branch: %s", e)
            return False

    def snapshot_before_edit(self, files: list[str]) -> bool:
        """Auto-commit current state before modifying files.

        This ensures we have a revert point if the edit fails.
        Returns True if snapshot was created.
        """
        if not self._session_active:
            return False

        try:
            # Stage and commit any pending changes
            self._git.commit(
                f"agent: snapshot before editing {', '.join(files[:5])}{'...' if len(files) > 5 else ''}",
                paths=files,
            )
            self._last_good_commit = self._get_current_commit()
            logger.debug("Snapshot created for %d files", len(files))
            return True
        except Exception as e:
            logger.warning("Failed to create snapshot: %s", e)
            return False

    def commit_after_edit(
        self,
        files: list[str],
        task_description: str = "",
        success: bool = True,
    ) -> bool:
        """Commit after successful edits.

        Args:
            files: List of modified file paths
            task_description: Description of what was done
            success: Whether the edit was successful

        Returns True if commit was created.
        """
        if not self._session_active or not files:
            return False

        try:
            prefix = "agent: " if success else "agent: (partial) "
            msg = prefix + task_description[:200] if task_description else \
                prefix + f"modified {len(files)} file(s): {', '.join(files[:5])}"
            committed = self._git.commit(msg, paths=files)
            if committed:
                self._last_good_commit = self._get_current_commit()
                logger.info("Committed %d files: %s", len(files), msg[:80])
            return committed
        except Exception as e:
            logger.warning("Failed to commit: %s", e)
            return False

    def rollback_on_failure(self, reason: str = "") -> bool:
        """Rollback to the last known good commit.

        This is called when:
        - A critical loop is detected
        - Recovery attempts are exhausted
        - A verification step fails catastrophically

        Returns True if rollback was successful.
        """
        if not self._session_active or not self._last_good_commit:
            return False

        try:
            self._git.rollback("HEAD~1")
            logger.info("Rolled back due to: %s", reason[:100] if reason else "unknown failure")
            return True
        except Exception as e:
            logger.warning("Failed to rollback: %s", e)
            return False

    def get_diff_summary(self) -> str:
        """Get a summary of changes since the session started."""
        if not self._session_active:
            return ""
        try:
            return self._git.status()
        except Exception:
            return ""

    def end_session(self, accept: bool = True) -> bool:
        """End the agent session, optionally merging changes back to the original branch.

        Args:
            accept: If True, merge session branch changes back to original.
                    If False, discard session branch entirely.

        Returns True if finalize was successful.
        """
        if not self._session_active:
            return False

        try:
            result = self._git.finalize(accept=accept)
            self._session_active = False
            self._last_good_commit = None
            self._edits_since_commit.clear()
            return result
        except Exception as e:
            logger.warning("Failed to finalize session: %s", e)
            return False

    def _get_current_commit(self) -> str | None:
        """Get the current HEAD commit hash."""
        try:
            commits = self._git.get_recent_commits(1)
            if commits:
                return commits[0].get("sha", "")
            return None
        except Exception:
            return None

    def track_edit(self, file_path: str, action: str = "edit") -> None:
        """Track a file edit for commit aggregation."""
        self._edits_since_commit.append((file_path, action))

    def commit_pending_edits(self, task: str = "") -> bool:
        """Commit all pending tracked edits."""
        if not self._edits_since_commit:
            return False
        files = list(set(f for f, _ in self._edits_since_commit))
        result = self.commit_after_edit(files, task)
        self._edits_since_commit.clear()
        return result

    @property
    def pending_edit_count(self) -> int:
        return len(self._edits_since_commit)

    # ── Checkpoint API (reversibility) ──────────────────────────────────────

    _MAX_CHECKPOINT_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
    _MAX_CHECKPOINT_TOTAL_SIZE = 100 * 1024 * 1024  # 100 MB

    def create_checkpoint(self, label: str = "") -> dict[str, Any] | None:
        """Snapshot the workspace in Zircon runtime storage before an agent turn.

        Checkpoints intentionally live under ``.zircon-code/checkpoints`` and
        never create commits, branches, or staged changes in the user's Git
        repository.

        Size guards: individual files exceeding 20 MB are skipped, and the
        total checkpoint size is capped at 100 MB. Files that would push the
        total over the limit are omitted with a debug log.
        """
        try:
            timestamp = time.time()
            checkpoint_id = f"{int(timestamp * 1000):x}-{uuid.uuid4().hex[:8]}"
            root = zircon_path(self.repo_path, "checkpoints")
            snapshot_dir = root / checkpoint_id / "files"
            snapshot_dir.mkdir(parents=True, exist_ok=False)

            files: list[str] = []
            total_size: int = 0
            skipped_oversized: list[str] = []
            skipped_budget: int = 0

            for source in self._checkpoint_files():
                file_size = source.stat().st_size
                if file_size > self._MAX_CHECKPOINT_FILE_SIZE:
                    skipped_oversized.append(source.relative_to(self.repo_path).as_posix())
                    continue
                if total_size + file_size > self._MAX_CHECKPOINT_TOTAL_SIZE:
                    skipped_budget += 1
                    continue

                relative = source.relative_to(self.repo_path)
                target = snapshot_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                files.append(relative.as_posix())
                total_size += file_size

            if skipped_oversized:
                logger.debug(
                    "Checkpoint %s: skipped %d file(s) over 20 MB: %s",
                    checkpoint_id,
                    len(skipped_oversized),
                    ", ".join(skipped_oversized[:5]),
                )
            if skipped_budget:
                logger.debug(
                    "Checkpoint %s: skipped %d file(s) exceeding 100 MB budget",
                    checkpoint_id,
                    skipped_budget,
                )

            message = f"checkpoint: {label}" if label else "checkpoint: before agent turn"
            metadata = {
                "sha": checkpoint_id,
                "message": message,
                "author": "Zircon",
                "timestamp": timestamp,
                "total_size_bytes": total_size,
                "files": files[:8],
                "all_files": files,
            }
            (root / checkpoint_id / "checkpoint.json").write_text(
                json.dumps(metadata, indent=2), encoding="utf-8"
            )
            return metadata
        except Exception as e:
            logger.warning("Failed to create checkpoint: %s", e)
            return None

    def list_checkpoints(self, n: int = 20) -> list[dict[str, Any]]:
        """Return Zircon-owned snapshots usable as revert targets."""
        try:
            root = zircon_path(self.repo_path, "checkpoints")
            if not root.is_dir():
                return []
            checkpoints: list[dict[str, Any]] = []
            for metadata_path in root.glob("*/checkpoint.json"):
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    if isinstance(metadata, dict) and metadata.get("sha"):
                        checkpoints.append(metadata)
                except (OSError, json.JSONDecodeError):
                    continue
            checkpoints.sort(key=lambda checkpoint: float(checkpoint.get("timestamp", 0)), reverse=True)
            return checkpoints[:n]
        except Exception:
            return []

    def revert_to_checkpoint(self, sha: str) -> bool:
        """Restore the workspace from a Zircon-owned checkpoint snapshot."""
        try:
            checkpoint = next(
                (item for item in self.list_checkpoints(n=10_000) if item.get("sha") == sha),
                None,
            )
            if checkpoint is None:
                return False

            snapshot_dir = zircon_path(self.repo_path, "checkpoints", sha, "files")
            if not snapshot_dir.is_dir():
                return False

            saved_files = set(checkpoint.get("all_files", checkpoint.get("files", [])))
            for source in snapshot_dir.rglob("*"):
                if not source.is_file():
                    continue
                relative = source.relative_to(snapshot_dir)
                target = self.repo_path / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

            # Remove files created after the checkpoint, but leave Zircon's
            # runtime directory and user dotfiles alone.
            for source in self._checkpoint_files():
                relative = source.relative_to(self.repo_path).as_posix()
                if relative not in saved_files:
                    source.unlink()

            logger.info("Reverted to Zircon checkpoint %s", sha)
            return True
        except Exception as e:
            logger.warning("Failed to revert to checkpoint %s: %s", sha, e)
            return False

    def _checkpoint_files(self) -> list[Path]:
        """Return project files that belong in a checkpoint snapshot."""
        excluded_dirs = {".git", ".zircon-code", "__pycache__"}
        files: list[Path] = []
        for path in self.repo_path.rglob("*"):
            if not path.is_file() or any(part in excluded_dirs for part in path.relative_to(self.repo_path).parts):
                continue
            files.append(path)
        return files
