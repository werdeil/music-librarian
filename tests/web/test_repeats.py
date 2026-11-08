"""The fields holding a value twice or several values, on the dashboard."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import html
import re

import pytest
from mutagen.mp4 import MP4

from music_librarian import scan
from music_librarian.core.log import Log

EN = {"Accept-Language": "en"}


@pytest.fixture
def d(tmp_path, make_track, serve):
    root = tmp_path / "Music"
    for n, title in ((1, ["T1", "T1"]), (2, ["T2", "T2 (Remix)"]), (3, ["T3"])):
        p = make_track(root / f"A/B/0{n} T{n}.m4a", artist="A", albumartist="A", album="B", track=n)
        f = MP4(p)
        f["\xa9nam"] = title
        f.save()
    make_track(root / "C/D/01 X.m4a", title="X", artist="C", albumartist="C", album="D", track=1)
    scan.run(root, tmp_path / "state", Log())
    return serve(root, tmp_path / "state", tmp_path / "Inbox")


def test_tile_and_list(d):
    home = d.client.get("/", headers=EN).get_data(as_text=True)
    tile = home[home.index("Tracks with repeated values") - 300:home.index("Tracks with repeated values")]
    assert 'class="tile tile-todo"' in tile and '<span class="tile-value">2' in tile
    library_page = d.client.get("/library", headers=EN).get_data(as_text=True)
    assert 'href="/library?c=repeated:yes">Repeated values <span class="muted">(1)</span></a>' in library_page
    page = d.client.get("/library/repeats", headers=EN).get_data(as_text=True)
    assert "/library/album?folder=A/B" in page and "<td>2 / 3</td>" in page and "C/D" not in page


def test_album_page_marks_the_fields(d):
    page = d.client.get("/library/album?folder=A/B", headers=EN).get_data(as_text=True)
    assert "keep only the right one." in page
    assert ('value="T1; T1" aria-label="Title" class="repeated" '
            'title="A value twice: enrich keeps it only once"') in page
    assert ('value="T2; T2 (Remix)" aria-label="Title" class="repeated" '
            'title="Several values: keep only the right one, without &#34;;&#34; in a title or an '
            'album"') in page
    assert page.count('class="repeated"') == 2
    clean = d.client.get("/library/album?folder=C/D", headers=EN).get_data(as_text=True)
    assert "repeated" not in clean


@pytest.mark.parametrize("title", ["T1; T2", "Movement 1; Largo"])
def test_a_title_with_a_separator_refused(d, title):
    page = d.client.get("/library/album?folder=A/B", headers=EN).get_data(as_text=True)
    seen = html.unescape(re.search(r'name="seen" value="([^"]*)"', page)[1])
    response = d.client.post("/library/album", headers=EN, data={
        "csrf": d.token, "folder": "A/B", "seen": seen, "t2.title": title})
    assert response.status_code == 400 and d.requests.pending() == []
    assert "title takes one value, without &#34;;&#34;" in response.get_data(as_text=True)
