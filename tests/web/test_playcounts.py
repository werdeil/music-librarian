"""Lyrion's orphan play counts on the dashboard (web/playcounts.py)."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import sqlite3
import time

import pytest

from music_librarian import scan
from music_librarian.core.log import Log
from music_librarian.integrations import lyrion
from music_librarian.playcounts import decisions

EN = {"Accept-Language": "en"}
OLD, OTHER = "Beyonce/Lemonade/01 Formation.m4a", "Beyonce/Lemonade/02 Sorry.m4a"
TRACK = "Beyoncé/Lemonade/01 Formation.m4a"


@pytest.fixture
def lib(tmp_path, make_track):
    root = tmp_path / "Music"
    make_track(root / TRACK, title="Formation", artist="Beyoncé", albumartist="Beyoncé", album="Lemonade")
    make_track(root / "Beyoncé/Lemonade/02 Sorry.m4a", title="Sorry", artist="Beyoncé",
               albumartist="Beyoncé", album="Lemonade")
    make_track(root / "Queen/Jazz/01 Mustapha.m4a", title="Mustapha", artist="Queen", albumartist="Queen",
               album="Jazz")
    scan.run(root, tmp_path / "state", Log())
    return root


@pytest.fixture
def database(lib, tmp_path, lyrion_db):
    return lyrion.Lyrion(lyrion_db, "/music", lib, tmp_path / "state" / lyrion.BACKUPS)


@pytest.fixture
def d(lib, tmp_path, serve, database):
    """A dashboard over two orphans, and a track played 3 times that Lyrion counts none for."""
    with sqlite3.connect(database.db) as c:
        for rel, plays, rating, alt in ((OLD, 7, 80, None), (OTHER, 2, None, None), (TRACK, 0, None, 3)):
            url = database.url(lib / rel)
            c.execute("INSERT INTO tracks_persistent (url, urlmd5, playCount, rating, lastPlayed) "
                      "VALUES (?, ?, ?, ?, 1000)", (url, lyrion.md5(url), plays, rating))
            if alt is not None:
                c.execute("INSERT INTO alternativeplaycount (url, urlmd5, playCount, skipCount, dynPSval) "
                          "VALUES (?, ?, ?, 0, 34)", (url, lyrion.md5(url), alt))
    c.close()
    out = serve(lib, tmp_path / "state", tmp_path / "Inbox", database)
    out.key = lambda rel: lyrion.md5(database.url(lib / rel))
    out.matches = tmp_path / "state" / decisions.FILE
    return out


def page(d, url):
    return d.client.get(url, headers=EN, follow_redirects=True).get_data(as_text=True)


def rebuilt(d):
    """Wait for the lists built in the background to follow what the test changed."""
    d.client.get("/lyrion", headers=EN)
    for _ in range(200):
        if d.client.get("/built?page=lyrion_page").get_json()["ready"]:
            return
        time.sleep(0.02)
    raise AssertionError("still building")


def post(d, url, **form):
    return d.client.post(url, data=dict(form, csrf=d.token), headers=EN)


def test_no_tab_without_lyrion(lib, tmp_path, serve):
    d = serve(lib, tmp_path / "state", tmp_path / "Inbox")
    assert 'href="/lyrion"' not in page(d, "/")
    assert "Lyrion is not set up" in page(d, "/lyrion")
    assert d.client.get(f"/lyrion/orphans/{'0' * 32}").status_code == 404


def test_tab_tile_and_list(d):
    home = page(d, "/")
    assert '<span class="count" title="Orphans awaiting an answer">2</span>' in home
    assert "Orphan Lyrion play counts" in home
    text = page(d, "/lyrion")
    assert "Waiting (2)" in text and "Settled (0)" in text and "Ignored (0)" in text
    assert text.index(OLD) < text.index(OTHER)                       # the most played first
    assert f'/lyrion/orphans/{d.key(OLD)}"' in text and "★★★★" in text
    assert "copy the plugin&#39;s plays where Lyrion counts none <span class=\"muted\">(1)</span>" in text
    assert 'value="playcounts"' in text


def test_orphan_page_suggests_the_closest_tracks(d):
    text = page(d, f"/lyrion/orphans/{d.key(OLD)}")
    assert "Orphans awaiting an answer: 2" in text and "Plays: 7" in text
    assert text.index(f'value="{TRACK}" checked') < text.index("02 Sorry.m4a") < text.index("Mustapha")
    assert '<span class="status note-likely">100 %</span>' in text
    assert f'/lyrion/orphans/{d.key(OTHER)}"' in text                  # later
    assert "Mustapha" in page(d, f"/lyrion/orphans/{d.key(OLD)}?q=queen JAZZ")
    assert "Formation.m4a\" checked" not in page(d, f"/lyrion/orphans/{d.key(OLD)}?q=queen")
    assert "No track matches." in page(d, f"/lyrion/orphans/{d.key(OLD)}?q=nothing")


def test_answers_are_written_to_the_matches_file_and_undone(d):
    answer = post(d, f"/lyrion/orphans/{d.key(OLD)}", choice="match", target=TRACK)
    assert answer.headers["Location"] == f"/lyrion/orphans/{d.key(OTHER)}?done={d.key(OLD)}"
    assert decisions.load(d.matches) == ({d.key(OLD): TRACK}, set())
    text = page(d, answer.headers["Location"])
    assert "Recorded:" in text and "Orphans awaiting an answer: 1" in text

    answer = post(d, f"/lyrion/orphans/{d.key(OTHER)}", choice="ignore")
    assert answer.headers["Location"] == f"/lyrion?done={d.key(OTHER)}"
    assert decisions.load(d.matches) == ({d.key(OLD): TRACK}, {d.key(OTHER)})
    assert "No orphan awaiting an answer." in page(d, "/lyrion?state=waiting")
    settled = page(d, "/lyrion")
    assert "Settled orphans to write: 1" in settled
    assert "Settled (1)" in settled and TRACK in settled and "from a journal" not in settled
    assert "Ignored: its play counts stay" in page(d, f"/lyrion/orphans/{d.key(OTHER)}")
    assert 'class="count" title="Orphans' not in page(d, "/")

    post(d, f"/lyrion/orphans/{d.key(OTHER)}", choice="forget")
    assert decisions.load(d.matches) == ({d.key(OLD): TRACK}, set())


@pytest.mark.parametrize("form", [dict(choice="match", target="Nobody/Nothing/01 X.m4a"),
                                  dict(choice="match", target="../persist.db"), dict(choice="other")])
def test_refused_answers(d, form):
    assert post(d, f"/lyrion/orphans/{d.key(OLD)}", **form).status_code == 400
    assert not d.matches.exists()


def test_answers_need_the_token_and_a_known_orphan(d):
    assert d.client.post(f"/lyrion/orphans/{d.key(OLD)}", data=dict(choice="ignore")).status_code == 403
    assert post(d, f"/lyrion/orphans/{'0' * 32}", choice="ignore").status_code == 404


def test_pass_asked_from_the_page_gives_the_history(d, database):
    post(d, f"/lyrion/orphans/{d.key(OLD)}", choice="match", target=TRACK)
    asked = post(d, "/passes", command="playcounts", backfill="1")
    d.requests.tick(Log())
    run = page(d, asked.headers["Location"])
    assert "MATCH" in run and "BACKFILL" in run and "Back to Lyrion" in run
    with sqlite3.connect(database.db) as c:
        assert c.execute("SELECT playCount FROM tracks_persistent WHERE urlmd5 = ?",
                         (d.key(OLD),)).fetchone() == (7,)
    c.close()

    post(d, "/passes", command="playcounts", apply="1")
    d.requests.tick(Log())
    with sqlite3.connect(database.db) as c:
        counts = c.execute("SELECT tp.playCount, tp.rating, ap.playCount FROM tracks_persistent tp JOIN "
                           "alternativeplaycount ap USING (urlmd5) WHERE urlmd5 = ?", (d.key(TRACK),))
        assert counts.fetchone() == (7, 80, 10)
        assert c.execute("SELECT COUNT(*) FROM tracks_persistent").fetchone() == (2,)
    c.close()
    assert decisions.load(d.matches) == ({}, set())
    rebuilt(d)
    assert "Waiting (1)" in page(d, "/lyrion")


def test_unreadable_database_is_said(d, database):
    with sqlite3.connect(database.db) as c:
        c.execute("DROP TABLE alternativeplaycount")
    c.close()
    rebuilt(d)
    assert "Lyrion&#39;s database is unreadable" in page(d, "/lyrion")


def test_file_gone_since_it_was_cached_leaves_an_orphan(d, lib, database):
    (lib / "Queen/Jazz/01 Mustapha.m4a").rename(lib / "Queen/Jazz/01 Mustapha (Remaster).m4a")
    with sqlite3.connect(database.db) as c:
        url = database.url(lib / "Queen/Jazz/01 Mustapha.m4a")
        c.execute("INSERT INTO tracks_persistent (url, urlmd5, playCount) VALUES (?, ?, 5)",
                  (url, lyrion.md5(url)))
    c.close()
    rebuilt(d)
    text = page(d, "/lyrion")
    assert "Waiting (3)" in text and "Queen/Jazz/01 Mustapha.m4a" in text
    assert "Mustapha.m4a\"" not in page(d, f"/lyrion/orphans/{d.key(OLD)}?q=queen")
