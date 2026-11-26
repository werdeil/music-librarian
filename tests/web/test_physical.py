"""The physical albums on the dashboard (web/physical.py): the filter, an album's mark, the review."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import html

import pytest

from music_librarian import scan
from music_librarian.core.log import Log
from music_librarian.media import fields
from music_librarian.physical import collection, marks
from music_librarian.physical.matching import album_key, pair_key
from music_librarian.web import physical

EN = {"Accept-Language": "en"}
RELEASES = [dict(id="11", artist="Air", artists=["Air"], title="Moon Safari", year="1998", formats=["Vinyl"]),
            dict(id="22", artist="Radiohead", artists=["Radiohead"], title="OK Computer", year="1997",
                 formats=["CD"])]


@pytest.fixture
def lib(tmp_path, make_track):
    root = tmp_path / "Music"
    for i in (1, 2):
        track = make_track(root / f"Air/Moon Safari/0{i} T.m4a", title="T", artist="Air", albumartist="Air",
                           album="Moon Safari")
        fields.write(track, "comment", ["PHYSICAL"])
        fields.write(track, "discogs_release", ["11"])
    make_track(root / "Radiohead/OKNOTOK/01 T.m4a", title="T", artist="Radiohead", albumartist="Radiohead",
               album="OK Computer OKNOTOK 1997 2017")
    make_track(root / "Björk/Debut/01 T.flac", title="T", artist="Björk", albumartist="Björk", album="Debut")
    scan.run(root, tmp_path / "state", Log())
    collection.keep(tmp_path / "state", "me", RELEASES)
    return root


@pytest.fixture
def d(lib, tmp_path, serve):
    return serve(lib, tmp_path / "state", tmp_path / "Inbox")


def page(d, url):
    return html.unescape(d.client.get(url, headers=EN).get_data(as_text=True))


def test_the_library_filters_and_badges_the_physical_albums(d, settled):
    settled(lambda: page(d, "/library"))
    text = page(d, "/library")
    assert text.count('class="physical-badge"') == 1 and "Physical <span class=\"muted\">(1)</span>" in text
    text = page(d, "/library?c=physical:yes")
    assert "Moon Safari" in text and "Debut" not in text


def test_an_album_toggles_its_mark_through_tag(d):
    text = page(d, "/library/album?folder=Air/Moon Safari")
    assert 'aria-pressed="true"' in text and "https://www.discogs.com/release/11" in text
    assert "Physical medium" not in text
    text = page(d, "/library/album?folder=Björk/Debut")
    assert 'aria-pressed="false"' in text and 'name="physical" value="1"' in text
    response = d.client.post("/library/album/physical", data={"csrf": d.token, "folder": "Björk/Debut",
                                                              "physical": "1"})
    asked = d.requests.pending()[0]
    assert (asked.command, asked.apply) == ("tag", True) and "request=" in response.location
    assert asked.options["edits"] == [["Björk/Debut/01 T.flac", "comment", "PHYSICAL", []]]
    d.requests.tick(Log())
    filed = next(d.ctx.root.rglob("*.flac"))   # tag files the album again as it writes
    assert fields.read_field(filed, "comment") == ["PHYSICAL"]
    assert not (d.state / marks.FILE).exists()


def test_the_review_asks_about_the_close_release_then_undoes(d, settled):
    settled(lambda: page(d, "/reviews/physical"))
    text = page(d, "/reviews")
    assert '<span class="tile-value">1</span> <span>albums to settle</span>' in text
    text = page(d, "/reviews/physical")
    assert "Question 1 of 1" in text and "Radiohead – OK Computer" in text and "Radiohead/OKNOTOK" in text
    key = pair_key("22", album_key("Radiohead", "OK Computer OKNOTOK 1997 2017"))
    response = d.client.post("/reviews/physical", data={"csrf": d.token, "kind": "match", "key": key,
                                                        "choice": "yes", "album": "Radiohead/OKNOTOK"})
    assert marks.load(d.state)["answers"] == {key: True}
    text = page(d, response.location)
    assert "No question left." in text and "same album" in text and "Undo this answer" in text
    d.client.post("/reviews/physical/undo", data={"csrf": d.token, "name": "answers", "key": key})
    assert marks.load(d.state)["answers"] == {}


def test_an_album_marked_asks_its_edition_then_whether_to_add_it(d, settled, monkeypatch):
    debut = d.ctx.root / "Björk/Debut/01 T.flac"
    fields.write(debut, "comment", ["PHYSICAL"])
    scan.run(d.ctx.root, d.state, Log())
    settled(lambda: page(d, "/reviews/physical"))
    key = album_key("Björk", "Debut")
    text = page(d, "/reviews/physical?from=1")
    assert "never synced" in text and "Add it to Discogs" in text
    d.client.post("/reviews/physical", data={"csrf": d.token, "kind": "conflict", "key": key,
                                             "choice": "add"})
    assert marks.load(d.state)["conflicts"] == {key: "add"}

    settled(lambda: page(d, "/reviews/physical"))
    text = page(d, "/reviews/physical?from=1")
    assert "holds no Discogs id" in text and "Editions on Discogs" in text
    monkeypatch.setattr("music_librarian.editions.discogs.fetch", lambda album: [
        dict(url="https://www.discogs.com/release/77-Bjork-Debut", title=album["title"], artist="Björk",
             date="1993", country="UK", label="One Little Indian", catalog="TPLP31", format="CD")])
    text = page(d, "/reviews/physical?from=1&editions=1")
    assert 'name="choice" value="77"' in text and "One Little Indian" in text
    d.client.post("/reviews/physical", data={"csrf": d.token, "kind": "edition", "key": key,
                                             "choice": "https://www.discogs.com/release/78-Bjork-Debut"})
    assert marks.load(d.state)["editions"] == {key: "78"}
    assert d.client.post("/reviews/physical", data={"csrf": d.token, "kind": "edition", "key": key,
                                                    "choice": "Debut"}).status_code == 400


def test_without_a_collection_the_review_says_so(d, settled):
    (d.state / collection.FILE).unlink()
    settled(lambda: page(d, "/reviews/physical"))
    assert "No physical pass has read the Discogs collection yet" in page(d, "/reviews/physical")
    assert "Albums to sync with Discogs" not in page(d, "/")
    text = page(d, "/reviews")
    assert text.count("No physical pass has read the Discogs collection yet") == 1
    assert 'name="command" value="physical"' in text


@pytest.mark.parametrize("typed, found", [
    ("77", "77"), (" 77 ", "77"), ("https://www.discogs.com/release/78-Bjork-Debut", "78"),
    ("https://www.discogs.com/fr/release/79", "79"), ("Debut", None), ("", None),
    ("https://www.discogs.com/master/80", None), ("9" * 50000 + "x", None)])
def test_a_release_id_is_read_from_what_is_typed(typed, found):
    assert physical.release_id(typed) == found
