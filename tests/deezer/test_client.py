import pytest
import requests

from music_librarian.deezer.client import Client, DeezerError


def test_requests_are_half_a_second_apart_across_clients(deezer, http):
    now, slept = [10.0], []

    def sleep(seconds):
        slept.append(round(seconds, 3))
        now[0] += seconds
    first, second = (Client("dummy-arl", session=deezer({"https://x/": http.Response()}).session,
                            clock=lambda: now[0], sleep=sleep) for _ in range(2))
    first.get("https://x/")
    now[0] += 0.2
    second.get("https://x/")
    now[0] += 1.0
    first.get("https://x/")
    assert slept == [0.3]


def test_the_arl_cookie_and_the_browser_headers_are_sent(deezer, http):
    client = deezer({"https://www.deezer.com/de/album/1": http.Response()})
    client.get("https://www.deezer.com/de/album/1")
    sent = client.session.sent[0]
    assert sent["cookies"] == {"arl": "dummy-arl"}
    assert sent["headers"]["Origin"] == "https://www.deezer.com"
    assert sent["headers"]["Referer"] == "https://www.deezer.com/"
    assert sent["headers"]["X-Requested-With"] == "XMLHttpRequest"
    assert "Mozilla" in sent["headers"]["User-Agent"]


def test_a_cover_goes_without_the_cookie(deezer, http):
    client = deezer({"https://cdn/cover.jpg": http.Response()})
    client.get("https://cdn/cover.jpg", cookie=False)
    assert client.session.sent[0]["cookies"] is None


def test_one_transport_error_is_tried_again(deezer, http):
    client = deezer({"https://x/": [requests.ConnectionError("reset"), http.Response(204)]})
    assert client.get("https://x/").status_code == 204
    assert len(client.session.sent) == 2


def test_two_transport_errors_fail_naming_the_host_only(deezer):
    client = deezer({"https://api.deezer.com/playlist/1": requests.ConnectionError("reset")})
    with pytest.raises(DeezerError, match=r"^ConnectionError on api.deezer.com$"):
        client.get("https://api.deezer.com/playlist/1", params={"access_token": "dummy-token"})
    assert len(client.session.sent) == 2
    assert "dummy-arl" not in repr(client)
