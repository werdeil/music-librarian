"""The albums whose tracks disagree on an album field, on the dashboard."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import pytest

from music_librarian import scan
from music_librarian.core.log import Log
from music_librarian.web import library

EN = {"Accept-Language": "en"}


@pytest.fixture
def d(tmp_path, make_track, serve):
    root = tmp_path / "Music"
    for n in (1, 2, 3):
        make_track(root / f"A/B/0{n} T{n}.m4a", title=f"T{n}", artist="A",
                   albumartist=None if n == 3 else "A", album="B", track=n, disc=1,
                   disctotal=2 if n == 1 else None)
    for n in (1, 2):
        make_track(root / f"C/D/0{n} X{n}.flac", title=f"X{n}", artist="C", albumartist="C", album="D",
                   track=n, extra={"genre": "Pop" if n == 1 else "Rock"})
    for n in (1, 2):
        make_track(root / f"Compilations/E/0{n} Y{n}.flac", title=f"Y{n}", artist=f"Y{n}", album="E",
                   track=n, compilation=True, extra={"genre": "Pop" if n == 1 else "Rock"})
    scan.run(root, tmp_path / "state", Log())
    return serve(root, tmp_path / "state", tmp_path / "Inbox")


@pytest.mark.parametrize("tags, mixed", [
    ([{"album": ["B"]}, {"album": ["B"]}], ()),
    ([{"albumartist": ["A"]}, {"album artist": ["A"]}], ()),
    ([{"albumartist": ["A"]}, {}], ("albumartist",)),
    ([{"discnumber": ["1/2"]}, {"disctotal": ["2"], "discnumber": ["2"]}], ()),
    ([{"discnumber": ["1/2"]}, {"discnumber": ["2"]}], ("disctotal",)),
    ([{"date": ["2011"], "genre": ["Pop"]}, {"year": ["2012"], "genre": ["Rock"]}], ("year", "genre")),
    ([{"genre": ["Pop"], "compilation": ["1"]}, {"genre": ["Rock"]}], ("compilation",)),
])
def test_mixed_fields(tags, mixed):
    assert library.mixed_fields(tags) == mixed


def test_tile_and_list(d):
    home = d.client.get("/", headers=EN).get_data(as_text=True)
    at = home.index("Albums with inconsistent fields")
    tile = home[at - 300:at]
    assert 'class="tile tile-todo"' in tile and '<span class="tile-value">2' in tile
    assert 'href="/library/album?folder=A/B&amp;list=mixed&amp;at=0"' in tile
    page = d.client.get("/library/mixed", headers=EN).get_data(as_text=True)
    assert "Albums concerned: 2" in page and "Compilations/E" not in page
    assert page.index("folder=A/B") < page.index("folder=C/D")       # the most fields first
    assert "<td>Album artist, Discs</td>" in page and "<td>Genre</td>" in page
    assert 'href="/library/album?folder=C/D&amp;list=mixed&amp;at=1"' in page


def test_album_page_in_the_list(d):
    page = d.client.get("/library/album?folder=A/B&list=mixed&at=0", headers=EN).get_data(as_text=True)
    assert 'href="/library/mixed">' in page and "Inconsistent albums</a>" in page
    assert 'href="/library/album?folder=C/D&amp;list=mixed&amp;at=1">Next →</a>' in page
    assert 'data-spread="album.albumartist"' in page


def test_no_album_inconsistent(tmp_path, make_track, serve):
    root = tmp_path / "Music"
    make_track(root / "A/B/01 T.m4a", title="T", artist="A", albumartist="A", album="B", track=1)
    scan.run(root, tmp_path / "state", Log())
    page = serve(root, tmp_path / "state", tmp_path / "Inbox").client.get("/library/mixed", headers=EN)
    assert "The tracks of every album agree on its fields." in page.get_data(as_text=True)


def test_card_on_the_reviews_page(d):
    page = d.client.get("/reviews", headers=EN).get_data(as_text=True)
    at = page.index("<h2>Albums with inconsistent fields</h2>")
    card = page[at:]
    assert page.index("<h2>Tracks without lyrics</h2>") < at
    assert "Albums concerned: 2" in card and 'href="/library/mixed">See the list</a>' in card
    assert 'href="/library/album?folder=A/B&amp;list=mixed&amp;at=0">Answer</a>' in card


def test_card_cleared(tmp_path, make_track, serve):
    root = tmp_path / "Music"
    make_track(root / "A/B/01 T.m4a", title="T", artist="A", albumartist="A", album="B", track=1)
    scan.run(root, tmp_path / "state", Log())
    page = serve(root, tmp_path / "state", tmp_path / "Inbox").client.get("/reviews", headers=EN)
    card = page.get_data(as_text=True).split("<h2>Albums with inconsistent fields</h2>")[1]
    card = card.split("</section>")[0]
    assert "No question left." in card and "See the list" not in card
