"""Deezer's stream encryption: one 2048-byte chunk in three is Blowfish-CBC, the others are plain."""
import hashlib

from Crypto.Cipher import Blowfish

CHUNK = 2048


def blowfish_key(song_id, pre_key):
    """The 16-byte key of a recording: the halves of its id's MD5 hex digest and `pre_key` XORed."""
    digest = hashlib.md5(str(song_id).encode()).hexdigest().encode()
    return bytes(digest[i] ^ digest[i + 16] ^ pre_key[i] for i in range(16))


def decrypt_stream(chunks, key, iv):
    """The plain bytes of an encrypted stream given in pieces of any size. Raises ValueError for an
    IV that is not 8 bytes long."""
    Blowfish.new(key, Blowfish.MODE_CBC, iv)
    buf = bytearray()
    index = 0
    for piece in chunks:
        buf += piece
        while len(buf) >= CHUNK:
            block, buf = bytes(buf[:CHUNK]), buf[CHUNK:]
            # each encrypted chunk starts a new CBC chain from the IV
            yield Blowfish.new(key, Blowfish.MODE_CBC, iv).decrypt(block) if index % 3 == 0 else block
            index += 1
    if buf:
        yield bytes(buf)
