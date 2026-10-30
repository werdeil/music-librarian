"""The split and dedupe reviews on the dashboard (web/reviews.py)."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import html
import json
import re
import threading

import pytest

from music_librarian import scan
from music_librarian.core.log import Log
from music_librarian.dedupe import aliases
from music_librarian.split import decisions
from music_librarian.web import app as web
from music_librarian.web import library, reviews

EN = {"Accept-Language": "en"}


@pytest.fixture
def lib(tmp_path, make_track):
    root = tmp_path / "Music"
    for i in range(3):
        make_track(root / f"Queen/Jazz/0{i} T.m4a", title="T", artist="Queen", albumartist="Queen",
                   composer="Brian May/Roger Taylor")
    make_track(root / "Queen/Innuendo/01 Innuendo.m4a", title="Innuendo", artist="Queen",
               albumartist="Queen", composer="Brian May, Roger Taylor, Freddie Mercury")
    make_track(root / "Queen/Opera/01 Rhapsody.m4a", title="Rhapsody", artist="Queen",
               albumartist="Queen", composer="Freddie Mercury")
    for i in range(2):
        make_track(root / f"Beyoncé/Lemonade/0{i} T.m4a", title="T", artist="Beyoncé", albumartist="Beyoncé")
    make_track(root / "Beyonce/Single/01 T.mp3", title="T", artist="Beyonce", albumartist="Beyonce")
    scan.run(root, tmp_path / "state", Log())
    return root


@pytest.fixture
def d(lib, tmp_path, serve):
    return serve(lib, tmp_path / "state", tmp_path / "Inbox")


def page(d, url):
    return d.client.get(url, headers=EN).get_data(as_text=True)


def decided(d):
    return decisions.load(d.state / decisions.FILE, print)


def test_overview(d):
    text = page(d, "/reviews")
    assert "Credits awaiting an answer: 2" in text and "Groups awaiting an answer: 1" in text
    assert "/reviews/split" in text and 'value="dedupe"' in text


def test_home_counts_the_same_questions(d):
    text = page(d, "/")
    tile = lambda label: text[text.index(label) - 300:text.index(label)]
    assert '<span class="tile-value">2' in tile("Credits to split")
    assert '/reviews/split"' in tile("Credits to split")
    assert '<span class="tile-value">1' in tile("Artists to unify")
    assert "Import the Inbox" in text and "Split the credits" not in text        # four passes only
    assert '<span class="count" title="Questions awaiting an answer">3</span>' in page(d, "/library")


def test_split_question_then_an_answer_that_settles_another(d):
    text = page(d, "/reviews/split")
    assert "Question 1 of 2" in text and "Brian May/Roger Taylor" in text and "Tracks: 3" in text
    assert 'value="split" class="primary"' in text                      # the suggestion
    response = d.client.post("/reviews/split", data={"csrf": d.token, "credit": "Brian May/Roger Taylor",
                                                     "choice": "split", "position": "0"})
    assert decided(d) == {"brian may/roger taylor": "split"}
    text = page(d, response.location)
    assert "Recorded:" in text and "Brian May/Roger Taylor =&gt; split" in text
    assert "No question left." in text                                  # its names settle the other


def test_split_answer_with_names_then_undo(d):
    d.client.post("/reviews/split", data={"csrf": d.token, "credit": "Brian May/Roger Taylor",
                                          "choice": "names", "names": "Brian May ; Roger Taylor",
                                          "position": "0"})
    assert decided(d) == {"brian may/roger taylor": "Brian May; Roger Taylor"}
    d.client.post("/reviews/split/undo", data={"csrf": d.token, "credit": "Brian May/Roger Taylor",
                                               "before": "", "position": "0"})
    assert not decided(d)


def test_split_later_skips(d):
    text = page(d, "/reviews/split?from=1")
    assert "Question 2 of 2" in text and "Brian May, Roger Taylor, Freddie Mercury" in text
    assert "No question left." in page(d, "/reviews/split?from=2")


def test_split_answer_refused_without_a_decision(d):
    assert d.client.post("/reviews/split", data={"csrf": d.token, "credit": "X", "choice": "names",
                                                 "names": " ; "}).status_code == 400
    assert d.client.post("/reviews/split", data={"credit": "X", "choice": "split"}).status_code == 403


def names_shown(text):
    return html.unescape(re.search(r'name="names" value="([^"]*)"', text)[1])


def test_dedupe_question_then_unify(d):
    text = page(d, "/reviews/dedupe")
    assert "same name once normalized" in text and 'value="Beyoncé" checked' in text
    assert "<code>Beyoncé/Lemonade</code>" in text and "<code>Beyonce/Single</code>" in text
    response = d.client.post("/reviews/dedupe", data={"csrf": d.token, "position": "0", "choice": "unify",
                                                      "target": "Beyoncé", "names": names_shown(text)})
    assert aliases.load(d.state / aliases.FILE, print)[0] == {"beyonce": "Beyoncé"}
    text = page(d, response.location)
    assert "Beyonce =&gt; Beyoncé" in text and "No question left." in text
    entries = html.unescape(re.search(r'name="entries" value="([^"]*)"', text)[1])
    d.client.post("/reviews/dedupe/undo", data={"csrf": d.token, "entries": entries, "position": "0"})
    assert aliases.load(d.state / aliases.FILE, print) == ({}, set())


def test_dedupe_different_and_other_spelling(d):
    text = page(d, "/reviews/dedupe")
    d.client.post("/reviews/dedupe", data={"csrf": d.token, "position": "0", "choice": "different",
                                           "names": names_shown(text)})
    assert aliases.load(d.state / aliases.FILE, print)[1] == {frozenset({"beyoncé", "beyonce"})}
    (d.state / aliases.FILE).unlink()
    d.client.post("/reviews/dedupe", data={"csrf": d.token, "position": "0", "choice": "unify",
                                           "target": "Beyoncé", "other": "Beyoncé Knowles",
                                           "names": names_shown(text)})
    assert aliases.load(d.state / aliases.FILE, print)[0] == {"beyoncé": "Beyoncé Knowles",
                                                             "beyonce": "Beyoncé Knowles"}


def test_dedupe_answer_to_a_group_changed_meanwhile_asked_again(d):
    text = page(d, "/reviews/dedupe")
    aliases.update(d.state / aliases.FILE, add=[("Beyonce", "=>", "Beyoncé")])
    response = d.client.post("/reviews/dedupe", data={"csrf": d.token, "position": "0", "choice": "different",
                                                      "names": names_shown(text)})
    assert response.status_code == 302 and "done" not in response.location
    assert aliases.load(d.state / aliases.FILE, print)[1] == set()
    assert json.loads(names_shown(text)) == ["Beyoncé", "Beyonce"]


def test_apply_the_answers_as_a_dry_run(d):
    d.client.post("/reviews/split", data={"csrf": d.token, "credit": "Brian May/Roger Taylor",
                                          "choice": "split", "position": "0"})
    d.client.post("/passes", data={"csrf": d.token, "command": "split"})
    [asked] = d.requests.pending()
    assert (asked.command, asked.apply) == ("split", False)


def test_counts_while_the_first_build_runs(d, monkeypatch):
    monkeypatch.setattr(library, "PATIENCE", 0.01)
    release = threading.Event()
    monkeypatch.setattr(reviews.Reviews, "_build", lambda self: release.wait(5) and None)
    client = web.create_app(d.ctx).test_client()
    try:
        text = client.get("/reviews", headers=EN).get_data(as_text=True)
        assert "Still being worked out" in text and "Credits awaiting" not in text
        assert "Still being worked out" in client.get("/reviews/split", headers=EN).get_data(as_text=True)
        assert "No question left" not in client.get("/reviews/dedupe", headers=EN).get_data(as_text=True)
        assert client.get("/", headers=EN).status_code == 200
    finally:
        release.set()


def test_recorded_answers_pages(d):
    aliases.update(d.state / aliases.FILE, add=[("Beyonce", "=>", "Beyoncé"), ("Queen", "!=", "Queens")])
    decisions.record(d.state / decisions.FILE, "Brian May/Roger Taylor", "split")
    decisions.record(d.state / decisions.FILE, "AC/DC", "keep")
    decisions.append_pending(d.state / decisions.FILE, ["A, B"])
    text = page(d, "/reviews")
    assert "Answers (2)</a>" in text and "Decisions (3)</a>" in text
    text = page(d, "/reviews/dedupe/aliases")
    assert "<td>Beyonce</td><td>Beyoncé</td>" in text and "<td>Queen</td>" in text and "Queens" in text
    text = page(d, "/reviews/split/decisions")
    assert "<td>Brian May/Roger Taylor</td>" in text and "awaiting an answer" in text
    assert re.search(r"<td>AC/DC</td>\s*<td>Keep whole</td>", text)


def test_recorded_answers_pages_before_any_answer(d):
    assert "No line yet." in page(d, "/reviews/dedupe/aliases")
    assert "No line yet." in page(d, "/reviews/split/decisions")
