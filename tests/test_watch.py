"""Surveillance de l'Inbox (watch.py) : quand l'import est lancé, et arrêt."""
from datetime import date

import pytest

import import_inbox
import music_ranger
import watch
from music_ranger import EXIT_LOCKED, STOP


@pytest.fixture
def inbox(tmp_path):
    d = tmp_path / "Inbox"
    d.mkdir()
    return d


class Calls(list):
    codes = None  # codes retour à renvoyer, dans l'ordre (0 ensuite)


@pytest.fixture
def calls(monkeypatch):
    """Remplace l'import : enregistre les appels."""
    out = Calls()
    out.codes = []

    def fake(argv):
        out.append(argv)
        return out.codes.pop(0) if out.codes else 0

    monkeypatch.setattr(import_inbox, "main", fake)
    return out


@pytest.fixture
def ticks(monkeypatch):
    """Fait tourner la boucle : chaque attente exécute l'action suivante, puis arrête."""
    def run(inbox, actions, *argv):
        todo = list(actions)

        def wait(timeout=None):
            if todo:
                todo.pop(0)()
            else:
                STOP.set()
            return STOP.is_set()

        monkeypatch.setattr(STOP, "wait", wait)
        return watch.main(["--inbox", str(inbox), "--interval", "0", *argv])
    return run


def nothing():
    pass


def test_empty_inbox_runs_once_per_day(inbox, calls, ticks, monkeypatch):
    days = iter([date(2026, 10, 17)] * 3 + [date(2026, 10, 18)] * 10)

    class FakeDate(date):
        @classmethod
        def today(cls):
            return next(days)

    monkeypatch.setattr(watch, "date", FakeDate)
    assert ticks(inbox, [nothing, nothing, nothing]) == 0
    # au démarrage (purge), puis au changement de jour
    assert len(calls) == 2


def test_waits_until_content_is_stable(inbox, calls, ticks):
    album = inbox / "Album"

    def start_copy():
        album.mkdir()
        (album / "1.flac").write_bytes(b"x")

    def copy_more():
        (album / "2.flac").write_bytes(b"y")

    ticks(inbox, [start_copy, copy_more, nothing, nothing, nothing])
    # 1 au démarrage, 1 une fois la copie stable, pas de relance ensuite
    assert len(calls) == 2


def test_item_left_in_place_not_retried_until_changed(inbox, calls, ticks):
    item = inbox / "1.flac"

    def deposit():
        item.write_bytes(b"x")

    def change():
        item.write_bytes(b"xy")

    ticks(inbox, [deposit, nothing, nothing, nothing, change, nothing, nothing])
    assert len(calls) == 3


def test_retried_while_locked(inbox, calls, ticks):
    (inbox / "1.flac").write_bytes(b"x")
    calls.codes += [EXIT_LOCKED, EXIT_LOCKED, 0]
    ticks(inbox, [nothing, nothing, nothing, nothing])
    assert len(calls) == 3


def test_import_crash_logged_and_loop_goes_on(inbox, ticks, monkeypatch, capsys):
    n = []

    def boom(argv):
        n.append(argv)
        raise RuntimeError("oups")

    monkeypatch.setattr(import_inbox, "main", boom)
    (inbox / "1.flac").write_bytes(b"x")
    assert ticks(inbox, [nothing, nothing]) == 0
    assert len(n) == 1
    out = capsys.readouterr().out
    assert "ERREUR" in out and "RuntimeError: oups" in out


def test_options_passed_to_import(inbox, calls, ticks):
    ticks(inbox, [], "--settle", "5", "--apply")
    assert calls == [["--inbox", str(inbox), "--settle", "5", "--apply"]]
    calls.clear()
    STOP.clear()
    ticks(inbox, [])
    assert "--apply" not in calls[0]


def test_missing_inbox(tmp_path, calls, ticks):
    assert ticks(tmp_path / "absente", [nothing]) == 0
    assert len(calls) == 1


def test_stop_during_import_ends_loop(inbox, ticks, monkeypatch):
    def stopping(argv):
        STOP.set()
        return music_ranger.EXIT_STOPPED

    monkeypatch.setattr(import_inbox, "main", stopping)
    waits = []
    monkeypatch.setattr(STOP, "wait", lambda timeout=None: waits.append(1) or True)
    assert watch.main(["--inbox", str(inbox), "--interval", "0"]) == 0
    assert len(waits) == 1


def test_real_import(tmp_path, ticks, make_track, monkeypatch):
    lib, inbox, state = tmp_path / "Musique", tmp_path / "Inbox", tmp_path / "state"
    lib.mkdir()
    inbox.mkdir()
    monkeypatch.setattr(import_inbox, "enrichers", lambda args: [])

    def deposit():
        make_track(inbox / "Album/1.flac", title="Un", artist="A", album="B", track=1)

    ticks(inbox, [deposit, nothing, nothing], "--root", str(lib), "--state-dir", str(state),
          "--settle", "0", "--apply")
    assert [str(p.relative_to(lib)) for p in lib.rglob("*.m4a")] == ["A/B/01 Un.m4a"]
    assert not (inbox / "Album").exists()
    assert list((inbox / import_inbox.DONE).glob("*/Album/1.flac"))
