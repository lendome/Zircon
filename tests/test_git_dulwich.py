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


class TestDulwichGitManager:
    def test_is_git_repo(self, manager):
        assert manager.is_git_repo()

    def test_create_session_branch(self, manager):
        ok = manager.create_session_branch("abc123")
        assert ok

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

    def test_finalize_accept(self, manager, git_repo):
        original = manager.get_current_branch()
        manager.create_session_branch("test")
        (git_repo / "final.py").write_text("done\n")
        manager.commit("final change")
        ok = manager.finalize(accept=True)
        assert ok

    def test_finalize_reject(self, manager, git_repo):
        manager.create_session_branch("test")
        (git_repo / "reject.py").write_text("bad\n")
        manager.commit("bad change")
        ok = manager.finalize(accept=False)
        assert ok

    def test_finalize_no_session(self, manager):
        assert not manager.finalize()

    def test_status(self, manager, git_repo):
        manager.create_session_branch("test")
        (git_repo / "untracked.py").write_text("x\n")
        status = manager.status()
        assert isinstance(status, str)

    def test_not_git_repo(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        gm = GitManager(str(empty))
        gm.commit("init on empty")
        assert gm.is_git_repo()


class TestCheckpoints:
    """Regression tests for the git-checkpoint (reversibility) flow.

    These previously failed silently because get_recent_commits / list_checkpoints
    used porcelain.walk, which does not exist in modern dulwich (AttributeError
    was swallowed and [] / None returned), so the TUI reported "No checkpoints
    available" even though a checkpoint commit existed.
    """

    def test_create_checkpoint_returns_commit(self, tmp_path):
        (tmp_path / "game.html").write_text("v1\n")
        gm = GitManager(str(tmp_path))
        cp = gm.create_checkpoint("before agent turn")
        assert cp is not None
        assert cp["sha"]
        assert "checkpoint" in cp["message"] or cp["message"]

    def test_list_checkpoints_after_create(self, tmp_path):
        (tmp_path / "game.html").write_text("v1\n")
        gm = GitManager(str(tmp_path))
        gm.create_checkpoint("turn 1")
        cps = gm.list_checkpoints(20)
        assert len(cps) >= 1
        assert all(isinstance(c["sha"], str) and c["sha"] for c in cps)

    def test_checkpoint_progresses_with_edits(self, tmp_path):
        f = tmp_path / "game.html"
        f.write_text("v1\n")
        gm = GitManager(str(tmp_path))
        gm.create_checkpoint("turn 1")
        # simulate agent work
        f.write_text("v2 improved\n")
        cp2 = gm.create_checkpoint("turn 2")
        assert cp2 is not None
        cps = gm.list_checkpoints(20)
        # turn 2 checkpoint + initial/checkpoint commits present
        assert len(cps) >= 2
        assert any("turn 2" in c["message"] for c in cps)

    def test_revert_to_checkpoint_restores_content(self, tmp_path):
        f = tmp_path / "game.html"
        f.write_text("original\n")
        gm = GitManager(str(tmp_path))
        gm.create_checkpoint("safe state")
        f.write_text("agent ruined it\n")
        gm.create_checkpoint("post agent")
        cps = gm.list_checkpoints(20)
        safe = next(c for c in cps if "safe state" in c["message"])
        assert gm.revert_to_commit(safe["sha"])
        assert f.read_text() == "original\n"

    def test_get_recent_commits_returns_messages_and_shas(self, tmp_path):
        (tmp_path / "a.py").write_text("1\n")
        gm = GitManager(str(tmp_path))
        gm.create_checkpoint("alpha")
        (tmp_path / "a.py").write_text("2\n")
        gm.create_checkpoint("beta")
        commits = gm.get_recent_commits(10)
        assert len(commits) >= 2
        msgs = " ".join(c["message"] for c in commits)
        assert "alpha" in msgs and "beta" in msgs
        # WalkEntry.commit yields a real SHA string and a timestamp
        assert isinstance(commits[0]["timestamp"], int)

    def test_search_commit_messages(self, tmp_path):
        (tmp_path / "a.py").write_text("1\n")
        gm = GitManager(str(tmp_path))
        gm.create_checkpoint("fix login bug")
        gm.create_checkpoint("refactor api")
        hits = gm.search_commit_messages("login", 10)
        assert hits and any("login" in h["message"] for h in hits)
        assert not any("refactor" in h["message"] for h in hits)
