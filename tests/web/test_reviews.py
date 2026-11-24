"""The split and dedupe reviews on the dashboard (web/reviews.py)."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import contextlib
import html
import json
import re
import threading
import time

import pytest

from music_librarian import scan
from music_librarian.core.log import Log
from music_librarian.dedupe import aliases
from music_librarian.media import fields
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
    for track in root.rglob("*.*"):
        fields.write(str(track), "year", ["1978"])
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
    assert '<span class="tile-value">2</span> <span>credits to settle</span>' in text
    assert '<span class="tile-value">1</span> <span>groups to settle</span>' in text
    assert "/reviews/split" in text and 'value="dedupe"' in text


def test_home_counts_the_same_questions(d):
    text = page(d, "/")
    tile = lambda label: text[text.index(label) - 300:text.index(label)]
    assert '<span class="chip-count">2' in tile("Credits to split")
    assert '/reviews/split"' in tile("Credits to split")
    assert '<span class="chip-count">1' in tile("Artists to unify")
    assert "Import the Inbox" in text and "Split the credits" not in text        # four passes only
    badge = '<span class="count" title="Questions awaiting an answer">11</span>'   # 8 tracks without lyrics
    assert badge in page(d, "/library")


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


def test_split_question_shows_the_tags_today_and_each_answer(d):
    text = page(d, "/reviews/split")
    now = text[text.index("In the tags today"):text.index("Becomes")]
    assert '<span class="part whole">Brian May/Roger Taylor</span>' in now and "Composer · Tracks: 3" in now
    assert 'data-joint="/" aria-pressed="false"' in text and "Names: 2" in text and "Names: 1" in text
    assert 'name="names" value="Brian May; Roger Taylor" placeholder' in text
    assert 'class="review-choice suggested" data-joints' in text


def test_split_recorded_shows_the_names_written(d):
    response = d.client.post("/reviews/split", data={"csrf": d.token, "credit": "Brian May/Roger Taylor",
                                                     "choice": "split"})
    recorded = page(d, response.location).split('class="recorded"')[1].split("</form>")[0]
    assert '<span class="part known">Brian May</span><span class="part known">Roger Taylor</span>' in recorded


def test_dedupe_question_shows_what_each_spelling_becomes(d):
    text = page(d, "/reviews/dedupe")
    assert '<li data-name="Beyonce"><span class="part">Beyonce</span><span class="arrow">→</span>' in text
    assert '<li data-name="Beyoncé" hidden>' in text and "this group is not asked about again" in text


def test_pieces_keep_each_separator():
    assert reviews.pieces("Jon Secada, Emilio Estefan, Jr./Randall Barlow") == [
        ("", "Jon Secada"), (", ", "Emilio Estefan"), (", ", "Jr."), ("/", "Randall Barlow")]


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


@pytest.mark.parametrize("review", ["split", "dedupe"])
def test_a_review_leads_back_and_previews_its_answers(d, review):
    text = page(d, f"/reviews/{review}")
    assert '<a class="button" href="/reviews">' in text
    assert f'name="command" value="{review}"' in text and 'name="review" value="1"' in text
    rules = {"split": "/reviews/split/decisions", "dedupe": "/reviews/dedupe/aliases"}[review]
    assert f'href="{rules}?review=1"' in text
    assert f'href="/reviews/{review}"' in page(d, f"{rules}?review=1")


def test_a_pass_asked_from_a_review_leads_back_to_it(d):
    asked = d.client.post("/passes", data={"csrf": d.token, "command": "split", "review": "1"})
    assert asked.headers["Location"].endswith("?review=1")
    d.requests.tick(Log())
    run = d.client.get(asked.headers["Location"])
    assert run.headers["Location"].endswith("?review=1")
    text = page(d, run.headers["Location"])
    assert 'href="/reviews/split"' in text and "Back to the review<" in text
    assert 'name="review" value="1"' in text


def test_a_review_without_question_says_so_on_the_overview(d):
    d.client.post("/reviews/dedupe", data={"csrf": d.token, "choice": "different",
                                           "names": names_shown(page(d, "/reviews/dedupe"))})
    text = page(d, "/reviews")
    assert "groups to settle" not in text and "No question left." in text


def test_split_example_opens_its_album(d):
    assert '<a href="/library/album?folder=Queen/Jazz">' in page(d, "/reviews/split")


def test_counts_while_the_first_build_runs(d, monkeypatch):
    monkeypatch.setattr(library, "PATIENCE", 0.01)
    # the album list builds beside the blocked review build instead of waiting for it
    monkeypatch.setattr(library, "_one_build", contextlib.nullcontext())
    release = threading.Event()
    monkeypatch.setattr(reviews.Reviews, "_build", lambda self: release.wait(5) and None)
    client = web.create_app(d.ctx).test_client()
    try:
        text = client.get("/reviews", headers=EN).get_data(as_text=True)
        assert text.count(">Being worked out") == 1 and "credits to settle" not in text
        assert '<p class="muted">being worked out</p>' in text
        assert 'data-reload-when-built="/built?page=reviews_page"' in text
        assert client.get("/built?page=reviews_page").get_json()["ready"] is False
        for _ in range(100):                                # the album list does not wait on the reviews
            if client.get("/built?page=library_page").get_json()["ready"]:
                break
            time.sleep(0.05)
        assert client.get("/built?page=library_page").get_json() == {"ready": True, "percent": 100}
        assert "Being worked out" in client.get("/reviews/split", headers=EN).get_data(as_text=True)
        assert "No question left" not in client.get("/reviews/dedupe", headers=EN).get_data(as_text=True)
        token = re.search(r'name="csrf" value="([^"]+)"', text)[1]
        for kind in ("lyrics", "mixed"):
            client.post(f"/reviews/{kind}/switch", data={"csrf": token, "on": "0"})
        home = client.get("/", headers=EN).get_data(as_text=True)
        assert 'class="chip chip-building"' in home and "chip-todo" not in home
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


def test_questions_put_off_counted_round_by_round(tmp_path, monkeypatch):
    clock = iter(range(1, 100))
    monkeypatch.setattr(reviews.time, "time", lambda: float(next(clock)))
    later = reviews.Postponed(tmp_path)

    def round_of(*put):
        for key in put:
            later.put("lyrics", key)
        times = later.times("lyrics")
        return later.seen("lyrics", sorted("abc", key=lambda k: times.get(k, 0.0)))
    assert [round_of(), round_of("a"), round_of("b"), round_of("c"), round_of("a"), round_of("b", "c")] == \
        [0, 1, 2, 0, 1, 0]


def test_a_review_switched_off_leaves_every_count(d):
    badge = '<span class="count" title="Questions awaiting an answer">{n}</span>'
    text = page(d, "/reviews")
    assert badge.format(n=11) in text and 'aria-checked="true"' in text
    assert re.search(r'<section class="review-card review-todo">\s*<header>\s*<h2>Credits holding', text)
    response = d.client.post("/reviews/split/switch", data={"csrf": d.token, "on": "0"})
    assert response.location.endswith("/reviews")
    text = page(d, "/reviews")
    assert badge.format(n=9) in text and 'class="review-card review-off"' in text
    assert '<span class="tile-value">2</span> <span>credits to settle</span>' in text      # still shown
    assert 'action="/reviews/split/switch"' in text and 'name="on" value="1"' in text
    home = page(d, "/")
    off = 'title="review switched off"><span class="chip-mark" aria-hidden="true">–</span>Credits to split'
    assert off in home
    d.client.post("/reviews/split/switch", data={"csrf": d.token, "on": "1"})
    assert badge.format(n=11) in page(d, "/reviews")
    assert "chip-off" not in page(d, "/")
    assert d.client.post("/reviews/nothing/switch", data={"csrf": d.token, "on": "0"}).status_code == 404


def test_cards_with_the_most_left_first_and_those_switched_off_last(d):
    def titles():
        return re.findall(r'<section class="review-card[^"]*">\s*<header>\s*<h2>([^<]+)</h2>',
                          page(d, "/reviews"))
    assert titles()[:3] == ["Tracks without lyrics", "Credits holding several names",
                            "One artist spelled several ways"]
    d.client.post("/reviews/lyrics/switch", data={"csrf": d.token, "on": "0"})
    assert titles()[0] == "Credits holding several names" and titles()[-1] == "Tracks without lyrics"
