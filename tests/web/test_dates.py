"""The albums whose tracks' years break the rule of dates, on the dashboard."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import pytest

from music_librarian import scan
from music_librarian.core.log import Log
from music_librarian.media import fields

EN = {"Accept-Language": "en"}


@pytest.fixture
def d(tmp_path, make_track, serve):
    root = tmp_path / "Music"
    for folder, years in (("A/Good", ("2011", "2011")), ("B/Whole", ("2011-05-02", "")),
                          ("C/Missing", ("2011", ""))):
        for n, year in enumerate(years, 1):
            p = make_track(root / folder / f"0{n} T{n}.m4a", title=f"T{n}", artist="A", album=folder,
                           track=n)
            if year:
                fields.write(str(p), "year", [year])
    scan.run(root, tmp_path / "state", Log())
    return serve(root, tmp_path / "state", tmp_path / "Inbox")


def test_tile_and_list(d):
    home = d.client.get("/", headers=EN).get_data(as_text=True)
    at = home.index("Albums with years to fix")
    tile = home[at - 300:at]
    assert 'class="tile tile-todo"' in tile and '<span class="tile-value">2' in tile
    assert 'href="/library/album?folder=B/Whole&amp;list=dates&amp;at=0"' in tile
    page = d.client.get("/library/dates", headers=EN).get_data(as_text=True)
    assert "Albums concerned: 2" in page and "A/Good" not in page
    assert page.index("folder=B/Whole") < page.index("folder=C/Missing")    # the most problems first
    assert "<td>year missing, not a year alone</td>" in page and "<td>year missing</td>" in page


def test_album_page_in_the_list(d):
    page = d.client.get("/library/album?folder=B/Whole&list=dates&at=0", headers=EN).get_data(as_text=True)
    assert 'href="/library/dates">' in page and "Years to fix</a>" in page
    assert 'href="/library/album?folder=C/Missing&amp;list=dates&amp;at=1">Next<svg' in page


def test_card_on_the_reviews_page(d):
    page = d.client.get("/reviews", headers=EN).get_data(as_text=True)
    card = page[page.index("<h2>Albums with years to fix</h2>"):].split("</section>")[0]
    assert '<span class="tile-value">2</span> <span>albums with years to fix</span>' in card
    assert 'href="/library/dates">See the list</a>' in card
    assert 'href="/library/album?folder=B/Whole&amp;list=dates&amp;at=0">Fix</a>' in card


def test_every_year_alone(tmp_path, make_track, serve):
    root = tmp_path / "Music"
    p = make_track(root / "A/B/01 T.m4a", title="T", artist="A", album="B", track=1)
    fields.write(str(p), "year", ["2011"])
    scan.run(root, tmp_path / "state", Log())
    client = serve(root, tmp_path / "state", tmp_path / "Inbox").client
    page = client.get("/library/dates", headers=EN).get_data(as_text=True)
    assert "Every track holds a year alone." in page
    card = client.get("/reviews", headers=EN).get_data(as_text=True).split("years to fix</h2>")[1]
    assert "No question left." in card.split("</section>")[0]


def test_switched_off(d):
    d.client.post("/reviews/dates/switch", data={"csrf": d.token, "on": "0"})
    assert "Albums with years to fix" not in d.client.get("/", headers=EN).get_data(as_text=True)
    page = d.client.get("/reviews", headers=EN).get_data(as_text=True)
    assert 'class="review-card review-off"' in page and "albums with years to fix" in page
