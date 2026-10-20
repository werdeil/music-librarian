"""Lock between passes, clean stop (SIGTERM / Ctrl-C) and single entry point."""
# pylint: disable=redefined-outer-name,unused-argument  # pytest fixtures
import fcntl
import json

import pytest

from music_librarian import cli, importer, organize
from music_librarian.core.lock import EXIT_LOCKED, EXIT_STOPPED, LOCK, STOP
from music_librarian.organize import organize as organize_module, undo as undo_module


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    lib, inbox_dir, state = tmp_path / "Musique", tmp_path / "Inbox", tmp_path / "state"
    lib.mkdir()
    inbox_dir.mkdir()
    monkeypatch.setattr(importer, "enrichers", lambda: [])
    return lib, inbox_dir, state


def run_organize(lib, state, *a):
    return organize.main(["--root", str(lib), "--state-dir", str(state), *a])


def import_inbox(lib, inbox_dir, state, *a):
    return importer.main(["--inbox", str(inbox_dir), "--root", str(lib),
                              "--state-dir", str(state), "--settle", "0", *a])


@pytest.fixture
def held_lock(dirs):
    """The lock held by "another pass" (another descriptor, like another process)."""
    state = dirs[2]
    state.mkdir()
    with open(state / LOCK, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def files(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


# ---------- lock ----------

def test_apply_refused_while_locked(dirs, held_lock, make_track, capsys):
    lib, _, state = dirs
    make_track(lib / "vrac/1.m4a", title="Un", artist="A", album="B", track=1)
    assert run_organize(lib, state, "--apply") == EXIT_LOCKED
    assert files(lib) == ["vrac/1.m4a"]
    assert "LOCKED" in capsys.readouterr().out
    # a dry run does not take the lock
    assert run_organize(lib, state) == 0


def test_import_refused_while_locked(dirs, held_lock, make_track):
    lib, inbox_dir, state = dirs
    make_track(inbox_dir / "Album/1.m4a", title="Un", artist="A", album="B", track=1)
    assert import_inbox(lib, inbox_dir, state, "--apply") == EXIT_LOCKED
    assert files(lib) == []
    assert import_inbox(lib, inbox_dir, state) == 0


def test_undo_refused_while_locked(dirs, make_track):
    lib, _, state = dirs
    make_track(lib / "vrac/1.m4a", title="Un", artist="A", album="B", track=1)
    assert run_organize(lib, state, "--apply") == 0
    [j] = (state / "journal").glob("*.jsonl")
    with open(state / LOCK, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert run_organize(lib, state, "--undo", str(j), "--apply") == EXIT_LOCKED
    assert files(lib) == ["A/B/01 Un.m4a"]


def test_lock_released_after_run(dirs, make_track):
    lib, inbox_dir, state = dirs
    make_track(inbox_dir / "Album/1.m4a", title="Un", artist="A", album="B", track=1)
    assert import_inbox(lib, inbox_dir, state, "--apply") == 0   # import calls organize
    with open(state / LOCK, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)      # does not raise: lock released


# ---------- stop ----------

def stop_after(n, real):
    calls = []

    def wrapper(*a, **kw):
        out = real(*a, **kw)
        calls.append(1)
        if len(calls) >= n:
            STOP.set()
        return out
    return wrapper


def test_organize_stops_after_current_move(dirs, make_track, monkeypatch):
    lib, _, state = dirs
    for i in range(1, 4):
        make_track(lib / f"vrac/{i}.m4a", title=f"T{i}", artist="A", album="B", track=i)
    monkeypatch.setattr(organize_module, "move_file", stop_after(1, organize_module.move_file))
    assert run_organize(lib, state, "--apply") == EXIT_STOPPED
    assert files(lib) == ["A/B/01 T1.m4a", "vrac/2.m4a", "vrac/3.m4a"]
    [j] = (state / "journal").glob("*.jsonl")
    assert sum(json.loads(line)["type"] == "move" for line in j.read_text().splitlines()) == 1
    assert not (state / "last_run").exists()     # incomplete pass: --since must not move forward


def test_undo_stops_after_current_move(dirs, make_track, monkeypatch):
    lib, _, state = dirs
    for i in range(1, 3):
        make_track(lib / f"vrac/{i}.m4a", title=f"T{i}", artist="A", album="B", track=i)
    assert run_organize(lib, state, "--apply") == 0
    [j] = (state / "journal").glob("*.jsonl")
    monkeypatch.setattr(undo_module, "move_file", stop_after(1, undo_module.move_file))
    assert run_organize(lib, state, "--undo", str(j), "--apply") == EXIT_STOPPED
    assert len(files(lib)) == 2 and any(f.startswith("vrac/") for f in files(lib))


def test_import_stop_during_conversion_leaves_item(dirs, make_track, monkeypatch):
    lib, inbox_dir, state = dirs
    for i in range(1, 3):
        make_track(inbox_dir / f"Album/{i}.flac", title=f"T{i}", artist="A", album="B", track=i)
    monkeypatch.setattr(importer, "convert", stop_after(1, importer.convert))
    assert import_inbox(lib, inbox_dir, state, "--apply") == EXIT_STOPPED
    assert files(lib) == []
    assert files(inbox_dir) == ["Album/1.flac", "Album/2.flac"]   # neither _failed nor _processing


def test_import_finishes_current_item_then_stops(dirs, make_track, monkeypatch):
    lib, inbox_dir, state = dirs
    for name in ("A1", "A2"):
        for i in range(1, 3):
            make_track(inbox_dir / f"{name}/{i}.m4a", title=f"T{i}", artist="X", album=name, track=i)
    # the stop lands while the first item is being filed: it is completed anyway
    monkeypatch.setattr(organize_module, "move_file", stop_after(1, organize_module.move_file))
    assert import_inbox(lib, inbox_dir, state, "--apply") == EXIT_STOPPED
    assert files(lib) == ["X/A1/01 T1.m4a", "X/A1/02 T2.m4a"]
    assert (inbox_dir / "A2/1.m4a").exists()


# ---------- entry point ----------

def test_cli_dispatch(dirs, make_track, monkeypatch):
    lib, inbox_dir, state = dirs
    monkeypatch.setenv("MUSIC_LIBRARIAN_ROOT", str(lib))
    monkeypatch.setenv("MUSIC_LIBRARIAN_STATE", str(state))
    make_track(lib / "vrac/1.m4a", title="Un", artist="A", album="B", track=1)

    assert cli.main(["organize"]) == 0                  # --root and state from the environment
    assert cli.main(["organize", "--apply"]) == 0
    assert files(lib) == ["A/B/01 Un.m4a"]
    [j] = (state / "journal").glob("*.jsonl")
    assert cli.main(["undo", str(j), "--apply"]) == 0
    assert files(lib) == ["vrac/1.m4a"]
    assert cli.main(["import", "--inbox", str(inbox_dir), "--settle", "0"]) == 0


@pytest.mark.parametrize("argv, code", [([], 2), (["--help"], 0), (["oups"], 2), (["range"], 2),
                                        (["undo"], 2)])
def test_cli_usage(argv, code):
    assert cli.main(argv) == code
