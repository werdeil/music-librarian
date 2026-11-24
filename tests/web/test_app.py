"""The dashboard (web/): its pages, read from a state folder of synthetic runs."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import html
import os
import re
import socket
import sqlite3
from datetime import datetime, time
from types import SimpleNamespace

import pytest

from music_librarian.core import runs
from music_librarian.core.log import Log
from music_librarian.integrations import lyrion
from music_librarian.web import app as web
from music_librarian.web import history, library
from music_librarian.web.i18n import TRANSLATIONS

EN, FR = {"Accept-Language": "en"}, {"Accept-Language": "fr-FR,fr;q=0.9"}


@pytest.fixture
def ctx(tmp_path):
    inbox = tmp_path / "Inbox"
    inbox.mkdir()
    return web.Context(root=tmp_path / "Music", inbox=inbox, state_dir=tmp_path / "state",
                       organize_at=time(2, 0), enrich_at=(6, time(3, 0)))


@pytest.fixture
def client(ctx):
    return web.create_app(ctx).test_client()


def record(ctx, command, code=0, lines=("MOVE     a\n         → b",), summary=(("to move", 1),),
           apply=True):
    log = Log()

    def fn():
        for line in lines:
            log(line)
        log("\n--- Summary ---")
        for key, value in summary:
            log(f"{key:>20}: {value}")
        return code

    runs.recorded(fn, log, ctx.state_dir, command, apply)
    return runs.recent(ctx.state_dir, 1)[0]["id"]


def unfinished(ctx, command, pid, host=None):
    db = runs.connect(ctx.state_dir)
    with db:
        run_id = db.execute("INSERT INTO runs (command, origin, apply, started, host, pid) "
                            "VALUES (?, 'watch', 1, ?, ?, ?)",
                            (command, datetime.now().timestamp() - 90, host or socket.gethostname(),
                             pid)).lastrowid
    db.close()
    runs.log_path(ctx.state_dir, run_id).parent.mkdir(parents=True, exist_ok=True)
    runs.log_path(ctx.state_dir, run_id).write_text("ENRICH   (lyrics)\nNONE     A/B/01", encoding="utf-8")
    return run_id


def dead_pid():
    pid = 2 ** 22 - 1
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return pid
        except PermissionError:
            pass
        pid -= 1


def test_translations_have_the_same_keys():
    assert TRANSLATIONS["fr"].keys() == TRANSLATIONS["en"].keys()
    assert len(TRANSLATIONS["fr"]["days"]) == len(TRANSLATIONS["en"]["days"]) == 7


def test_home_without_history(client):
    page = client.get("/", headers=EN).get_data(as_text=True)
    assert 'class="activity"' not in page and "Never" in page
    assert "Every day at 02:00" in page and "Every Sunday at 03:00" in page
    assert "The Inbox is empty." in page and "Read-only (dry run)" in page


def test_no_mode_badge_when_watch_applies(client, ctx):
    ctx.apply = True
    assert "Read-only" not in client.get("/", headers=EN).get_data(as_text=True)


def test_home_in_french(client, ctx):
    record(ctx, "organize")
    page = client.get("/", headers=FR).get_data(as_text=True)
    assert '<html lang="fr">' in page
    assert "Tous les jours à 02:00" in page and "Chaque dimanche à 03:00" in page
    assert "<h2>Passages</h2>" in page and "À traiter" in page


def test_home_shows_latest_runs_problems_and_inbox(client, ctx):
    first = record(ctx, "organize", code=2)
    last = record(ctx, "organize")
    (ctx.inbox / "New Album").mkdir()
    (ctx.inbox / "_failed" / "Broken").mkdir(parents=True)
    page = client.get("/", headers=EN).get_data(as_text=True)
    assert f"/runs/{last}" in page and f"/runs/{first}" not in page        # the latest of each command
    row = page[page.index(f"/runs/{last}") - 200:page.index(f"/runs/{last}")]
    assert '<tr class="row-link">' in row
    assert 'Unchecked runs with problems over the last 7 days: 1 · <a href="/runs?problems=1">see</a>' in page
    assert "Waiting to be imported:" in page and "<li>New Album</li>" in page
    failed = page[page.index("Failed imports") - 400:page.index("Failed imports")]
    assert 'class="tile tile-todo"' in failed and '<span class="tile-value">1' in failed


def test_tiles_at_zero_and_without_a_genre_list(client):
    page = client.get("/", headers=EN).get_data(as_text=True)
    assert "Nothing to handle" in page and 'class="tile tile-' not in page and 'class="chip chip-ok"' in page
    assert 'Genres outside the list <span class="muted">(no validated list)</span></a>' in page
    assert "tile-building" not in page


def test_tiles_to_handle_first_the_rest_as_chips(client, ctx):
    for name in ("One", "Two"):
        (ctx.inbox / "_failed" / name).mkdir(parents=True)
    page = client.get("/tiles", headers=EN).get_data(as_text=True)
    assert page.count('class="tile tile-todo"') == 1 and "Failed imports" in page
    assert "Nothing to handle" not in page
    ok = page[page.index('<div class="chips">'):]
    assert ok.count('class="chip chip-ok"') == 6
    assert "Credits to split" in ok and "Tracks without lyrics" in ok and "Failed imports" not in ok


def test_header_searches_the_library_and_follows_the_overview(client):
    page = client.get("/", headers=EN).get_data(as_text=True)
    nav = page[page.index("<nav>"):page.index("</nav>")]
    assert [nav.index(name) for name in ("Overview", "Reviews", "Library", "History")] == sorted(
        nav.index(name) for name in ("Overview", "Reviews", "Library", "History"))
    assert '<form class="top-search search-pill" method="get" action="/library"' in page
    library = client.get("/library?q=daft", headers=EN).get_data(as_text=True)
    assert 'class="top-search' not in library and '<div class="search-pill" data-header-search>' in library
    assert 'name="q" form="library-search" value="daft"' in library and 'id="library-search"' in library


def test_missing_inbox(client, ctx):
    ctx.inbox.rmdir()
    assert "Inbox not found" in client.get("/", headers=EN).get_data(as_text=True)


def test_history_in_pages_of_fifty(client, ctx):
    for _ in range(history.HISTORY_PAGE + 5):
        record(ctx, "scan", code=2)
    page = client.get("/runs?problems=1", headers=EN).get_data(as_text=True)
    assert page.count('<td>scan</td>') == history.HISTORY_PAGE and "1 / 2" in page
    assert 'href="/runs?problems=1&amp;page=2"' in page
    page = client.get("/runs?problems=1&page=2", headers=EN).get_data(as_text=True)
    assert page.count('<td>scan</td>') == 5 and "Previous" in page
    assert '<h1 class="visually-hidden">History</h1>' in page


def test_environment_at_the_bottom_of_the_overview(client, ctx, monkeypatch):
    monkeypatch.setenv("LYRICS_PROVIDERS", "netease,nowhere")
    monkeypatch.setenv("GENRE_LOOKUP", "itunes")
    page = client.get("/", headers=EN).get_data(as_text=True)
    env = page[page.index("<summary>Environment</summary>"):page.index("<footer>")]
    assert str(ctx.root) in env and str(ctx.inbox) in env and str(ctx.state_dir) in env
    assert "<dd>previews only</dd>" in env and "<dt>Lyrion</dt><dd>none</dd>" in env
    assert "<dd>netease</dd>" in env and "no validated list · iTunes lookup (US)" in env
    assert str(ctx.state_dir) not in client.get("/runs", headers=EN).get_data(as_text=True)


def test_environment_names_lyrion_without_its_login(tmp_path, lyrion_db):
    inbox = tmp_path / "Inbox"
    inbox.mkdir()
    db = lyrion.Lyrion(lyrion_db, "/music", tmp_path / "Music", tmp_path / "backups",
                       scan=lyrion.Scan("http://me:secret@lms.local:9000/"), rescans=True)
    ctx = web.Context(root=tmp_path / "Music", inbox=inbox, state_dir=tmp_path / "state", lyrion=db)
    page = web.create_app(ctx).test_client().get("/", headers=EN).get_data(as_text=True)
    env = page[page.index("<summary>Environment</summary>"):]
    shown = re.findall(r'<code class="path">([^<]*)</code>', env)
    assert shown[-2:] == [str(lyrion_db), "http://lms.local:9000"] and "scan after each pass" in env
    assert "secret" not in page


def test_history_filters(client, ctx):
    ok = record(ctx, "organize")
    issues = record(ctx, "enrich", code=2)
    page = client.get("/runs?command=enrich", headers=EN).get_data(as_text=True)
    assert f"/runs/{issues}?command=enrich\"" in page and f"/runs/{ok}?" not in page
    page = client.get("/runs?problems=1", headers=EN).get_data(as_text=True)
    assert f"/runs/{issues}?problems=1\"" in page and f"/runs/{ok}?" not in page and "To check" in page
    assert 'name="problems" value="1" data-autosubmit checked' in page
    page = client.get("/runs?command=enrich", headers=EN).get_data(as_text=True)
    assert 'name="command" value="enrich"' in page and "data-autosubmit>" in page
    page = client.get("/runs?command=scan&problems=1", headers=EN).get_data(as_text=True)
    assert "No run matches" in page
    record(ctx, "organize")
    page = client.get("/runs", headers=EN).get_data(as_text=True)
    assert 'All <span class="muted">(3)</span>' in page and 'organize <span class="muted">(2)</span>' in page
    page = client.get("/runs?problems=1", headers=EN).get_data(as_text=True)          # counts follow it
    assert 'All <span class="muted">(1)</span>' in page and 'organize <span class="muted">(0)</span>' in page


def test_run_page(client, ctx):
    run_id = record(ctx, "organize", lines=("[APPLY] root: /music", "ERROR    broken.m4a",
                                            "CONFLICT /music/x\n         → /music/y", "MOVE     z"),
                    summary=(("scanned", 3), ("journal", "/state/journal/1.jsonl")))
    page = client.get(f"/runs/{run_id}", headers=EN).get_data(as_text=True)
    assert '<span class="line error">ERROR    broken.m4a</span>' in page
    assert '<span class="line error">         → /music/y</span>' in page
    assert '<span class="line">MOVE     z</span>' in page
    assert '<span class="line section">--- Summary ---</span>' in page
    assert "<dt>journal</dt><dd>/state/journal/1.jsonl</dd>" in page
    assert "data-live" not in page


BACK = ('<svg class="icon" aria-hidden="true" focusable="false">'
        '<use href="/static/icons.svg#back"></use></svg>')


@pytest.mark.parametrize("command, back", [
    ("scan", f'<a class="button" href="/runs">{BACK}Back to the history</a>'),
    ("split", f'<a class="button" href="/reviews">{BACK}Back to the reviews</a>')])
def test_ended_run_leads_with_the_way_back_then_its_outcome(client, ctx, command, back):
    run_id = record(ctx, command, code=2, apply=False)
    page = client.get(f"/runs/{run_id}", headers=EN).get_data(as_text=True)
    assert back in page and "<h2>Ended with problems</h2>" in page
    assert page.index(back) < page.index(f"<code>{command}") < page.index("Ended with problems")
    assert page.index("Ended with problems") < page.index("<dt>Mode</dt>") < page.index('id="log"')
    assert '<details class="output">' in page


def test_running_run_output_open(client, ctx):
    run_id = unfinished(ctx, "enrich", os.getpid())
    page = client.get(f"/runs/{run_id}", headers=EN).get_data(as_text=True)
    assert '<details class="output" open>' in page
    assert f'<a class="button" href="/runs">{BACK}Back to the history</a>' in page
    assert "Mark checked" not in page


def test_pass_cards_say_a_preview_comes_first(client, ctx):
    ctx.requests = SimpleNamespace(pending=lambda: [])
    page = html.unescape(client.get("/", headers=FR).get_data(as_text=True))
    assert "<code>organize</code> · aperçu d'abord, rien n'est modifié" in page
    assert "<code>scan</code> · ne modifie aucun fichier" in page


def test_trigger_shown_with_the_origin(client, ctx):
    runs.recorded(lambda: 0, Log(), ctx.state_dir, "import", True, origin="watch", trigger="start")
    run_id = runs.recent(ctx.state_dir, 1)[0]["id"]
    assert "watch · at startup" in client.get("/runs", headers=EN).get_data(as_text=True)
    assert "watch · au démarrage" in client.get(f"/runs/{run_id}", headers=FR).get_data(as_text=True)


def test_run_summary_in_french(client, ctx):
    run_id = record(ctx, "organize", summary=(("to move", 1), ("Lyrion renamed", 1), ("new key", 2)))
    page = client.get(f"/runs/{run_id}", headers=FR).get_data(as_text=True)
    assert "<dt>à déplacer</dt>" in page and "<dt>Lyrion renommés</dt>" in page
    assert "<dt>new key</dt>" in page and "<dd>appliqué</dd>" in page


def test_plain_text_log(client, ctx):
    run_id = record(ctx, "scan")
    response = client.get(f"/runs/{run_id}/log.txt")
    assert response.mimetype == "text/plain"
    assert response.get_data(as_text=True) == runs.log_path(ctx.state_dir, run_id).read_text(encoding="utf-8")


def test_unknown_run(client):
    assert client.get("/runs/42", headers=EN).status_code == 404
    assert client.get("/runs/42/log.json").status_code == 404
    assert "No such run" in client.get("/runs/42", headers=EN).get_data(as_text=True)


def test_running_run_followed_live(client, ctx):
    run_id = unfinished(ctx, "enrich", os.getpid())
    page = client.get(f"/runs/{run_id}", headers=EN).get_data(as_text=True)
    assert "Running" in page and f'data-live="/runs/{run_id}/log.json"' in page
    assert "NONE     A/B/01" not in page                     # its line is still being written
    header = client.get("/library", headers=EN).get_data(as_text=True)
    assert "enrich running" in header and f'href="/runs/{run_id}"' in header
    assert "enrich running" in client.get("/activity", headers=EN).get_data(as_text=True)
    with open(runs.log_path(ctx.state_dir, run_id), "a", encoding="utf-8") as f:
        f.write(" (no lyrics)\nERROR    lrclib\n")
    first = client.get(f"/runs/{run_id}/log.json?from=0").get_json()
    assert [line["text"] for line in first["lines"]] == ["ENRICH   (lyrics)", "NONE     A/B/01 (no lyrics)",
                                                         "ERROR    lrclib"]
    assert first["status"] == "running" and first["lines"][2]["kind"] == "error"
    assert first["duration"].endswith(" s") and 'id="run-duration"' in page
    with open(runs.log_path(ctx.state_dir, run_id), "a", encoding="utf-8") as f:
        f.write("         unreachable\n")
    more = client.get(f"/runs/{run_id}/log.json?from={first['next']}&kind=error").get_json()
    assert more["lines"] == [{"text": "         unreachable", "kind": "error"}]


def test_killed_run_shown_interrupted(client, ctx):
    run_id = unfinished(ctx, "enrich", dead_pid())
    assert runs.status(runs.get(ctx.state_dir, run_id)) == runs.INTERRUPTED
    assert "Interrupted" in client.get(f"/runs/{run_id}", headers=EN).get_data(as_text=True)


def test_run_of_another_host_taken_as_running(ctx):
    run_id = unfinished(ctx, "scan", dead_pid(), host="another-container")
    assert runs.status(runs.get(ctx.state_dir, run_id)) == runs.RUNNING


def token_of(page):
    return html.unescape(page.split('name="csrf" value="', 1)[1].split('"', 1)[0])


def test_problems_marked_checked_from_their_page(client, ctx):
    first = record(ctx, "enrich", code=2)
    second = record(ctx, "scan", code=1)
    page = client.get("/runs?problems=1&command=enrich", headers=EN).get_data(as_text=True)
    assert "Mark checked" not in page and f'href="/runs/{first}?command=enrich&amp;problems=1"' in page
    page = client.get(f"/runs/{first}?command=enrich&problems=1", headers=EN).get_data(as_text=True)
    history = f'<a class="button" href="/runs?command=enrich&amp;problems=1">{BACK}Back to the history</a>'
    assert history in page
    response = client.post(f"/runs/{first}/checked", data={"csrf": token_of(page), "command": "enrich",
                                                            "problems": "1"})
    assert response.location.endswith(f"/runs/{first}?command=enrich&problems=1")
    page = client.get("/runs?problems=1", headers=EN).get_data(as_text=True)
    assert f"/runs/{first}?problems=1\"" not in page and f"/runs/{second}?problems=1\"" in page
    assert "Unchecked runs with problems over the last 7 days: 1" in client.get("/", headers=EN).get_data(
        as_text=True)
    page = client.get("/runs", headers=EN).get_data(as_text=True)
    assert page.count('class="status status-checked"') == 1
    assert page.count('class="status status-issues"') == 0 and page.count('class="status status-failed"') == 1
    page = client.get(f"/runs/{first}", headers=EN).get_data(as_text=True)
    assert "Mark unchecked" in page
    client.post(f"/runs/{first}/checked", data={"csrf": token_of(page), "checked": "0"})
    assert runs.get(ctx.state_dir, first)["checked"] is None


def test_running_run_not_marked_checked(client, ctx):
    run_id = unfinished(ctx, "scan", os.getpid())
    assert runs.check(ctx.state_dir, [run_id]) == 0
    token = token_of(client.get(f"/runs/{record(ctx, 'scan', code=2)}", headers=EN).get_data(as_text=True))
    assert client.post(f"/runs/{run_id}/checked", data={"csrf": token}).status_code == 404


def test_stale_runs_closed_at_start(ctx):
    mine = unfinished(ctx, "enrich", os.getpid())                   # the watch killed before this one
    dead = unfinished(ctx, "organize", dead_pid())
    elsewhere = unfinished(ctx, "scan", dead_pid(), host="another-container")
    runs.close_stale(ctx.state_dir)
    assert [runs.status(runs.get(ctx.state_dir, i)) for i in (mine, dead, elsewhere)] == \
        [runs.INTERRUPTED, runs.INTERRUPTED, runs.RUNNING]


def test_history_of_version_0_1_read(ctx):
    ctx.state_dir.mkdir()
    db = sqlite3.connect(ctx.state_dir / runs.FILE)
    db.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, command TEXT, origin TEXT, "
               "apply INTEGER, started REAL, ended REAL, code INTEGER, summary TEXT)")
    db.execute("INSERT INTO runs (command, origin, apply, started, ended, code, summary) "
               "VALUES ('scan', 'cli', 0, 1, 2, 0, 'files: 3')")
    db.commit()
    db.close()
    record(ctx, "organize")
    old, new = runs.recent(ctx.state_dir)[::-1]
    assert (old["summary"], old["host"], new["host"]) == (["files: 3"], None, socket.gethostname())


def test_security_headers_and_health(client):
    response = client.get("/health")
    assert response.get_json()["status"] == "ok"
    assert response.headers["Content-Security-Policy"] == "default-src 'self'; img-src 'self' blob:"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1:8080", "[::1]:8080", "192.168.1.20", "nas:8080",
                                  "nas.local", "NAS.Lan.", "music.home.arpa:8080", "box.internal"])
def test_local_names_answered(client, host):
    assert client.get("/", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["attacker.example", "attacker.example:8080", "192.168.1.20.nip.io",
                                  "localhost.attacker.example"])
def test_other_names_refused(client, host):
    response = client.get("/", headers={"Host": host, **EN})
    assert response.status_code == 403 and response.mimetype == "text/plain"
    assert "does not answer to the name" in response.get_data(as_text=True)


def test_other_names_refused_before_any_form_or_token(tmp_path):
    dashboard = web.create_app(web.Context(root=tmp_path, inbox=tmp_path, state_dir=tmp_path / "state",
                                           requests=object())).test_client()
    response = dashboard.post("/passes", data={"command": "organize"}, headers={"Host": "attacker.example"})
    assert response.status_code == 403 and "csrf" not in response.get_data(as_text=True)


def test_names_given_answered(ctx):
    ctx.hosts = frozenset({"music.example.org"})
    client = web.create_app(ctx).test_client()
    assert client.get("/", headers={"Host": "Music.Example.org:8080"}).status_code == 200
    assert client.get("/", headers={"Host": "other.example.org"}).status_code == 403


@pytest.mark.parametrize("now, slot", [
    (datetime(2026, 10, 18, 2, 0), datetime(2026, 10, 18, 3, 0)),   # Sunday, before the slot
    (datetime(2026, 10, 18, 3, 0), datetime(2026, 10, 25, 3, 0)),   # on it: the next one
    (datetime(2026, 10, 21, 12, 0), datetime(2026, 10, 25, 3, 0)),  # the Wednesday after
])
def test_next_weekly(now, slot):
    assert web.next_weekly(now, 6, time(3, 0)) == slot


def test_next_daily():
    assert web.next_daily(datetime(2026, 10, 18, 1, 0), time(2, 0)) == datetime(2026, 10, 18, 2, 0)
    assert web.next_daily(datetime(2026, 10, 18, 2, 0), time(2, 0)) == datetime(2026, 10, 19, 2, 0)


@pytest.mark.parametrize("seconds, text", [(5, "5 s"), (125, "2 min 05 s"), (3725, "1 h 02 min")])
def test_duration(seconds, text):
    assert history.duration(seconds) == text


@pytest.mark.parametrize("build_now, started", [(False, 0), (True, 3)])   # albums, credits, genres
def test_lists_built_at_startup_only_when_asked(ctx, monkeypatch, build_now, started):
    calls = []
    monkeypatch.setattr(library.Refreshed, "start", lambda self: calls.append(self) or (None, 0))
    web.create_app(ctx, build_now=build_now)
    assert len(calls) == started


def test_every_response_says_how_long_the_app_took(client):
    timing = client.get("/").headers["Server-Timing"]
    assert "reviews;dur=" in timing and timing.split(", ")[-1].startswith("total;dur=")


def test_progress_of_a_running_pass(ctx):
    run_id = unfinished(ctx, "tag", os.getpid())
    runs.log_path(ctx.state_dir, run_id).write_text(
        "TAG      A/B/01 T.m4a\nMOVE     A/B/01 T.m4a\n         → C/B/01 T.m4a\n", encoding="utf-8")
    with web.create_app(ctx).test_request_context():
        shown = history.progress(ctx.state_dir, runs.get(ctx.state_dir, run_id))
        assert (shown["step"], shown["url"]) == ("MOVE     A/B/01 T.m4a", f"/runs/{run_id}")
        assert shown["duration"].startswith("1 min")                      # started 90 s ago
        assert history.progress(ctx.state_dir, None) is None
