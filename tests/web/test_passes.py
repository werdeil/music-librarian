"""Passes asked for from the dashboard (web/passes.py and its routes): queued, then run by watch."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import re
from datetime import time

import pytest

from music_librarian import importer, organize, watch
from music_librarian.core import runs
from music_librarian.core.lock import EXIT_LOCKED, STOP
from music_librarian.core.log import Log
from music_librarian.web import app as web
from music_librarian.web.passes import DONE, QUEUED, Requests, passes

EN = {"Accept-Language": "en"}


class Fake:
    """Stands in for the passes: records each request, returns the codes given in order."""

    def __init__(self):
        self.calls, self.codes = [], []

    def __call__(self, request, log):
        self.calls.append((request.command, request.options, request.apply))
        return self.codes.pop(0) if self.codes else 0


@pytest.fixture
def fake():
    return Fake()


@pytest.fixture
def requests(fake):
    return Requests({command: fake for command in ("import", "organize", "enrich", "scan", "split",
                                                   "dedupe", "undo")}, watch.safe)


def dashboard(tmp_path, requests, apply=True):
    inbox = tmp_path / "Inbox"
    inbox.mkdir(exist_ok=True)
    ctx = web.Context(root=tmp_path / "Music", inbox=inbox, state_dir=tmp_path / "state", apply=apply,
                      organize_at=time(2, 0), requests=requests)
    client = web.create_app(ctx).test_client()
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/", headers=EN).get_data(as_text=True))[1]
    return ctx, client, token


def test_queue_runs_in_order(requests, fake):
    requests.submit("scan", {"force": True}, False)
    requests.submit("organize", {}, True)
    assert requests.wake.is_set() and [r.state for r in requests.pending()] == [QUEUED, QUEUED]
    requests.tick(Log())
    assert fake.calls == [("scan", {"force": True}, False), ("organize", {}, True)]
    assert requests.pending() == [] and not requests.wake.is_set()
    assert [r.state for r in requests.known.values()] == [DONE, DONE]


def test_stop_leaves_the_rest_queued(requests):
    first = requests.submit("scan", {}, False)

    def stop(_request, _log):
        STOP.set()
        return 0

    requests.runners["scan"] = stop
    requests.submit("import", {}, False)
    requests.tick(Log())
    assert first.state == DONE and [r.command for r in requests.pending()] == ["import"]


def test_crash_logged_not_raised(requests, capsys):
    def boom(request, log):
        raise RuntimeError("oups")

    requests.runners["scan"] = boom
    request = requests.submit("scan", {}, False)
    requests.tick(Log())
    assert request.code == 2 and "ERROR    scan interrupted" in capsys.readouterr().out


def test_pause_cut_short_by_a_request(monkeypatch):
    waits = []
    monkeypatch.setattr(STOP, "wait", waits.append)
    requests = Requests({}, watch.safe)
    requests.wake.set()
    watch.pause(30, requests.wake)
    assert waits == [watch.WAKE_CHECK]


def test_forms_need_the_page_token(tmp_path, requests):
    _, client, token = dashboard(tmp_path, requests)
    assert client.post("/passes", data={"command": "scan"}).status_code == 403
    assert client.post("/passes", data={"command": "scan", "csrf": "forged"}).status_code == 403
    assert requests.pending() == []
    response = client.post("/passes", data={"command": "scan", "force": "1", "csrf": token})
    assert response.status_code == 302 and response.location.endswith("/requests/1")
    assert [(r.command, r.options, r.apply) for r in requests.pending()] == [("scan", {"force": True}, False)]


def test_options_taken_from_the_form(tmp_path, requests):
    _, client, token = dashboard(tmp_path, requests)
    client.post("/passes", data={"command": "enrich", "rescan": "1", "no_dedupe": "1", "since": "1",
                                 "csrf": token})
    client.post("/passes", data={"command": "rm", "csrf": token})
    assert [(r.command, r.options) for r in requests.pending()] == \
        [("enrich", {"rescan": True, "no_dedupe": True})]


def test_apply_only_when_watch_applies(tmp_path, requests):
    _, client, token = dashboard(tmp_path, requests, apply=False)
    assert client.post("/passes", data={"command": "import", "apply": "1", "csrf": token}).status_code == 403
    page = client.get("/", headers=EN).get_data(as_text=True)
    assert "watch runs as a dry run" in page
    _, client, token = dashboard(tmp_path, Requests({}, watch.safe), apply=True)
    assert client.post("/passes", data={"command": "import", "apply": "1", "csrf": token}).status_code == 302


def test_no_form_without_a_queue(tmp_path):
    ctx = web.Context(root=tmp_path, inbox=tmp_path, state_dir=tmp_path / "state")
    client = web.create_app(ctx).test_client()
    assert "Run a pass" not in client.get("/", headers=EN).get_data(as_text=True)


def test_request_page_follows_the_request(tmp_path, requests):
    ctx, client, token = dashboard(tmp_path, requests)
    client.post("/passes", data={"command": "scan", "csrf": token})
    page = client.get("/requests/1", headers=EN).get_data(as_text=True)
    assert "Waiting for the current pass to end" in page and 'http-equiv="refresh"' in page
    assert "Queued <code>scan</code>" in client.get("/", headers=EN).get_data(as_text=True)

    def recorded(request, log):
        return runs.recorded(lambda: 0, log, ctx.state_dir, "scan", False, "web", request)

    requests.runners["scan"] = recorded
    requests.tick(Log())
    assert client.get("/requests/1").location.endswith("/runs/1")
    assert client.get("/requests/9").status_code == 404


def test_request_that_found_the_lock_taken(tmp_path, requests, fake):
    _, client, token = dashboard(tmp_path, requests)
    fake.codes.append(EXIT_LOCKED)
    client.post("/passes", data={"command": "organize", "csrf": token})
    requests.tick(Log())
    assert "Another pass held the lock" in client.get("/requests/1", headers=EN).get_data(as_text=True)


@pytest.fixture
def library(tmp_path, make_track):
    lib, inbox, state = tmp_path / "Music", tmp_path / "Inbox", tmp_path / "state"
    inbox.mkdir()
    make_track(lib / "vrac/1.m4a", title="Un", artist="A", album="B", track=1)
    import_opts = importer.Options(inbox=inbox, root=lib, state_dir=state)
    organize_opts = organize.Options(root=lib, since=True, state_dir=state)
    requests = Requests(passes(import_opts, organize_opts, None, Log()), watch.safe)
    ctx = web.Context(root=lib, inbox=inbox, state_dir=state, apply=True, requests=requests)
    client = web.create_app(ctx).test_client()
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/", headers=EN).get_data(as_text=True))[1]
    return lib, state, requests, client, token


def test_dry_run_then_apply_then_undo(library):
    lib, state, requests, client, token = library
    client.post("/passes", data={"command": "organize", "csrf": token})       # a full pass
    requests.tick(Log())
    [dry] = runs.recent(state)
    assert (dry["origin"], dry["apply"], dry["options"]) == ("web", False, {})
    assert (lib / "vrac/1.m4a").exists()
    page = client.get(f"/runs/{dry['id']}", headers=EN).get_data(as_text=True)
    assert "Apply this pass" in page and "Undo this pass" not in page

    assert client.post(f"/runs/{dry['id']}/apply", data={"csrf": token}).status_code == 302
    requests.tick(Log())
    applied = runs.recent(state)[0]
    assert applied["apply"] and (lib / "A/B/01 Un.m4a").exists()
    page = client.get(f"/runs/{applied['id']}", headers=EN).get_data(as_text=True)
    assert "Apply this pass" not in page
    journal = re.search(r'name="journal" value="([^"]+)"', page)[1]

    client.post("/passes", data={"command": "undo", "journal": journal, "csrf": token})
    requests.tick(Log())
    undo_dry = runs.recent(state)[0]
    assert (undo_dry["command"], undo_dry["apply"]) == ("undo", False) and (lib / "A/B/01 Un.m4a").exists()
    client.post(f"/runs/{undo_dry['id']}/apply", data={"csrf": token})
    requests.tick(Log())
    assert (lib / "vrac/1.m4a").exists() and not (lib / "A/B/01 Un.m4a").exists()


def test_undo_of_a_journal_outside_the_state_folder_refused(library, tmp_path):
    _, _, requests, client, token = library
    (tmp_path / "elsewhere.jsonl").write_text("", encoding="utf-8")
    for name in ("../elsewhere.jsonl", "missing.jsonl"):
        response = client.post("/passes", data={"command": "undo", "journal": name, "csrf": token})
        assert response.status_code == 404
    assert requests.pending() == []


def test_apply_refused_for_a_run_started_elsewhere(library):
    lib, state, _, client, token = library
    assert organize.main(["--root", str(lib), "--state-dir", str(state)]) == 0
    [cli] = runs.recent(state)
    assert "Apply this pass" not in client.get(f"/runs/{cli['id']}", headers=EN).get_data(as_text=True)
    assert client.post(f"/runs/{cli['id']}/apply", data={"csrf": token}).status_code == 404
