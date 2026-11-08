"""The library in the dashboard (web/library.py and its routes): albums, an album's page, its edits."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import html
import io
import json
import os
import re
import struct
import threading
import time

import pytest

from music_librarian import scan
from music_librarian.core.log import Log
from music_librarian.media import artwork, fields, tags
from music_librarian.media.metadata_cache import MetadataCache
from music_librarian.web import app as web
from music_librarian.web import library, passes, query, reviews

EN = {"Accept-Language": "en"}
BACK = ('<svg class="icon" aria-hidden="true" focusable="false">'
        '<use href="/static/icons.svg#back"></use></svg>')
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


def settled(get):
    """get(), called again once the builds its first call started have ended."""
    get()
    for thread in threading.enumerate():
        if thread.name == "web-build":
            thread.join()
    return get()


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
    tracks = index.catalog().tracks
    assert [(t.folder, t.name) for t in tracks] == [
        ("Beyonce/4", "01 Run the World.m4a"), ("Beyonce/4", "02 Best Thing.m4a"),
        ("Compilations/Hits", "01 One.flac"), ("Compilations/Hits", "02 Two.flac")]
    one = tracks[2]
    assert one.flags == {"cover", "compilation"} and one.format == "flac" and one.first("artist") == "U2"


def found(index, words="", search=query.Search(), sort="artist", view="albums"):
    return library.find(index.catalog(), words, search, sort, view)


def test_find_by_words_track_by_track(lib, tmp_path):
    index = library.Index(lib, tmp_path / "state")
    hits = found(index, "moby")
    assert [a.folder for a in hits.albums] == ["Compilations/Hits"]
    assert hits.matched == {"Compilations/Hits": 1}
    assert [t.name for t in found(index, "beyoncé run").tracks] == ["01 Run the World.m4a"]
    assert not found(index).matched and len(found(index).albums) == 2


def test_find_by_conditions(lib, tmp_path):
    index = library.Index(lib, tmp_path / "state")
    no_composer = query.Search((query.parse("composer:empty"),))
    assert [t.name for t in found(index, search=no_composer).tracks] == ["01 One.flac", "02 Two.flac"]
    either = query.Search((query.parse("artist:is:moby"), query.parse("title:starts:best")), either=True)
    assert [t.name for t in found(index, search=either).tracks] == ["02 Best Thing.m4a", "02 Two.flac"]
    counts = found(index, view="tracks").counts
    assert counts["lyrics"] == 4 and counts["compilations"] == 1 and counts["cover"] == 3
    assert found(index).counts["compilations"] == 1 and found(index, "beyonce").counts["composer"] == 0
    by_title = found(index, search=no_composer, sort="title")
    assert [a.folder for a in by_title.albums] == ["Compilations/Hits"]


def test_reading_the_cache_leaves_its_stamp_alone(lib, tmp_path):
    state = tmp_path / "state"
    before = library.cache_stamp(state)
    with MetadataCache(state):
        library.cached_files(lib, state)
        assert library.cache_stamp(state) == before
    assert library.cache_stamp(state) == before


def test_no_cache_created_by_the_dashboard(tmp_path, serve):
    d = serve(tmp_path / "Music", tmp_path / "state", tmp_path / "Inbox")
    settled(lambda: d.client.get("/", headers=EN))
    assert not (tmp_path / "state" / "metadata.db").exists()


def test_index_leaves_out_files_gone_and_follows_the_cache(lib, tmp_path, make_track):
    index = library.Index(lib, tmp_path / "state")
    assert len(index.get()) == 2
    (lib / "Beyonce/4/01 Run the World.m4a").unlink()
    (lib / "Beyonce/4/02 Best Thing.m4a").unlink()
    make_track(lib / "Moby/Play/01 Honey.m4a", title="Honey", artist="Moby", albumartist="Moby",
               album="Play", track=1)
    scan.run(lib, tmp_path / "state", Log())
    assert [a.folder for a in settled(index.get)] == ["Moby/Play", "Compilations/Hits"]


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


def test_refreshed_waits_on_the_first_build_only(monkeypatch):
    monkeypatch.setattr(library, "PATIENCE", 5)
    stamp, release = ["a"], threading.Event()
    value = library.Refreshed(lambda: ((stamp[0], None),), slow_build(release, ["first", "second"]))
    release.set()
    assert value.get() == "first"
    release.clear()
    stamp[0] = "b"
    began = time.monotonic()
    assert value.get() == "first" and time.monotonic() - began < 1
    release.set()


def test_refreshed_patience_runs_from_the_build_start(monkeypatch):
    monkeypatch.setattr(library, "PATIENCE", 1)
    release = threading.Event()
    value = library.Refreshed(lambda: (("a", None),), slow_build(release, ["first"]))
    value.start()
    time.sleep(1)
    began = time.monotonic()
    assert value.get() is None and time.monotonic() - began < 0.5
    release.set()


def test_refreshed_progress_follows_its_build_reports():
    reported, release = threading.Event(), threading.Event()

    def build():
        library.report(1, 4)
        reported.set()
        release.wait(5)
        return "done"
    value = library.Refreshed(lambda: (("a", None),), build)
    assert value.progress() == 1.0
    value.start()
    reported.wait(5)
    assert value.progress() == 0.25
    library.report(3, 4)                                # not from the build's thread
    assert value.progress() == 0.25
    building = value.building
    release.set()
    building.join()
    assert value.progress() == 1.0


def test_parts_of_a_build_share_its_progress():
    seen = []

    def build():
        with library.part(0, 0.5):
            library.report(1, 2)
            seen.append(value.done)
            with library.part(0.5, 1):
                library.report(1, 2)
                seen.append(value.done)
        seen.append(value.done)
        library.report(3, 4)
        seen.append(value.done)
    value = library.Refreshed(lambda: (("a", None),), build)
    value.start()
    value.building.join()
    assert seen == [0.25, 0.375, 0.5, 0.75]


def test_cached_files_reports_the_files_read(lib, tmp_path, monkeypatch):
    reports = []
    monkeypatch.setattr(library, "REPORT_EVERY", 3)
    monkeypatch.setattr(library, "report", lambda done, total: reports.append((done, total)))
    assert len(library.cached_files(lib, tmp_path / "state")) == 4
    assert reports == [(3, 4), (4, 4)]


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
        page = client.get("/library", headers=EN).get_data(as_text=True)
        assert "Being worked out" in page and '<progress class="building" max="100" value="' in page
        assert "Being worked out" in client.get("/library/lyrics", headers=EN).get_data(as_text=True)
        tiles = client.get("/", headers=EN).get_data(as_text=True)
        assert 'class="tile-line tile-none"' in tiles and "being worked out" in tiles
        assert 'data-refresh="/tiles"' in tiles and "data-reload-when-built" not in tiles
        assert 'class="tile-line tile-none"' in client.get("/tiles", headers=EN).get_data(as_text=True)
        assert client.get("/built").get_json()["ready"] is False
    finally:
        release.set()
    for _ in range(100):
        if client.get("/built").get_json()["ready"]:
            break
        time.sleep(0.05)
    assert client.get("/built").get_json() == {"ready": True, "percent": 100}
    assert "data-reload-when-built" not in client.get("/library", headers=EN).get_data(as_text=True)


def test_home_waits_once_on_the_first_builds(dashboard, monkeypatch):
    monkeypatch.setattr(library, "PATIENCE", 1.5)
    release = threading.Event()
    for cls in (library.Index, reviews.Reviews, reviews.GenreReview):
        monkeypatch.setattr(cls, "_build", lambda self: release.wait(5) and None)
    client = web.create_app(dashboard.ctx).test_client()
    try:
        began = time.monotonic()
        assert client.get("/", headers=EN).status_code == 200
        assert time.monotonic() - began < 3 * library.PATIENCE     # the builds share one patience
    finally:
        release.set()


def test_page_reloads_once_a_stale_list_is_rebuilt(dashboard, lib, tmp_path, make_track, monkeypatch):
    monkeypatch.setattr(library, "PATIENCE", 0.01)
    settled(lambda: dashboard.client.get("/library", headers=EN))
    assert "data-reload-when-built" not in dashboard.client.get("/library", headers=EN).get_data(as_text=True)
    release = threading.Event()
    files = library.cached_files
    monkeypatch.setattr(library, "cached_files", lambda *a: release.wait(5) and files(*a))
    make_track(lib / "Moby/Play/01 Honey.m4a", title="Honey", artist="Moby", albumartist="Moby",
               album="Play", track=1)
    scan.run(lib, tmp_path / "state", Log())
    try:
        page = dashboard.client.get("/library", headers=EN).get_data(as_text=True)
        assert "Beyonce/4" in page and "Moby/Play" not in page          # the previous list, at once
        assert 'data-reload-when-built="/built?page=library_page"' in page
        assert dashboard.client.get("/built").get_json()["ready"] is False
    finally:
        release.set()
    page = settled(lambda: dashboard.client.get("/library", headers=EN)).get_data(as_text=True)
    assert "Moby/Play" in page and "data-reload-when-built" not in page
    assert dashboard.client.get("/built").get_json() == {"ready": True, "percent": 100}


def test_tiles_follow_the_cache(dashboard, lib, tmp_path, make_track):
    def lyrics_tile():
        tiles = settled(lambda: dashboard.client.get("/tiles", headers=EN)).get_data(as_text=True)
        return re.search(r'(\d+)</span>\s*<span class="tile-label">Tracks without lyrics', tiles)[1]
    assert lyrics_tile() == "4"
    make_track(lib / "Moby/Play/01 Honey.m4a", title="Honey", artist="Moby", albumartist="Moby",
               album="Play", track=1)
    scan.run(lib, tmp_path / "state", Log())
    assert lyrics_tile() == "5"


def test_no_reload_on_a_page_without_the_lists(dashboard, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(library.Index, "_build", lambda self: release.wait(5) and [])
    client = web.create_app(dashboard.ctx).test_client()
    try:
        assert "data-reload-when-built" not in client.get("/runs", headers=EN).get_data(as_text=True)
    finally:
        release.set()


def test_library_page(dashboard):
    page = dashboard.client.get("/library", headers=EN).get_data(as_text=True)
    assert '<a href="/library" aria-current="true">All <span class="muted">(2)</span>' in page
    assert "Albums: " not in page and "/library/album?folder=Beyonce/4" in page
    switch = '<a href="/library" aria-current="true">Albums</a><a href="/library?view=tracks">Tracks</a>'
    assert switch in page
    assert '<span class="track-count" title="2 tracks" aria-label="2 tracks">2</span>' in page
    hits = page[page.index("folder=Compilations/Hits"):]
    assert '<span class="muted">Beyonce</span>' in page
    assert 'class="muted compilation">Compilation</span>' not in hits         # one track only flagged
    assert "Tracks: " not in page
    page = dashboard.client.get("/library?q=hits", headers=EN).get_data(as_text=True)
    assert 'aria-current="true">All <span class="muted">(1)</span>' in page and "Beyonce/4" not in page
    assert 'href="/library?q=hits&amp;view=tracks">Tracks</a>' in page
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
    assert "Preview the changes" in page and '<code class="muted" title="01 One.flac">FLAC</code>' in page
    assert f'href="/library">{BACK}Library</a>' in page
    assert '<table class="tracks" id="tracks">' in page and "Discs and grouping</button>" in page   # folded


def test_disc_numbers_shown_for_several_discs(dashboard, lib, make_track, tmp_path):
    make_track(lib / "Beyonce/4/03 Bonus.m4a", title="Bonus", artist="Beyonce", albumartist="Beyonce",
               album="4", track=1, disc=2, disctotal=2)
    scan.run(lib, tmp_path / "state", Log())
    page = dashboard.client.get("/library/album?folder=Beyonce/4", headers=EN).get_data(as_text=True)
    assert 'name="t2.disc" value="2"' in page and '<table class="tracks discs" id="tracks">' in page
    assert "Disc 1" in page and 'name="d2.discsubtitle" value=""' in page
    assert "Discs and grouping</button>" not in page


def test_disc_name_once_per_disc(dashboard, lib, tmp_path):
    for name in ("01 Run the World", "02 Best Thing"):
        fields.write(lib / f"Beyonce/4/{name}.m4a", "discsubtitle", ["Deluxe"])
    fields.write(lib / "Beyonce/4/01 Run the World.m4a", "grouping", ["Bonus"])
    scan.run(lib, tmp_path / "state", Log())
    page = dashboard.client.get("/library/album?folder=Beyonce/4", headers=EN).get_data(as_text=True)
    assert page.count('class="disc-row"') == 1 and 'name="d1.discsubtitle" value="Deluxe"' in page
    assert '<th class="t-grouping">Grouping</th>' in page and 'name="t0.grouping" value="Bonus"' in page


def test_disc_name_mixed_on_a_disc(dashboard, lib, tmp_path):
    fields.write(lib / "Beyonce/4/01 Run the World.m4a", "discsubtitle", ["Deluxe"])
    scan.run(lib, tmp_path / "state", Log())
    page = dashboard.client.get("/library/album?folder=Beyonce/4", headers=EN).get_data(as_text=True)
    assert re.search(r'name="d1.discsubtitle" value=""\s+placeholder="various values"', page)
    response = dashboard.client.post("/library/album", data=album_form(dashboard, "Beyonce/4"))
    assert response.location.endswith("unchanged=1")


def test_disc_name_edits_every_track_of_its_disc(dashboard, lib, make_track, tmp_path):
    make_track(lib / "Beyonce/4/03 Bonus.m4a", title="Bonus", artist="Beyonce", albumartist="Beyonce",
               album="4", track=1, disc=2, disctotal=2)
    scan.run(lib, tmp_path / "state", Log())
    form = album_form(dashboard, "Beyonce/4", **{"d1.discsubtitle": "Jupiter", "apply": "1"})
    dashboard.client.post("/library/album", data=form)
    [asked] = dashboard.requests.pending()
    assert asked.options["edits"] == [["Beyonce/4/01 Run the World.m4a", "discsubtitle", "Jupiter", []],
                                      ["Beyonce/4/02 Best Thing.m4a", "discsubtitle", "Jupiter", []]]


@pytest.mark.parametrize("folder", ["../", "Beyonce/9", "Beyonce/4/01 Run the World.m4a"])
def test_album_not_in_the_library(dashboard, folder):
    response = dashboard.client.get(f"/library/album?folder={folder}", headers=EN)
    assert response.status_code == 404 and "no longer in the library" in response.get_data(as_text=True)


def test_untouched_form_asks_nothing(dashboard):
    response = dashboard.client.post("/library/album", data=album_form(dashboard, "Beyonce/4"))
    assert response.location.endswith("unchanged=1") and dashboard.requests.pending() == []


def test_album_field_edits_every_track(dashboard):
    form = album_form(dashboard, "Beyonce/4",
                      **{"album.albumartist": "Beyoncé", "t1.title": "Best Thing I Never Had", "apply": "1"})
    response = dashboard.client.post("/library/album", data=form)
    [asked] = dashboard.requests.pending()
    assert response.location == f"/library/album?folder=Beyonce/4&request={asked.id}"
    assert (asked.command, asked.apply) == ("tag", True)
    assert asked.options["edits"] == [
        ["Beyonce/4/01 Run the World.m4a", "albumartist", "Beyoncé", ["Beyonce"]],
        ["Beyonce/4/02 Best Thing.m4a", "albumartist", "Beyoncé", ["Beyonce"]],
        ["Beyonce/4/02 Best Thing.m4a", "title", "Best Thing I Never Had", ["Best Thing"]],
    ]


@pytest.mark.parametrize("name", ["../../Compilations/Hits/01 One.flac", "03 Missing.m4a", 3])
def test_edit_of_a_track_not_in_the_album_refused(dashboard, name):
    form = album_form(dashboard, "Beyonce/4", **{"t0.title": "Other"})
    seen = json.loads(form["seen"])
    seen["tracks"][0][0] = name
    form["seen"] = json.dumps(seen)
    assert dashboard.client.post("/library/album", data=form).status_code == 400
    assert dashboard.requests.pending() == []


def test_mixed_values_left_alone_unless_set(dashboard):
    form = album_form(dashboard, "Compilations/Hits", **{"t0.track": "01"})
    assert dashboard.client.post("/library/album", data=form).location.endswith("unchanged=1")
    form = album_form(dashboard, "Compilations/Hits", **{"album.compilation": "0", "apply": "1"})
    dashboard.client.post("/library/album", data=form)
    [asked] = dashboard.requests.pending()
    assert [(e[1], e[2], e[3]) for e in asked.options["edits"]] == [("compilation", "0", ["1"]),
                                                                    ("compilation", "0", [])]


def test_bad_value_refused_on_the_album_page_as_typed(dashboard):
    form = album_form(dashboard, "Beyonce/4", **{"t0.track": "first", "t1.title": "Typed"})
    response = dashboard.client.post("/library/album", data=form, headers=EN)
    page = response.get_data(as_text=True)
    assert response.status_code == 400 and "Value refused" in page
    assert 'name="t0.track" value="first"' in page and 'name="t1.title" value="Typed"' in page
    assert dashboard.requests.pending() == []


def test_covers(dashboard):
    response = dashboard.client.get("/library/cover?folder=Beyonce/4")
    assert (response.mimetype, response.data) == ("image/jpeg", JPEG)
    assert dashboard.client.get("/library/cover?folder=Compilations/Hits").data == JPEG   # embedded
    assert dashboard.client.get("/library/cover?folder=../").status_code == 404


def test_cover_named_in_any_case(dashboard, lib):
    (lib / "Beyonce/4/folder.jpg").rename(lib / "Beyonce/4/Folder.JPG")
    assert dashboard.client.get("/library/cover?folder=Beyonce/4").data == JPEG


def test_placeholder_without_a_cover(dashboard, lib):
    (lib / "Bare").mkdir()
    response = dashboard.client.get("/library/cover?folder=Bare")
    assert response.status_code == 200 and response.mimetype == "image/svg+xml"


def test_edit_previewed_on_the_album_page(dashboard, lib):
    form = album_form(dashboard, "Beyonce/4", **{"album.albumartist": "Beyoncé", "t1.title": "Best"})
    page = dashboard.client.post("/library/album", data=form, headers=EN).get_data(as_text=True)
    assert "To apply" in page and dashboard.requests.pending() == []
    assert "<del>Beyonce</del> → <ins>Beyoncé</ins>" in page and "· 2 tracks" in page
    assert "<del>Best Thing</del> → <ins>Best</ins>" in page and "· 02 Best Thing.m4a" in page
    assert "<del>Beyonce/4</del> → <ins>Beyoncé/4</ins>" in page
    assert 'name="album.albumartist" value="Beyoncé"' in page                  # as typed
    assert 'form="album-form" name="apply" value="1"' in page
    assert fields.read(lib / "Beyonce/4/01 Run the World.m4a")["albumartist"] == ["Beyonce"]
    assert not (dashboard.state / "journal").exists()


def test_edit_applied_then_undone_from_the_album_page(dashboard, lib):
    form = album_form(dashboard, "Beyonce/4", **{"album.albumartist": "Beyoncé", "album.year": "2011",
                                                 "apply": "1"})
    following = dashboard.client.post("/library/album", data=form).location
    [asked] = dashboard.requests.pending()
    page = dashboard.client.get(following, headers=EN).get_data(as_text=True)
    assert "Queued: starts" in page and f'data-follow="/requests/{asked.id}.json?folder=Beyonce/4"' in page
    assert dashboard.client.get(f"/requests/{asked.id}.json?folder=Beyonce/4").get_json() == \
        {"done": False, "album": None}

    dashboard.requests.tick(Log())
    state = dashboard.client.get(f"/requests/{asked.id}.json?folder=Beyonce/4").get_json()
    assert state == {"done": True, "album": f"/library/album?folder=Beyonc%C3%A9/4&request={asked.id}"}
    page = dashboard.client.get(state["album"], headers=EN).get_data(as_text=True)
    assert "<h2>Applied</h2>" in page and ">Undo</button>" in page and "see the details" in page
    moved = lib / "Beyoncé/4/01 Run the World.m4a"
    assert fields.read(moved)["albumartist"] == ["Beyoncé"] and fields.read(moved)["year"] == ["2011"]
    assert (lib / "Beyoncé/4/folder.jpg").exists() and not (lib / "Beyonce").exists()
    albums = settled(lambda: dashboard.client.get("/library", headers=EN)).get_data(as_text=True)
    assert "folder=Beyonc%C3%A9/4" in albums and "folder=Beyonce/4" not in albums

    journal = re.search(r'name="journal" value="([^"]+)"', page)[1]
    asked_undo = {"csrf": dashboard.token, "command": "undo", "journal": journal, "apply": "1",
                  "album": "Beyoncé/4"}
    undo = dashboard.client.post("/passes", data=asked_undo).location
    [undoing] = dashboard.requests.pending()
    assert undo == f"/library/album?folder=Beyonc%C3%A9/4&request={undoing.id}"
    dashboard.requests.tick(Log())
    state = dashboard.client.get(f"/requests/{undoing.id}.json?folder=Beyoncé/4").get_json()
    assert state["album"] == f"/library/album?folder=Beyonce/4&request={undoing.id}"
    assert "<h2>Undone</h2>" in dashboard.client.get(state["album"], headers=EN).get_data(as_text=True)
    assert fields.read(lib / "Beyonce/4/01 Run the World.m4a")["albumartist"] == ["Beyonce"]


def test_queued_pass_cancelled_from_the_album_page(dashboard):
    form = album_form(dashboard, "Beyonce/4", **{"album.year": "2011", "apply": "1"})
    dashboard.client.post("/library/album", data=form)
    [asked] = dashboard.requests.pending()
    back = dashboard.client.post(f"/requests/{asked.id}/cancel",
                                 data={"csrf": dashboard.token, "album": "Beyonce/4"}).location
    assert back == f"/library/album?folder=Beyonce/4&request={asked.id}"
    assert "Request cancelled" in dashboard.client.get(back, headers=EN).get_data(as_text=True)


def test_no_apply_offered_while_watch_runs_without_apply(dashboard):
    dashboard.ctx.apply = False
    form = album_form(dashboard, "Beyonce/4", **{"album.year": "2011"})
    page = dashboard.client.post("/library/album", data=form, headers=EN).get_data(as_text=True)
    assert "watch runs without --apply" in page and 'name="apply" value="1"' not in page
    form["apply"] = "1"
    assert dashboard.client.post("/library/album", data=form).status_code == 403


def test_edit_refused_when_the_track_changed_meanwhile(dashboard, lib):
    form = album_form(dashboard, "Beyonce/4", **{"t0.composer": "The-Dream"})
    fields.write(lib / "Beyonce/4/01 Run the World.m4a", "composer", ["Someone Else"])
    page = dashboard.client.post("/library/album", data=form, headers=EN).get_data(as_text=True)
    assert "Nothing to write" in page and "Left alone" in page
    assert "REFUSED  (composer changed since" in page and 'name="apply" value="1"' not in page
    assert fields.read(lib / "Beyonce/4/01 Run the World.m4a")["composer"] == ["Someone Else"]


def test_rebuilt_at_most_every_minute_while_a_pass_writes_the_cache(dashboard, lib, tmp_path, monkeypatch):
    reads = []
    monkeypatch.setattr(library, "cached_files", lambda *a: reads.append(a) or [])
    clock = [library.time.monotonic() + library.REBUILD_EVERY]        # long after the fixture's build
    monkeypatch.setattr(library.time, "monotonic", lambda: clock[0])
    with MetadataCache(tmp_path / "state", force=True) as cache:   # a pass rewriting the cache
        for name in ("01 Run the World.m4a", "02 Best Thing.m4a"):
            cache.snapshot(lib / "Beyonce/4" / name)
            cache.commit()
            settled(lambda: dashboard.client.get("/library"))
            clock[0] += 5
        assert len(reads) == 1
        clock[0] += library.REBUILD_EVERY
        cache.snapshot(lib / "Compilations/Hits/01 One.flac")
        cache.commit()
        settled(lambda: dashboard.client.get("/library"))
        assert len(reads) == 2
    settled(lambda: dashboard.client.get("/library"))                            # the pass ended: at once
    assert len(reads) == 3


def test_activity_counts_no_review(dashboard, monkeypatch):
    monkeypatch.setattr(reviews.Reviews, "credit", lambda *a: pytest.fail("reviews counted"))
    assert dashboard.client.get("/activity").status_code == 200


def test_queued_passes_head_the_history(dashboard):
    dashboard.requests.submit("scan", {}, False)
    dashboard.requests.submit("organize", {"since": True}, False)
    page = dashboard.client.get("/runs", headers=EN).get_data(as_text=True)
    rows = re.findall(r"<td><code>(\w+)</code></td>\s*<td>[^<]*</td>\s*<td>[^<]*</td>\s*<td></td>\s*"
                      r'<td><span class="status status-queued">Queued</span>', page)
    assert rows == ["organize", "scan"]                     # the next to run just above the runs
    for filtered in ("/runs?command=import", "/runs?problems=1"):
        assert "status-queued" not in dashboard.client.get(filtered, headers=EN).get_data(as_text=True)


def test_compilation_named_as_such(dashboard, lib, make_track, tmp_path):
    make_track(lib / "Compilations/Now/01 A.m4a", title="A", artist="Moby", album="Now", track=1,
               compilation=True)
    make_track(lib / "Compilations/Now/02 B.mp3", title="B", artist="U2", album="Now", track=2,
               compilation=True)
    scan.run(lib, tmp_path / "state", Log())
    page = settled(lambda: dashboard.client.get("/library", headers=EN)).get_data(as_text=True)
    start = page.index("folder=Compilations/Now")
    now = page[start:page.index("</li>", start)]
    assert 'class="muted compilation">Compilation</span>' in now and "Moby" not in now
    assert "Compilations/Now" in dashboard.client.get("/library?q=moby", headers=EN).get_data(as_text=True)


def big_jpeg(width, height):
    return b"\xff\xd8\xff\xc0" + struct.pack(">HBHHB", 17, 8, height, width, 3) + b"\x01\x11\x00" * 3


def upload(d, data, name="new.jpg", **changes):
    """The album form posted with the image `data` picked for its cover."""
    form = album_form(d, "Beyonce/4", **changes)
    form["cover_image"] = (io.BytesIO(data), name)
    return d.client.post("/library/album", headers=EN, content_type="multipart/form-data", data=form)


def test_cover_picked_previewed_then_applied_with_the_form(dashboard, lib):
    new = big_jpeg(1000, 1000)
    page = dashboard.client.get("/library/album?folder=Beyonce/4", headers=EN).get_data(as_text=True)
    assert 'name="cover_image" form="album-form"' in page and 'enctype="multipart/form-data"' in page
    shown = re.search(r'<img src="(/library/cover[^"]+)"', page)[1]
    assert shown.startswith("/library/cover?folder=Beyonce/4&amp;v=")

    page = upload(dashboard, new).get_data(as_text=True)
    kept = re.search(r'<input type="hidden" name="cover" value="([0-9a-f]+\.jpg)">', page)[1]
    assert f'<img src="/library/upload/{kept}"' in page
    assert "<strong>Cover</strong> <del>(none)</del> → <ins>1000 × 1000</ins>" in page
    assert 'value="1" class="primary"><svg class="icon"' in page
    assert '#apply"></use></svg>Apply</button>' in page
    assert dashboard.requests.pending() == []
    assert dashboard.client.get(f"/library/upload/{kept}").data == new
    assert (lib / "Beyonce/4/folder.jpg").read_bytes() == JPEG

    applied = album_form(dashboard, "Beyonce/4", cover=kept, apply="1")
    assert dashboard.client.post("/library/album", data=applied).status_code == 302
    [asked] = dashboard.requests.pending()
    assert (asked.command, asked.apply, asked.options["cover"]) == ("tag", True, ["Beyonce/4", kept])
    dashboard.requests.tick(Log())
    assert (lib / "Beyonce/4/folder.jpg").read_bytes() == new
    assert tags.read_cover(lib / "Beyonce/4/01 Run the World.m4a") == new
    assert dashboard.client.get("/library/cover?folder=Beyonce/4").data == new
    page = dashboard.client.get("/library/album?folder=Beyonce/4", headers=EN).get_data(as_text=True)
    assert re.search(r'<img src="(/library/cover[^"]+)"', page)[1] != shown
    albums = settled(lambda: dashboard.client.get("/library", headers=EN)).get_data(as_text=True)
    assert html.unescape(re.search(r'<img src="(/library/cover\?folder=Beyonce/4[^"]*)"', albums)[1]) == \
        html.unescape(re.search(r'<img src="(/library/cover[^"]+)"', page)[1])


def test_cover_and_fields_in_one_pass(dashboard):
    page = upload(dashboard, big_jpeg(500, 500), **{"album.year": "2011"}).get_data(as_text=True)
    kept = re.search(r'name="cover" value="([^"]+)"', page)[1]
    assert "<ins>500 × 500</ins>" in page and "<ins>2011</ins>" in page
    dashboard.client.post("/library/album", data=album_form(dashboard, "Beyonce/4", cover=kept, apply="1",
                                                            **{"album.year": "2011"}))
    [asked] = dashboard.requests.pending()
    assert len(asked.options["edits"]) == 2 and asked.options["cover"] == ["Beyonce/4", kept]


@pytest.mark.parametrize("data", [b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, b"not an image at all"])
def test_cover_refused(dashboard, data):
    response = upload(dashboard, data)
    assert response.status_code == 400
    assert "This file is not a readable image" in response.get_data(as_text=True)
    assert dashboard.requests.pending() == [] and not (dashboard.state / passes.UPLOADS).exists()


def test_no_image_picked_changes_nothing(dashboard):
    assert "unchanged=1" in upload(dashboard, b"").location


def test_cover_converted_to_jpeg(dashboard, image):
    page = upload(dashboard, image("cover.webp", 64, 48), name="cover.webp").get_data(as_text=True)
    kept = re.search(r'name="cover" value="([^"]+)"', page)[1]
    assert artwork.image_size(passes.upload_path(dashboard.state, kept).read_bytes()) == ("jpeg", 64, 48)


def test_cover_too_large(dashboard):
    dashboard.client.application.config["MAX_CONTENT_LENGTH"] = 1000
    response = upload(dashboard, big_jpeg(10, 10) + b"\x00" * 2000)
    assert response.status_code == 413 and "Image too large" in response.get_data(as_text=True)


def test_cover_kept_name_checked(dashboard):
    assert "unchanged=1" in dashboard.client.post(
        "/library/album", data=album_form(dashboard, "Beyonce/4", cover="../x.jpg")).location
    assert dashboard.client.get("/library/upload/..%2Fx.jpg").status_code == 404


def test_cover_upload_gone(dashboard, capsys):
    dashboard.requests.submit("tag", {"edits": [], "cover": ["Beyonce/4", "0" * 32 + ".jpg"]}, False)
    dashboard.requests.tick(Log())
    assert "ERROR    the uploaded image is gone" in capsys.readouterr().out


def test_old_uploads_removed(tmp_path):
    old = passes.keep_upload(tmp_path, b"old")
    stale = time.time() - (passes.UPLOAD_DAYS + 1) * 86400
    os.utime(tmp_path / passes.UPLOADS / old, (stale, stale))
    new = passes.keep_upload(tmp_path, b"new")
    assert passes.upload_path(tmp_path, old) is None
    assert passes.upload_path(tmp_path, new).read_bytes() == b"new"
    assert passes.upload_path(tmp_path, "../x.jpg") is None




def test_replaygain_struck_then_removed_with_the_album_form(dashboard, lib):
    one, two = lib / "Beyonce/4/01 Run the World.m4a", lib / "Beyonce/4/02 Best Thing.m4a"
    tags.write_replaygain(one, -7.12, 0.98)
    tags.write_replaygain(two, -5.5, 0.9)
    page = dashboard.client.get("/library/album?folder=Beyonce/4", headers=EN).get_data(as_text=True)
    assert 'name="t0.replaygain_delete"' in page and "<span>-7.12 dB</span>" in page
    form = album_form(dashboard, "Beyonce/4", **{"t0.replaygain_delete": "1"})
    page = dashboard.client.post("/library/album", data=form, headers=EN).get_data(as_text=True)
    assert "<del>track gain -7.12 dB</del> → <ins>(none)</ins>" in page
    assert re.search(r'name="t0.replaygain_delete"[^>]* checked>', page)            # still struck
    assert tags.has_replaygain(one)

    dashboard.client.post("/library/album", data={**form, "apply": "1"})
    dashboard.requests.tick(Log())
    assert not tags.has_replaygain(one) and tags.has_replaygain(two)


def test_replaygain_removal_refused_when_it_changed_since_shown(dashboard, lib):
    track = lib / "Beyonce/4/01 Run the World.m4a"
    tags.write_replaygain(track, -7.12, 0.98)
    form = album_form(dashboard, "Beyonce/4", **{"t0.replaygain_delete": "1"})
    tags.write_replaygain(track, -3.0, 0.5)
    page = dashboard.client.post("/library/album", data=form, headers=EN).get_data(as_text=True)
    assert tags.has_replaygain(track) and "REFUSED  (replaygain changed since" in page


def test_no_replaygain_to_strike_without_any(dashboard):
    page = dashboard.client.get("/library/album?folder=Beyonce/4", headers=EN).get_data(as_text=True)
    assert "replaygain_delete" not in page


def test_album_opened_from_a_work_list_steps_through_it(dashboard):
    page = settled(lambda: dashboard.client.get("/library/lyrics", headers=EN)).get_data(as_text=True)
    assert 'href="/library/album?folder=Beyonce/4&amp;list=lyrics&amp;at=0"' in page
    assert 'href="/library/album?folder=Compilations/Hits&amp;list=lyrics&amp;at=1"' in page
    first = dashboard.client.get("/library/album?folder=Beyonce/4&list=lyrics&at=0", headers=EN)
    page = first.get_data(as_text=True)
    assert f'href="/library/lyrics">{BACK}Without lyrics</a>' in page and "Album 1 of 2" in page
    assert 'href="/library/album?folder=Compilations/Hits&amp;list=lyrics&amp;at=1">Next →</a>' in page
    assert "← Previous" not in page and f"{BACK}Library" not in page
    assert '<input type="hidden" name="list" value="lyrics">' in page          # kept by the album's form
    page = dashboard.client.get("/library/album?folder=Compilations/Hits&list=lyrics&at=1",
                                headers=EN).get_data(as_text=True)
    assert "Album 2 of 2" in page and "Last of the list" in page
    assert 'href="/library/album?folder=Beyonce/4&amp;list=lyrics&amp;at=0">← Previous</a>' in page


def test_album_no_longer_in_its_work_list_steps_from_where_it_stood(dashboard):
    settled(lambda: dashboard.client.get("/library/repeats"))
    page = dashboard.client.get("/library/album?folder=Beyonce/4&list=repeats&at=0", headers=EN)
    page = page.get_data(as_text=True)
    assert f'href="/library/repeats">{BACK}Repeated values</a>' in page and "Albums left: 0" in page
    assert "Last of the list" in page and "Next →" not in page
    page = dashboard.client.get("/library/album?folder=Beyonce/4&list=nope", headers=EN)
    page = page.get_data(as_text=True)
    assert f"{BACK}Library" in page and "work-list" not in page


def test_work_list_kept_through_preview_and_apply(dashboard):
    settled(lambda: dashboard.client.get("/library/lyrics"))
    form = album_form(dashboard, "Beyonce/4&list=lyrics&at=0", **{"album.year": "2011"})
    form["folder"] = "Beyonce/4"
    assert (form["list"], form["at"]) == ("lyrics", "0")
    page = dashboard.client.post("/library/album", data=form, headers=EN).get_data(as_text=True)
    assert "To apply" in page and "Album 1 of 2" in page and 'name="list" value="lyrics"' in page
    following = dashboard.client.post("/library/album", data={**form, "apply": "1"}).location
    [asked] = dashboard.requests.pending()
    assert following == f"/library/album?folder=Beyonce/4&request={asked.id}&list=lyrics&at=0"
    page = dashboard.client.get(following, headers=EN).get_data(as_text=True)
    assert f'data-follow="/requests/{asked.id}.json?folder=Beyonce/4&amp;list=lyrics&amp;at=0"' in page
    dashboard.requests.tick(Log())
    state = dashboard.client.get(f"/requests/{asked.id}.json?folder=Beyonce/4&list=lyrics&at=0").get_json()
    assert state["album"] == f"/library/album?folder=Beyonce/4&request={asked.id}&list=lyrics&at=0"


def test_library_sorted_and_filtered(dashboard):
    page = settled(lambda: dashboard.client.get("/library?only=lyrics&sort=title", headers=EN))
    page = page.get_data(as_text=True)
    assert page.index("folder=Beyonce/4") < page.index("folder=Compilations/Hits")
    assert '<option value="title" selected>Album</option>' in page
    assert '<a href="/library?sort=title">All <span class="muted">(2)</span></a>' in page
    assert 'href="/library?c=lyrics:no&amp;sort=title" aria-current="true">Without lyrics' in page
    assert 'href="/library?c=compilation:yes&amp;sort=title">Compilations <span class="muted">(1)' in page
    assert 'href="/library/lyrics">one by one</a>' in page
    assert "Repeated values" not in page                                        # keeps no album
    page = dashboard.client.get("/library?c=repeated:yes", headers=EN).get_data(as_text=True)
    assert "No album matches" in page and "<details class=\"advanced\" open>" in page
    assert 'aria-current="true">Repeated values <span class="muted">(0)</span>' in page
    page = dashboard.client.get("/library?sort=nope&only=nope&c=nope:is:x", headers=EN).get_data(as_text=True)
    assert "All <span class=\"muted\">(2)" in page and '<option value="artist" selected>' in page
    assert '<a href="/library" aria-current="true">All' in page


def test_library_searched_field_by_field(dashboard):
    page = dashboard.client.get("/library?c=composer:empty", headers=EN).get_data(as_text=True)
    assert "Albums: " not in page and "Beyonce/4" not in page
    assert '<span class="track-count" title="2 of 2 tracks match"' not in page        # every track kept
    assert 'aria-current="true">Without composer' in page and "1 condition" in page
    link = "/library/album?folder=Compilations/Hits&amp;list=search&amp;at=0&amp;search=c%3Dcomposer:empty"
    assert f'href="{link}"' in page
    page = dashboard.client.get("/library?c=artist:is:moby", headers=EN).get_data(as_text=True)
    assert 'title="1 of 2 tracks match" aria-label="1 of 2 tracks match">1/2</span>' in page
    page = dashboard.client.get("/library?c=title:contains:THING&c=artist:is:u2&match=any&view=tracks",
                                headers=EN).get_data(as_text=True)
    assert "Tracks: 2" in page and "Best Thing" in page and "<td>U2</td>" in page
    assert "Run the World" not in page
    assert '<option value="any" selected>at least one condition</option>' in page
    assert 'name="v" value="THING"' in page and 'name="v" value="u2"' in page


def test_library_track_list_shows_the_fields_searched(dashboard):
    page = dashboard.client.get("/library?view=tracks&c=composer:filled&c=cover:no&c=format:is:m4a",
                                headers=EN).get_data(as_text=True)
    assert "<th>Composer</th><th>Embedded cover</th><th>Format</th>" in page
    assert '<td class="hit">Terius Nash</td>' in page and '<td class="hit">no</td>' in page
    assert "Tracks: 2" in page and "Hits" not in page.split("<tbody>")[1]
    page = dashboard.client.get("/library?view=tracks&c=composer:is:nobody", headers=EN)
    page = page.get_data(as_text=True)
    assert "No track matches" in page


def test_library_form_rows_read_without_script(dashboard):
    rows = "f=year&k=&o=empty&v=&w=&f=tag&k=ENCODER&o=filled&v=&w=&f=any&k=&o=contains&v=&w="
    page = dashboard.client.get(f"/library?view=tracks&{rows}", headers=EN).get_data(as_text=True)
    assert "Tracks: 2" in page and "<th>encoder</th>" in page and "2 conditions" in page
    assert 'name="k" value="encoder"' in page
    page = dashboard.client.get(f"/library?view=tracks&{rows}&drop=1", headers=EN).get_data(as_text=True)
    assert "Tracks: 4" in page and "1 condition" in page


def test_album_opened_from_a_search_steps_through_it(dashboard):
    settled(lambda: dashboard.client.get("/library"))
    search = "search=c%3Dlyrics:no%26sort%3Dtitle"
    page = dashboard.client.get(f"/library/album?folder=Beyonce/4&list=search&at=0&{search}", headers=EN)
    page = page.get_data(as_text=True)
    assert f'href="/library?c=lyrics:no&amp;sort=title">{BACK}Search</a>' in page and "Album 1 of 2" in page
    following = f"/library/album?folder=Compilations/Hits&amp;list=search&amp;{search}&amp;at=1"
    assert f'href="{following}">Next' in page
    assert '<input type="hidden" name="search" value="c=lyrics:no&amp;sort=title">' in page


def test_library_pages_keep_the_search(dashboard, monkeypatch):
    monkeypatch.setattr(library, "PAGE", 1)
    page = settled(lambda: dashboard.client.get("/library?q=e&sort=year", headers=EN)).get_data(as_text=True)
    assert 'href="/library?page=2&amp;q=e&amp;sort=year">Next' in page
    assert 'id="results"' in page and "data-live-search" in page
