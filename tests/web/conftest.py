"""A dashboard over a library, its passes run by the queue of a real watch."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import re
import threading
from types import SimpleNamespace

import pytest

from music_librarian import enrich, importer, organize, scan, watch
from music_librarian.core.log import Log
from music_librarian.web import app as web
from music_librarian.web.passes import Requests, passes

JPEG = b"\xff\xd8\xff\xe0" + b"cover" * 10 + b"\xff\xd9"


@pytest.fixture
def serve():
    def dashboard(lib, state, inbox, lyrion=None):
        inbox.mkdir(exist_ok=True)
        import_opts = importer.Options(inbox=inbox, root=lib, state_dir=state, lyrion=lyrion)
        organize_opts = organize.Options(root=lib, since=True, state_dir=state)
        enrich_opts = enrich.Options(root=lib, state_dir=state, rescan=True)
        requests = Requests(passes(import_opts, organize_opts, enrich_opts, Log()), watch.safe)
        ctx = web.Context(root=lib, inbox=inbox, state_dir=state, apply=True, requests=requests,
                          lyrion=lyrion)
        client = web.create_app(ctx).test_client()
        page = client.get("/", headers={"Accept-Language": "en"}).get_data(as_text=True)
        token = re.search(r'name="csrf" value="([^"]+)"', page)[1]
        return SimpleNamespace(ctx=ctx, client=client, token=token, requests=requests, state=state)
    return dashboard


@pytest.fixture
def lib(tmp_path, make_track):
    root = tmp_path / "Music"
    for n, title in ((1, "Run the World"), (2, "Best Thing")):
        make_track(root / f"Beyonce/4/0{n} {title}.m4a", title=title, artist="Beyonce",
                   albumartist="Beyonce", album="4", track=n, composer="Terius Nash")
    (root / "Beyonce/4/folder.jpg").write_bytes(JPEG)
    make_track(root / "Compilations/Hits/01 One.flac", title="One", artist="U2", album="Hits",
               track=1, compilation=True, cover=JPEG)
    make_track(root / "Compilations/Hits/02 Two.flac", title="Two", artist="Moby", album="Hits", track=2)
    scan.run(root, tmp_path / "state", Log())
    return root


@pytest.fixture
def dashboard(lib, tmp_path, serve):
    return serve(lib, tmp_path / "state", tmp_path / "Inbox")


@pytest.fixture
def settled():
    """get(), called again once the builds its first call started have ended."""
    def again(get):
        get()
        for thread in threading.enumerate():
            if thread.name == "web-build":
                thread.join()
        return get()
    return again
