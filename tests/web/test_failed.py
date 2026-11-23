"""The failed imports' page of the dashboard (web/failed.py)."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import re
from datetime import date

import pytest

from music_librarian import importer, watch
from music_librarian.core import runs
from music_librarian.core.log import Log
from music_librarian.web import app as web
from music_librarian.web.passes import Requests

EN = {"Accept-Language": "en"}


@pytest.fixture
def ctx(tmp_path):
    inbox = tmp_path / "Inbox"
    (inbox / importer.ERRORS).mkdir(parents=True)
    return web.Context(root=tmp_path / "Music", inbox=inbox, state_dir=tmp_path / "state", apply=True,
                       requests=Requests({}, watch.safe))


@pytest.fixture
def client(ctx):
    return web.create_app(ctx).test_client()


@pytest.fixture
def post(client):
    page = client.get("/", headers=EN).get_data(as_text=True)
    token = re.search(r'name="csrf" value="([^"]+)"', page)[1]

    def send(action, name):
        return client.post(f"/inbox/failed/{action}", data={"csrf": token, "name": name}, headers=EN)
    return send


def failed_import(ctx, name, reason, folder=True):
    """An import run that sends the Inbox item `name` to _failed/, its id."""
    path = ctx.inbox / name
    if folder:
        path.mkdir()
    else:
        path.write_bytes(b"audio")
    opts = importer.Options(inbox=ctx.inbox, root=ctx.root, apply=True, state_dir=ctx.state_dir)
    log = Log()

    def fn():
        importer.to_errors(opts, importer.Item(path), reason, log)
        return 2

    runs.recorded(fn, log, ctx.state_dir, "import", True)
    return runs.recent(ctx.state_dir, 1)[0]["id"]


def test_the_tile_opens_the_page(client, ctx):
    failed_import(ctx, "Broken", "unsupported audio format: .wav")
    tiles = client.get("/tiles", headers=EN).get_data(as_text=True)
    assert 'href="/inbox/failed"' in tiles and "Failed imports" in tiles


def test_each_item_with_its_reason_and_run(client, ctx):
    first = failed_import(ctx, "Broken", "unsupported audio format: .wav")
    second = failed_import(ctx, "Broken", "refused by organize (see above)")
    failed_import(ctx, "song.mp3", "no title", folder=False)
    (ctx.inbox / importer.ERRORS / "Copied by hand").mkdir()
    (ctx.inbox / importer.ERRORS / ".DS_Store").write_bytes(b"")
    page = client.get("/inbox/failed", headers=EN).get_data(as_text=True)
    assert '<span class="tile-value">4</span>' in page and ".DS_Store" not in page
    assert f'unsupported audio format: .wav · <a href="/runs/{first}">see</a>' in page
    assert f'refused by organize (see above) · <a href="/runs/{second}">see</a>' in page
    assert "file · no title" in page and "Copied by hand</span>" in page
    assert page.count("Retry</button>") == 4
    home = client.get("/tiles", headers=EN).get_data(as_text=True)
    assert '<span class="tile-value">4</span>' in home


def test_retry_puts_it_back_in_the_inbox(ctx, post):
    failed_import(ctx, "Broken", "no title")
    (ctx.inbox / "Broken").mkdir()
    response = post("retry", "Broken")
    assert response.status_code == 302 and response.location.endswith("retried=Broken+(2)")
    assert (ctx.inbox / "Broken (2)").is_dir() and not any((ctx.inbox / importer.ERRORS).iterdir())
    assert [it.name for it in importer.list_items(ctx.inbox)] == ["Broken", "Broken (2)"]


def test_dismiss_sets_it_aside_in_done(ctx, post, client):
    failed_import(ctx, "song.mp3", "no title", folder=False)
    response = post("dismiss", "song.mp3")
    assert response.status_code == 302
    assert (ctx.inbox / importer.DONE / date.today().isoformat() / "song.mp3").is_file()
    page = client.get(response.location, headers=EN).get_data(as_text=True)
    assert "Set aside in _done/: song.mp3." in page and "No failed import." in page


def test_only_an_item_of_failed(ctx, post, tmp_path):
    (tmp_path / "outside").mkdir()
    (ctx.inbox / "Waiting").mkdir()
    for name in ("../../outside", "../Waiting", "", "Missing"):
        assert post("retry", name).status_code == 404
        assert post("dismiss", name).status_code == 404
    assert (tmp_path / "outside").is_dir() and (ctx.inbox / "Waiting").is_dir()


def test_read_only_without_apply(ctx, client, post):
    failed_import(ctx, "Broken", "no title")
    ctx.apply = False
    page = client.get("/inbox/failed", headers=EN).get_data(as_text=True)
    assert "Read-only: watch runs without --apply." in page and "disabled>" in page
    assert post("retry", "Broken").status_code == 403 and post("dismiss", "Broken").status_code == 403
    assert (ctx.inbox / importer.ERRORS / "Broken").is_dir()


def test_the_dry_run_of_an_import_gives_no_reason(client, ctx):
    (ctx.inbox / importer.ERRORS / "Broken").mkdir()
    log = Log()

    def fn():
        log(f"ERROR    Broken: would go to {importer.ERRORS}/ (see above)")
        log(f"         → {ctx.inbox / importer.ERRORS / 'Broken'}")
        return 2

    runs.recorded(fn, log, ctx.state_dir, "import", False)
    page = client.get("/inbox/failed", headers=EN).get_data(as_text=True)
    assert "would go to" not in page and "folder</span>" in page
