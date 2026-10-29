"""A dashboard over a library, its passes run by the queue of a real watch."""
import re
from types import SimpleNamespace

import pytest

from music_librarian import importer, organize, watch
from music_librarian.core.log import Log
from music_librarian.web import app as web
from music_librarian.web.passes import Requests, passes


@pytest.fixture
def serve():
    def dashboard(lib, state, inbox):
        inbox.mkdir(exist_ok=True)
        import_opts = importer.Options(inbox=inbox, root=lib, state_dir=state)
        organize_opts = organize.Options(root=lib, since=True, state_dir=state)
        requests = Requests(passes(import_opts, organize_opts, None, Log()), watch.safe)
        ctx = web.Context(root=lib, inbox=inbox, state_dir=state, apply=True, requests=requests)
        client = web.create_app(ctx).test_client()
        page = client.get("/", headers={"Accept-Language": "en"}).get_data(as_text=True)
        token = re.search(r'name="csrf" value="([^"]+)"', page)[1]
        return SimpleNamespace(ctx=ctx, client=client, token=token, requests=requests, state=state)
    return dashboard
