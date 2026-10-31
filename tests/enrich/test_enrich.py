"""Weekly enrichment (enrich.py) and its slot in watch."""
# pylint: disable=redefined-outer-name,unused-argument  # pytest fixtures
# pylint: disable=attribute-defined-outside-init  # fixture environment objects
import fcntl
import sys
from datetime import datetime, time

import pytest

from music_librarian import enrich, importer, watch
from music_librarian.core import runs
from music_librarian.core.lock import EXIT_LOCKED, EXIT_STOPPED, LOCK, STOP
from music_librarian.media.metadata_cache import MetadataCache
from music_librarian.media import credit_tags


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
    fails, stops = set(), set()

    def enrichers():
        out = []
        for name in ("genres", "replaygain", "covers", "lyrics"):
            code = 1 if name in fails else EXIT_STOPPED if name in stops else 0
            script = (f"import sys; open({str(log)!r}, 'a').write({name!r} + ' ' + "
                      f"' '.join(sys.argv[1:]) + '\\n'); sys.exit({code})")
            out.append((name, [sys.executable, "-c", script]))
        return out

    monkeypatch.setattr(enrich, "enrichers", enrichers)

    class T:
        def calls(self):
            return log.read_text().splitlines() if log.exists() else []

    t = T()
    t.fails, t.stops = fails, stops
    return t


def run(lib, state, *a):
    return enrich.main(["--root", str(lib), "--state-dir", str(state), "--no-split", "--no-dedupe", *a])


def test_album_dirs(lib):
    assert [str(d.relative_to(lib)) for d in enrich.album_dirs(lib)[0]] == \
        ["A/B", "A/C", "D/E", "D/E/CD2"]


def test_batches_and_last_run(lib, tools, tmp_path):
    state = tmp_path / "state"
    assert run(lib, state, "--apply", "--batch", "3") == 0
    calls = tools.calls()
    assert [c.split()[0] for c in calls] == [n for n in ("genres", "replaygain", "covers", "lyrics")
                                             for _ in range(2)]
    assert calls[2].endswith(f"{lib}/A/B {lib}/A/C {lib}/D/E")
    assert calls[3] == f"replaygain {lib}/D/E/CD2"
    assert enrich.last_run(state) is not None


def test_no_lyrics_leaves_the_weekly_pass(lib, tools, tmp_path):
    state = tmp_path / "state"
    assert run(lib, state, "--apply", "--no-lyrics") == 0
    assert [c.split()[0] for c in tools.calls()] == ["genres", "replaygain", "covers"]
    assert enrich.last_run(state) is None


def test_no_genres_leaves_the_weekly_pass(lib, tools, tmp_path):
    state = tmp_path / "state"
    assert run(lib, state, "--apply", "--no-genres") == 0
    assert [c.split()[0] for c in tools.calls()] == ["replaygain", "covers", "lyrics"]
    assert enrich.last_run(state) is None


@pytest.mark.parametrize("tool", ["replaygain", "covers"])
def test_tool_left_out(lib, tools, tmp_path, tool):
    state = tmp_path / "state"
    assert run(lib, state, "--apply", f"--no-{tool}") == 0
    assert [c.split()[0] for c in tools.calls()] == [t for t in enrich.TOOLS if t != tool]
    assert enrich.last_run(state) is None


def test_files_gone_dropped_from_cache(lib, tools, tmp_path, capsys):
    state = tmp_path / "state"
    old = lib / "Z" / "01.m4a"
    old.parent.mkdir()
    old.write_bytes(b"x")
    with MetadataCache(state) as cache:
        for p in (lib / "A/B/01.m4a", old):
            cache.snapshot(p)
    old.unlink()
    assert run(lib, state) == 0
    assert "gone from cache: 1" in capsys.readouterr().out


def test_rescan_reads_everything_first(lib, tools, tmp_path, capsys):
    state = tmp_path / "state"
    with MetadataCache(state) as cache:
        cache.snapshot(lib / "A/B/01.m4a")
    assert run(lib, state, "--rescan") == 0
    out = capsys.readouterr().out
    assert "read: 4" in out and "from cache" not in out.split("Enrichment summary")[0]
    assert out.index("Scan summary") < out.index("Enrichment summary")


def test_dry_run(lib, tools, tmp_path):
    state = tmp_path / "state"
    assert run(lib, state, "--batch", "1") == 0
    assert tools.calls() == [f"{name} --dry-run {lib}" for name in enrich.TOOLS]
    assert enrich.last_run(state) is None


def test_dry_run_adds_up_the_estimates(lib, tmp_path, monkeypatch, capsys):
    def tool(seconds):
        line = f"      estimated time: ~{seconds // 60} min ({seconds} s)" if seconds else "nothing to do"
        return [sys.executable, "-c", f"print({line!r})"]
    monkeypatch.setattr(enrich, "enrichers", lambda: [("genres", tool(90)), ("covers", tool(0)),
                                                      ("lyrics", tool(5400))])
    assert run(lib, tmp_path / "state") == 0
    summary = capsys.readouterr().out.split("Enrichment summary")[1]
    assert "genres time: ~2 min" in summary and "lyrics time: ~1 h 30 min" in summary
    assert "covers time" not in summary and "estimated time: ~1 h 32 min" in summary


def test_tool_failure_reported(lib, tools, tmp_path, capsys):
    tools.fails.add("covers")
    assert run(lib, tmp_path / "state", "--apply") == 2
    assert "FAILED   covers" in capsys.readouterr().out
    assert [c.split()[0] for c in tools.calls()] == ["genres", "replaygain", "covers", "lyrics"]


def test_stopped_tool_is_not_a_failure(lib, tools, tmp_path, capsys):
    tools.stops.add("replaygain")
    assert run(lib, tmp_path / "state", "--apply") == 0
    assert "FAILED" not in capsys.readouterr().out


def test_stop_between_batches(lib, tools, tmp_path, monkeypatch):
    state = tmp_path / "state"
    real = enrich.run_tool

    def run_then_stop(cmd, env, log):
        code = real(cmd, env, log)
        STOP.set()          # docker stop during the first batch
        return code

    monkeypatch.setattr(enrich, "run_tool", run_then_stop)
    assert run(lib, state, "--apply", "--batch", "2") == EXIT_STOPPED
    assert len(tools.calls()) == 1                    # the current batch finished, not the others
    assert enrich.last_run(state) is None             # picked up on the next pass


def test_stop_during_the_last_batch(lib, tools, tmp_path, monkeypatch, capsys):
    real = enrich.run_tool

    def stop_during_the_last_tool(cmd, env, log):
        code = real(cmd, env, log)
        if "'lyrics'" in " ".join(cmd):
            STOP.set()
        return code

    monkeypatch.setattr(enrich, "run_tool", stop_during_the_last_tool)
    assert run(lib, tmp_path / "state", "--apply") == EXIT_STOPPED       # one batch: the last
    out = capsys.readouterr().out
    assert len(tools.calls()) == 4 and "ENRICH   (split)" not in out
    assert enrich.last_run(tmp_path / "state") is None


def test_tool_output_in_the_run_log(lib, tmp_path, monkeypatch, capsys):
    script = "print('LYRICS   Beyoncé — Halo'); print('--- Lyrics summary ---'); print('   written: 1')"
    monkeypatch.setattr(enrich, "enrichers", lambda: [("lyrics", [sys.executable, "-c", script])])
    state = tmp_path / "state"
    assert run(lib, state) == 0
    [record] = runs.recent(state)
    text = runs.log_path(state, record["id"]).read_text(encoding="utf-8")
    assert text.count("LYRICS   Beyoncé — Halo") == 1
    assert text == capsys.readouterr().out
    assert record["command"] == "enrich" and record["summary"][0] == "folders: 4"


def test_refused_while_locked(lib, tools, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    with open(state / LOCK, "a", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert run(lib, state, "--apply") == EXIT_LOCKED
    assert tools.calls() == []
    assert run(lib, state) == 0                        # a dry run does not take the lock


def test_split_after_the_tools(tools, tmp_path, make_track, capsys):
    root = tmp_path / "Music"
    track = make_track(root / "The Beatles/Help!/01 Help!.m4a", title="Help!", artist="The Beatles",
                       albumartist="The Beatles", composer="John Lennon, Paul McCartney")
    make_track(root / "John Lennon/Imagine/01 Imagine.m4a", title="Imagine", composer="John Lennon",
               albumartist="John Lennon")
    make_track(root / "Paul McCartney/Ram/01 Ram On.m4a", title="Ram On", composer="Paul McCartney",
               albumartist="Paul McCartney")
    state = tmp_path / "state"
    assert enrich.main(["--root", str(root), "--state-dir", str(state)]) == 0
    assert "SPLIT    composer: John Lennon, Paul McCartney" in capsys.readouterr().out
    assert enrich.main(["--root", str(root), "--state-dir", str(state), "--apply"]) == 0
    assert credit_tags.read(track)["composer"] == ["John Lennon; Paul McCartney"]


# ---------- weekly slot in watch ----------

@pytest.mark.parametrize("t, slot", [
    (datetime(2026, 10, 18, 3, 0), datetime(2026, 10, 18, 3, 0)),   # Sunday, on time
    (datetime(2026, 10, 18, 2, 59), datetime(2026, 10, 11, 3, 0)),  # dimanche, juste avant
    (datetime(2026, 10, 21, 12, 0), datetime(2026, 10, 18, 3, 0)),  # mercredi suivant
])
def test_last_slot(t, slot):
    assert watch.last_slot(t, 6, time(3, 0)) == slot


@pytest.mark.parametrize("s, slot", [("sun 03:00", (6, time(3, 0))), ("Monday 8:30", (0, time(8, 30))),
                                     ("dim 03:00", (6, time(3, 0)))])   # French names accepted
def test_enrich_at_days(s, slot):
    assert watch.weekly(s) == slot


@pytest.mark.parametrize("s", ["sun", "sunday 3h", "xyz 03:00"])
def test_enrich_at_format(s):
    with pytest.raises(SystemExit):
        watch.main(["--inbox", "inbox", "--root", "lib", "--enrich-at", s])


@pytest.fixture
def weekly(tmp_path, monkeypatch, ticks):
    inbox_dir, state = tmp_path / "Inbox", tmp_path / "state"
    inbox_dir.mkdir()
    state.mkdir()
    monkeypatch.setattr(importer, "run", lambda opts, log: 0)

    class W:
        clock = [datetime(2026, 10, 17, 12, 0)]     # Saturday
        calls = []
        codes = []

        def at(self, year, month, day, hour, minute):
            return lambda: self.clock.__setitem__(0, datetime(year, month, day, hour, minute))

        def stamp(self, t):
            (state / enrich.LAST_RUN).write_text(f"{t.timestamp()}\n")

        def run(self, actions, *argv):
            return ticks(inbox_dir, actions, "--state-dir", str(state),
                         "--enrich-at", "sun 03:00", *argv)

    w = W()

    def fake(opts, log):
        w.calls.append(opts)
        return w.codes.pop(0) if w.codes else 0

    monkeypatch.setattr(enrich, "run", fake)
    monkeypatch.setattr(watch, "now", lambda: w.clock[0])
    return w


def test_first_run_waits_for_next_slot(weekly):
    # no known pass, container started on Saturday: nothing before Sunday 03:00
    weekly.run([weekly.at(2026, 10, 18, 2, 0), weekly.at(2026, 10, 18, 3, 0), lambda: None],
               "--apply")
    assert [(c.apply, c.state_dir) for c in weekly.calls] == [(True, weekly.calls[0].state_dir)]
    assert weekly.calls[0].rescan


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


def test_a_stop_is_passed_on_to_the_tool(tmp_path):
    script = tmp_path / "tool.py"
    script.write_text(
        "import signal, sys, time\n"
        "stop = []\n"
        "signal.signal(signal.SIGTERM, lambda *a: stop.append(1))\n"
        "for i in range(100):\n"
        "    if stop:\n"
        "        print('STOP     requested', flush=True)\n"
        "        sys.exit(4)\n"
        "    print(f'LINE     {i}', flush=True)\n"
        "    time.sleep(0.02)\n", encoding="utf-8")
    lines = []

    def log(line):
        lines.append(line)
        STOP.set()

    assert enrich.run_tool([sys.executable, str(script)], None, log) == EXIT_STOPPED
    assert lines[-1] == "STOP     requested" and len(lines) < 20
