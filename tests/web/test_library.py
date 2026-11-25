"""The library in the dashboard (web/library.py): the index of the metadata cache and its builds."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import re
import threading
import time

import pytest

from music_librarian import scan
from music_librarian.core.log import Log
from music_librarian.media.metadata_cache import MetadataCache
from music_librarian.web import app as web
from music_librarian.web import library, query, reviews

EN = {"Accept-Language": "en"}


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


def test_no_cache_created_by_the_dashboard(tmp_path, serve, settled):
    d = serve(tmp_path / "Music", tmp_path / "state", tmp_path / "Inbox")
    settled(lambda: d.client.get("/", headers=EN))
    assert not (tmp_path / "state" / "metadata.db").exists()


def test_index_leaves_out_files_gone_and_follows_the_cache(lib, tmp_path, make_track, settled):
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
        assert 'class="chip chip-building"' in tiles and "being worked out" in tiles
        assert 'data-refresh="/tiles"' in tiles and "data-reload-when-built" not in tiles
        assert 'class="chip chip-building"' in client.get("/tiles", headers=EN).get_data(as_text=True)
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


def test_page_reloads_once_a_stale_list_is_rebuilt(dashboard, lib, tmp_path, make_track, monkeypatch,
                                                 settled):
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


def test_tiles_follow_the_cache(dashboard, lib, tmp_path, make_track, settled):
    def lyrics_tile():
        tiles = settled(lambda: dashboard.client.get("/tiles", headers=EN)).get_data(as_text=True)
        return re.search(r'(\d+)</span>Tracks without lyrics', tiles)[1]
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


def test_rebuilt_at_most_every_minute_while_a_pass_writes_the_cache(dashboard, lib, tmp_path, monkeypatch,
                                                                   settled):
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
    rows = re.findall(r"<td>(\w+)</td>\s*<td>[^<]*</td>\s*<td>[^<]*</td>\s*<td></td>\s*"
                      r'<td><span class="status status-queued">Queued</span>', page)
    assert rows == ["organize", "scan"]                     # the next to run just above the runs
    for filtered in ("/runs?command=import", "/runs?problems=1"):
        assert "status-queued" not in dashboard.client.get(filtered, headers=EN).get_data(as_text=True)


def test_warmer_reads_the_albums_asked_for_last_once(tmp_path, monkeypatch):
    read, gate = [], threading.Event()

    def tracks_of(folder):
        gate.wait(5)
        read.append(folder.name)
    monkeypatch.setattr(library, "tracks_of", tracks_of)
    warmer = library.Warmer()
    warmer.ask(tmp_path / "a", tmp_path / "b")
    warmer.ask(tmp_path / "c", tmp_path / "d")
    gate.set()
    for thread in threading.enumerate():
        if thread.name == "web-warm":
            thread.join(5)
    warmer.ask(tmp_path / "d", tmp_path / "e")
    for thread in threading.enumerate():
        if thread.name == "web-warm":
            thread.join(5)
    assert read == ["a", "c", "d", "e"]
