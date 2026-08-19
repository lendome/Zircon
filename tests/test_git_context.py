import pytest
from unittest.mock import MagicMock, patch

from zirconAgent.core.git_context import GitConventionAnalyzer


class FakeCommit:
    def __init__(self, id, message, author=b"dev", author_time=0, parents=[]):
        self.id = id
        self.message = message
        self.author = author
        self.author_time = author_time
        self.parents = parents


class FakeEntry:
    def __init__(self, commit):
        self.commit = commit


class TestGitConventionAnalyzer:
    @pytest.fixture
    def analyzer(self, tmp_path):
        return GitConventionAnalyzer(str(tmp_path))

    def test_is_available_false_when_no_git(self, tmp_path):
        a = GitConventionAnalyzer(str(tmp_path))
        assert not a.is_available()

    def test_analyze_empty_repo(self, tmp_path):
        a = GitConventionAnalyzer(str(tmp_path))
        with patch.object(a.git, "is_git_repo", return_value=True):
            with patch.object(a.git, "get_recent_commits", return_value=[]):
                profile = a.analyze()
        assert profile["commit_style"] == {}
        assert profile["recent_fixes"] == []

    def test_infer_commit_style_prefix(self):
        commits = [
            {"message": "feat(auth): add login endpoint"},
            {"message": "fix(tests): correct flaky assertion"},
            {"message": "refactor(db): extract connection pool"},
        ]
        style = GitConventionAnalyzer._infer_commit_style(commits)
        assert style["uses_prefix"] is True
        assert style["example_prefix"] == "feat(auth)"

    def test_infer_commit_style_no_prefix(self):
        commits = [
            {"message": "Add login endpoint"},
            {"message": "Fix flaky assertion"},
        ]
        style = GitConventionAnalyzer._infer_commit_style(commits)
        assert style["uses_prefix"] is False

    def test_infer_commit_style_imperative(self):
        commits = [
            {"message": "Add login"},
            {"message": "Fix bug"},
            {"message": "Update docs"},
        ]
        style = GitConventionAnalyzer._infer_commit_style(commits)
        assert style["uses_imperative"] is True

    def test_infer_commit_style_not_imperative(self):
        commits = [
            {"message": "Added login"},
            {"message": "Fixing bug"},
            {"message": "Merged PR"},
        ]
        style = GitConventionAnalyzer._infer_commit_style(commits)
        assert style["uses_imperative"] is False

    def test_find_recent_fixes(self):
        commits = [
            {"message": "feat: new feature"},
            {"message": "fix: correct null pointer"},
            {"message": "docs: update readme"},
            {"message": "bugfix: handle edge case"},
        ]
        fixes = GitConventionAnalyzer._find_recent_fixes(commits)
        assert len(fixes) == 2
        assert "fix" in fixes[0]["message"].lower()

    def test_format_context_empty(self):
        a = GitConventionAnalyzer("/tmp/fake")
        with patch.object(a, "analyze", return_value={}):
            text = a.format_context("do something")
        assert text == ""

    def test_format_context_with_data(self):
        a = GitConventionAnalyzer("/tmp/fake")
        profile = {
            "commit_style": {
                "uses_prefix": True,
                "example_prefix": "feat(api)",
                "uses_imperative": True,
                "avg_len": 42,
                "examples": ["feat(api): add endpoint"],
            },
            "recent_fixes": [
                {"sha": "abc123", "message": "fix: correct typo"},
            ],
            "blame_snippets": [
                {"file": "app.py", "lines": [{"author": "alice"}]},
            ],
        }
        with patch.object(a, "analyze", return_value=profile):
            text = a.format_context("fix typo")
        assert "<repo_conventions>" in text
        assert "feat(api)" in text
        assert "alice" in text
        assert "</repo_conventions>" in text

    def test_search_similar_fixes(self, tmp_path):
        a = GitConventionAnalyzer(str(tmp_path))
        with patch.object(a.git, "search_commit_messages", return_value=[{"sha": "a1b2", "message": "fix crash"}]) as mock_search:
            result = a.search_similar_fixes("crash")
        mock_search.assert_called_once_with("crash", n=5)
        assert result[0]["message"] == "fix crash"
