"""A Deezer download asked from the dashboard (web/deezer.py, the deezer pass of web/passes.py)."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import re

import pytest

from music_librarian import deezer, enrich, importer, organize, watch
from music_librarian.core.log import Log
from music_librarian.web import app as web
from music_librarian.web.passes import Requests, passes

EN, FR = {"Accept-Language": "en"}, {"Accept-Language": "fr"}
SETTINGS = {"DEEZER_ARL": "dummy-arl-value", "DEEZER_PRE_KEY": "AAAAAAAAAAAAAAAA",
            "DEEZER_IV": "0001020304050607"}


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
def test_the_form_is_shown_once_configured(ctx):
    _client, page, _token = dashboard(ctx)
    assert "Download from Deezer" in page and 'action="/deezer"' in page
    _client, page, _token = dashboard(ctx, FR)
    assert "Télécharger depuis Deezer" in page


def test_nothing_shown_without_configuration(ctx):
    client, page, token = dashboard(ctx)
    assert 'action="/deezer"' not in page
    assert client.post("/deezer", data={"csrf": token, "item": "302127"}).status_code == 403
    assert not ctx.requests.pending()


@pytest.mark.usefixtures("configured")
def test_refused_without_token_or_without_apply(ctx):
    client, page, token = dashboard(ctx)
    assert client.post("/deezer", data={"item": "302127"}).status_code == 403
    ctx.apply = False
    client, page, token = dashboard(ctx)
    assert "Read-only: watch runs without --apply." in page
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
