"""A track's stream downloaded and decrypted into a file, and the album's cover."""
import os
import time

import requests
from mutagen.flac import FLAC

from music_librarian.deezer import crypto
from music_librarian.deezer.client import DeezerError

ATTEMPTS = 10
PAUSE = 0.5
PIECE = 64 * 1024


def fetch(client, url, song_id, dest, cfg, attempts=ATTEMPTS, sleep=time.sleep):
    """Download the stream at `url` into `dest`, decrypted with the key of `song_id`; return the
    number of bytes written. A read cut short starts the file over, up to `attempts` times in all.
    Raises DeezerError, `dest` then removed."""
    key = crypto.blowfish_key(song_id, cfg.pre_key)
    try:
        for attempt in range(1, attempts + 1):
            response = client.get(url, stream=True)
            try:
                if response.status_code != 200:
                    raise DeezerError(f"stream: HTTP {response.status_code}")
                try:
                    return _write(_pieces(response), dest, key, cfg.iv)
                except _CutShort as exc:
                    if attempt == attempts:
                        cause = type(exc.__cause__).__name__
                        raise DeezerError(f"stream cut short {attempts} times ({cause})") from exc
                    sleep(PAUSE)
            finally:
                response.close()
        raise DeezerError("no attempt made")
    except BaseException:
        if os.path.exists(dest):
            os.remove(dest)
        raise


class _CutShort(Exception):
    """The stream's body could not be read to its end."""


def _pieces(response):
    """The stream's body; a read cut short raises _CutShort, a local error (a full disk) does not."""
    pieces = response.iter_content(PIECE)
    while True:
        try:
            piece = next(pieces)
        except StopIteration:
            return
        except requests.RequestException as exc:
            raise _CutShort from exc
        yield piece


def _write(pieces, dest, key, iv):
    written = 0
    with open(dest, "wb") as out:
        for block in crypto.decrypt_stream(pieces, key, iv):
            out.write(block)
            written += len(block)
    return written


def cover(client, url, dest):
    """Save the image at `url` as `dest`. Raises DeezerError."""
    response = client.get(url, cookie=False)
    try:
        if response.status_code != 200:
            raise DeezerError(f"cover: HTTP {response.status_code}")
        data = response.content
    finally:
        response.close()
    if not data:
        raise DeezerError("cover: empty")
    with open(dest, "wb") as out:
        out.write(data)


def resolution(path):
    """A FLAC file's bit depth and sample rate, as "24-bit/96kHz". Raises mutagen's errors."""
    info = FLAC(path).info
    return f"{info.bits_per_sample}-bit/{info.sample_rate / 1000:g}kHz"
