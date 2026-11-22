"""Deezer's stream encryption: one 2048-byte chunk in three is Blowfish-CBC, the others are plain."""
import hashlib

from music_librarian.deezer.client import DeezerError

CHUNK = 2048
MISSING = "pycryptodome is not installed: rebuild the image, or pip install -r requirements.txt"


def _blowfish():
    """pycryptodome's Blowfish, imported only to decrypt, so that a missing package never stops the
    other commands. Raises DeezerError."""
    try:
        from Crypto.Cipher import Blowfish  # pylint: disable=import-outside-toplevel
    except ImportError as exc:
        raise DeezerError(MISSING) from exc
    return Blowfish


def missing():
    """MISSING when pycryptodome cannot be imported, else None."""
    try:
        _blowfish()
    except DeezerError as exc:
        return str(exc)
    return None


def blowfish_key(song_id, pre_key):
    """The 16-byte key of a recording: the halves of its id's MD5 hex digest and `pre_key` XORed."""
    digest = hashlib.md5(str(song_id).encode()).hexdigest().encode()
    return bytes(digest[i] ^ digest[i + 16] ^ pre_key[i] for i in range(16))


def decrypt_stream(chunks, key, iv):
    """The plain bytes of an encrypted stream given in pieces of any size, as an iterator.
    Raises ValueError at once for a key or an IV Blowfish refuses, DeezerError without pycryptodome."""
    blowfish = _blowfish()
    blowfish.new(key, blowfish.MODE_CBC, iv)
    return _decrypt(blowfish, chunks, key, iv)


def _decrypt(blowfish, chunks, key, iv):
    buf = bytearray()
    index = 0
    for piece in chunks:
        buf += piece
        while len(buf) >= CHUNK:
            block, buf = bytes(buf[:CHUNK]), buf[CHUNK:]
            # each encrypted chunk starts a new CBC chain from the IV
            yield blowfish.new(key, blowfish.MODE_CBC, iv).decrypt(block) if index % 3 == 0 else block
            index += 1
    if buf:
        yield bytes(buf)
