"""Surveillance de l'Inbox (watch.py) : quand l'import est lancé, et arrêt."""
# pylint: disable=redefined-outer-name,unused-argument  # fixtures pytest
from datetime import date, datetime, timedelta
from datetime import time as dtime

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


# ---------- rangement de nuit (--range-at) ----------

@pytest.fixture
def nightly(tmp_path, inbox, calls, ticks, monkeypatch):
    """Horloge simulée, rangement remplacé par un enregistreur."""
    state = tmp_path / "state"
    state.mkdir()

    class N:
        clock = [datetime(2026, 10, 18, 1, 0)]
        ranges = Calls()

        def last_run(self, t):
            (state / music_ranger.LAST_RUN).write_text(f"{t.timestamp()}\n")

        def run(self, actions, *argv):
            return ticks(inbox, actions, "--range-at", "02:00", "--state-dir", str(state),
                         *argv)

        def at(self, *hm):
            return lambda: self.clock.__setitem__(0, datetime(2026, 10, 18, *hm))

    n = N()
    n.ranges.codes = []

    def fake_range(argv):
        n.ranges.append(argv)
        return n.ranges.codes.pop(0) if n.ranges.codes else 0

    monkeypatch.setattr(music_ranger, "main", fake_range)
    monkeypatch.setattr(watch, "now", lambda: n.clock[0])
    return n


def test_range_once_after_slot(nightly):
    nightly.last_run(datetime(2026, 10, 17, 2, 0))
    nightly.run([nothing, nightly.at(2, 0), nothing, nightly.at(3, 0), nothing], "--apply")
    # pas avant 02:00, puis une seule fois
    assert nightly.ranges == [["--since", "--state-dir", nightly.ranges[0][2], "--apply"]]


def test_range_catches_up_at_startup(nightly):
    nightly.clock[0] = datetime(2026, 10, 18, 10, 0)  # conteneur relancé dans la matinée
    nightly.last_run(datetime(2026, 10, 17, 2, 0))
    nightly.run([nothing])
    assert len(nightly.ranges) == 1


def test_range_skipped_after_manual_full_run(nightly):
    nightly.clock[0] = datetime(2026, 10, 18, 10, 0)
    nightly.last_run(datetime(2026, 10, 18, 9, 0))    # passage complet fait à la main ce matin
    nightly.run([nothing, nothing])
    assert nightly.ranges == []


def test_range_never_without_last_run(nightly, capsys):
    nightly.clock[0] = datetime(2026, 10, 18, 3, 0)
    nightly.run([nothing, nothing, nothing])
    assert nightly.ranges == []
    assert capsys.readouterr().out.count("rangement de nuit sauté") == 1


def test_range_retried_while_locked(nightly):
    nightly.clock[0] = datetime(2026, 10, 18, 3, 0)
    nightly.last_run(datetime(2026, 10, 17, 2, 0))
    nightly.ranges.codes += [EXIT_LOCKED]
    nightly.run([nothing, nothing, nothing])
    assert len(nightly.ranges) == 2


def test_range_dry_run_once_a_day(nightly):
    nightly.clock[0] = datetime(2026, 10, 18, 3, 0)
    nightly.last_run(datetime(2026, 10, 17, 2, 0))    # un dry-run ne fait pas avancer last_run
    nightly.run([nothing, nothing, nothing])
    assert nightly.ranges == [["--since", "--state-dir", nightly.ranges[0][2]]]


def test_range_crash_logged(nightly, monkeypatch, capsys):
    nightly.clock[0] = datetime(2026, 10, 18, 3, 0)
    nightly.last_run(datetime(2026, 10, 17, 2, 0))

    def boom(argv):
        raise RuntimeError("oups")

    monkeypatch.setattr(music_ranger, "main", boom)
    assert nightly.run([nothing, nothing]) == 0
    assert "ERREUR   rangement interrompu" in capsys.readouterr().out


def test_range_at_format():
    with pytest.raises(SystemExit):
        watch.main(["--inbox", "inbox", "--range-at", "2h"])


def test_real_nightly_range(tmp_path, ticks, make_track, monkeypatch):
    lib, inbox, state = tmp_path / "Musique", tmp_path / "Inbox", tmp_path / "state"
    inbox.mkdir()
    make_track(lib / "C/D/01 Rien.m4a", title="Rien", artist="C", album="D", track=1)
    assert music_ranger.main(["--root", str(lib), "--state-dir", str(state), "--apply"]) == 0
    before = float((state / music_ranger.LAST_RUN).read_text())
    # tag édité depuis le Mac dans la journée
    make_track(lib / "vrac/1.m4a", title="Un", artist="A", album="B", track=1)
    # le lendemain à 03:00 (indépendant de l'heure à laquelle tourne le test)
    next_day = datetime.fromtimestamp(before).date() + timedelta(days=1)
    monkeypatch.setattr(watch, "now", lambda: datetime.combine(next_day, dtime(3, 0)))
    monkeypatch.setattr(import_inbox, "main", lambda argv: 0)
    ticks(inbox, [nothing], "--root", str(lib), "--state-dir", str(state), "--apply",
          "--range-at", "02:00")
    assert (lib / "A/B/01 Un.m4a").exists() and not (lib / "vrac").exists()
    assert float((state / music_ranger.LAST_RUN).read_text()) > before
