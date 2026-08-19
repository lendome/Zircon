from pathlib import Path

from zirconAgent.core.git_integration import GitIntegration


def test_checkpoint_is_stored_outside_project_git(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("value = 1\n", encoding="utf-8")
    git = GitIntegration(tmp_path)

    checkpoint = git.create_checkpoint("before edit")

    assert checkpoint is not None
    assert not (tmp_path / ".git").exists()
    assert (tmp_path / ".zircon-code" / "checkpoints" / checkpoint["sha"] / "files" / "main.py").is_file()


def test_checkpoint_revert_restores_files_and_removes_new_files(tmp_path: Path) -> None:
    source = tmp_path / "main.py"
    source.write_text("value = 1\n", encoding="utf-8")
    git = GitIntegration(tmp_path)
    checkpoint = git.create_checkpoint("safe state")

    source.write_text("value = 2\n", encoding="utf-8")
    (tmp_path / "new.py").write_text("created later\n", encoding="utf-8")

    assert checkpoint is not None
    assert git.revert_to_checkpoint(checkpoint["sha"])
    assert source.read_text(encoding="utf-8") == "value = 1\n"
    assert not (tmp_path / "new.py").exists()
