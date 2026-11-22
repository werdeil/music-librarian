import pytest
import requests
from Crypto.Cipher import Blowfish

from music_librarian.deezer import download
from music_librarian.deezer.crypto import CHUNK, blowfish_key
from music_librarian.deezer.client import DeezerError

URL = "https://stream/track"


def encrypt(data, song_id, cfg):
    key = blowfish_key(song_id, cfg.pre_key)
    chunks = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)]
    return b"".join(Blowfish.new(key, Blowfish.MODE_CBC, cfg.iv).encrypt(c) if i % 3 == 0 and len(c) == CHUNK
                    else c for i, c in enumerate(chunks))


def pieces(data, size=1000):
    return [data[i:i + size] for i in range(0, len(data), size)]


def test_a_stream_gives_back_the_file_it_was_made_from(deezer, http, cfg, templates, tmp_path):
    original = templates[".flac"].read_bytes()
    client = deezer({URL: http.Response(pieces=pieces(encrypt(original, "101", cfg)))})
    dest = tmp_path / "01 - First.flac"
    assert download.fetch(client, URL, "101", dest, cfg) == len(original)
    assert dest.read_bytes() == original
    assert download.resolution(dest) == "16-bit/44.1kHz"


def test_a_read_cut_short_starts_the_file_over(deezer, http, cfg, templates, tmp_path):
    original = templates[".mp3"].read_bytes()
    encrypted = encrypt(original, "101", cfg)
    cut = http.Response(pieces=pieces(encrypted)[:2], error=requests.exceptions.ChunkedEncodingError("cut"))
    client, pauses = deezer({URL: [cut, http.Response(pieces=pieces(encrypted))]}), []
    dest = tmp_path / "01 - First.mp3"
    assert download.fetch(client, URL, "101", dest, cfg, sleep=pauses.append) == len(original)
    assert dest.read_bytes() == original
    assert pauses == [0.5]


def test_ten_reads_cut_short_give_up(deezer, http, cfg, tmp_path):
    cut = http.Response(pieces=[b"x" * 10], error=requests.exceptions.ChunkedEncodingError("cut"))
    client = deezer({URL: cut})
    dest = tmp_path / "01 - First.flac"
    with pytest.raises(DeezerError, match="cut short 10 times"):
        download.fetch(client, URL, "101", dest, cfg, sleep=lambda _s: None)
    assert len(client.session.sent) == 10
    assert not dest.exists()


def test_a_local_error_is_not_taken_for_a_cut_stream(deezer, http, cfg, tmp_path):
    client = deezer({URL: http.Response(pieces=[b"x" * 10])})
    with pytest.raises(OSError):
        download.fetch(client, URL, "101", tmp_path / "missing" / "x.flac", cfg)
    assert len(client.session.sent) == 1


def test_a_refused_stream_fails_at_once(deezer, http, cfg, tmp_path):
    client = deezer({URL: http.Response(403)})
    with pytest.raises(DeezerError, match="HTTP 403"):
        download.fetch(client, URL, "101", tmp_path / "x.flac", cfg)
    assert len(client.session.sent) == 1


def test_cover(deezer, http, tmp_path, image):
    jpeg = image("cover.jpg", 10, 10)
    client = deezer({"https://cdn/xl.jpg": http.Response(body=jpeg),
                     "https://cdn/gone.jpg": http.Response(404)})
    download.cover(client, "https://cdn/xl.jpg", tmp_path / "folder.jpg")
    assert (tmp_path / "folder.jpg").read_bytes() == jpeg
    with pytest.raises(DeezerError, match="HTTP 404"):
        download.cover(client, "https://cdn/gone.jpg", tmp_path / "other.jpg")
