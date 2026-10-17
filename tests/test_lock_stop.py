"""Verrou entre passages, arrêt propre (SIGTERM / Ctrl-C) et point d'entrée unique."""
# pylint: disable=redefined-outer-name,unused-argument  # fixtures pytest
import fcntl
import importlib.machinery
import importlib.util
import json
from pathlib import Path

import pytest

import import_inbox
import music_ranger


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    lib, inbox, state = tmp_path / "Musique", tmp_path / "Inbox", tmp_path / "state"
    lib.mkdir()
    inbox.mkdir()
    monkeypatch.setattr(import_inbox, "enrichers", lambda args: [])
    return lib, inbox, state


def ranger(lib, state, *a):
    return music_ranger.main(["--root", str(lib), "--state-dir", str(state), *a])


def importer(lib, inbox, state, *a):
    return import_inbox.main(["--inbox", str(inbox), "--root", str(lib),
                              "--state-dir", str(state), "--settle", "0", *a])


@pytest.fixture
def held_lock(dirs):
    """Le verrou tenu par un « autre passage » (autre descripteur, comme un autre processus)."""
    state = dirs[2]
    state.mkdir()
    with open(state / music_ranger.LOCK, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def files(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


# ---------- verrou ----------

def test_apply_refused_while_locked(dirs, held_lock, make_track, capsys):
    lib, _, state = dirs
    make_track(lib / "vrac/1.m4a", title="Un", artist="A", album="B", track=1)
    assert ranger(lib, state, "--apply") == music_ranger.EXIT_LOCKED
    assert files(lib) == ["vrac/1.m4a"]
    assert "VERROU" in capsys.readouterr().out
    # un dry-run ne prend pas le verrou
    assert ranger(lib, state) == 0


def test_import_refused_while_locked(dirs, held_lock, make_track):
    lib, inbox, state = dirs
    make_track(inbox / "Album/1.m4a", title="Un", artist="A", album="B", track=1)
    assert importer(lib, inbox, state, "--apply") == music_ranger.EXIT_LOCKED
    assert files(lib) == []
    assert importer(lib, inbox, state) == 0


def test_undo_refused_while_locked(dirs, make_track):
    lib, _, state = dirs
    make_track(lib / "vrac/1.m4a", title="Un", artist="A", album="B", track=1)
    assert ranger(lib, state, "--apply") == 0
    [j] = (state / "journal").glob("*.jsonl")
    with open(state / music_ranger.LOCK, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert ranger(lib, state, "--undo", str(j), "--apply") == music_ranger.EXIT_LOCKED
    assert files(lib) == ["A/B/01 Un.m4a"]


def test_lock_released_after_run(dirs, make_track):
    lib, inbox, state = dirs
    make_track(inbox / "Album/1.m4a", title="Un", artist="A", album="B", track=1)
    assert importer(lib, inbox, state, "--apply") == 0   # l'import appelle le rangement
    with open(state / music_ranger.LOCK, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)      # ne lève pas : verrou libéré


# ---------- arrêt ----------

def stop_after(n, real):
    calls = []

    def wrapper(*a, **kw):
        out = real(*a, **kw)
        calls.append(1)
        if len(calls) >= n:
            music_ranger.STOP.set()
        return out
    return wrapper


def test_range_stops_after_current_move(dirs, make_track, monkeypatch):
    lib, _, state = dirs
    for i in range(1, 4):
        make_track(lib / f"vrac/{i}.m4a", title=f"T{i}", artist="A", album="B", track=i)
    monkeypatch.setattr(music_ranger, "move_file", stop_after(1, music_ranger.move_file))
    assert ranger(lib, state, "--apply") == music_ranger.EXIT_STOPPED
    assert files(lib) == ["A/B/01 T1.m4a", "vrac/2.m4a", "vrac/3.m4a"]
    [j] = (state / "journal").glob("*.jsonl")
    assert sum(json.loads(line)["type"] == "move" for line in j.read_text().splitlines()) == 1
    assert not (state / "last_run").exists()     # passage incomplet : --since ne doit pas avancer


def test_undo_stops_after_current_move(dirs, make_track, monkeypatch):
    lib, _, state = dirs
    for i in range(1, 3):
        make_track(lib / f"vrac/{i}.m4a", title=f"T{i}", artist="A", album="B", track=i)
    assert ranger(lib, state, "--apply") == 0
    [j] = (state / "journal").glob("*.jsonl")
    monkeypatch.setattr(music_ranger, "move_file", stop_after(1, music_ranger.move_file))
    assert ranger(lib, state, "--undo", str(j), "--apply") == music_ranger.EXIT_STOPPED
    assert len(files(lib)) == 2 and any(f.startswith("vrac/") for f in files(lib))


def test_import_stop_during_conversion_leaves_item(dirs, make_track, monkeypatch):
    lib, inbox, state = dirs
    for i in range(1, 3):
        make_track(inbox / f"Album/{i}.flac", title=f"T{i}", artist="A", album="B", track=i)
    monkeypatch.setattr(import_inbox, "convert", stop_after(1, import_inbox.convert))
    assert importer(lib, inbox, state, "--apply") == music_ranger.EXIT_STOPPED
    assert files(lib) == []
    assert files(inbox) == ["Album/1.flac", "Album/2.flac"]   # ni _erreurs, ni _en_cours


def test_import_finishes_current_item_then_stops(dirs, make_track, monkeypatch):
    lib, inbox, state = dirs
    for name in ("A1", "A2"):
        for i in range(1, 3):
            make_track(inbox / f"{name}/{i}.m4a", title=f"T{i}", artist="X", album=name, track=i)
    # l'arrêt tombe pendant le rangement du premier élément : il est terminé quand même
    monkeypatch.setattr(music_ranger, "move_file", stop_after(1, music_ranger.move_file))
    assert importer(lib, inbox, state, "--apply") == music_ranger.EXIT_STOPPED
    assert files(lib) == ["X/A1/01 T1.m4a", "X/A1/02 T2.m4a"]
    assert (inbox / "A2/1.m4a").exists()


# ---------- point d'entrée ----------

@pytest.fixture(scope="module")
def cli():
    path = str(Path(__file__).resolve().parent.parent / "music-ranger")
    loader = importlib.machinery.SourceFileLoader("music_ranger_cli", path)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(mod)
    return mod


def test_cli_dispatch(cli, dirs, make_track, monkeypatch):
    lib, inbox, state = dirs
    monkeypatch.setenv("MUSIC_RANGER_ROOT", str(lib))
    monkeypatch.setenv("MUSIC_RANGER_STATE", str(state))
    make_track(lib / "vrac/1.m4a", title="Un", artist="A", album="B", track=1)

    assert cli.main(["range"]) == 0                     # --root et state depuis l'environnement
    assert cli.main(["range", "--apply"]) == 0
    assert files(lib) == ["A/B/01 Un.m4a"]
    [j] = (state / "journal").glob("*.jsonl")
    assert cli.main(["undo", str(j), "--apply"]) == 0
    assert files(lib) == ["vrac/1.m4a"]
    assert cli.main(["import", "--inbox", str(inbox), "--settle", "0"]) == 0


@pytest.mark.parametrize("argv, code", [([], 2), (["--help"], 0), (["oups"], 2), (["undo"], 2)])
def test_cli_usage(cli, argv, code):
    assert cli.main(argv) == code
