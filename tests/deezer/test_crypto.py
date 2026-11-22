import pytest
from Crypto.Cipher import Blowfish

from music_librarian.deezer.crypto import CHUNK, blowfish_key, decrypt_stream

PRE_KEY = b"AAAAAAAAAAAAAAAA"
IV = bytes.fromhex("0001020304050607")


def encrypt(data, key):
    """A stream as Deezer serves it: every third whole chunk encrypted from the IV."""
    chunks = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)]
    return b"".join(Blowfish.new(key, Blowfish.MODE_CBC, IV).encrypt(c) if i % 3 == 0 and len(c) == CHUNK
                    else c for i, c in enumerate(chunks))


def test_key_matches_the_go_implementation():
    # produced by deezer-music-download's calcBfKey with the same dummy pre_key
    assert blowfish_key("123456", PRE_KEY).hex() == "461544164012474f134a13464c401346"
    assert blowfish_key(42, PRE_KEY).hex() == "444811471347124a14131010434a1240"


def test_key_depends_on_the_song_and_the_pre_key():
    key = blowfish_key("123456", PRE_KEY)
    assert len(key) == 16
    assert key != blowfish_key("654321", PRE_KEY)
    assert key != blowfish_key("123456", b"BBBBBBBBBBBBBBBB")


def test_a_chunk_decrypts_as_the_go_implementation_encrypts_it():
    key = blowfish_key("42", PRE_KEY)
    # the first 32 bytes deezer-music-download's Blowfish-CBC gives this plain chunk
    head = bytes.fromhex("a68658f93dcf8c925a86054b4d431336a78b34e52d7a00db6d914cab4a1fe779")
    encrypted = encrypt(b"deezer!!" * (CHUNK // 8), key)
    assert encrypted[:32] == head
    assert b"".join(decrypt_stream([encrypted], key, IV)) == b"deezer!!" * (CHUNK // 8)


def test_round_trip_over_irregular_pieces():
    key = blowfish_key("42", PRE_KEY)
    plain = bytes(range(256)) * ((3 * CHUNK + 100) // 256) + bytes(range((3 * CHUNK + 100) % 256))
    assert len(plain) == 3 * CHUNK + 100
    encrypted = encrypt(plain, key)
    assert encrypted[CHUNK:3 * CHUNK] == plain[CHUNK:3 * CHUNK]   # the other chunks are plain
    sizes, pieces, at = [1, 700, 2047, 3000, 5, 999999], [], 0
    for size in sizes:
        pieces.append(encrypted[at:at + size])
        at += size
    assert b"".join(decrypt_stream(pieces, key, IV)) == plain


def test_a_last_incomplete_chunk_is_kept_as_it_is():
    key = blowfish_key("42", PRE_KEY)
    assert b"".join(decrypt_stream([b"x" * 100], key, IV)) == b"x" * 100


def test_an_invalid_iv_is_refused():
    with pytest.raises(ValueError):
        decrypt_stream([], blowfish_key("42", PRE_KEY), b"\x00" * 3)
