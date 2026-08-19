from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dulwich import porcelain
from dulwich.repo import Repo
from dulwich.index import build_index_from_tree


_AUTHOR = b"Agent <agent@agent.dev>"

# Directories that bloat staging (dependency caches, build output, runtime
# data). Pruned from os.walk so _stage_all doesn't walk/stage tens of
# thousands of files on every checkpoint — which froze the TUI on repos
# with a venv/ or node_modules/.
_PRUNE_DIRS = {
    "node_modules", "__pycache__", "venv", ".venv", "env",
    "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "site-packages", "target",
}


class GitManager:
    def __init__(self, repo_path: str | Path):
        self.repo_path = Path(repo_path).resolve()
        self._session_branch: str | None = None
        self._original_branch: str | None = None

    def _open(self) -> Repo:
        return Repo(str(self.repo_path))

    def _get_head_ref(self, repo: Repo) -> bytes:
        head_file = self.repo_path / ".git" / "HEAD"
        if head_file.exists():
            content = head_file.read_text().strip()
            if content.startswith("ref: "):
                return content[5:].strip().encode()
        return b"refs/heads/main"

    def _get_head_commit(self, repo: Repo) -> bytes:
        ref = self._get_head_ref(repo)
        return repo.refs[ref]

    def _set_head_commit(self, repo: Repo, commit_id: bytes):
        ref = self._get_head_ref(repo)
        repo.refs[ref] = commit_id

    def is_git_repo(self) -> bool:
        try:
            Repo(str(self.repo_path))
            return True
        except Exception:
            return False

    def get_current_branch(self) -> str:
        try:
            repo = self._open()
            ref = self._get_head_ref(repo)
            if ref.startswith(b"refs/heads/"):
                return ref[len(b"refs/heads/"):].decode()
            return ref.decode()
        except Exception:
            return "main"

    def _ensure_init(self):
        if not (self.repo_path / ".git").exists():
            porcelain.init(str(self.repo_path))

    def _ensure_initial_commit(self):
        try:
            repo = self._open()
            self._get_head_commit(repo)
        except Exception:
            self._stage_all()
            try:
                porcelain.commit(
                    str(self.repo_path),
                    message=b"initial commit",
                    author=_AUTHOR,
                    committer=_AUTHOR,
                )
            except Exception:
                pass

    def create_session_branch(self, session_id: str) -> bool:
        try:
            self._ensure_init()
            self._ensure_initial_commit()

            branch_name = f"agent/{session_id}"
            self._session_branch = branch_name
            self._original_branch = self.get_current_branch()

            repo = self._open()
            head_commit = self._get_head_commit(repo)
            branch_ref = f"refs/heads/{branch_name}".encode()
            repo.refs[branch_ref] = head_commit
            repo.refs.set_symbolic_ref(b"HEAD", branch_ref)
            return True
        except Exception:
            return False

    def commit(self, message: str, paths: list[str] | None = None) -> bool:
        try:
            self._ensure_init()
            self._stage_all(paths)
            porcelain.commit(
                str(self.repo_path),
                message=message.encode("utf-8"),
                author=_AUTHOR,
                committer=_AUTHOR,
            )
            return True
        except Exception:
            return False

    def _stage_all(self, paths: list[str] | None = None):
        repo_path = str(self.repo_path)
        to_add = []
        if paths:
            for p in paths:
                full = self.repo_path / p
                if full.is_file():
                    rel = str(full.relative_to(self.repo_path)).replace("\\", "/")
                    to_add.append(rel.encode())
        else:
            for dirpath, dirnames, filenames in os.walk(repo_path):
                dirnames[:] = [
                    d for d in dirnames
                    if not d.startswith(".") and d not in _PRUNE_DIRS
                ]
                for fname in filenames:
                    fpath = Path(dirpath) / fname
                    if ".git" in str(fpath):
                        continue
                    try:
                        rel = str(fpath.relative_to(self.repo_path)).replace("\\", "/")
                        to_add.append(rel.encode())
                    except Exception:
                        continue
        if to_add:
            porcelain.add(repo_path, to_add)

    def rollback(self, ref: str = "HEAD~1") -> bool:
        try:
            repo = self._open()
            head_commit = self._get_head_commit(repo)
            obj = repo.object_store[head_commit]
            if obj.parents:
                parent = obj.parents[0]
                parent_obj = repo.object_store[parent]
                self._set_head_commit(repo, parent)
                build_index_from_tree(
                    repo.path, repo.index_path(),
                    repo.object_store, parent_obj.tree,
                )
                return True
            return False
        except Exception:
            return False

    def revert_to_commit(self, sha: str) -> bool:
        """Reset HEAD and working tree to an arbitrary commit SHA.

        Accepts a full or abbreviated SHA. Returns True on success.
        """
        try:
            repo = self._open()
            # Resolve abbreviated SHA to a full commit id by walking history
            commit_id: bytes | None = None
            for entry in repo.get_walker(include=[repo.head()]):
                cid = entry.commit.id
                if cid.decode().startswith(sha) or cid.decode() == sha:
                    commit_id = cid
                    break
            if commit_id is None:
                return False
            target_obj = repo.object_store[commit_id]
            self._set_head_commit(repo, commit_id)
            build_index_from_tree(
                repo.path, repo.index_path(),
                repo.object_store, target_obj.tree,
            )
            return True
        except Exception:
            return False

    def create_checkpoint(self, label: str = "") -> dict[str, Any] | None:
        """Stage all changes and create a checkpoint commit.

        Returns a dict with sha/message/timestamp, or None on failure.
        """
        try:
            self._ensure_init()
            self._ensure_initial_commit()
            msg = f"checkpoint: {label}" if label else "checkpoint: before agent turn"
            self._stage_all()
            # Always create a distinct, labeled checkpoint commit for this turn
            # (dulwich permits empty commits). This guarantees every agent turn
            # has its own revert point that shows up in the checkpoint picker,
            # even when the working tree is unchanged from HEAD.
            porcelain.commit(
                str(self.repo_path),
                message=msg.encode("utf-8"),
                author=_AUTHOR,
                committer=_AUTHOR,
            )
            commits = self.get_recent_commits(1)
            return commits[0] if commits else None
        except Exception:
            return None

    def list_checkpoints(self, n: int = 20) -> list[dict[str, Any]]:
        """Return recent commits usable as revert targets."""
        return self.get_recent_commits(n)

    def status(self) -> str:
        try:
            repo = self._open()
            index = repo.open_index()
            tracked = set(k.decode() for k in index)
            current = set()
            for dirpath, dirnames, filenames in os.walk(str(self.repo_path)):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for fname in filenames:
                    fpath = Path(dirpath) / fname
                    if ".git" in str(fpath):
                        continue
                    try:
                        rel = str(fpath.relative_to(self.repo_path)).replace("\\", "/")
                        current.add(rel)
                    except Exception:
                        continue
            new = current - tracked
            removed = tracked - current
            parts = []
            for f in sorted(new)[:20]:
                parts.append(f"  new: {f}")
            for f in sorted(removed)[:20]:
                parts.append(f"  removed: {f}")
            return "\n".join(parts) if parts else "clean"
        except Exception:
            return ""

    def diff(self, ref: str | None = None) -> str:
        """Return a unified diff of the working tree against *ref* (default HEAD).

        Uses the ``git`` CLI for a real, byte-accurate diff (dulwich's
        porcelain diff is awkward to format). Falls back to "" when git is
        unavailable or the repo has no commits. Output is bounded to keep it
        usable as prompt context.
        """
        import subprocess
        from ..core.proc_spawn import popen_kwargs
        try:
            args = ["git", "diff", "--no-color"]
            if ref:
                args.append(ref)
            result = subprocess.run(
                args, capture_output=True, text=True,
                cwd=str(self.repo_path), timeout=15,
                **popen_kwargs(),
            )
            if result.returncode == 0:
                return result.stdout[:20000]
            # No commits yet / bad ref: try an unstaged diff against the index.
            if not ref and "does not have" in (result.stderr + result.stdout):
                result2 = subprocess.run(
                    ["git", "diff", "--no-color", "--no-index", "/dev/null", "."],
                    capture_output=True, text=True,
                    cwd=str(self.repo_path), timeout=15,
                    **popen_kwargs(),
                )
                if result2.returncode in (0, 1):
                    return result2.stdout[:20000]
        except Exception:
            pass
        return ""

    def finalize(self, accept: bool = True) -> bool:
        if not self._session_branch or not self._original_branch:
            return False
        try:
            if accept:
                self._stage_all()
                try:
                    porcelain.commit(
                        str(self.repo_path),
                        message=b"agent: finalize session",
                        author=_AUTHOR,
                        committer=_AUTHOR,
                    )
                except Exception:
                    pass

                repo = self._open()
                session_ref = f"refs/heads/{self._session_branch}".encode()
                original_ref = f"refs/heads/{self._original_branch}".encode()
                if session_ref in repo.refs:
                    session_commit = repo.refs[session_ref]
                    repo.refs[original_ref] = session_commit
                    repo.refs.set_symbolic_ref(b"HEAD", original_ref)
                    del repo.refs[session_ref]
            else:
                repo = self._open()
                original_ref = f"refs/heads/{self._original_branch}".encode()
                repo.refs.set_symbolic_ref(b"HEAD", original_ref)
                session_ref = f"refs/heads/{self._session_branch}".encode()
                if session_ref in repo.refs:
                    del repo.refs[session_ref]

            self._session_branch = None
            self._original_branch = None
            return True
        except Exception:
            return False

    def get_diff_stats(self) -> dict[str, Any]:
        """Return per-file add/delete counts for working-tree changes vs HEAD.

        Includes tracked modifications/deletions (``git diff --numstat``) and
        untracked new files (``git ls-files --others``), so the caller sees the
        full set of real mutations. Uses the ``git`` CLI for byte accuracy.
        """
        import subprocess
        from ..core.proc_spawn import popen_kwargs
        cwd = str(self.repo_path)
        files: list[dict[str, Any]] = []
        seen: set[str] = set()
        try:
            r = subprocess.run(
                ["git", "diff", "--numstat", "HEAD"],
                capture_output=True, text=True, cwd=cwd, timeout=15,
                **popen_kwargs(),
            )
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    parts = line.split("\t")
                    if len(parts) == 3:
                        added, deleted, path = parts
                        path = path.replace("\\", "/")
                        files.append({
                            "path": path,
                            "added": 0 if added == "-" else int(added),
                            "deleted": 0 if deleted == "-" else int(deleted),
                            "status": "modified",
                        })
                        seen.add(path)
        except Exception:
            pass
        try:
            r = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard"],
                capture_output=True, text=True, cwd=cwd, timeout=15,
                **popen_kwargs(),
            )
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    path = line.strip().replace("\\", "/")
                    if path and path not in seen:
                        files.append({"path": path, "added": 0, "deleted": 0, "status": "untracked"})
                        seen.add(path)
        except Exception:
            pass
        return {"files_changed": len(files), "files": files}


    def get_recent_commits(self, n: int = 10) -> list[dict[str, Any]]:
        try:
            repo = self._open()
            # dulwich's Repo.get_walker yields WalkEntry objects whose .commit
            # attribute is the Commit. (porcelain.walk does not exist in
            # modern dulwich releases.)
            walker = repo.get_walker(include=[repo.head()])
            commits = []
            for entry in walker:
                if len(commits) >= n:
                    break
                commit = entry.commit
                files = []
                if commit.parents:
                    parent = repo.object_store[commit.parents[0]]
                    try:
                        changes = porcelain.get_tree_changes(repo, parent.tree, commit.tree)
                        files = list(
                            k.decode()
                            for k in changes["add"] + changes["modify"] + changes["delete"]
                        )
                    except Exception:
                        files = []
                commits.append({
                    "sha": commit.id.decode()[:12],
                    "message": commit.message.decode("utf-8", errors="replace").strip(),
                    "author": commit.author.decode("utf-8", errors="replace"),
                    "timestamp": commit.author_time,
                    "files": files[:8],
                })
            return commits
        except Exception:
            return []

    def blame_file(self, path: str, max_lines: int = 30) -> list[dict[str, Any]]:
        try:
            repo = self._open()
            full_path = self.repo_path / path
            if not full_path.is_file():
                return []
            import subprocess
            from ..core.proc_spawn import popen_kwargs
            result = subprocess.run(
                ["git", "blame", "-L", f"1,{max_lines}", "--porcelain", path],
                capture_output=True,
                text=True,
                cwd=str(self.repo_path),
                **popen_kwargs(),
            )
            if result.returncode != 0:
                return []
            lines = result.stdout.splitlines()
            annotations = []
            current = {}
            for line in lines:
                if line.startswith("author "):
                    current["author"] = line[7:]
                elif line.startswith("author-time "):
                    current["time"] = int(line[12:])
                elif line.startswith("\t"):
                    current["line"] = line[1:]
                    annotations.append(current)
                    current = {}
            return annotations[:max_lines]
        except Exception:
            return []

    def search_commit_messages(self, query: str, n: int = 8) -> list[dict[str, Any]]:
        try:
            repo = self._open()
            matches = []
            q = query.lower()
            for entry in repo.get_walker(include=[repo.head()]):
                if len(matches) >= n:
                    break
                commit = entry.commit
                msg = commit.message.decode("utf-8", errors="replace").lower()
                if any(term in msg for term in q.split()):
                    matches.append({
                        "sha": commit.id.decode()[:12],
                        "message": commit.message.decode("utf-8", errors="replace").strip(),
                        "author": commit.author.decode("utf-8", errors="replace"),
                    })
            return matches
        except Exception:
            return []

    def get_commit_diff(self, sha: str) -> str:
        try:
            import subprocess
            from ..core.proc_spawn import popen_kwargs
            result = subprocess.run(
                ["git", "show", "--stat", "--format=", sha],
                capture_output=True,
                text=True,
                cwd=str(self.repo_path),
                **popen_kwargs(),
            )
            if result.returncode == 0:
                return result.stdout[:4000]
        except Exception:
            pass
        return ""
