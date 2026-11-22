"""A Deezer download asked from the dashboard (web/deezer.py, the deezer pass of web/passes.py)."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import re

import pytest

from music_librarian import deezer, enrich, importer, organize, watch
from music_librarian.core.log import Log
from music_librarian.web import app as web
from music_librarian.web import deezer as web_deezer
from music_librarian.web.passes import Requests, passes

EN, FR = {"Accept-Language": "en"}, {"Accept-Language": "fr"}
SETTINGS = {"DEEZER_ARL": "dummy-arl-value", "DEEZER_PRE_KEY": "AAAAAAAAAAAAAAAA",
            "DEEZER_IV": "0001020304050607", "DEEZER_LICENSE_TOKEN": "dummy-env-token"}


@pytest.fixture
def configured(monkeypatch):
    for name, value in SETTINGS.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def ctx(tmp_path):
    (tmp_path / "Inbox").mkdir()
    return web.Context(tmp_path / "Music", tmp_path / "Inbox", tmp_path / "state", True,
                       requests=Requests({}, watch.safe))


def dashboard(ctx, headers=None):
    app = web.create_app(ctx)
    client = app.test_client()
    page = client.get("/", headers=headers or EN).get_data(as_text=True)
    return client, page, re.search(r'name="csrf" value="([^"]+)"', page)[1]


@pytest.mark.usefixtures("configured")
def test_a_button_beside_the_upload_opens_the_form(ctx):
    _client, page, _token = dashboard(ctx)
    assert "Download from Deezer" in page and 'action="/deezer"' in page
    actions = page[page.index('<div class="inbox-actions">'):]
    assert actions.index("data-upload-open") < actions.index("data-opens-dialog>") \
        < actions.index('class="pass-dialog deezer-dialog"')
    assert 'class="pass-dialog deezer-dialog"' in page and 'name="item"' in page
    _client, page, _token = dashboard(ctx, FR)
    assert "Télécharger depuis Deezer" in page


def test_nothing_shown_without_configuration(ctx):
    client, page, token = dashboard(ctx)
    assert 'action="/deezer"' not in page
    assert client.post("/deezer", data={"csrf": token, "item": "302127"}).status_code == 403
    assert not ctx.requests.pending()


@pytest.mark.usefixtures("configured")
def test_without_a_license_token_the_form_is_off(ctx, monkeypatch):
    monkeypatch.delenv("DEEZER_LICENSE_TOKEN")
    client, page, token = dashboard(ctx)
    assert "Set DEEZER_LICENSE_TOKEN to download from here." in page
    assert "data-opens-dialog disabled" in page and 'name="item"' not in page
    assert client.post("/deezer", data={"csrf": token, "item": "302127"}).status_code == 403


@pytest.mark.usefixtures("configured")
def test_a_short_link_is_explained(ctx):
    client, _page, token = dashboard(ctx)
    answer = client.post("/deezer", data={"csrf": token, "item": "https://link.deezer.com/s/abc"}, headers=EN)
    assert answer.status_code == 400
    assert "Short links are not followed" in answer.get_data(as_text=True)


@pytest.mark.usefixtures("configured")
def test_refused_without_token_or_without_apply(ctx):
    client, page, token = dashboard(ctx)
    assert client.post("/deezer", data={"item": "302127"}).status_code == 403
    ctx.apply = False
    client, page, token = dashboard(ctx)
    assert "Read-only: watch runs without --apply." in page and "data-opens-dialog disabled" in page
    assert client.post("/deezer", data={"csrf": token, "item": "302127"}).status_code == 403
    assert not ctx.requests.pending()


@pytest.mark.usefixtures("configured")
def test_a_download_is_queued_with_its_options(ctx):
    client, _page, token = dashboard(ctx)
    answer = client.post("/deezer", data={"csrf": token, "item": "https://www.deezer.com/fr/playlist/9?x=1",
                                          "kind": "album", "complete_only": "1"})
    assert answer.status_code == 302
    (asked,) = ctx.requests.pending()
    assert (asked.command, asked.apply, asked.options) == (
        "deezer", True, {"kind": "playlist", "ids": ["9"], "complete_only": True})
    assert answer.location.endswith(f"/requests/{asked.id}")
    client.post("/deezer", data={"csrf": token, "item": "302127", "kind": "album"})
    assert ctx.requests.pending()[1].options == {"kind": "album", "ids": ["302127"], "complete_only": False}


@pytest.mark.usefixtures("configured")
def test_not_an_album_or_a_playlist(ctx):
    client, _page, token = dashboard(ctx)
    answer = client.post("/deezer", data={"csrf": token, "item": "https://example.com/x"}, headers=EN)
    assert answer.status_code == 400
    assert "Not a Deezer album or playlist: https://example.com/x" in answer.get_data(as_text=True)
    assert not ctx.requests.pending()


@pytest.mark.usefixtures("configured")
def test_the_license_token_is_never_written(tmp_path, monkeypatch, capsys):
    inbox, root, state = tmp_path / "Inbox", tmp_path / "Music", tmp_path / "state"
    inbox.mkdir()
    root.mkdir()
    seen = []

    def fake_run(opts, log):
        seen.append(opts)
        log(f"[APPLY] inbox: {opts.inbox}")
        log(repr(opts))
        log("\n--- Deezer summary ---\n              albums: 1")
        return 0
    monkeypatch.setattr(deezer, "run", fake_run)
    import_opts = importer.Options(inbox=inbox, root=root, state_dir=state)
    runners = passes(import_opts, organize.Options(root=root, state_dir=state),
                     enrich.Options(root=root, state_dir=state), Log())
    requests = Requests(runners, watch.safe)
    requests.submit("deezer", {"kind": "album", "ids": ["302127"], "complete_only": False}, True,
                    license_token="dummy-license-secret")
    requests.tick(Log())
    printed = capsys.readouterr().out
    assert seen[0].license_token == "dummy-license-secret" and seen[0].inbox == inbox.resolve()
    assert "DRY" not in printed and "inbox:" in printed
    assert "dummy-license-secret" not in printed and "dummy-license-secret" not in repr(requests.known)
    written = [p for p in state.rglob("*") if p.is_file()]
    assert written
    for path in written:
        assert b"dummy-license-secret" not in path.read_bytes(), path
        assert b"dummy-arl-value" not in path.read_bytes(), path


ORIGIN = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
KEY = "dummy-extension-key"


@pytest.fixture
def extension(monkeypatch, configured):  # pylint: disable=unused-argument
    monkeypatch.setenv("DEEZER_EXTENSION_KEY", KEY)
    monkeypatch.setenv("DEEZER_EXTENSION_ORIGIN", ORIGIN)


def ext(client, method, path, key=KEY, origin=ORIGIN, headers=None, **kwargs):
    headers = {"Origin": origin, **({"Authorization": f"Bearer {key}"} if key else {}), **(headers or {})}
    return client.open(path, method=method, headers=headers, **kwargs)


@pytest.mark.usefixtures("configured")
def test_the_extension_routes_are_off_without_a_key(ctx):
    client = web.create_app(ctx).test_client()
    assert ext(client, "GET", "/deezer/ext/health").status_code == 404
    assert ext(client, "POST", "/deezer/ext/request", json={"kind": "album", "id": "1"}).status_code == 404


@pytest.mark.usefixtures("extension")
def test_the_extension_needs_its_origin_and_its_key(ctx):
    client = web.create_app(ctx).test_client()
    assert ext(client, "GET", "/deezer/ext/health", origin="https://evil.example").status_code == 403
    assert ext(client, "GET", "/deezer/ext/health", key=None).status_code == 401
    assert ext(client, "GET", "/deezer/ext/health", key="wrong").status_code == 401
    answer = ext(client, "GET", "/deezer/ext/health")
    assert answer.status_code == 200 and answer.json == {"status": "ok", "enabled": True, "apply": True}
    assert answer.headers["Access-Control-Allow-Origin"] == ORIGIN
    assert ext(client, "GET", "/deezer/ext/health", headers={"Host": "evil.example.com"}).status_code == 403


@pytest.mark.usefixtures("extension")
def test_a_preflight_is_answered_for_the_origin_only(ctx):
    client = web.create_app(ctx).test_client()
    answer = ext(client, "OPTIONS", "/deezer/ext/request", key=None)
    assert answer.status_code == 204
    assert answer.headers["Access-Control-Allow-Origin"] == ORIGIN
    assert "Authorization" in answer.headers["Access-Control-Allow-Headers"]
    elsewhere = ext(client, "OPTIONS", "/deezer/ext/request", key=None, origin="https://evil.example")
    assert elsewhere.status_code == 403


@pytest.mark.usefixtures("extension")
def test_the_extension_queues_a_download_and_follows_it(ctx):
    client = web.create_app(ctx).test_client()
    answer = ext(client, "POST", "/deezer/ext/request",
                 json={"kind": "playlist", "id": "908622995",
                       "license_token": "dummy-token-from-the-browser"})
    assert answer.status_code == 201
    (asked,) = ctx.requests.pending()
    assert answer.json == {"id": asked.id}
    assert asked.options == {"kind": "playlist", "ids": ["908622995"], "complete_only": False} and asked.apply
    assert asked.license_token == "dummy-token-from-the-browser"
    state = ext(client, "GET", f"/deezer/ext/request/{asked.id}")
    assert state.json == {"state": "queued", "code": None, "done": 0, "total": 0, "step": ""}
    assert ext(client, "GET", "/deezer/ext/request/999").status_code == 404


@pytest.mark.usefixtures("extension")
def test_the_extension_is_refused_what_the_dashboard_is(ctx):
    client = web.create_app(ctx).test_client()
    assert ext(client, "POST", "/deezer/ext/request", json={"kind": "track", "id": "1"}).status_code == 400
    assert ext(client, "POST", "/deezer/ext/request", json={"kind": "album", "id": "x"}).status_code == 400
    ctx.apply = False
    assert ext(client, "POST", "/deezer/ext/request", json={"kind": "album", "id": "1"}).status_code == 403
    assert not ctx.requests.pending()


def test_tracks_done():
    lines = ["[APPLY] inbox: /x", "ALBUM    A - B (3 tracks)", "DOWNLOAD One as FLAC", "SKIPPED  Two: no",
             "PLAYLIST Mix (1 track)", "WARNING  cover"]
    assert web_deezer.tracks_done(lines) == (2, 4)
