"""Inbox watching (watch.py): when the import runs, and stopping."""
# pylint: disable=redefined-outer-name,unused-argument  # pytest fixtures
from datetime import date, datetime, timedelta
from datetime import time as dtime

import pytest

from music_librarian import importer, organize, watch
from music_librarian.core.lock import EXIT_LOCKED, EXIT_STOPPED, STOP


@pytest.fixture
def inbox_dir(tmp_path):
    d = tmp_path / "Inbox"
    d.mkdir()
    return d


class Calls(list):
    codes = None  # exit codes to return, in order (0 afterwards)


@pytest.fixture
def calls(monkeypatch):
    """Stands in for the import: records the calls."""
    out = Calls()
    out.codes = []

    def fake(argv):
        out.append(argv)
        return out.codes.pop(0) if out.codes else 0

    monkeypatch.setattr(importer, "main", fake)
    return out


def nothing():
    pass


def test_empty_inbox_runs_once_per_day(inbox_dir, calls, ticks, monkeypatch):
    days = iter([date(2026, 10, 17)] * 3 + [date(2026, 10, 18)] * 10)

    class FakeDate(date):
        @classmethod
        def today(cls):
            return next(days)

    monkeypatch.setattr(watch, "date", FakeDate)
    assert ticks(inbox_dir, [nothing, nothing, nothing]) == 0
    # at startup (purge), then when the day changes
    assert len(calls) == 2


def test_waits_until_content_is_stable(inbox_dir, calls, ticks):
    album = inbox_dir / "Album"

    def start_copy():
        album.mkdir()
        (album / "1.flac").write_bytes(b"x")

    def copy_more():
        (album / "2.flac").write_bytes(b"y")

    ticks(inbox_dir, [start_copy, copy_more, nothing, nothing, nothing])
    # 1 at startup, 1 once the copy is stable, no rerun afterwards
    assert len(calls) == 2


def test_item_left_in_place_not_retried_until_changed(inbox_dir, calls, ticks):
    item = inbox_dir / "1.flac"

    def deposit():
        item.write_bytes(b"x")

    def change():
        item.write_bytes(b"xy")

    ticks(inbox_dir, [deposit, nothing, nothing, nothing, change, nothing, nothing])
    assert len(calls) == 3


def test_retried_while_locked(inbox_dir, calls, ticks):
    (inbox_dir / "1.flac").write_bytes(b"x")
    calls.codes += [EXIT_LOCKED, EXIT_LOCKED, 0]
    ticks(inbox_dir, [nothing, nothing, nothing, nothing])
    assert len(calls) == 3


def test_import_crash_logged_and_loop_goes_on(inbox_dir, ticks, monkeypatch, capsys):
    n = []

    def boom(argv):
        n.append(argv)
        raise RuntimeError("oups")

    monkeypatch.setattr(importer, "main", boom)
    (inbox_dir / "1.flac").write_bytes(b"x")
    assert ticks(inbox_dir, [nothing, nothing]) == 0
    assert len(n) == 1
    out = capsys.readouterr().out
    assert "ERROR" in out and "RuntimeError: oups" in out


def test_options_passed_to_import(inbox_dir, calls, ticks):
    ticks(inbox_dir, [], "--settle", "5", "--apply")
    assert calls == [["--inbox", str(inbox_dir), "--settle", "5", "--apply"]]
    calls.clear()
    STOP.clear()
    ticks(inbox_dir, [])
    assert "--apply" not in calls[0]


def test_missing_inbox(tmp_path, calls, ticks):
    assert ticks(tmp_path / "absente", [nothing]) == 0
    assert len(calls) == 1


def test_stop_during_import_ends_loop(inbox_dir, ticks, monkeypatch):
    def stopping(argv):
        STOP.set()
        return EXIT_STOPPED

    monkeypatch.setattr(importer, "main", stopping)
    waits = []
    monkeypatch.setattr(STOP, "wait", lambda timeout=None: waits.append(1) or True)
    assert watch.main(["--inbox", str(inbox_dir), "--interval", "0"]) == 0
    assert len(waits) == 1


def test_real_import(tmp_path, ticks, make_track, monkeypatch):
    lib, inbox_dir, state = tmp_path / "Musique", tmp_path / "Inbox", tmp_path / "state"
    lib.mkdir()
    inbox_dir.mkdir()
    monkeypatch.setattr(importer, "enrichers", lambda args: [])

    def deposit():
        make_track(inbox_dir / "Album/1.flac", title="Un", artist="A", album="B", track=1)

    ticks(inbox_dir, [deposit, nothing, nothing], "--root", str(lib), "--state-dir", str(state),
          "--settle", "0", "--apply")
    assert [str(p.relative_to(lib)) for p in lib.rglob("*.m4a")] == ["A/B/01 Un.m4a"]
    assert not (inbox_dir / "Album").exists()
    assert list((inbox_dir / importer.DONE).glob("*/Album/1.flac"))


# ---------- nightly organize (--organize-at) ----------

@pytest.fixture
def nightly(tmp_path, inbox_dir, calls, ticks, monkeypatch):
    """Simulated clock, organize replaced by a recorder."""
    state = tmp_path / "state"
    state.mkdir()

    class N:
        clock = [datetime(2026, 10, 18, 1, 0)]
        passes = Calls()

        def last_run(self, t):
            (state / organize.LAST_RUN).write_text(f"{t.timestamp()}\n")

        def run(self, actions, *argv):
            return ticks(inbox_dir, actions, "--organize-at", "02:00", "--state-dir", str(state),
                         *argv)

        def at(self, *hm):
            return lambda: self.clock.__setitem__(0, datetime(2026, 10, 18, *hm))

    n = N()
    n.passes.codes = []

    def fake_organize(argv):
        n.passes.append(argv)
        return n.passes.codes.pop(0) if n.passes.codes else 0

    monkeypatch.setattr(organize, "main", fake_organize)
    monkeypatch.setattr(watch, "now", lambda: n.clock[0])
    return n


def test_organize_once_after_slot(nightly):
    nightly.last_run(datetime(2026, 10, 17, 2, 0))
    nightly.run([nothing, nightly.at(2, 0), nothing, nightly.at(3, 0), nothing], "--apply")
    # not before 02:00, then only once
    assert nightly.passes == [["--since", "--state-dir", nightly.passes[0][2], "--apply"]]


def test_organize_catches_up_at_startup(nightly):
    nightly.clock[0] = datetime(2026, 10, 18, 10, 0)  # container restarted in the morning
    nightly.last_run(datetime(2026, 10, 17, 2, 0))
    nightly.run([nothing])
    assert len(nightly.passes) == 1


def test_organize_skipped_after_manual_full_run(nightly):
    nightly.clock[0] = datetime(2026, 10, 18, 10, 0)
    nightly.last_run(datetime(2026, 10, 18, 9, 0))    # full pass run by hand this morning
    nightly.run([nothing, nothing])
    assert nightly.passes == []


def test_organize_never_without_last_run(nightly, capsys):
    nightly.clock[0] = datetime(2026, 10, 18, 3, 0)
    nightly.run([nothing, nothing, nothing])
    assert nightly.passes == []
    assert capsys.readouterr().out.count("nightly organize skipped") == 1


def test_organize_retried_while_locked(nightly):
    nightly.clock[0] = datetime(2026, 10, 18, 3, 0)
    nightly.last_run(datetime(2026, 10, 17, 2, 0))
    nightly.passes.codes += [EXIT_LOCKED]
    nightly.run([nothing, nothing, nothing])
    assert len(nightly.passes) == 2


def test_organize_dry_run_once_a_day(nightly):
    nightly.clock[0] = datetime(2026, 10, 18, 3, 0)
    nightly.last_run(datetime(2026, 10, 17, 2, 0))    # a dry run does not move last_run forward
    nightly.run([nothing, nothing, nothing])
    assert nightly.passes == [["--since", "--state-dir", nightly.passes[0][2]]]


def test_organize_crash_logged(nightly, monkeypatch, capsys):
    nightly.clock[0] = datetime(2026, 10, 18, 3, 0)
    nightly.last_run(datetime(2026, 10, 17, 2, 0))

    def boom(argv):
        raise RuntimeError("oups")

    monkeypatch.setattr(organize, "main", boom)
    assert nightly.run([nothing, nothing]) == 0
    assert "ERROR    organize interrupted" in capsys.readouterr().out


def test_organize_at_format():
    with pytest.raises(SystemExit):
        watch.main(["--inbox", "inbox", "--organize-at", "2h"])


def test_real_nightly_organize(tmp_path, ticks, make_track, monkeypatch):
    lib, inbox_dir, state = tmp_path / "Musique", tmp_path / "Inbox", tmp_path / "state"
    inbox_dir.mkdir()
    make_track(lib / "C/D/01 Rien.m4a", title="Rien", artist="C", album="D", track=1)
    assert organize.main(["--root", str(lib), "--state-dir", str(state), "--apply"]) == 0
    before = float((state / organize.LAST_RUN).read_text())
    # tag edited from the Mac during the day
    make_track(lib / "vrac/1.m4a", title="Un", artist="A", album="B", track=1)
    # the next day at 03:00 (whatever time the test runs at)
    next_day = datetime.fromtimestamp(before).date() + timedelta(days=1)
    monkeypatch.setattr(watch, "now", lambda: datetime.combine(next_day, dtime(3, 0)))
    monkeypatch.setattr(importer, "main", lambda argv: 0)
    # --range-at, the former name of --organize-at, still works
    ticks(inbox_dir, [nothing], "--root", str(lib), "--state-dir", str(state), "--apply",
          "--range-at", "02:00")
    assert (lib / "A/B/01 Un.m4a").exists() and not (lib / "vrac").exists()
    assert float((state / organize.LAST_RUN).read_text()) > before
