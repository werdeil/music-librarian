"""Weekly enrichment (enrich.py) and its slot in watch."""
# pylint: disable=redefined-outer-name,unused-argument  # pytest fixtures
# pylint: disable=attribute-defined-outside-init  # fixture environment objects
import fcntl
import sys
from datetime import datetime, time

import pytest

from music_librarian import enrich, inbox, librarian, watch
from music_librarian.librarian import EXIT_LOCKED, EXIT_STOPPED, STOP


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "Musique"
    for d in ("A/B", "A/C", "D/E", "D/E/CD2", ".cache/x"):
        (root / d).mkdir(parents=True)
    for f in ("A/B/01.m4a", "A/C/01.mp3", "D/E/01.flac", "D/E/CD2/01.m4a", ".cache/x/1.m4a"):
        (root / f).write_bytes(b"x")
    (root / "A/folder.jpg").write_bytes(b"jpg")      # artist folder without audio
    return root


@pytest.fixture
def tools(tmp_path, monkeypatch):
    """Fake tools: each call is recorded (name, arguments) in a file."""
    log = tmp_path / "calls.log"
    fails = set()

    def enrichers(args):
        out = []
        for name in ("replaygain", "pochettes", "paroles"):
            code = 1 if name in fails else 0
            script = (f"import sys; open({str(log)!r}, 'a').write({name!r} + ' ' + "
                      f"' '.join(sys.argv[1:]) + '\\n'); sys.exit({code})")
            out.append((name, [sys.executable, "-c", script]))
        return out

    monkeypatch.setattr(inbox, "enrichers", enrichers)

    class T:
        def calls(self):
            return log.read_text().splitlines() if log.exists() else []

    t = T()
    t.fails = fails
    return t


def run(lib, state, *a):
    return enrich.main(["--root", str(lib), "--state-dir", str(state), *a])


def test_album_dirs(lib):
    assert [str(d.relative_to(lib)) for d in enrich.album_dirs(lib)] == \
        ["A/B", "A/C", "D/E", "D/E/CD2"]


def test_batches_and_last_run(lib, tools, tmp_path):
    state = tmp_path / "state"
    assert run(lib, state, "--apply", "--batch", "3") == 0
    calls = tools.calls()
    assert [c.split()[0] for c in calls] == ["replaygain"] * 2 + ["pochettes"] * 2 + ["paroles"] * 2
    assert calls[0].endswith(f"{lib}/A/B {lib}/A/C {lib}/D/E")
    assert calls[1] == f"replaygain {lib}/D/E/CD2"
    assert enrich.last_run(state) is not None


def test_dry_run(lib, tools, tmp_path):
    state = tmp_path / "state"
    assert run(lib, state) == 0
    assert all(" --dry-run " in c for c in tools.calls())
    assert enrich.last_run(state) is None


def test_tool_failure_reported(lib, tools, tmp_path, capsys):
    tools.fails.add("pochettes")
    assert run(lib, tmp_path / "state", "--apply") == 2
    assert "ÉCHEC    pochettes" in capsys.readouterr().out
    assert [c.split()[0] for c in tools.calls()] == ["replaygain", "pochettes", "paroles"]


def test_stop_between_batches(lib, tools, tmp_path, monkeypatch):
    state = tmp_path / "state"
    real = enrich.subprocess.run

    def run_then_stop(cmd, **kw):
        r = real(cmd, **kw)  # pylint: disable=subprocess-run-check  # check passed by enrich
        STOP.set()          # docker stop during the first batch
        return r

    monkeypatch.setattr(enrich.subprocess, "run", run_then_stop)
    assert run(lib, state, "--apply", "--batch", "2") == EXIT_STOPPED
    assert len(tools.calls()) == 1                    # the current batch finished, not the others
    assert enrich.last_run(state) is None             # picked up on the next pass


def test_refused_while_locked(lib, tools, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    with open(state / librarian.LOCK, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert run(lib, state, "--apply") == EXIT_LOCKED
    assert tools.calls() == []
    assert run(lib, state) == 0                        # a dry run does not take the lock


# ---------- weekly slot in watch ----------

@pytest.mark.parametrize("t, slot", [
    (datetime(2026, 10, 18, 3, 0), datetime(2026, 10, 18, 3, 0)),   # Sunday, on time
    (datetime(2026, 10, 18, 2, 59), datetime(2026, 10, 11, 3, 0)),  # dimanche, juste avant
    (datetime(2026, 10, 21, 12, 0), datetime(2026, 10, 18, 3, 0)),  # mercredi suivant
])
def test_last_slot(t, slot):
    assert watch.last_slot(t, 6, time(3, 0)) == slot


@pytest.mark.parametrize("s", ["dim", "dimanche 3h", "xyz 03:00"])
def test_enrich_at_format(s):
    with pytest.raises(SystemExit):
        watch.main(["--inbox", "inbox", "--enrich-at", s])


@pytest.fixture
def weekly(tmp_path, monkeypatch, ticks):
    inbox_dir, state = tmp_path / "Inbox", tmp_path / "state"
    inbox_dir.mkdir()
    state.mkdir()
    monkeypatch.setattr(inbox, "main", lambda argv: 0)

    class W:
        clock = [datetime(2026, 10, 17, 12, 0)]     # samedi
        calls = []
        codes = []

        def at(self, year, month, day, hour, minute):
            return lambda: self.clock.__setitem__(0, datetime(year, month, day, hour, minute))

        def stamp(self, t):
            (state / enrich.LAST_RUN).write_text(f"{t.timestamp()}\n")

        def run(self, actions, *argv):
            return ticks(inbox_dir, actions, "--state-dir", str(state),
                         "--enrich-at", "dim 03:00", *argv)

    w = W()

    def fake(argv):
        w.calls.append(argv)
        return w.codes.pop(0) if w.codes else 0

    monkeypatch.setattr(enrich, "main", fake)
    monkeypatch.setattr(watch, "now", lambda: w.clock[0])
    return w


def test_first_run_waits_for_next_slot(weekly):
    # no known pass, container started on Saturday: nothing before Sunday 03:00
    weekly.run([weekly.at(2026, 10, 18, 2, 0), weekly.at(2026, 10, 18, 3, 0), lambda: None],
               "--apply")
    assert weekly.calls == [["--state-dir", weekly.calls[0][1], "--apply"]]


def test_caught_up_after_missed_slot(weekly):
    weekly.clock[0] = datetime(2026, 10, 19, 9, 0)    # Monday: the container was stopped
    weekly.stamp(datetime(2026, 10, 11, 5, 0))         # last pass the week before
    weekly.run([lambda: None])
    assert len(weekly.calls) == 1


def test_not_twice_in_a_week(weekly):
    weekly.clock[0] = datetime(2026, 10, 19, 9, 0)
    weekly.stamp(datetime(2026, 10, 18, 3, 0))         # already done on Sunday
    weekly.run([lambda: None, weekly.at(2026, 10, 24, 23, 0)])
    assert weekly.calls == []


def test_retried_while_locked_then_once(weekly):
    weekly.clock[0] = datetime(2026, 10, 18, 3, 0)
    weekly.stamp(datetime(2026, 10, 11, 5, 0))
    weekly.codes += [EXIT_LOCKED]
    weekly.run([lambda: None, lambda: None, lambda: None])  # dry run: last_run does not move forward
    assert len(weekly.calls) == 2
