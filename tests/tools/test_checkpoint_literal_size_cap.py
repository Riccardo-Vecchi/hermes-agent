"""Size-cap filtering must not treat literal filenames as Git globs (#135605)."""

import pytest

from tools import checkpoint_manager as checkpoints


@pytest.fixture
def capped_project(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoints, "CHECKPOINT_BASE", tmp_path / "checkpoints")
    work = tmp_path / "project"
    work.mkdir()
    (work / "main.py").write_text("print('keep')\n", encoding="utf-8")
    (work / "a1.bin").write_text("original small file\n", encoding="utf-8")
    (work / "a[12].bin").write_bytes(b"x" * (1024 * 1024 + 1))
    manager = checkpoints.CheckpointManager(enabled=True, max_file_size_mb=1)
    assert manager.ensure_checkpoint(str(work), "before agent write")
    target = manager.list_checkpoints(str(work))[0]["hash"]
    return work, manager, target


def test_post_stage_size_cap_excludes_only_the_literal_oversize_path(capped_project):
    work, manager, _ = capped_project
    store = checkpoints._store_path()
    index = checkpoints._index_path(store, checkpoints._project_hash(str(work)))
    oversized = work / "a[12].bin"
    oversized.write_text("small when staged\n", encoding="utf-8")
    ok, _, error = checkpoints._run_git(["add", "-A"], store, str(work), index_file=index)
    assert ok, error
    oversized.write_bytes(b"x" * (1024 * 1024 + 1))

    manager._drop_oversize_from_index(store, str(work), index)

    ok, output, error = checkpoints._run_git(
        ["ls-files", "--cached", "-z"], store, str(work), index_file=index,
    )
    assert ok, error
    assert set(filter(None, output.split("\0"))) == {"main.py", "a1.bin"}
    assert (work / "a[12].bin").stat().st_size == 1024 * 1024 + 1


def test_safe_restore_keeps_the_small_file_matching_an_oversize_glob(capped_project):
    work, manager, target = capped_project
    small = work / "a1.bin"
    small.write_text("agent edit\n", encoding="utf-8")
    manager.record_agent_write(str(small))
    (work / "main.py").write_text("user edit\n", encoding="utf-8")

    result = manager.restore(str(work), target, safe=True)

    assert result["success"], result
    assert small.is_file(), "safe restore deleted a pre-existing file omitted by glob matching"
    assert small.read_text(encoding="utf-8") == "original small file\n"
    assert result["restored_files"] == ["a1.bin"]
    assert "main.py" in result["skipped_user_edits"]
    assert (work / "main.py").read_text(encoding="utf-8") == "user edit\n"
    assert (work / "a[12].bin").stat().st_size == 1024 * 1024 + 1
