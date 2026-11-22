# pylint: disable=redefined-outer-name  # pytest fixtures
import hashlib
import os
import sys
import time

import pytest
from Crypto.Cipher import Blowfish
from mutagen.flac import FLAC

from music_librarian import deezer, importer
from music_librarian.core import runs
from music_librarian.core.lock import STOP
from music_librarian.core.log import Buffer
from music_librarian.deezer import download, kept, staging, tags
from music_librarian.deezer.api import GET_URL, GW
from music_librarian.deezer.crypto import CHUNK, blowfish_key

ALBUM_API = "https://api.deezer.com/album/302127"
ALBUM_PAGE = "https://www.deezer.com/de/album/302127"
COVER = "https://cdn/xl.jpg"
ALBUM = {"id": 302127, "title": "Fake Album", "artist": {"name": "Alpha"}, "nb_tracks": 3,
         "release_date": "2001-05-07", "cover_xl": COVER, "genres": {"data": [{"name": "Pop"}]}}


def encrypt(data, song_id, cfg):
    key = blowfish_key(song_id, cfg.pre_key)
    chunks = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)]
    return b"".join(Blowfish.new(key, Blowfish.MODE_CBC, cfg.iv).encrypt(c) if i % 3 == 0 and len(c) == CHUNK
                    else c for i, c in enumerate(chunks))


def tree(folder):
    """A checksum of every path and content under `folder`."""
    digest = hashlib.sha1()
    for path in sorted(folder.rglob("*")) if folder.exists() else []:
        digest.update(str(path.relative_to(folder)).encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


@pytest.fixture
def site(deezer, http, cfg, templates, image):
    """A Deezer serving the album of fixtures/album.html; `unavailable` holds the track tokens
    get_url has no stream for, `expired` the license tokens it calls expired, `user` the account's
    data the arl gives. site() -> the client."""
    flac = templates[".flac"].read_bytes()
    jpeg = image("cover.jpg", 16, 16)
    unavailable = {"dummy-track-token-3"}    # the third track streams through its fallback
    expired = set()
    user = {"USER_ID": 42, "OPTIONS": {"license_token": "dummy-fresh-token"}}

    def get_url(json=None, **_kwargs):  # pylint: disable=redefined-outer-name
        if json["license_token"] in expired:
            return http.answer({"errors": [{"code": 1001, "message": "License token is expired."}]}, 403)
        token = json["track_tokens"][0]
        if token in unavailable:
            return http.answer({"data": [{"media": []}]})
        return http.answer({"data": [{"media": [{"format": "FLAC", "sources": [
            {"provider": "ak", "url": f"https://stream/{token}"}]}]}]})

    def stream_of(song_id):
        return lambda **_kwargs: http.Response(pieces=[encrypt(flac, song_id, cfg)])

    def make(extra=None):
        routes = {ALBUM_API: http.answer(ALBUM), ALBUM_PAGE: http.fixture("album.html"), GET_URL: get_url,
                  GW: lambda **_kwargs: http.answer({"results": {"USER": user}}),
                  COVER: http.Response(body=jpeg), "https://stream/dummy-fallback-token": stream_of("999")}
        routes.update({f"https://stream/dummy-track-token-{i}": stream_of(str(100 + i)) for i in (1, 2, 3)})
        routes.update(extra or {})
        return deezer(routes)
    make.unavailable, make.expired, make.user = unavailable, expired, user
    return make


@pytest.fixture
def inbox(tmp_path):
    folder = tmp_path / "Inbox"
    folder.mkdir()
    return folder


def run(inbox, cfg, client, *ids, kind="album", **options):
    log = Buffer()
    opts = deezer.Options(kind=kind, ids=list(ids or ["302127"]), inbox=inbox,
                          state_dir=inbox.parent / "state", **options)
    code = deezer.run(opts, log, cfg=cfg, client=client)
    return code, log.text()


def test_item_ids():
    assert deezer.item_id("album", "302127") == "302127"
    assert deezer.item_id("album", "https://www.deezer.com/fr/album/302127?utm=x") == "302127"
    assert deezer.item_id("playlist", "deezer.com/playlist/908622995") == "908622995"
    assert deezer.item_id("album", "https://www.deezer.com/en-gb/album/1/") == "1"
    with pytest.raises(ValueError):
        deezer.item_id("album", "١٢٣")
    for value, message in (("https://www.deezer.com/fr/playlist/1", "not of an album"),
                           ("https://link.deezer.com/s/abc", "short links are not followed"),
                           ("https://example.com/album/1", "neither an id")):
        with pytest.raises(ValueError, match=message):
            deezer.item_id("album", value)


def test_a_dry_run_reads_the_metadata_only(inbox, cfg, site):
    client = site()
    before = tree(inbox)
    code, out = run(inbox, cfg, client)
    assert code == 0
    assert "PLAN     Alpha - Fake Album (3 tracks), best of FLAC/MP3_320/MP3_128" in out
    assert "albums to download: 1" in out and "tracks to download: 3" in out
    assert tree(inbox) == before
    assert [s["url"] for s in client.session.sent] == [ALBUM_API, ALBUM_PAGE]


def test_apply_drops_the_album_into_the_inbox_once_complete(inbox, cfg, site, monkeypatch):
    seen_meanwhile = []
    fetch = download.fetch

    def watched(*args, **kwargs):
        seen_meanwhile.append(([i.name for i in importer.list_items(inbox)],
                               [p.name for p in inbox.glob(staging.PREFIX + "*")]))
        return fetch(*args, **kwargs)
    monkeypatch.setattr(download, "fetch", watched)

    code, out = run(inbox, cfg, site(), apply=True)
    assert code == 0, out
    assert all(not items and len(hidden) == 1 for items, hidden in seen_meanwhile)
    assert [i.name for i in importer.list_items(inbox)] == ["Alpha - Fake Album"]
    assert not list(inbox.glob(".*"))
    album = inbox / "Alpha - Fake Album"
    assert sorted(p.name for p in album.iterdir()) == [
        "1-01 - First.flac", "1-02 - Second (Live).flac", "2-03 - Third.flac", "folder.jpg"]
    written = FLAC(album / "1-02 - Second (Live).flac")
    assert {k: written[k] for k in ("title", "artist", "albumartist", "date", "genre")} == {
        "title": ["Second (Live)"], "artist": ["Alpha, Zoé"], "albumartist": ["Alpha"], "date": ["2001"],
        "genre": ["Pop"]}
    assert written.pictures
    assert "FALLBACK Third: no rights of its own, recording 999 downloaded instead" in out
    assert "DOWNLOAD First as FLAC 16-bit/44.1kHz" in out
    assert "ADDED    Alpha - Fake Album" in out
    assert "albums: 1" in out and "tracks: 3" in out and "formats: 3 FLAC" in out


def test_an_incomplete_album_is_still_dropped(inbox, cfg, site):
    client = site()
    site.unavailable.add("dummy-track-token-2")
    code, out = run(inbox, cfg, client, apply=True)
    assert code == 2
    assert "SKIPPED  Second (Live): no playable format" in out
    assert "unavailable: 1 track (Second (Live))" in out
    assert len(list((inbox / "Alpha - Fake Album").glob("*.flac"))) == 2


def test_complete_only_leaves_an_incomplete_album_out(inbox, cfg, site):
    client = site()
    site.unavailable.add("dummy-track-token-2")
    code, out = run(inbox, cfg, client, apply=True, complete_only=True)
    assert code == 2
    assert "SKIPPED  Alpha - Fake Album: left out at the first track unavailable, Second (Live) " \
           "(--complete-only)" in out
    assert not any(inbox.iterdir())


def test_an_album_without_any_track_is_not_dropped(inbox, cfg, site):
    client = site()
    site.unavailable.update({"dummy-track-token-1", "dummy-track-token-2", "dummy-fallback-token"})
    code, out = run(inbox, cfg, client, apply=True)
    assert code == 1
    assert "ERROR    Alpha - Fake Album: not one track downloaded" in out
    assert not any(inbox.iterdir())


def test_a_name_taken_in_the_inbox(inbox, cfg, site):
    (inbox / "Alpha - Fake Album").mkdir()
    code, out = run(inbox, cfg, site(), apply=True)
    assert code == 0
    assert "ADDED    Alpha - Fake Album (2)" in out
    assert len(list((inbox / "Alpha - Fake Album (2)").glob("*.flac"))) == 3


def test_a_stop_removes_the_album_in_waiting(inbox, cfg, site, monkeypatch):
    fetch = download.fetch

    def then_stop(*args, **kwargs):
        written = fetch(*args, **kwargs)
        STOP.set()
        return written
    monkeypatch.setattr(download, "fetch", then_stop)
    code, out = run(inbox, cfg, site(), apply=True)
    assert code == 4
    assert "--- Deezer summary (stopped) ---" in out
    assert not any(inbox.iterdir())


def test_two_albums_of_one_name_and_tracks_of_one_title(inbox, cfg, site, http, templates):
    flac = templates[".flac"].read_bytes()
    def intro(song_id, album_id, disc):
        return http.page({"DATA": {"SNG_ID": song_id, "SNG_TITLE": "Intro", "ALB_ID": album_id,
                                   "ALB_TITLE": "Hits", "TRACK_NUMBER": "1", "DISK_NUMBER": disc,
                                   "TRACK_TOKEN": f"dummy-track-token-{song_id[-1]}"}})
    page, other = intro("107", "555", "1"), intro("108", "556", "2")
    hits = {"title": "Hits", "artist": {"name": "Various Artists"}}
    client = site({
        "https://api.deezer.com/playlist/9": http.answer(
            {"title": "Mix", "tracks": {"data": [{"id": 107}, {"id": 108}, {"id": 107}]}}),
        "https://www.deezer.com/de/track/107": page, "https://www.deezer.com/de/track/108": other,
        "https://api.deezer.com/album/555": http.answer({"id": 555, **hits}),
        "https://api.deezer.com/album/556": http.answer({"id": 556, **hits}),
        "https://stream/dummy-track-token-7": lambda **_kw: http.Response(pieces=[encrypt(flac, "107", cfg)]),
        "https://stream/dummy-track-token-8": lambda **_kw: http.Response(pieces=[encrypt(flac, "108", cfg)]),
    })
    code, out = run(inbox, cfg, client, "9", kind="playlist", apply=True)
    assert code == 0, out
    assert sorted(p.name for p in inbox.iterdir()) == ["Various Artists - Hits", "Various Artists - Hits (2)"]
    assert [p.name for p in (inbox / "Various Artists - Hits (2)").iterdir()] == ["2-01 - Intro.flac"]
    assert "tracks: 2" in out


def test_a_local_error_fails_the_item_with_a_summary(inbox, cfg, site, monkeypatch):
    def full(*_args, **_kwargs):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(download, "fetch", full)
    code, out = run(inbox, cfg, site(), apply=True)
    assert code == 1
    assert "ERROR    album 302127: [Errno 28] No space left on device" in out
    assert "--- Deezer summary ---" in out
    assert not any(inbox.iterdir())


def test_a_cover_that_fails_is_tried_once(inbox, cfg, site, http):
    client = site({COVER: http.Response(404)})
    code, out = run(inbox, cfg, client, apply=True)
    assert code == 0
    assert out.count("WARNING  cover of Fake Album") == 1
    assert [s["url"] for s in client.session.sent].count(COVER) == 1


def test_a_long_name_is_cut():
    assert len(staging.clean("é" * 300).encode()) <= staging.MAX_BYTES
    assert staging.clean(".hidden/name") == "hidden-name"
    assert staging.clean("...") == "Unknown"


def test_an_album_not_found(inbox, cfg, site, http):
    code, out = run(inbox, cfg, site({ALBUM_API: http.answer({"error": {"message": "no data"}})}), apply=True)
    assert code == 1
    assert "ERROR    album 302127: album 302127: no data" in out


def test_abandoned_downloads_are_removed(inbox, cfg, site):
    old = inbox / f"{staging.PREFIX}0123456789abcdef"
    (old / "Some Album").mkdir(parents=True)
    long_ago = time.time() - 25 * 3600
    for path in (old / "Some Album", old):
        os.utime(path, (long_ago, long_ago))
    run(inbox, cfg, site(), apply=True)
    assert not old.exists()


def test_a_playlist_dry_run(inbox, cfg, site, http):
    client = site({"https://api.deezer.com/playlist/9": http.answer(
        {"title": "Mix", "tracks": {"data": [{"id": 107}]}})})
    code, out = run(inbox, cfg, client, "9", kind="playlist")
    assert code == 0 and "PLAN     playlist Mix (1 track)" in out
    assert not any(inbox.iterdir())


def test_a_playlist_files_each_track_in_its_album(inbox, cfg, site, http, templates):
    flac = templates[".flac"].read_bytes()
    client = site({
        "https://api.deezer.com/playlist/9": http.answer({"title": "Mix", "tracks": {"data": [{"id": 107}]}}),
        "https://www.deezer.com/de/track/107": http.fixture("track.html"),
        "https://api.deezer.com/album/555": http.answer({"id": 555, "title": "Other Album",
                                                         "artist": {"name": "Alpha"}, "nb_tracks": 9}),
        "https://stream/dummy-track-token-7": lambda **_kw: http.Response(pieces=[encrypt(flac, "107", cfg)]),
        tags.COVER_URL.format("0123456789abcdef0123456789abcdef"): http.Response(404),
    })
    code, out = run(inbox, cfg, client, "9", kind="playlist", apply=True)
    assert code == 0, out
    assert "ADDED    Alpha - Other Album" in out
    assert "WARNING  cover of Other Album: cover: HTTP 404" in out
    assert FLAC(inbox / "Alpha - Other Album" / "07 - Seventh.flac")["tracktotal"] == ["9"]


def test_without_configuration_a_clear_error(inbox, monkeypatch):
    for name in ("DEEZER_ARL", "DEEZER_PRE_KEY", "DEEZER_IV"):
        monkeypatch.delenv(name, raising=False)
    state = inbox.parent / "state"
    code = deezer.main(["album", "302127", "--inbox", str(inbox), "--state-dir", str(state)])
    assert code == 1
    run_row = runs.recent(state)[0]
    assert run_row["command"] == "deezer" and run_row["code"] == 1
    assert "the Deezer download is off: set DEEZER_PRE_KEY and DEEZER_IV, and an arl" in \
        "\n".join(runs.read_log(state, run_row["id"])[0])


def sent_tokens(client):
    return [s["json"]["license_token"] for s in client.session.sent if s["url"] == GET_URL]


def test_without_a_license_token_one_is_read_from_the_arl_and_kept(inbox, cfg, site):
    client = site()
    code, out = run(inbox, cfg, client, apply=True)
    assert code == 0, out
    assert "TOKEN    license token renewed from the arl" in out and "dummy-fresh-token" not in out
    assert set(sent_tokens(client)) == {"dummy-fresh-token"}
    state = inbox.parent / "state"
    assert kept.token(state, "dummy-arl") == "dummy-fresh-token"
    assert (state / kept.TOKEN).stat().st_mode & 0o777 == 0o600
    assert b"dummy-arl" not in (state / kept.TOKEN).read_bytes()
    assert kept.token(state, "another-dummy-arl") == ""      # set aside once the arl changes
    again = site()
    assert run(inbox, cfg, again, "302127", apply=True)[0] == 0
    assert not [s for s in again.session.sent if s["url"] == GW]     # the kept token is used


def test_an_expired_token_is_renewed_once(inbox, cfg, site):
    client = site()
    kept.save_token(inbox.parent / "state", "dummy-arl", "dummy-token")
    site.expired.add("dummy-token")
    code, out = run(inbox, cfg, client, apply=True)
    assert code == 0, out
    assert out.count("TOKEN    license token renewed from the arl") == 1
    assert sent_tokens(client)[:2] == ["dummy-token", "dummy-fresh-token"]
    assert set(sent_tokens(client)[1:]) == {"dummy-fresh-token"}


def test_an_expired_token_and_a_refused_arl_end_in_one_try(inbox, cfg, site):
    client = site()
    kept.save_token(inbox.parent / "state", "dummy-arl", "dummy-token")
    site.expired.add("dummy-token")
    site.user["USER_ID"] = 0
    code, out = run(inbox, cfg, client, apply=True)
    assert code == 1
    assert len([s for s in client.session.sent if s["url"] == GW]) == 1
    assert out.count("the arl is refused by Deezer") == 1


def test_a_token_that_cannot_be_kept_still_serves(inbox, cfg, site, monkeypatch):
    def full(*_args):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(kept, "save_token", full)
    code, out = run(inbox, cfg, site(), apply=True)
    assert code == 0, out
    assert "WARNING  license token not kept in the state folder (No space left on device)" in out


def test_a_refused_arl_is_said_once(inbox, cfg, site):
    client = site()
    site.user["USER_ID"] = 0
    code, out = run(inbox, cfg, client, apply=True)
    assert code == 1
    assert "ERROR    the arl is refused by Deezer: take the arl cookie of a logged-in session again" in out
    assert not sent_tokens(client)
    state = inbox.parent / "state"
    assert kept.refused(state, "dummy-arl") and not kept.refused(state, "another-dummy-arl")
    site.user["USER_ID"] = 42
    assert run(inbox, cfg, site(), apply=True)[0] == 0
    assert kept.refused(state, "dummy-arl") is None       # forgotten once Deezer takes it again


def test_a_dry_run_reads_no_token(inbox, cfg, site):
    client = site()
    assert run(inbox, cfg, client)[0] == 0
    assert not [s for s in client.session.sent if s["url"] == GW]


def test_no_download_without_pycryptodome(inbox, cfg, site, monkeypatch):
    monkeypatch.setitem(sys.modules, "Crypto.Cipher", None)
    client = site()
    code, out = run(inbox, cfg, client, apply=True)
    assert code == 1
    assert "ERROR    pycryptodome is not installed: rebuild the image" in out
    assert not client.session.sent
    assert run(inbox, cfg, site())[0] == 0   # a dry run needs no decryption


def test_a_malformed_value_is_named_not_shown(inbox, monkeypatch):
    monkeypatch.setenv("DEEZER_ARL", "dummy-arl")
    monkeypatch.setenv("DEEZER_PRE_KEY", "short-secret")
    monkeypatch.setenv("DEEZER_IV", "0001020304050607")
    log = Buffer()
    assert deezer.run(deezer.Options(kind="album", ids=["1"], inbox=inbox), log) == 1
    assert "DEEZER_PRE_KEY must be 16 bytes long" in log.text() and "short-secret" not in log.text()


def test_downloaded_then_imported_into_the_library(inbox, cfg, site, tmp_path):
    code, _out = run(inbox, cfg, site(), apply=True)
    assert code == 0
    library, state = tmp_path / "Musique", tmp_path / "state"
    library.mkdir()
    assert importer.main(["--inbox", str(inbox), "--root", str(library), "--state-dir", str(state),
                          "--settle", "0", "--no-enrich", "--apply"]) == 0
    filed = sorted(str(p.relative_to(library)) for p in library.rglob("*.m4a"))
    assert filed == ["Alpha/Fake Album/1-01 First.m4a", "Alpha/Fake Album/1-02 Second (Live).m4a",
                     "Alpha/Fake Album/2-03 Third.m4a"]
