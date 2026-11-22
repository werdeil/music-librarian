"""Files sent to the Inbox from the dashboard (web/uploads.py)."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import os
import re
import time
from collections import namedtuple
from types import SimpleNamespace

import pytest

from music_librarian import importer, watch
from music_librarian.core import files
from music_librarian.web import app as web
from music_librarian.web import uploads
from music_librarian.web.passes import Requests

EN = {"Accept-Language": "en"}


@pytest.fixture
def ctx(tmp_path):
    inbox = tmp_path / "Inbox"
    inbox.mkdir()
    return web.Context(root=tmp_path / "Music", inbox=inbox, state_dir=tmp_path / "state", apply=True,
                       requests=Requests({}, watch.safe))


@pytest.fixture
def d(ctx):
    client = web.create_app(ctx).test_client()
    token = re.search(r'name="csrf" value="([^"]+)"', client.get("/", headers=EN).get_data(as_text=True))[1]

    def start(size=0):
        return client.post("/inbox/uploads", data={"csrf": token, "size": size}, headers=EN)

    def put(batch, path, data=b"audio", csrf=None):
        headers = {"X-CSRF-Token": token if csrf is None else csrf, **EN}
        return client.post(f"/inbox/uploads/{batch}", query_string={"path": path}, data=data,
                           headers=headers, content_type="application/octet-stream")

    def end(batch, what="done"):
        return client.post(f"/inbox/uploads/{batch}/{what}", data={"csrf": token}, headers=EN)

    return namedtuple("Dashboard", "client token start put end")(client, token, start, put, end)


def test_home_offers_the_upload(d):
    page = d.client.get("/", headers=EN).get_data(as_text=True)
    assert "The Inbox is empty." in page and "Add to the Inbox" in page
    assert 'data-upload="/inbox/uploads"' in page and ".flac" in page and "Read-only" not in page


def test_read_only_without_apply(ctx):
    ctx.apply = False
    client = web.create_app(ctx).test_client()
    page = client.get("/", headers=EN).get_data(as_text=True)
    assert "data-upload-open disabled" in page and "Read-only: watch runs without --apply." in page
    token = re.search(r'name="csrf" value="([^"]+)"', page)[1]
    assert client.post("/inbox/uploads", data={"csrf": token}).status_code == 403
    assert not any(ctx.inbox.iterdir())


def test_no_upload_without_a_queue(tmp_path):
    (tmp_path / "Inbox").mkdir()
    client = web.create_app(web.Context(root=tmp_path, inbox=tmp_path / "Inbox", state_dir=tmp_path / "s",
                                        apply=True)).test_client()
    assert "data-upload" not in client.get("/", headers=EN).get_data(as_text=True)


def test_a_batch_reaches_the_inbox_only_once_done(d, ctx):
    response = d.start(size=30)
    assert response.status_code == 201
    batch = response.json["id"]
    for path in ("Artist - Album/CD1/01 One.flac", "Artist - Album/cover.jpg", "Loose.mp3"):
        assert d.put(batch, path).status_code == 204
    assert importer.list_items(ctx.inbox) == []
    answer = d.end(batch)
    assert answer.json == {"added": ["Artist - Album", "Loose.mp3"],
                           "message": "Added to the Inbox: 2. watch imports them within a minute."}
    assert sorted(p.name for p in ctx.inbox.iterdir()) == ["Artist - Album", "Loose.mp3"]
    assert (ctx.inbox / "Artist - Album" / "CD1" / "01 One.flac").read_bytes() == b"audio"
    assert [it.name for it in importer.list_items(ctx.inbox)] == ["Artist - Album", "Loose.mp3"]


def test_a_name_already_in_the_inbox_is_kept(d, ctx):
    (ctx.inbox / "Album").mkdir()
    (ctx.inbox / "Album" / "01 Old.flac").write_bytes(b"old")
    batch = d.start().json["id"]
    d.put(batch, "Album/01 New.flac")
    assert d.end(batch).json["added"] == ["Album (2)"]
    assert (ctx.inbox / "Album" / "01 Old.flac").read_bytes() == b"old"
    assert (ctx.inbox / "Album (2)" / "01 New.flac").is_file()


@pytest.mark.parametrize("path", ["../escape.flac", "/abs.flac", "Album/../../x.flac", ".hidden/a.flac",
                                  "Album/.a.flac", "_failed/a.flac", "_done", "Album/notes.txt",
                                  "Album/Thumbs.db", "Album\\a.flac", "Album//a.flac", ""])
def test_paths_refused(d, ctx, path):
    batch = d.start().json["id"]
    response = d.put(batch, path)
    assert response.status_code == 400 and response.json["error"].startswith("File refused:")
    assert not any(p.is_file() for p in ctx.inbox.rglob("*"))
    assert not (ctx.inbox.parent / "escape.flac").exists()


def test_a_file_sent_twice(d):
    batch = d.start().json["id"]
    d.put(batch, "A/01.flac")
    response = d.put(batch, "A/01.flac", data=b"other")
    assert response.status_code == 409 and response.json["error"] == "File sent twice: A/01.flac"


def test_a_file_without_the_token(d, ctx):
    batch = d.start().json["id"]
    assert d.put(batch, "A/01.flac", csrf="").status_code == 403
    assert d.put(batch, "A/01.flac", csrf="forged").status_code == 403
    assert not any(p.is_file() for p in ctx.inbox.rglob("*"))


def test_a_file_too_large(d, monkeypatch):
    monkeypatch.setattr(uploads, "MAX_FILE", 4)
    batch = d.start().json["id"]
    response = d.put(batch, "A/01.flac", data=b"12345")
    assert response.status_code == 413 and response.json["error"].startswith("File too large")
    assert d.put(batch, "A/02.flac", data=b"1234").status_code == 204


def test_no_room_on_the_disk(d, ctx, monkeypatch):
    monkeypatch.setattr(uploads.shutil, "disk_usage", lambda _path: SimpleNamespace(free=3 * 1024 ** 2))
    response = d.start(size=10 * 1024 ** 2)
    assert response.status_code == 507 and response.json["error"] == "Not enough room on the disk: 3 MB free."
    assert not any(ctx.inbox.iterdir())


def test_cancel_and_unknown_batches(d, ctx):
    batch = d.start().json["id"]
    d.put(batch, "A/01.flac")
    assert d.end(batch, "cancel").status_code == 204
    assert not any(ctx.inbox.iterdir())
    assert d.end(batch).status_code == 404
    assert d.put("0123456789abcdef", "A/01.flac").status_code == 404
    assert d.end("..").status_code == 404


def test_abandoned_batches_dropped(d, ctx):
    old = d.start().json["id"]
    d.put(old, "A/01.flac")
    long_ago = time.time() - (files.ABANDONED_HOURS + 1) * 3600
    for p in (ctx.inbox / f".upload-{old}").rglob("*"):
        os.utime(p, (long_ago, long_ago))
    recent = d.start().json["id"]
    d.put(recent, "B/01.flac")
    d.start()
    names = {p.name for p in ctx.inbox.iterdir()}
    assert f".upload-{old}" not in names and f".upload-{recent}" in names
