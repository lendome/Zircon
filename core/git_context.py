from __future__ import annotations

import re
from typing import Any

from ..vcs.git import GitManager


class GitConventionAnalyzer:
    def __init__(self, repo_path: str):
        self.git = GitManager(repo_path)
        self._cache: dict[str, Any] = {}

    def is_available(self) -> bool:
        return self.git.is_git_repo()

    def analyze(self) -> dict[str, Any]:
        if "profile" in self._cache:
            return self._cache["profile"]

        if not self.is_available():
            self._cache["profile"] = {}
            return {}

        commits = self.git.get_recent_commits(n=10)
        profile = {
            "commit_style": self._infer_commit_style(commits[:10]),
            "recent_fixes": self._find_recent_fixes(commits[:10]),
            "blame_snippets": [],
        }

        files_to_blame = []
        for c in commits[:5]:
            for f in c.get("files", []):
                if f not in files_to_blame and f.endswith(".py"):
                    files_to_blame.append(f)
                if len(files_to_blame) >= 3:
                    break
            if len(files_to_blame) >= 3:
                break

        for f in files_to_blame:
            blame = self.git.blame_file(f, max_lines=20)
            if blame:
                profile["blame_snippets"].append({"file": f, "lines": blame[:5]})

        self._cache["profile"] = profile
        return profile

    def search_similar_fixes(self, query: str) -> list[dict[str, Any]]:
        return self.git.search_commit_messages(query, n=5)

    def format_context(self, task_description: str = "") -> str:
        profile = self.analyze()
        if not profile:
            return ""

        lines = ["<repo_conventions>"]

        cs = profile.get("commit_style", {})
        if cs:
            lines.append("Commit-message style (from recent history):")
            if cs.get("uses_prefix"):
                lines.append(f"  - Uses type prefix (e.g., '{cs['example_prefix']}').")
            if cs.get("uses_imperative"):
                lines.append("  - Prefers imperative mood ('Add', 'Fix', not 'Added', 'Fixed').")
            if cs.get("avg_len"):
                lines.append(f"  - Average message length: ~{cs['avg_len']} chars.")
            examples = cs.get("examples", [])
            if examples:
                lines.append("  - Examples:")
                for ex in examples[:3]:
                    lines.append(f"      {ex}")

        fixes = profile.get("recent_fixes", [])
        if fixes and task_description:
            lines.append("Recent similar fixes:")
            for f in fixes[:3]:
                lines.append(f"  - {f['sha']}: {f['message']}")

        blame = profile.get("blame_snippets", [])
        if blame:
            lines.append("File authorship / naming hints:")
            for snippet in blame[:2]:
                lines.append(f"  - {snippet['file']}: maintained by {snippet['lines'][0].get('author', 'unknown')}")

        lines.append("</repo_conventions>")
        return "\n".join(lines)

    @staticmethod
    def _infer_commit_style(commits: list[dict[str, Any]]) -> dict[str, Any]:
        if not commits:
            return {}

        prefixes = []
        imperative = 0
        lengths = []
        examples = []

        for c in commits:
            msg = c.get("message", "")
            first_line = msg.split("\n")[0]
            lengths.append(len(first_line))
            examples.append(first_line[:80])

            m = re.match(r"^([a-z]+\([^)]+\))!?:", first_line, re.IGNORECASE)
            if m:
                prefixes.append(m.group(1))

            words = first_line.split()
            if words:
                w = words[0].lower()
                if not (w.endswith("ed") or w.endswith("ing") or w.startswith("merged") or w.startswith("updated")):
                    imperative += 1

        result = {
            "examples": examples[:3],
            "avg_len": round(sum(lengths) / max(1, len(lengths))),
        }
        if prefixes:
            result["uses_prefix"] = True
            result["example_prefix"] = prefixes[0]
        else:
            result["uses_prefix"] = False

        result["uses_imperative"] = imperative >= len(commits) * 0.5
        return result

    @staticmethod
    def _find_recent_fixes(commits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        fix_keywords = {"fix", "bug", "repair", "correct", "resolve", "patch", "hotfix"}
        fixes = []
        for c in commits:
            msg = c.get("message", "").lower()
            if any(k in msg for k in fix_keywords):
                fixes.append(c)
        return fixes
