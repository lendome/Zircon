import pytest
from pathlib import Path

from zirconAgent.vcs.git import GitManager


@pytest.fixture
def git_repo(tmp_path):
    (tmp_path / "README.md").write_text("# Test\n")
    (tmp_path / "main.py").write_text("print('hello')\n")
    gm = GitManager(str(tmp_path))
    gm.commit("initial commit")
    return tmp_path


@pytest.fixture
def manager(git_repo):
    return GitManager(str(git_repo))


class TestGitManager:
    def test_is_git_repo(self, manager):
        assert manager.is_git_repo()

    def test_get_current_branch(self, manager):
        branch = manager.get_current_branch()
        assert branch in ("main", "master")

    def test_create_session_branch(self, manager):
        ok = manager.create_session_branch("abc123")
        assert ok
        assert manager.get_current_branch() == "agent/abc123"

    def test_commit(self, manager, git_repo):
        manager.create_session_branch("test")
        (git_repo / "new_file.py").write_text("pass\n")
        ok = manager.commit("test commit")
        assert ok

    def test_rollback(self, manager, git_repo):
        manager.create_session_branch("test")
        (git_repo / "extra.py").write_text("extra\n")
        manager.commit("add extra")
        ok = manager.rollback()
        assert ok

    def test_status(self, manager, git_repo):
        manager.create_session_branch("test")
        (git_repo / "untracked.py").write_text("x\n")
        status = manager.status()
        assert "untracked.py" in status

    def test_finalize_accept(self, manager, git_repo):
        original = manager.get_current_branch()
        manager.create_session_branch("test")
        (git_repo / "final.py").write_text("done\n")
        manager.commit("final change")
        ok = manager.finalize(accept=True)
        assert ok
        assert manager.get_current_branch() == original

    def test_finalize_reject(self, manager, git_repo):
        original = manager.get_current_branch()
        manager.create_session_branch("test")
        (git_repo / "reject.py").write_text("bad\n")
        manager.commit("bad change")
        ok = manager.finalize(accept=False)
        assert ok
        assert manager.get_current_branch() == original

    def test_finalize_no_session(self, manager):
        assert not manager.finalize()

    def test_not_git_repo(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        gm = GitManager(str(empty))
        gm.commit("init")
        assert gm.is_git_repo()
