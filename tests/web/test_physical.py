"""The physical albums on the dashboard (web/physical.py): the filter, an album's mark, the review."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import html

import pytest

from music_librarian import scan
from music_librarian.core.log import Log
from music_librarian.media import fields
from music_librarian.physical import collection, marks
from music_librarian.physical.matching import album_key, pair_key

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


def test_an_album_shows_its_mark_and_its_release(d):
    text = page(d, "/library/album?folder=Air/Moon Safari")
    assert 'class="chip chip-on"' in text and "https://www.discogs.com/release/11" in text
    assert 'name="album.physical"' not in text
    text = page(d, "/library/album?folder=Björk/Debut")
    assert "chip-on" not in text and "discogs.com" not in text


def test_the_review_asks_about_the_close_release_then_undoes(d, settled):
    settled(lambda: page(d, "/reviews/physical"))
    text = page(d, "/reviews")
    assert '<span class="tile-value">1</span> <span>releases to match</span>' in text
    text = page(d, "/reviews/physical")
    assert "Question 1 of 1" in text and "Radiohead – OK Computer" in text and "Radiohead/OKNOTOK" in text
    key = pair_key("22", album_key("Radiohead", "OK Computer OKNOTOK 1997 2017"))
    response = d.client.post("/reviews/physical", data={"csrf": d.token, "key": key, "choice": "yes",
                                                        "album": "Radiohead/OKNOTOK"})
    assert marks.load(d.state)["answers"] == {key: True}
    text = page(d, response.location)
    assert "No question left." in text and "Same album" in text and "Undo this answer" in text
    d.client.post("/reviews/physical/undo", data={"csrf": d.token, "key": key})
    assert marks.load(d.state)["answers"] == {}


def test_without_a_collection_the_review_says_so(d, settled):
    (d.state / collection.FILE).unlink()
    settled(lambda: page(d, "/reviews/physical"))
    assert "The Discogs collection has not been read yet." in page(d, "/reviews/physical")
    assert "Discogs releases to match" not in page(d, "/")
    text = page(d, "/reviews")
    assert text.count("The Discogs collection has not been read yet.") == 1
    assert 'name="command" value="physical"' in text and "Read the Discogs collection</button>" in text
    assert text.index("One artist spelled several ways") < text.index("Discogs releases close to an album")


def test_a_compilation_is_shown_as_one(d, settled, make_track):
    for i in (1, 2):
        make_track(d.ctx.root / f"Compilations/Vintage FG/0{i} T.m4a", title="T", artist=f"Artist {i}",
                   album="Vintage FG Mix", compilation=True)
    collection.keep(d.state, "me", RELEASES + [dict(id="44", artist="Various", artists=["Various"],
                                                    title="Vintage FG", year="2006", formats=["CD"])])
    scan.run(d.ctx.root, d.state, Log())
    settled(lambda: page(d, "/reviews/physical"))
    text = page(d, "/reviews/physical?from=1")
    assert "Compilation – Vintage FG Mix" in text and "Artist 1 – Vintage FG Mix" not in text


def test_the_overview_offers_the_pass_with_a_token(d, monkeypatch):
    assert 'name="command" value="physical"' not in page(d, "/")
    monkeypatch.setenv("DISCOGS_TOKEN", "t0k")
    text = page(d, "/")
    assert 'name="command" value="physical"' in text and "Mark the albums of the Discogs collection" in text
