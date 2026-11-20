"""Lyrion history (persist.db) carry-over for moved files (lyrion.py)."""
# pylint: disable=redefined-outer-name,unused-argument  # pytest fixtures
# pylint: disable=attribute-defined-outside-init  # fixture environment objects
import base64
import hashlib
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from music_librarian import organize
from music_librarian.integrations import lyrion, plugin_counts
from music_librarian.integrations.lyrion import EXIT_LYRION

LYRION_ROOT = "/music/Musique"


def md5(u):
    return hashlib.md5(u.encode()).hexdigest()


def url(rel):
    return lyrion.Lyrion(Path("x"), LYRION_ROOT, Path("/lib"), Path("b")).url(Path("/lib") / rel)


@pytest.fixture
def env(tmp_path, lyrion_db):
    lib, state, db = tmp_path / "Musique", tmp_path / "state", lyrion_db
    lib.mkdir()

    class Env:
        def run(self, *a):
            return organize.main(["--root", str(lib), "--state-dir", str(state),
                                      "--lyrion-db", str(db), "--lyrion-root", LYRION_ROOT, *a])

        # pylint: disable-next=too-many-arguments,too-many-positional-arguments
        def add(self, rel, play=0, rating=None, skip=0, dps=0, added=100, last=0, alt=True):
            u = url(rel)
            with sqlite3.connect(db) as c:
                c.execute("INSERT INTO tracks_persistent (url, urlmd5, playCount, rating, "
                          "added, lastPlayed) VALUES (?, ?, ?, ?, ?, ?)",
                          (u, md5(u), play, rating, added, last))
                if alt:
                    c.execute("INSERT INTO alternativeplaycount (url, urlmd5, playCount, "
                              "skipCount, dynPSval, lastPlayed) VALUES (?, ?, ?, ?, ?, ?)",
                              (u, md5(u), play, skip, dps, last))
            c.close()

        def row(self, table, rel):
            with sqlite3.connect(db) as c:
                c.row_factory = sqlite3.Row
                r = c.execute(f"SELECT * FROM {table} WHERE urlmd5 = ?", (md5(url(rel)),))
                r = r.fetchone()
            c.close()
            return dict(r) if r else None

        def count(self, table):
            with sqlite3.connect(db) as c:
                n = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            c.close()
            return n

    e = Env()
    e.lib, e.state, e.db = lib, state, db
    return e


@pytest.fixture
def misplaced(env, make_track):
    """A misnamed file, with an Lyrion history."""
    make_track(env.lib / "vrac/1.m4a", title="Un", artist="A", album="B", track=1)
    env.add("vrac/1.m4a", play=7, rating=80, skip=2, dps=40, last=1000)
    return env


@pytest.mark.parametrize("rel, expected", [
    ("A B/01 Don't.m4a", "A%20B/01%20Don%27t.m4a"),
    ("A/#1; 100% (live) & co+=.m4a", "A/%231%3B%20100%25%20(live)%20&%20co+=.m4a"),
    ("Björk/Début/01 é.m4a", "Bj%C3%B6rk/D%C3%A9but/01%20%C3%A9.m4a"),
    ("Q/Villains/01 Feet Don\x92't.m4a", "Q/Villains/01%20Feet%20Don%C2%92%27t.m4a"),  # real case
    ("Compilations/_..Hits [Remix]!.mp3", "Compilations/_..Hits%20[Remix]!.mp3"),
])
def test_url_like_lyrion(rel, expected):
    assert url(rel) == f"file://{LYRION_ROOT}/{expected}"


def test_url_outside_library():
    assert lyrion.Lyrion(Path("x"), LYRION_ROOT, Path("/lib"), Path("b")).url(Path("/Inbox/1.m4a")) is None


def test_dry_run_writes_nothing(misplaced, capsys):
    assert misplaced.run() == 0
    assert misplaced.row("tracks_persistent", "vrac/1.m4a")["playCount"] == 7
    assert not (misplaced.state / lyrion.BACKUPS).exists()
    assert "Lyrion renamed: 1" in " ".join(capsys.readouterr().out.split())


def test_history_follows_renamed_file(misplaced):
    assert misplaced.run("--apply") == 0
    assert misplaced.row("tracks_persistent", "vrac/1.m4a") is None
    tp = misplaced.row("tracks_persistent", "A/B/01 Un.m4a")
    assert (tp["playCount"], tp["rating"], tp["url"]) == (7, 80, url("A/B/01 Un.m4a"))
    ap = misplaced.row("alternativeplaycount", "A/B/01 Un.m4a")
    assert (ap["playCount"], ap["skipCount"], ap["dynPSval"]) == (7, 2, 40)
    assert misplaced.count("tracks_persistent") == misplaced.count("alternativeplaycount") == 1
    [bk] = (misplaced.state / lyrion.BACKUPS).glob("persist-*.db")
    with sqlite3.connect(bk) as c:  # the backup is the database from before
        assert c.execute("SELECT url FROM tracks_persistent").fetchone()[0] == url("vrac/1.m4a")
    c.close()


def test_merged_when_lyrion_already_saw_new_file(misplaced):
    # Lyrion already indexed the renamed file: fresh entry, one play since
    misplaced.add("A/B/01 Un.m4a", play=1, added=500, last=2000)
    assert misplaced.run("--apply") == 0
    assert misplaced.row("tracks_persistent", "vrac/1.m4a") is None
    tp = misplaced.row("tracks_persistent", "A/B/01 Un.m4a")
    assert (tp["playCount"], tp["rating"], tp["added"], tp["lastPlayed"]) == (8, 80, 100, 2000)
    ap = misplaced.row("alternativeplaycount", "A/B/01 Un.m4a")
    assert (ap["playCount"], ap["skipCount"], ap["dynPSval"]) == (8, 2, 40)
    assert misplaced.count("tracks_persistent") == misplaced.count("alternativeplaycount") == 1


def test_only_in_tracks_persistent(env, make_track):
    make_track(env.lib / "vrac/1.m4a", title="Un", artist="A", album="B", track=1)
    env.add("vrac/1.m4a", play=3, alt=False)
    assert env.run("--apply") == 0
    assert env.row("tracks_persistent", "A/B/01 Un.m4a")["playCount"] == 3


def test_no_history(env, make_track, capsys):
    make_track(env.lib / "vrac/1.m4a", title="Un", artist="A", album="B", track=1)
    assert env.run("--apply") == 0
    assert "Lyrion no history: 1" in " ".join(capsys.readouterr().out.split())
    assert env.count("tracks_persistent") == 0


def test_annexes_and_files_in_place_ignored(misplaced, make_track):
    (misplaced.lib / "vrac/folder.jpg").write_bytes(b"jpg")
    make_track(misplaced.lib / "C/D/01 Rien.m4a", title="Rien", artist="C", album="D", track=1)
    misplaced.add("C/D/01 Rien.m4a", play=1)
    assert misplaced.run("--apply") == 0
    assert misplaced.row("tracks_persistent", "C/D/01 Rien.m4a")["playCount"] == 1
    assert misplaced.count("tracks_persistent") == 2


def refreshed(env, rel, play=None, last=None):
    """The plugin's refresh after a scan that missed `rel`, then one that found it: its entry is
    deleted, then added empty, `play` plays made since."""
    u = url(rel)
    with sqlite3.connect(env.db) as c:
        c.execute("DELETE FROM alternativeplaycount WHERE urlmd5 = ?", (md5(u),))
        c.execute("INSERT INTO alternativeplaycount (url, urlmd5, playCount, lastPlayed) VALUES (?, ?, ?, ?)",
                  (u, md5(u), play, last))
    c.close()


def test_plugin_counts_lost_to_its_refresh_are_given_back(misplaced, capsys):
    assert misplaced.run("--apply") == 0
    kept = json.loads((misplaced.state / plugin_counts.FILE).read_text())
    assert [(c["url"], c["playCount"], c["skipCount"]) for c in kept.values()] == [
        (url("A/B/01 Un.m4a"), 7, 2)]
    refreshed(misplaced, "A/B/01 Un.m4a", play=1, last=3000)
    capsys.readouterr()

    assert misplaced.run() == 0     # a dry run leaves it
    assert misplaced.row("alternativeplaycount", "A/B/01 Un.m4a")["playCount"] == 1

    assert misplaced.run("--apply") == 0
    assert "Lyrion plugin restored: 1" in " ".join(capsys.readouterr().out.split())
    ap = misplaced.row("alternativeplaycount", "A/B/01 Un.m4a")
    assert (ap["playCount"], ap["skipCount"], ap["dynPSval"], ap["lastPlayed"]) == (8, 2, 40, 3000)
    assert not (misplaced.state / plugin_counts.FILE).exists()


def test_plugin_counts_not_given_back_do_not_fail_a_pass_without_moves(misplaced, monkeypatch, capsys):
    assert misplaced.run("--apply") == 0
    refreshed(misplaced, "A/B/01 Un.m4a")
    monkeypatch.setattr(lyrion.Lyrion, "backup", lambda self: 1 / 0)
    assert misplaced.run("--apply") == 0
    assert "WARNING  Lyrion plugin counts not given back" in capsys.readouterr().out
    assert (misplaced.state / plugin_counts.FILE).exists()


def test_plugin_counts_kept_follow_a_track_moved_again(misplaced):
    assert misplaced.run("--apply") == 0
    with sqlite3.connect(misplaced.db) as c:    # deleted by the plugin, the file not scanned since
        c.execute("DELETE FROM alternativeplaycount")
    c.close()
    [j] = (misplaced.state / "journal").glob("*.jsonl")
    assert misplaced.run("--undo", str(j), "--apply") == 0
    [counts] = json.loads((misplaced.state / plugin_counts.FILE).read_text()).values()
    assert (counts["url"], counts["playCount"]) == (url("vrac/1.m4a"), 7)

    refreshed(misplaced, "vrac/1.m4a")
    assert misplaced.run("--apply") == 0
    ap = misplaced.row("alternativeplaycount", "A/B/01 Un.m4a")
    assert (ap["playCount"], ap["skipCount"], ap["dynPSval"]) == (7, 2, 40)


def test_plugin_counts_are_forgotten_after_a_while(misplaced, make_track):
    assert misplaced.run("--apply") == 0
    path = misplaced.state / plugin_counts.FILE
    kept = json.loads(path.read_text())
    for counts in kept.values():
        counts["kept"] -= plugin_counts.KEEP + 1
    path.write_text(json.dumps(kept))
    make_track(misplaced.lib / "vrac/2.m4a", title="Deux", artist="A", album="B", track=2)
    misplaced.add("vrac/2.m4a", play=3)
    assert misplaced.run("--apply") == 0
    assert [c["url"] for c in json.loads(path.read_text()).values()] == [url("A/B/02 Deux.m4a")]


def test_undo_brings_history_back(misplaced):
    assert misplaced.run("--apply") == 0
    [j] = (misplaced.state / "journal").glob("*.jsonl")
    assert misplaced.run("--undo", str(j), "--apply") == 0
    assert misplaced.row("tracks_persistent", "vrac/1.m4a")["playCount"] == 7
    assert misplaced.row("alternativeplaycount", "A/B/01 Un.m4a") is None


def test_lyrion_busy_files_still_moved_then_replay(misplaced, monkeypatch, capsys):
    monkeypatch.setattr(lyrion, "BUSY_TIMEOUT", 0.1)
    waits = []
    monkeypatch.setattr(lyrion, "pause", waits.append)
    other = sqlite3.connect(misplaced.db, isolation_level=None)
    other.execute("BEGIN EXCLUSIVE")  # Lyrion in the middle of a write
    assert misplaced.run("--apply") == EXIT_LYRION
    other.execute("ROLLBACK")
    other.close()
    assert waits == list(lyrion.LOCK_RETRIES)
    assert not list((misplaced.state / lyrion.BACKUPS).glob("persist-*.db"))
    out = capsys.readouterr().out
    assert "ERROR    Lyrion carry-over" in out and "--lyrion-replay" in out
    assert (misplaced.lib / "A/B/01 Un.m4a").exists()
    assert misplaced.row("tracks_persistent", "vrac/1.m4a")["playCount"] == 7

    [j] = (misplaced.state / "journal").glob("*.jsonl")
    assert misplaced.run("--lyrion-replay", str(j)) == 0          # dry run
    assert misplaced.row("tracks_persistent", "vrac/1.m4a") is not None
    assert misplaced.run("--lyrion-replay", str(j), "--apply") == 0
    assert misplaced.row("tracks_persistent", "A/B/01 Un.m4a")["playCount"] == 7
    # replaying a second time changes nothing
    assert misplaced.run("--lyrion-replay", str(j), "--apply") == 0
    assert misplaced.count("tracks_persistent") == 1


def test_backups_rotated(misplaced, monkeypatch):
    d = misplaced.state / lyrion.BACKUPS
    d.mkdir(parents=True)
    for i in range(lyrion.KEEP_BACKUPS + 3):
        (d / f"persist-20200101-{i:06d}.db").write_bytes(b"")
    assert misplaced.run("--apply") == 0
    kept = sorted(p.name for p in d.glob("persist-*.db"))
    assert len(kept) == lyrion.KEEP_BACKUPS and not kept[-1].startswith("persist-2020")


def test_disabled_without_lyrion_db(misplaced):
    assert organize.main(["--root", str(misplaced.lib), "--state-dir",
                              str(misplaced.state), "--apply"]) == 0
    assert misplaced.row("tracks_persistent", "vrac/1.m4a")["playCount"] == 7


def test_lyrion_root_required(misplaced):
    with pytest.raises(SystemExit):
        organize.main(["--root", str(misplaced.lib), "--lyrion-db", str(misplaced.db)])


def test_journal_unchanged_format(misplaced):
    assert misplaced.run("--apply") == 0
    [j] = (misplaced.state / "journal").glob("*.jsonl")
    assert [json.loads(x)["type"] for x in j.read_text().splitlines()] == ["meta", "move"]


@pytest.fixture
def no_pause(monkeypatch):
    """The waits of lyrion.py return at once; the list holds what each was asked to wait."""
    waits = []
    monkeypatch.setattr(lyrion, "pause", waits.append)
    return waits


@pytest.fixture
def lines():
    class Lines(list):
        def text(self):
            return "\n".join(self)

    out = Lines()
    out.log = out.append
    return out


@pytest.mark.parametrize("result, serverstatus, expected", [
    ({"_rescan": 1}, {}, True),
    ({"_rescan": "1"}, {}, True),
    ({"_rescan": 0}, {"rescan": 1}, False),
    ({"_rescan": "0"}, {}, False),
    ({}, {"rescan": "1", "progressname": "directory"}, True),
    ({}, {}, False),
])
def test_scan_running_reads_both_answers(server, result, serverstatus, expected):
    server.result, server.serverstatus = result, serverstatus
    assert lyrion.Scan(server.url + "/").running() is expected
    assert server.asked[0] == ["rescan", "?"]
    assert (["serverstatus", 0, 0] in server.asked) is ("_rescan" not in result)


def test_scan_sends_the_login(server):
    assert lyrion.Scan(server.url, auth=("me", "secret")).running() is False
    assert server.logins == ["Basic " + base64.b64encode(b"me:secret").decode()]


def test_wait_until_the_scan_ends(server, no_pause, lines):
    server.scans = [True, True]
    assert lyrion.Scan(server.url, poll=7).wait(lines.log) is True
    assert no_pause == [7, 7] and len(server.asked) == 3
    assert lines[0].startswith("LYRION   scanning: waiting for the end, up to 15 min")
    assert lines[1].startswith("LYRION   scan ended after") and len(lines) == 2


def test_wait_says_nothing_without_a_scan(server, no_pause, lines):
    assert lyrion.Scan(server.url).wait(lines.log) is True
    assert not lines and not no_pause


def test_wait_gives_up_on_a_scan_that_never_ends(server, lines):
    server.scans = [True] * 1000
    assert lyrion.Scan(server.url, poll=0.01, timeout=0.05).wait(lines.log) is False
    assert lines[-1].startswith("WARNING  Lyrion still scanning after")


def test_wait_ends_on_a_stop_request(server, monkeypatch, lines):
    server.scans = [True] * 1000
    monkeypatch.setattr(lyrion, "pause", lambda seconds: True)
    assert lyrion.Scan(server.url).wait(lines.log) is False
    assert lines[-1].startswith("STOP     ")


@pytest.mark.parametrize("status, result, why", [
    (401, None, "HTTP 401"),
    (200, "nonsense", "unexpected answer"),
])
def test_wait_goes_on_when_lyrion_answers_badly(server, lines, status, result, why):
    server.status, server.result = status, result
    assert lyrion.Scan(server.url).wait(lines.log) is True
    assert lines == [f"WARNING  Lyrion cannot be asked whether it scans ({why}): not waiting"]


def test_wait_goes_on_when_lyrion_is_unreachable(server, lines):
    closed = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = closed.server_port
    closed.server_close()
    assert lyrion.Scan(f"http://127.0.0.1:{port}").wait(lines.log) is True
    assert "connection failed" in lines.text()


def test_pass_waits_for_the_scan_before_moving_and_before_the_carry_over(misplaced, server, no_pause,
                                                                         capsys):
    server.scans = [True, False, True, False]
    assert misplaced.run("--apply", "--lyrion-url", server.url, "--scan-poll", "3") == 0
    assert no_pause == [3, 3] and len(server.asked) == 4
    out = capsys.readouterr().out
    assert out.index("LYRION   scan ended") < out.index("--- Organize summary ---")
    assert out.count("LYRION   scan ended") == 2
    assert misplaced.row("tracks_persistent", "A/B/01 Un.m4a")["playCount"] == 7


def test_scan_that_never_ends_leaves_the_journal_to_replay(misplaced, server, capsys):
    server.scans = [True] * 10000
    args = ["--lyrion-url", server.url, "--scan-poll", "0.01", "--scan-timeout", "0.05"]
    assert misplaced.run("--apply", *args) == EXIT_LYRION
    out = capsys.readouterr().out
    assert "ERROR    Lyrion carry-over: not done while Lyrion scans" in out and "--lyrion-replay" in out
    assert (misplaced.lib / "A/B/01 Un.m4a").exists()
    assert misplaced.row("tracks_persistent", "vrac/1.m4a")["playCount"] == 7
    assert not list((misplaced.state / lyrion.BACKUPS).glob("*"))

    server.scans = []
    [j] = (misplaced.state / "journal").glob("*.jsonl")
    assert misplaced.run("--lyrion-replay", str(j), "--apply", *args) == 0
    assert misplaced.row("tracks_persistent", "A/B/01 Un.m4a")["playCount"] == 7


def test_unreachable_lyrion_does_not_hold_the_pass(misplaced, capsys):
    closed = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = closed.server_port
    closed.server_close()
    assert misplaced.run("--apply", "--lyrion-url", f"http://127.0.0.1:{port}") == 0
    assert "WARNING  Lyrion cannot be asked" in capsys.readouterr().out
    assert misplaced.row("tracks_persistent", "A/B/01 Un.m4a")["playCount"] == 7


def test_dry_run_and_no_wait_scan_never_ask_lyrion(misplaced, server):
    assert misplaced.run("--lyrion-url", server.url) == 0
    assert misplaced.run("--apply", "--lyrion-url", server.url, "--no-wait-scan") == 0
    assert not server.asked
    assert misplaced.row("tracks_persistent", "A/B/01 Un.m4a")["playCount"] == 7


def test_locked_database_is_tried_again(misplaced, server, monkeypatch, capsys):
    monkeypatch.setattr(lyrion, "BUSY_TIMEOUT", 0.1)
    other = sqlite3.connect(misplaced.db, isolation_level=None)
    other.execute("BEGIN EXCLUSIVE")
    waits = []

    def pause(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            other.execute("ROLLBACK")

    monkeypatch.setattr(lyrion, "pause", pause)
    assert misplaced.run("--apply", "--lyrion-url", server.url) == 0
    other.close()
    assert waits == [5, 10]
    assert len(server.asked) == 4   # before the moves, then before each try
    assert capsys.readouterr().out.count("LYRION   database is locked, trying again in") == 2
    assert misplaced.row("tracks_persistent", "A/B/01 Un.m4a")["playCount"] == 7
    assert len(list((misplaced.state / lyrion.BACKUPS).glob("persist-*.db"))) == 1


def test_scan_asked_for_once_the_pass_moved_files(misplaced, server, capsys):
    assert misplaced.run("--apply", "--lyrion-url", server.url, "--lyrion-rescan") == 0
    assert server.started == 1 and server.asked[-1] == ["rescan"]
    out = capsys.readouterr().out
    assert out.index("--- Organize summary ---") < out.index("LYRION   scan asked for, the files moved")
    assert misplaced.run("--apply", "--lyrion-url", server.url, "--lyrion-rescan") == 0
    assert server.started == 1


def test_scan_asked_for_by_the_environment(misplaced, server, monkeypatch):
    monkeypatch.setenv("LYRION_URL", server.url)
    monkeypatch.setenv("LYRION_RESCAN", "1")
    assert misplaced.run("--apply") == 0
    assert server.started == 1


def test_no_scan_asked_for_by_a_dry_run_nor_without_the_option(misplaced, server):
    assert misplaced.run("--lyrion-url", server.url, "--lyrion-rescan") == 0
    assert not server.asked
    assert misplaced.run("--apply", "--lyrion-url", server.url) == 0
    assert server.started == 0


def test_rescan_needs_the_url(misplaced):
    with pytest.raises(SystemExit, match="--lyrion-rescan needs --lyrion-url"):
        misplaced.run("--apply", "--lyrion-rescan")


def test_scan_asked_for_after_the_one_running_ends(misplaced, server, no_pause, capsys):
    server.scans = [False, False, True, False]
    assert misplaced.run("--apply", "--lyrion-url", server.url, "--lyrion-rescan") == 0
    assert server.started == 1 and no_pause == [5]
    out = capsys.readouterr().out
    assert out.index("LYRION   scan ended") < out.index("LYRION   scan asked for")


def test_scan_running_without_waiting_is_left_to_lyrion(misplaced, server, capsys):
    server.scans = [True]
    args = ["--apply", "--lyrion-url", server.url, "--lyrion-rescan", "--no-wait-scan"]
    assert misplaced.run(*args) == 0
    assert server.started == 0 and server.asked == [["rescan", "?"]]
    assert "WARNING  Lyrion already scanning, its scan not asked for" in capsys.readouterr().out


def test_unreachable_lyrion_leaves_the_scan_to_it(misplaced, capsys):
    closed = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = closed.server_port
    closed.server_close()
    assert misplaced.run("--apply", "--lyrion-url", f"http://127.0.0.1:{port}", "--lyrion-rescan") == 0
    assert "WARNING  Lyrion's scan cannot be asked for (connection failed)" in capsys.readouterr().out


def test_scan_kept_while_lyrion_scans(misplaced, server, lines):
    db = lyrion.Lyrion(misplaced.db, LYRION_ROOT, misplaced.lib, misplaced.state / lyrion.BACKUPS,
                       lyrion.Scan(server.url), rescans=True)
    db.note([(misplaced.lib / "vrac/1.m4a", misplaced.lib / "A/B/01 Un.m4a")])
    server.scans = [True, True]
    lyrion.rescan(db, lines.log, keep=True)
    lyrion.rescan(db, lines.log, keep=True)
    assert server.started == 0 and lines == ["LYRION   scanning: a new scan is asked for once it ends"]
    lyrion.rescan(db, lines.log, keep=True)
    lyrion.rescan(db, lines.log, keep=True)
    assert server.started == 1 and lines[-1] == "LYRION   scan asked for, the files moved"


def test_only_audio_files_moved_into_the_library_call_for_a_scan(misplaced, server):
    db = lyrion.Lyrion(misplaced.db, LYRION_ROOT, misplaced.lib, misplaced.state / lyrion.BACKUPS,
                       lyrion.Scan(server.url), rescans=True)
    db.note([(misplaced.lib / "A/cover.jpg", misplaced.lib / "A/B/folder.jpg"),
             (misplaced.lib / "A/1.m4a", misplaced.lib.parent / "elsewhere/1.m4a")])
    assert not db.moved
    db.note([(misplaced.lib.parent / "Inbox/1.m4a", misplaced.lib / "A/B/01 Un.m4a")])
    assert db.moved
