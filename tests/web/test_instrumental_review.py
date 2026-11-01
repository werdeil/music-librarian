"""The review of the tracks without lyrics, each answered as instrumental or not (web/reviews.py)."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import pytest

from music_librarian import scan
from music_librarian.core import runs
from music_librarian.core.log import Log
from music_librarian.lyrics import instrumentals
from music_librarian.media import tags

EN = {"Accept-Language": "en"}


@pytest.fixture
def lib(tmp_path, make_track):
    root = tmp_path / "Music"
    for n in (1, 2, 3):
        make_track(root / f"Rob Dougan/Films/0{n} T{n}.m4a", title=f"T{n}", artist="Rob Dougan",
                   albumartist="Rob Dougan", album="Films", track=n)
    tags.write_lyrics(root / "Rob Dougan/Films/01 T1.m4a", "la la")
    scan.run(root, tmp_path / "state", Log())
    return root


@pytest.fixture
def d(lib, tmp_path, serve):
    return serve(lib, tmp_path / "state", tmp_path / "Inbox")


def page(d, url):
    return d.client.get(url, headers=EN).get_data(as_text=True)


def answer(d, path, position):
    return d.client.post("/reviews/instrumental", data={"csrf": d.token, "path": path, "position": position})


def test_each_track_without_lyrics_asked_in_turn(d):
    assert "Tracks without lyrics: 2" in page(d, "/reviews")
    text = page(d, "/reviews/instrumental")
    assert "T2" in text and "Rob Dougan · Films" in text and "Question 1 of 2" in text
    assert 'href="/reviews/instrumental?from=1">Not instrumental</a>' in text
    text = page(d, "/reviews/instrumental?from=1")
    assert "T3" in text and "Question 2 of 2" in text
    assert "No question left." in page(d, "/reviews/instrumental?from=2")


def test_answer_recorded_then_undone(d):
    text = page(d, answer(d, "Rob Dougan/Films/02 T2.m4a", 0).location)
    assert instrumentals.load(d.state) == ["Rob Dougan/Films/02 T2.m4a"]
    assert "Recorded:" in text and "T3" in text and "Tracks without lyrics: 1" in text
    assert "to mark instrumental: 1" in page(d, "/reviews")
    d.client.post("/reviews/instrumental/undo", data={"csrf": d.token, "path": "Rob Dougan/Films/02 T2.m4a",
                                                      "position": 0})
    assert instrumentals.load(d.state) == []
    assert answer(d, "Rob Dougan/Films/09 T9.m4a", 0).status_code == 404


def test_answers_applied_by_a_pass(d, lib):
    answer(d, "Rob Dougan/Films/02 T2.m4a", 0)
    d.client.post("/passes", data={"csrf": d.token, "command": "instrumental"})
    d.requests.tick(Log())
    [dry] = runs.recent(d.state, 1)
    assert dry["command"] == "instrumental" and not dry["apply"]
    assert not tags.has_lyrics(lib / "Rob Dougan/Films/02 T2.m4a")
    assert instrumentals.load(d.state) == ["Rob Dougan/Films/02 T2.m4a"]
    d.client.post(f"/runs/{dry['id']}/apply", data={"csrf": d.token})
    d.requests.tick(Log())
    assert tags.read_lyrics(lib / "Rob Dougan/Films/02 T2.m4a") == "[Instrumental]"
    assert instrumentals.load(d.state) == []
    [applied] = runs.recent(d.state, 1)
    assert "journal:" in runs.log_path(d.state, applied["id"]).read_text(encoding="utf-8")
