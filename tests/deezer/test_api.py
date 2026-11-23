# pylint: disable=redefined-outer-name  # pytest fixtures
import pytest

from music_librarian.deezer.api import (GET_URL, GW, Album, Api, Playlist, TokenExpired, Track, app_state,
                                        disc_total, pick_best, stream_url, user_license_token)
from music_librarian.deezer.client import DeezerError

ALBUM_API = "https://api.deezer.com/album/302127"
ALBUM_PAGE = "https://www.deezer.com/de/album/302127"
MEDIA = {"format": "FLAC", "sources": [{"provider": "ak", "url": "https://stream/ak"},
                                       {"provider": "other", "url": "https://stream/other"}]}


@pytest.fixture
def api(deezer):
    def make(routes):
        return Api(deezer(routes), license_token="dummy-token")
    return make


def media(*formats):
    return [{"format": f, "sources": [{"url": f"https://stream/{f}"}]} for f in formats]


@pytest.mark.parametrize("formats, best", [
    (("MP3_128", "MP3_320", "FLAC"), "FLAC"),
    (("MP3_128", "MP3_320"), "MP3_320"),
    (("MP3_128",), "MP3_128"),
    (("UNKNOWN", "mp3_320"), "mp3_320"),
])
def test_pick_best(formats, best):
    assert pick_best(media(*formats))["format"] == best


def test_stream_url():
    assert stream_url(MEDIA) == "https://stream/ak"
    assert stream_url({"format": "MP3_320", "sources": [{"url": "https://stream/x"}]}) == "https://stream/x"
    assert stream_url({"format": "FLAC", "sources": [{"url": "abc"}]}) == \
        "https://cdns-preview-d.dzcdn.net/stream/c-7/abc-9.flac"
    with pytest.raises(DeezerError):
        stream_url({"format": "FLAC", "sources": []})


def test_app_state_of_a_saved_page(http):
    state = app_state(http.fixture("album.html").text)
    assert [s["SNG_TITLE"] for s in state["SONGS"]["data"]] == ["First", "Second", "Third"]
    for html in ("<html></html>", "window.__DZR_APP_STATE__ = {", "window.__DZR_APP_STATE__ = {</script>"):
        with pytest.raises(DeezerError):
            app_state(html)


def test_album_tracks_of_its_page(api, http):
    tracks = api({ALBUM_PAGE: http.fixture("album.html")}).album_tracks("302127")
    first, second, third = tracks
    assert (first.song_id, first.title, first.track_number) == ("101", "First", "1")
    assert first.artists == ["Zoé", "Alpha"]
    assert first.composers == ["Jane Doe", "John Roe"]   # SNG_CONTRIBUTORS as an object
    assert second.composers == [] and second.version == "(Live)"
    assert third.composers == ["Jane Doe"]               # as a list
    assert third.fallback.song_id == "999" and third.fallback.track_token == "dummy-fallback-token"
    assert disc_total(Album("302127"), tracks) == 2
    assert disc_total(Album("302127", nb_discs=3), tracks) == 3


def test_album_of_the_api(api, http):
    album = api({ALBUM_API: http.answer({
        "id": 302127, "title": "Fake Album", "artist": {"name": "Alpha"}, "nb_tracks": 3,
        "release_date": "2001-05-07", "cover_xl": "https://cdn/xl.jpg", "label": "Fake Label",
        "genres": {"data": [{"name": "Pop"}, {"name": " "}, {"name": "Rock"}]}})}).album("302127")
    assert album == Album("302127", "Fake Album", "Alpha", 3, 0, "2001-05-07", "https://cdn/xl.jpg",
                          ["Pop", "Rock"])


def test_album_errors(api, http):
    with pytest.raises(DeezerError, match="HTTP 500"):
        api({ALBUM_API: http.Response(500)}).album("302127")
    with pytest.raises(DeezerError, match="no data"):
        api({ALBUM_API: http.answer({"error": {"message": "no data", "code": 800}})}).album("302127")
    with pytest.raises(DeezerError, match="no SONGS"):
        api({ALBUM_PAGE: http.page({"DATA": {}})}).album_tracks("302127")


def test_track_of_its_page(api, http):
    track = api({"https://www.deezer.com/de/track/107": http.fixture("track.html")}).track("107")
    assert (track.song_id, track.album, track.album_id) == ("107", "Other Album", "555")


def test_playlist_of_the_api_sends_the_license_token(api, http):
    session_api = api({"https://api.deezer.com/playlist/9": http.answer(
        {"title": "Mix", "tracks": {"data": [{"id": 7}, {"id": 8}], "total": 2}})})
    assert session_api.playlist("9") == Playlist("9", "Mix", ["7", "8"])
    assert session_api.client.session.sent[0]["params"] == {"access_token": "dummy-token"}


def test_playlist_of_the_api_over_several_pages(api, http):
    following = "https://api.deezer.com/playlist/9/tracks?index=2"
    playlist = api({"https://api.deezer.com/playlist/9": http.answer(
                        {"title": "Mix", "tracks": {"data": [{"id": 1}, {"id": 2}], "next": following}}),
                    following: http.answer({"data": [{"id": 3}], "total": 3})}).playlist("9")
    assert playlist.track_ids == ["1", "2", "3"]


def test_playlist_falls_back_on_its_page(api, http):
    playlist = api({"https://api.deezer.com/playlist/908622995": http.answer({"tracks": {"data": []}}),
                    "https://www.deezer.com/de/playlist/908622995": http.fixture("playlist.html")}
                   ).playlist("908622995")
    assert (playlist.title, playlist.track_ids) == ("Fake Playlist", ["7", "8"])
    with pytest.raises(DeezerError):
        api({"https://api.deezer.com/playlist/1": http.Response(403),
             "https://www.deezer.com/de/playlist/1": http.page({"DATA": {"TITLE": "Empty"}})}).playlist("1")


def test_resolve_asks_every_format_and_keeps_the_best(api, http):
    found = api({GET_URL: http.answer({"data": [{"media": [
        {"format": "MP3_320", "sources": [{"url": "https://stream/320"}]}, MEDIA]}]})})
    assert found.resolve("dummy-track-token-1") == ("FLAC", "https://stream/ak")
    body = found.client.session.sent[0]["json"]
    assert body["license_token"] == "dummy-token" and body["track_tokens"] == ["dummy-track-token-1"]
    assert [f["format"] for f in body["media"][0]["formats"]] == ["FLAC", "MP3_320", "MP3_128"]
    assert {f["cipher"] for f in body["media"][0]["formats"]} == {"BF_CBC_STRIPE"}


@pytest.mark.parametrize("response, message", [
    (("answer", {"errors": [{"code": 1002, "message": "Token has no rights"}]}, 403),
     "error 1002: Token has no rights"),
    (("Response", 502), "HTTP 502"),
    (("answer", {"data": []}), "empty data"),
    (("answer", {"data": [{"errors": [{"code": 2002, "message": "Track token has no sufficient rights"}]}]}),
     "Track token has no sufficient rights"),
    (("answer", {"data": [{"media": []}]}), "no media available"),
])
def test_each_get_url_error(api, http, response, message):
    kind, *args = response
    with pytest.raises(DeezerError, match=f"no playable format among FLAC/MP3_320/MP3_128 \\({message}\\)"):
        api({GET_URL: getattr(http, kind)(*args)}).resolve("dummy-track-token-1")


def test_stream_follows_the_fallback_recording(api, http):
    track = Track("103", track_token="dummy-track-token-3",
                  fallback=Track("999", track_token="dummy-fallback-token"))
    found = api({GET_URL: [http.answer({"data": [{"media": []}]}),
                           http.answer({"data": [{"media": [MEDIA]}]})]})
    stream = found.stream(track)
    assert (stream.format, stream.song_id, stream.fallback) == ("FLAC", "999", True)   # its key follows it
    assert [s["json"]["track_tokens"] for s in found.client.session.sent] == [["dummy-track-token-3"],
                                                                              ["dummy-fallback-token"]]


def test_stream_of_a_track_and_its_failures(api, http):
    ok = api({GET_URL: http.answer({"data": [{"media": [MEDIA]}]})}).stream(Track("1", track_token="t"))
    assert (ok.song_id, ok.fallback) == ("1", False)
    none = http.answer({"data": [{"media": []}]})
    with pytest.raises(DeezerError, match="no media available"):
        api({GET_URL: none}).stream(Track("1", track_token="t"))
    with pytest.raises(DeezerError, match="no media available"):
        api({GET_URL: none}).stream(Track("1", track_token="t", fallback=Track("2", track_token="u")))


def test_the_license_token_of_the_arl(deezer, http):
    client = deezer({GW: http.answer({"results": {"USER": {"USER_ID": 42, "OPTIONS": {
        "license_token": "dummy-fresh-token"}}}})})
    assert user_license_token(client) == "dummy-fresh-token"
    sent = client.session.sent[0]
    assert sent["params"]["method"] == "deezer.getUserData" and sent["cookies"] == {"arl": "dummy-arl"}


@pytest.mark.parametrize("answer, message", [
    ({"results": {"USER": {"USER_ID": 0, "OPTIONS": {"license_token": "anonymous"}}}}, "the arl is refused"),
    ({"results": {"USER": {"USER_ID": 42, "OPTIONS": {}}}}, "no license token"),
    ({"results": {}}, "the arl is refused"),
])
def test_no_license_token_from_the_arl(deezer, http, answer, message):
    with pytest.raises(DeezerError, match=message):
        user_license_token(deezer({GW: http.answer(answer)}))


def test_an_expired_token_given_as_text(api, http):
    with pytest.raises(TokenExpired):
        api({GET_URL: http.answer({"errors": [{"code": "1001", "message": "expired"}]}, 403)}).resolve("t")
    with pytest.raises(TokenExpired):
        api({GET_URL: http.answer({"data": [{"errors": [{"code": 1001, "message": "expired"}]}]})}
            ).resolve("t")


def test_an_expired_token_without_renewal(api, http):
    expired = http.answer({"errors": [{"code": 1001, "message": "License token is expired."}]}, 403)
    with pytest.raises(TokenExpired, match="error 1001: License token is expired."):
        api({GET_URL: expired}).resolve("dummy-track-token-1")
    renewed = Api(api({GET_URL: [expired, http.answer({"data": [{"media": [MEDIA]}]})]}).client,
                  "dummy-old-token", renew=lambda: "dummy-new-token")
    assert renewed.resolve("dummy-track-token-1") == ("FLAC", "https://stream/ak")
    assert [s["json"]["license_token"] for s in renewed.client.session.sent] == ["dummy-old-token",
                                                                                 "dummy-new-token"]
