"""playcounts over a generated library and persist.db: dry run, journals, review, apply."""
# pylint: disable=redefined-outer-name  # pytest fixtures
# pylint: disable=attribute-defined-outside-init  # fixture environment objects
import json
import sqlite3

import pytest

from music_librarian import cli, playcounts
from music_librarian.core.lock import locked
from music_librarian.integrations import lyrion
from music_librarian.playcounts import decisions

LYRION_ROOT = "/music"


@pytest.fixture
def env(tmp_path, lyrion_db):
    lib, state, db = tmp_path / "Music", tmp_path / "state", lyrion_db
    database = lyrion.Lyrion(db, LYRION_ROOT, lib, state / lyrion.BACKUPS)

    class Env:
        def run(self, *a):
            return playcounts.main(["--root", str(lib), "--state-dir", str(state),
                                    "--lyrion-db", str(db), "--lyrion-root", LYRION_ROOT, *a])

        def key(self, rel):
            return lyrion.md5(database.url(lib / rel))

        def track(self, rel, **counts):
            """A file of the library, known to Lyrion and to the plugin."""
            (lib / rel).parent.mkdir(parents=True, exist_ok=True)
            (lib / rel).touch()
            self.entry(rel, **counts)

        # pylint: disable-next=too-many-arguments,too-many-positional-arguments
        def entry(self, rel, play=0, rating=None, last=0, alt=True, alt_play=None, skip=0, dps=0):
            u = database.url(lib / rel)
            with sqlite3.connect(db) as c:
                c.execute("INSERT INTO tracks_persistent (url, urlmd5, playCount, rating, lastPlayed) "
                          "VALUES (?, ?, ?, ?, ?)", (u, lyrion.md5(u), play, rating, last))
                if alt:
                    c.execute("INSERT INTO alternativeplaycount (url, urlmd5, playCount, skipCount, "
                              "dynPSval, lastPlayed) VALUES (?, ?, ?, ?, ?, ?)",
                              (u, lyrion.md5(u), play if alt_play is None else alt_play, skip, dps, last))
            c.close()

        def row(self, table, rel):
            with sqlite3.connect(db) as c:
                c.row_factory = sqlite3.Row
                r = c.execute(f"SELECT * FROM {table} WHERE urlmd5 = ?", (self.key(rel),)).fetchone()
            c.close()
            return dict(r) if r else None

        def backups(self):
            return list((state / lyrion.BACKUPS).glob("*.db"))

    e = Env()
    e.lib, e.state, e.db, e.matches = lib, state, db, state / decisions.FILE
    return e


@pytest.fixture
def renamed(env):
    """A track played 3 times since its file was renamed, and the orphan of its 7 plays before."""
    env.track("Beyoncé/Lemonade/01 Formation.m4a", play=3, last=2000, dps=30)
    env.track("Beyoncé/Lemonade/02 Sorry.m4a")
    env.entry("Beyonce/Lemonade/01 Formation.m4a", play=7, rating=80, last=1000, alt=False)
    return env


def summary(capsys):
    return " ".join(capsys.readouterr().out.split())


def test_needs_the_lyrion_database(tmp_path):
    with pytest.raises(SystemExit, match="--lyrion-db"):
        playcounts.main(["--root", str(tmp_path)])


def test_dry_run_lists_the_orphans_and_writes_nothing(renamed, capsys):
    assert cli.main(["playcounts", "--root", str(renamed.lib), "--state-dir", str(renamed.state),
                     "--lyrion-db", str(renamed.db), "--lyrion-root", LYRION_ROOT]) == 0
    out = capsys.readouterr().out
    assert ("WAITING  Beyonce/Lemonade/01 Formation.m4a  (7 plays)\n"
            "         → Beyoncé/Lemonade/01 Formation.m4a  (100 %)") in out
    assert "orphans: 1" in out and "awaiting review: 1" in out
    assert "review: music-librarian playcounts --review" in out
    assert renamed.row("tracks_persistent", "Beyonce/Lemonade/01 Formation.m4a")["playCount"] == 7
    assert not renamed.backups() and not renamed.matches.exists()


def test_entries_without_history_or_with_their_file_are_not_orphans(env, capsys):
    env.track("A/B/01 Played.m4a", play=4, alt=False)
    env.entry("A/B/02 Gone.m4a", play=0, alt=False)
    assert env.run() == 0
    assert "orphans" not in capsys.readouterr().out


def test_review_then_apply_merges_into_the_track(renamed, answers, capsys):
    answers("x", "1")
    assert renamed.run("--review") == 0
    out = capsys.readouterr().out
    assert "REVIEW   [1/1] Beyonce/Lemonade/01 Formation.m4a  (7 plays)" in out
    assert "         1  100 %  Beyoncé/Lemonade/01 Formation.m4a  (3 plays)" in out
    assert "answered: 1" in out and "orphans left: 0" in out
    assert decisions.load(renamed.matches) == (
        {renamed.key("Beyonce/Lemonade/01 Formation.m4a"): "Beyoncé/Lemonade/01 Formation.m4a"}, set())
    assert renamed.row("tracks_persistent", "Beyonce/Lemonade/01 Formation.m4a")

    assert renamed.run() == 0
    assert "MATCH Beyonce/Lemonade/01 Formation.m4a (7 plays) → Beyoncé" in summary(capsys)
    assert not renamed.backups()

    assert renamed.run("--apply") == 0
    assert "matched: 1" in summary(capsys)
    assert renamed.row("tracks_persistent", "Beyonce/Lemonade/01 Formation.m4a") is None
    tp = renamed.row("tracks_persistent", "Beyoncé/Lemonade/01 Formation.m4a")
    assert (tp["playCount"], tp["rating"], tp["lastPlayed"]) == (10, 80, 2000)
    ap = renamed.row("alternativeplaycount", "Beyoncé/Lemonade/01 Formation.m4a")
    assert (ap["playCount"], ap["lastPlayed"], ap["dynPSval"]) == (10, 2000, 74)
    assert len(renamed.backups()) == 1
    assert decisions.load(renamed.matches) == ({}, set())

    assert renamed.run("--apply") == 0
    assert len(renamed.backups()) == 1


def test_review_ignore_later_and_undo(renamed, answers, capsys):
    renamed.entry("Beyonce/Lemonade/02 Sorry.m4a", play=2, alt=False)
    answers("i", "u", "", "i")                     # ignore, undo, later, ignore the second
    assert renamed.run("--review") == 0
    out = capsys.readouterr().out
    assert "UNDONE   previous answer" in out and "orphans left: 1" in out
    assert decisions.load(renamed.matches) == ({}, {renamed.key("Beyonce/Lemonade/02 Sorry.m4a")})

    assert renamed.run("--apply") == 0
    out = summary(capsys)
    assert "ignored: 1" in out and "awaiting review: 1" in out and "WAITING" not in out
    assert renamed.row("tracks_persistent", "Beyonce/Lemonade/02 Sorry.m4a")["playCount"] == 2


def test_orphan_takes_the_url_of_a_track_lyrion_has_not_seen(env):
    (env.lib / "A/B").mkdir(parents=True)
    (env.lib / "A/B/01 New.m4a").touch()
    env.entry("A/B/01 Old.m4a", play=5, alt=False)
    decisions.update(env.matches, match={env.key("A/B/01 Old.m4a"): "A/B/01 New.m4a"})
    assert env.run("--apply") == 0
    assert env.row("tracks_persistent", "A/B/01 New.m4a")["playCount"] == 5
    assert env.row("alternativeplaycount", "A/B/01 New.m4a") is None


def test_match_whose_track_is_gone_is_skipped(renamed, capsys):
    decisions.update(renamed.matches,
                     match={renamed.key("Beyonce/Lemonade/01 Formation.m4a"): "Beyoncé/Gone.m4a"})
    assert renamed.run("--apply") == 2
    out = summary(capsys)
    assert "SKIPPED (its track is gone) Beyonce/Lemonade/01 Formation.m4a → Beyoncé/Gone.m4a" in out
    assert renamed.row("tracks_persistent", "Beyonce/Lemonade/01 Formation.m4a")["playCount"] == 7
    assert decisions.load(renamed.matches)[0]


def test_journaled_move_is_followed_without_an_answer(env, capsys):
    env.track("A/B/01 Final.m4a", play=1)
    env.entry("vrac/1.m4a", play=7, alt=False)
    journal = env.state / "journal"
    journal.mkdir(parents=True)
    moves = [("20260101-020000", "vrac/1.m4a", "A/B/01 Un.m4a"),
             ("20260201-020000", "A/B/01 Un.m4a", "A/B/01 Final.m4a")]
    for name, src, dst in moves:
        (journal / f"{name}.jsonl").write_text(
            json.dumps(dict(type="meta")) + "\n"
            + json.dumps(dict(type="move", src=str(env.lib / src), dst=str(env.lib / dst))) + "\n",
            encoding="utf-8")
    assert env.run() == 0
    assert "JOURNAL vrac/1.m4a (7 plays) → A/B/01 Final.m4a" in summary(capsys)

    assert env.run("--apply") == 0
    assert "from journals: 1" in summary(capsys)
    assert env.row("tracks_persistent", "A/B/01 Final.m4a")["playCount"] == 8
    assert env.row("alternativeplaycount", "A/B/01 Final.m4a")["playCount"] == 8


def test_ignored_orphan_is_not_followed_through_a_journal(env, capsys):
    env.track("A/B/01 Final.m4a")
    env.entry("vrac/1.m4a", play=7, alt=False)
    (env.state / "journal").mkdir(parents=True)
    (env.state / "journal/20260101-020000.jsonl").write_text(json.dumps(
        dict(type="move", src=str(env.lib / "vrac/1.m4a"), dst=str(env.lib / "A/B/01 Final.m4a"))),
        encoding="utf-8")
    decisions.update(env.matches, ignore=[env.key("vrac/1.m4a")])
    assert env.run("--apply") == 0
    assert "ignored: 1" in summary(capsys)
    assert env.row("tracks_persistent", "vrac/1.m4a")["playCount"] == 7


def test_backfill_copies_the_plugin_plays(env, capsys):
    env.track("A/B/01 Zeroed.m4a", play=0, alt_play=6, last=500)
    env.track("A/B/02 Both.m4a", play=2, alt_play=9)
    assert env.run() == 0
    assert "BACKFILL A/B/01 Zeroed.m4a (6 plays)" in summary(capsys)
    assert env.row("tracks_persistent", "A/B/01 Zeroed.m4a")["playCount"] == 0

    assert env.run("--apply", "--no-backfill") == 0
    assert "backfilled" not in summary(capsys) and not env.backups()

    assert env.run("--apply") == 0
    assert "backfilled: 1" in summary(capsys)
    tp = env.row("tracks_persistent", "A/B/01 Zeroed.m4a")
    assert (tp["playCount"], tp["lastPlayed"]) == (6, 500)
    assert env.row("tracks_persistent", "A/B/02 Both.m4a")["playCount"] == 2


def test_dpsv_is_only_computed_when_asked(env, capsys):
    env.track("A/B/01 Off.m4a", play=1, dps=50)
    env.track("A/B/02 Right.m4a", play=1, dps=13)
    assert env.run("--apply") == 0
    assert env.row("alternativeplaycount", "A/B/01 Off.m4a")["dynPSval"] == 50
    capsys.readouterr()

    assert env.run("--dpsv") == 0
    out = summary(capsys)
    assert "DPSV A/B/01 Off.m4a (50 → 13)" in out and "DPSV computed: 1" in out
    assert env.run("--apply", "--dpsv") == 0
    assert env.row("alternativeplaycount", "A/B/01 Off.m4a")["dynPSval"] == 13


def test_missing_plugin_table_is_an_error(env, capsys):
    with sqlite3.connect(env.db) as c:
        c.execute("DROP TABLE alternativeplaycount")
    c.close()
    assert env.run("--apply") == 2
    assert "ERROR" in capsys.readouterr().out and not env.backups()


def test_apply_gives_up_when_another_pass_holds_the_lock(renamed, capsys):
    with locked(renamed.state):
        assert renamed.run("--apply") == 3
    assert "LOCKED" in capsys.readouterr().out
