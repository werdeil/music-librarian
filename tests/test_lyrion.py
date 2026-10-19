"""Lyrion history (persist.db) carry-over for moved files (lyrion.py)."""
# pylint: disable=redefined-outer-name,unused-argument  # pytest fixtures
# pylint: disable=attribute-defined-outside-init  # fixture environment objects
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from music_librarian import librarian, lyrion
from music_librarian.librarian import EXIT_LYRION

LYRION_ROOT = "/music/Musique"
SCHEMA = """
CREATE TABLE tracks_persistent (
  id INTEGER PRIMARY KEY AUTOINCREMENT, url text NOT NULL, musicbrainz_id varchar(40),
  added int(10), rating tinyint(1), playCount int(10), lastPlayed int(10),
  urlmd5 char(32) NOT NULL default '0', lastRated int(10), prevRating tinyint(1));
CREATE TABLE alternativeplaycount (
  url text NOT NULL COLLATE NOCASE, playCount int(10), lastPlayed int(10), skipCount int(10),
  lastSkipped int(10), dynPSval int(10), remote bool default '0',
  urlmd5 char(32) not null default '0', musicbrainz_id varchar(40) COLLATE NOCASE);
CREATE UNIQUE INDEX cpurlmd5UniqueIndex ON alternativeplaycount (urlmd5);
"""


def md5(u):
    return hashlib.md5(u.encode()).hexdigest()


def url(rel):
    return lyrion.Lyrion(Path("x"), LYRION_ROOT, Path("/lib"), Path("b")).url(Path("/lib") / rel)


@pytest.fixture
def env(tmp_path):
    lib, state, db = tmp_path / "Musique", tmp_path / "state", tmp_path / "persist.db"
    lib.mkdir()
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    con.execute("PRAGMA journal_mode=WAL")  # like Lyrion
    con.close()

    class Env:
        def run(self, *a):
            return librarian.main(["--root", str(lib), "--state-dir", str(state),
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
    assert "Lyrion renommées : 1" in " ".join(capsys.readouterr().out.split())


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
    assert "Lyrion sans historique : 1" in " ".join(capsys.readouterr().out.split())
    assert env.count("tracks_persistent") == 0


def test_annexes_and_files_in_place_ignored(misplaced, make_track):
    (misplaced.lib / "vrac/folder.jpg").write_bytes(b"jpg")
    make_track(misplaced.lib / "C/D/01 Rien.m4a", title="Rien", artist="C", album="D", track=1)
    misplaced.add("C/D/01 Rien.m4a", play=1)
    assert misplaced.run("--apply") == 0
    assert misplaced.row("tracks_persistent", "C/D/01 Rien.m4a")["playCount"] == 1
    assert misplaced.count("tracks_persistent") == 2


def test_undo_brings_history_back(misplaced):
    assert misplaced.run("--apply") == 0
    [j] = (misplaced.state / "journal").glob("*.jsonl")
    assert misplaced.run("--undo", str(j), "--apply") == 0
    assert misplaced.row("tracks_persistent", "vrac/1.m4a")["playCount"] == 7
    assert misplaced.row("alternativeplaycount", "A/B/01 Un.m4a") is None


def test_lyrion_busy_files_still_moved_then_replay(misplaced, monkeypatch, capsys):
    monkeypatch.setattr(lyrion, "BUSY_TIMEOUT", 0.1)
    other = sqlite3.connect(misplaced.db, isolation_level=None)
    other.execute("BEGIN EXCLUSIVE")  # Lyrion in the middle of a write
    assert misplaced.run("--apply") == EXIT_LYRION
    other.execute("ROLLBACK")
    other.close()
    out = capsys.readouterr().out
    assert "ERREUR   report Lyrion" in out and "--lyrion-replay" in out
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
    assert librarian.main(["--root", str(misplaced.lib), "--state-dir",
                              str(misplaced.state), "--apply"]) == 0
    assert misplaced.row("tracks_persistent", "vrac/1.m4a")["playCount"] == 7


def test_lyrion_root_required(misplaced):
    with pytest.raises(SystemExit):
        librarian.main(["--root", str(misplaced.lib), "--lyrion-db", str(misplaced.db)])


def test_journal_unchanged_format(misplaced):
    assert misplaced.run("--apply") == 0
    [j] = (misplaced.state / "journal").glob("*.jsonl")
    assert [json.loads(x)["type"] for x in j.read_text().splitlines()] == ["meta", "move"]
