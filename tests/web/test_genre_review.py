"""The review of the genres the validated list does not know (web/reviews.py)."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import json
import sys

import pytest

from music_librarian import enrich, scan
from music_librarian.core.log import Log
from music_librarian.enrich import genres
from music_librarian.lyrics.matching import fold_name
from music_librarian.media import tags

EN = {"Accept-Language": "en"}


@pytest.fixture
def lib(tmp_path, make_track):
    root, state = tmp_path / "Music", tmp_path / "state"
    for n, genre in ((1, "Hip Hop"), (2, "Hip Hop"), (3, "Zydeco")):
        track = make_track(root / f"Jay-Z/Blueprint/0{n} T.m4a", title="T", artist="Jay-Z",
                           albumartist="Jay-Z", album="The Blueprint", track=n)
        tags.write_genre(track, genre)
    tags.write_genre(make_track(root / "Queen/Jazz/01 T.m4a", title="T", artist="Queen",
                                albumartist="Queen", album="Jazz", track=1), "Rock")
    state.mkdir()
    (state / "genres.txt").write_text("Rock\nRap / Hip-hop: rap\n", encoding="utf-8")
    (state / genres.LOOKUP_CACHE).write_text(json.dumps(
        {f"{fold_name('Jay-Z')}\t{fold_name('The Blueprint')}": {"genre": "Hip-Hop/Rap", "at": 1}}),
        encoding="utf-8")
    scan.run(root, state, Log())
    return root


@pytest.fixture
def d(lib, tmp_path, serve):
    return serve(lib, tmp_path / "state", tmp_path / "Inbox")


def page(d, url):
    return d.client.get(url, headers=EN).get_data(as_text=True)


def test_overview_and_question(d):
    assert "Genres awaiting an answer: 2" in page(d, "/reviews")
    text = page(d, "/reviews/genres")
    assert "Hip Hop" in text and "Tracks: 2" in text and "iTunes files these albums under" in text
    assert '<option value="Rap / Hip-hop" selected>' in text


def test_alias_then_undo(d):
    response = d.client.post("/reviews/genres", data={"csrf": d.token, "genre": "Hip Hop", "choice": "alias",
                                                      "validated": "Rap / Hip-hop", "position": "0"})
    assert genres.load(d.state / "genres.txt")[genres.key("Hip Hop")] == "Rap / Hip-hop"
    text = page(d, response.location)
    assert "Rap / Hip-hop: Hip Hop" in text and "Zydeco" in text
    d.client.post("/reviews/genres/undo", data={"csrf": d.token, "genre": "Rap / Hip-hop",
                                                "alias": "Hip Hop", "position": "0"})
    assert (d.state / "genres.txt").read_text(encoding="utf-8") == "Rock\nRap / Hip-hop: rap\n"


def test_new_genre(d):
    d.client.post("/reviews/genres", data={"csrf": d.token, "genre": "Zydeco", "choice": "new",
                                           "name": "Zydeco", "position": "1"})
    assert genres.load(d.state / "genres.txt")[genres.key("zydeco")] == "Zydeco"
    assert "Genres awaiting an answer: 1" in page(d, "/reviews")


@pytest.mark.parametrize("form", [
    {"choice": "new", "name": "rap"},                       # already a name of Rap / Hip-hop
    {"choice": "alias", "validated": "Disco"},              # not a validated genre
])
def test_answers_refused(d, form):
    response = d.client.post("/reviews/genres", data={"csrf": d.token, "genre": "Zydeco", **form})
    assert response.status_code == 400
    assert (d.state / "genres.txt").read_text(encoding="utf-8") == "Rock\nRap / Hip-hop: rap\n"


def test_without_a_list(d):
    (d.state / "genres.txt").unlink()
    assert "No validated genre list" in page(d, "/reviews/genres")
    assert d.client.post("/reviews/genres", data={"csrf": d.token, "genre": "Zydeco",
                                                  "choice": "new"}).status_code == 404


def test_genres_pass_queued_from_the_reviews(d, lib, tmp_path, monkeypatch):
    calls = tmp_path / "calls.log"
    script = f"import sys; open({str(calls)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')"
    monkeypatch.setattr(enrich, "enrichers", lambda: [("genres", [sys.executable, "-c", script])])
    assert 'value="genres"' in page(d, "/reviews")
    d.client.post("/passes", data={"csrf": d.token, "command": "genres"})
    d.requests.tick(Log())
    assert calls.read_text(encoding="utf-8").splitlines() == [f"--dry-run {lib}"]
