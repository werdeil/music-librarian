"""The library in the dashboard (web/library.py and its routes): albums, an album's page, its edits."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import html
import re
import threading

import pytest

from music_librarian import scan
from music_librarian.core import runs
from music_librarian.core.log import Log
from music_librarian.media import fields
from music_librarian.media.metadata_cache import MetadataCache
from music_librarian.web import app as web
from music_librarian.web import library, reviews

EN = {"Accept-Language": "en"}
JPEG = b"\xff\xd8\xff\xe0" + b"cover" * 10 + b"\xff\xd9"


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


def album_form(d, folder, **changes):
    """The album page's form as a browser would post it, with `changes` ({input name: value})."""
    page = d.client.get(f"/library/album?folder={folder}", headers=EN).get_data(as_text=True)
    form = {"csrf": d.token, "folder": folder}
    for name, value in re.findall(r'<input[^>]* name="([^"]+)" value="([^"]*)"', page):
        form[name] = value
    form["seen"] = html.unescape(re.search(r'name="seen" value="([^"]*)"', page)[1])
    form["album.compilation"] = re.search(r'<option value="([^"]*)"[^>]* selected', page)[1]
    form.update(changes)
    return form


def test_index_from_the_cache(lib, tmp_path):
    index = library.Index(lib, tmp_path / "state")
    assert [(a.folder, a.artist, a.title, a.tracks) for a in index.get()] == \
        [("Beyonce/4", "Beyonce", "4", 2), ("Compilations/Hits", "U2", "Hits", 2)]
    assert [a.folder for a in index.search("moby")] == ["Compilations/Hits"]
    assert [a.folder for a in index.search("beyoncé run")] == ["Beyonce/4"]


def test_reading_the_cache_leaves_its_stamp_alone(lib, tmp_path):
    state = tmp_path / "state"
    before = library.cache_stamp(state)
    with MetadataCache(state):
        library.cached_files(lib, state)
        assert library.cache_stamp(state) == before
    assert library.cache_stamp(state) == before


def test_index_leaves_out_files_gone_and_follows_the_cache(lib, tmp_path, make_track):
    index = library.Index(lib, tmp_path / "state")
    assert len(index.get()) == 2
    (lib / "Beyonce/4/01 Run the World.m4a").unlink()
    (lib / "Beyonce/4/02 Best Thing.m4a").unlink()
    make_track(lib / "Moby/Play/01 Honey.m4a", title="Honey", artist="Moby", albumartist="Moby",
               album="Play", track=1)
    scan.run(lib, tmp_path / "state", Log())
    assert [a.folder for a in index.get()] == ["Moby/Play", "Compilations/Hits"]


def slow_build(release, values):
    """A build that waits for `release`, then returns the next of `values`."""
    values = iter(values)

    def build():
        release.wait(5)
        return next(values)
    return build


def test_refreshed_never_keeps_a_request_waiting(monkeypatch):
    monkeypatch.setattr(library, "PATIENCE", 0.01)
    stamp, release = ["a"], threading.Event()
    value = library.Refreshed(lambda: ((stamp[0], None),), slow_build(release, ["first", "second"]))
    assert value.get() is None                          # first build under way
    building = value.building
    release.set()
    building.join()
    assert value.get() == "first"
    release.clear()
    stamp[0] = "b"
    assert value.get() == "first"                       # stale while the new one builds
    building = value.building
    release.set()
    building.join()
    assert value.get() == "second"


def test_refreshed_builds_once_per_stamp():
    builds = []
    value = library.Refreshed(lambda: (("a", None),), lambda: builds.append(1) or len(builds))
    assert value.get() == value.get() == 1 and len(builds) == 1


def test_pages_while_the_first_build_runs(dashboard, monkeypatch):
    monkeypatch.setattr(library, "PATIENCE", 0.01)
    release = threading.Event()
    monkeypatch.setattr(library.Index, "_build", lambda self: release.wait(5) and [])
    ctx = dashboard.ctx
    client = web.create_app(ctx).test_client()
    try:
        assert "Still being worked out" in client.get("/library", headers=EN).get_data(as_text=True)
        assert "Still being worked out" in client.get("/library/lyrics", headers=EN).get_data(as_text=True)
        tiles = client.get("/", headers=EN).get_data(as_text=True)
        assert 'class="tile tile-none"' in tiles and "being worked out" in tiles
    finally:
        release.set()


def test_library_page(dashboard):
    page = dashboard.client.get("/library", headers=EN).get_data(as_text=True)
    assert "Albums: 2" in page and "/library/album?folder=Beyonce/4" in page
    page = dashboard.client.get("/library?q=hits", headers=EN).get_data(as_text=True)
    assert "Albums: 1" in page and "Beyonce/4" not in page
    assert "No album matches" in dashboard.client.get("/library?q=zz", headers=EN).get_data(as_text=True)


def test_library_pages(dashboard, monkeypatch):
    monkeypatch.setattr(library, "PAGE", 1)
    page = dashboard.client.get("/library?page=2", headers=EN).get_data(as_text=True)
    assert "Compilations/Hits" in page and "2 / 2" in page and "Previous" in page and "Next" not in page


def test_library_before_any_scan(tmp_path):
    ctx = web.Context(root=tmp_path / "Music", inbox=tmp_path, state_dir=tmp_path / "state")
    page = web.create_app(ctx).test_client().get("/library", headers=EN).get_data(as_text=True)
    assert "The metadata cache is empty" in page


def test_album_page(dashboard):
    page = dashboard.client.get("/library/album?folder=Compilations/Hits", headers=EN).get_data(as_text=True)
    assert 'name="album.album" value="Hits"' in page
    assert 'name="album.albumartist" value=""' in page
    assert 'name="t1.artist" value="Moby"' in page
    assert '<option value="" selected>various values</option>' in page        # one flagged, one not
    assert "Preview the changes" in page
    assert 'href="/library">← Library</a>' in page and 'name="t0.disc"' not in page   # one disc


def test_disc_numbers_shown_for_several_discs(dashboard, lib, make_track, tmp_path):
    make_track(lib / "Beyonce/4/03 Bonus.m4a", title="Bonus", artist="Beyonce", albumartist="Beyonce",
               album="4", track=1, disc=2, disctotal=2)
    scan.run(lib, tmp_path / "state", Log())
    page = dashboard.client.get("/library/album?folder=Beyonce/4", headers=EN).get_data(as_text=True)
    assert 'name="t2.disc" value="2"' in page


@pytest.mark.parametrize("folder", ["../", "Beyonce/9", "Beyonce/4/01 Run the World.m4a"])
def test_album_not_in_the_library(dashboard, folder):
    response = dashboard.client.get(f"/library/album?folder={folder}", headers=EN)
    assert response.status_code == 404 and "no longer in the library" in response.get_data(as_text=True)


def test_untouched_form_asks_nothing(dashboard):
    response = dashboard.client.post("/library/album", data=album_form(dashboard, "Beyonce/4"))
    assert response.location.endswith("unchanged=1") and dashboard.requests.pending() == []


def test_album_field_edits_every_track(dashboard):
    form = album_form(dashboard, "Beyonce/4",
                      **{"album.albumartist": "Beyoncé", "t1.title": "Best Thing I Never Had"})
    assert dashboard.client.post("/library/album", data=form).status_code == 302
    [asked] = dashboard.requests.pending()
    assert (asked.command, asked.apply) == ("tag", False)
    assert asked.options["edits"] == [
        ["Beyonce/4/01 Run the World.m4a", "albumartist", "Beyoncé", ["Beyonce"]],
        ["Beyonce/4/02 Best Thing.m4a", "albumartist", "Beyoncé", ["Beyonce"]],
        ["Beyonce/4/02 Best Thing.m4a", "title", "Best Thing I Never Had", ["Best Thing"]],
    ]


def test_mixed_values_left_alone_unless_set(dashboard):
    form = album_form(dashboard, "Compilations/Hits", **{"t0.track": "01"})
    assert dashboard.client.post("/library/album", data=form).location.endswith("unchanged=1")
    form = album_form(dashboard, "Compilations/Hits", **{"album.compilation": "0"})
    dashboard.client.post("/library/album", data=form)
    [asked] = dashboard.requests.pending()
    assert [(e[1], e[2], e[3]) for e in asked.options["edits"]] == [("compilation", "0", ["1"]),
                                                                    ("compilation", "0", [])]


def test_bad_value_refused(dashboard):
    form = album_form(dashboard, "Beyonce/4", **{"t0.track": "first"})
    response = dashboard.client.post("/library/album", data=form, headers=EN)
    assert response.status_code == 400 and "Value refused" in response.get_data(as_text=True)
    assert dashboard.requests.pending() == []


def test_covers(dashboard):
    response = dashboard.client.get("/library/cover?folder=Beyonce/4")
    assert (response.mimetype, response.data) == ("image/jpeg", JPEG)
    assert dashboard.client.get("/library/cover?folder=Compilations/Hits").data == JPEG   # embedded
    assert dashboard.client.get("/library/cover?folder=../").status_code == 404


def test_placeholder_without_a_cover(dashboard, lib):
    (lib / "Bare").mkdir()
    response = dashboard.client.get("/library/cover?folder=Bare")
    assert response.status_code == 200 and response.mimetype == "image/svg+xml"


def test_edit_previewed_then_applied(dashboard, lib):
    form = album_form(dashboard, "Beyonce/4", **{"album.albumartist": "Beyoncé", "album.year": "2011"})
    dashboard.client.post("/library/album", data=form)
    dashboard.requests.tick(Log())
    [dry] = runs.recent(dashboard.state, 1)
    page = dashboard.client.get(f"/runs/{dry['id']}", headers=EN).get_data(as_text=True)
    assert 'TAG      albumartist: &#34;Beyonce&#34; → &#34;Beyoncé&#34;' in page and "Apply this pass" in page
    assert (lib / "Beyonce/4/01 Run the World.m4a").exists()

    dashboard.client.post(f"/runs/{dry['id']}/apply", data={"csrf": dashboard.token})
    dashboard.requests.tick(Log())
    moved = lib / "Beyoncé/4/01 Run the World.m4a"
    assert fields.read(moved)["albumartist"] == ["Beyoncé"] and fields.read(moved)["year"] == ["2011"]
    assert (lib / "Beyoncé/4/folder.jpg").exists() and not (lib / "Beyonce").exists()
    albums = dashboard.client.get("/library", headers=EN).get_data(as_text=True)
    assert "folder=Beyonc%C3%A9/4" in albums and "folder=Beyonce/4" not in albums


def test_edit_refused_when_the_track_changed_meanwhile(dashboard, lib, capsys):
    form = album_form(dashboard, "Beyonce/4", **{"t0.composer": "The-Dream"})
    fields.write(lib / "Beyonce/4/01 Run the World.m4a", "composer", ["Someone Else"])
    dashboard.client.post("/library/album", data=form)
    dashboard.requests.tick(Log())
    assert fields.read(lib / "Beyonce/4/01 Run the World.m4a")["composer"] == ["Someone Else"]
    assert "REFUSED  (composer changed since" in capsys.readouterr().out


def test_rebuilt_at_most_every_minute_while_a_pass_writes_the_cache(dashboard, lib, tmp_path, monkeypatch):
    reads = []
    monkeypatch.setattr(library, "cached_files", lambda *a: reads.append(a) or [])
    clock = [1000.0]
    monkeypatch.setattr(library.time, "monotonic", lambda: clock[0])
    with MetadataCache(tmp_path / "state", force=True) as cache:   # a pass rewriting the cache
        for name in ("01 Run the World.m4a", "02 Best Thing.m4a"):
            cache.snapshot(lib / "Beyonce/4" / name)
            cache.commit()
            dashboard.client.get("/library")
            clock[0] += 5
        assert len(reads) == 1
        clock[0] += library.REBUILD_EVERY
        cache.snapshot(lib / "Compilations/Hits/01 One.flac")
        cache.commit()
        dashboard.client.get("/library")
        assert len(reads) == 2
    dashboard.client.get("/library")                            # the pass ended: at once
    assert len(reads) == 3


def test_activity_counts_no_review(dashboard, monkeypatch):
    monkeypatch.setattr(reviews.Reviews, "credit", lambda *a: pytest.fail("reviews counted"))
    assert dashboard.client.get("/activity").status_code == 200
