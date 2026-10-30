"""The dashboard (web/): its pages, read from a state folder of synthetic runs."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import os
import socket
import sqlite3
from datetime import datetime, time

import pytest

from music_librarian.core import runs
from music_librarian.core.log import Log
from music_librarian.web import app as web
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
    assert "No pass running" in page and "No run recorded yet" in page
    assert "Every day at 02:00" in page and "Every Sunday at 03:00" in page
    assert "The Inbox is empty" in page and "Read-only (dry run)" in page


def test_no_mode_badge_when_watch_applies(client, ctx):
    ctx.apply = True
    assert "Read-only" not in client.get("/", headers=EN).get_data(as_text=True)


def test_home_in_french(client, ctx):
    record(ctx, "organize")
    page = client.get("/", headers=FR).get_data(as_text=True)
    assert '<html lang="fr">' in page
    assert "Tous les jours à 02:00" in page and "Chaque dimanche à 03:00" in page
    assert "Derniers passages" in page and "ligne de commande" in page


def test_home_shows_latest_runs_and_inbox(client, ctx):
    first = record(ctx, "organize", code=2)
    last = record(ctx, "organize")
    (ctx.inbox / "New Album").mkdir()
    (ctx.inbox / "_failed" / "Broken").mkdir(parents=True)
    page = client.get("/", headers=EN).get_data(as_text=True)
    assert f"/runs/{last}" in page and f"/runs/{first}" in page
    assert "New Album" in page and "In _failed/: 1" in page


def test_missing_inbox(client, ctx):
    ctx.inbox.rmdir()
    assert "Inbox not found" in client.get("/", headers=EN).get_data(as_text=True)


def test_history_filters(client, ctx):
    ok = record(ctx, "organize")
    issues = record(ctx, "enrich", code=2)
    page = client.get("/runs?command=enrich", headers=EN).get_data(as_text=True)
    assert f"/runs/{issues}\"" in page and f"/runs/{ok}\"" not in page
    page = client.get("/runs?problems=1", headers=EN).get_data(as_text=True)
    assert f"/runs/{issues}\"" in page and f"/runs/{ok}\"" not in page and "To check" in page


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
    assert "Running now" in client.get("/", headers=EN).get_data(as_text=True)
    with open(runs.log_path(ctx.state_dir, run_id), "a", encoding="utf-8") as f:
        f.write(" (no lyrics)\nERROR    lrclib\n")
    first = client.get(f"/runs/{run_id}/log.json?from=0").get_json()
    assert [line["text"] for line in first["lines"]] == ["ENRICH   (lyrics)", "NONE     A/B/01 (no lyrics)",
                                                         "ERROR    lrclib"]
    assert first["status"] == "running" and first["lines"][2]["kind"] == "error"
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
    assert response.headers["Content-Security-Policy"] == "default-src 'self'"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


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
    assert web.duration(seconds) == text
