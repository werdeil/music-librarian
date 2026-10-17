"""Règles de nommage, à partir des cas réels de la bibliothèque."""
import os
import unicodedata
from pathlib import Path

import pytest

from music_ranger import clean, is_in_place, read_tags, target_rel


def tags(**kw):
    base = dict(albumartist=None, artist=None, album=None, title=None,
                track=None, disc=None, disctotal=None, compilation=False)
    base.update(kw)
    return base


# ---------- clean ----------

@pytest.mark.parametrize("raw, expected", [
    ("Who Are You?", "Who Are You_"),
    ("Live: Paris", "Live_ Paris"),
    ('The "Best" Of', "The _Best_ Of"),
    ("Either | Or", "Either _ Or"),
    ("Don’t Stop", "Don_t Stop"),            # ’
    ("Don´t Stop", "Don_t Stop"),            # ´
    ("AC/DC", "AC_DC"),
    ("‘a’ “b” *<>\\", "_a_ _b_ ____"),
    (".38 Special", "_38 Special"),
    ("  espaces  ", "espaces"),
    ("", "_"),
])
def test_clean(raw, expected):
    assert clean(raw) == expected


def test_clean_trailing_dot_only_for_dirs():
    assert clean("Vol. 2 etc.", is_dir=True) == "Vol. 2 etc_"
    assert clean("Vol. 2 etc.") == "Vol. 2 etc."


def test_clean_normalizes_to_nfc():
    nfd = unicodedata.normalize("NFD", "Beyoncé")
    assert clean(nfd) == unicodedata.normalize("NFC", "Beyoncé")


# ---------- target_rel ----------

def test_target_basic():
    t = tags(albumartist="Queen", artist="Queen", album="Jazz", title="Mustapha", track=1)
    assert target_rel(t, ".M4A", "Compilations") == Path("Queen/Jazz/01 Mustapha.m4a")


def test_target_falls_back_on_artist():
    t = tags(artist="Björk", album="Debut", title="Human Behaviour", track=1)
    assert target_rel(t, ".m4a", "Compilations") == Path("Björk/Debut/01 Human Behaviour.m4a")


def test_target_compilation():
    t = tags(albumartist="Various", artist="Moby", album="Hits: 1999", title="Porcelain",
             track=7, compilation=True)
    assert target_rel(t, ".m4a", "Compilations") == Path("Compilations/Hits_ 1999/07 Porcelain.m4a")


@pytest.mark.parametrize("disc, disctotal, prefix", [
    (1, 2, "1-03 "),
    (2, None, "2-03 "),   # disque 2 sans total : multi-disques quand même
    (1, 1, "03 "),
    (None, None, "03 "),
    (None, 2, "1-03 "),
])
def test_target_multidisc(disc, disctotal, prefix):
    t = tags(artist="A", album="B", title="C", track=3, disc=disc, disctotal=disctotal)
    assert target_rel(t, ".mp3", "Compilations").name == f"{prefix}C.mp3"


@pytest.mark.parametrize("track, disc, name", [
    (1, None, "01 ...And The Gods Made Love.m4a"),  # cas réel : Electric Ladyland
    (1, 2, "2-01 ...And The Gods Made Love.m4a"),
    (None, None, "_..And The Gods Made Love.m4a"),  # sans numéro : pas de fichier caché
])
def test_target_title_leading_dot(track, disc, name):
    t = tags(artist="A", album="...Hits", title="...And The Gods Made Love", track=track,
             disc=disc)
    assert target_rel(t, ".m4a", "C") == Path("A/_..Hits") / name


def test_target_missing_tags():
    assert target_rel(tags(artist="A", album="B", track=1), ".m4a", "C") is None
    assert target_rel(tags(title="T"), ".m4a", "C") == Path("Unknown Artist/Unknown Album/T.m4a")
    assert target_rel(tags(artist="A", album="B", title="T"), ".m4a", "C") == Path("A/B/T.m4a")


# ---------- is_in_place ----------

def test_in_place_nfd_vs_nfc():
    cur = Path(unicodedata.normalize("NFD", "Beyoncé/Lemonade/01 Pray You Catch Me.m4a"))
    tgt = Path(unicodedata.normalize("NFC", "Beyoncé/Lemonade/01 Pray You Catch Me.m4a"))
    assert is_in_place(cur, tgt, strict_case=False)


def test_in_place_case_only():
    cur, tgt = Path("ACDC/Back in black/01 Hells Bells.m4a"), Path("ACDC/Back In Black/01 Hells Bells.m4a")
    assert is_in_place(cur, tgt, strict_case=False)
    assert not is_in_place(cur, tgt, strict_case=True)


def test_in_place_truncated():
    cur = Path("A/B/01 Ceci est un titre tellement long qu.m4a")
    tgt = Path("A/B/01 Ceci est un titre tellement long qu'Apple Music l'a coupé.m4a")
    assert is_in_place(cur, tgt, strict_case=False)
    # trop court pour être un nom tronqué
    assert not is_in_place(Path("A/B/01 Ceci.m4a"), tgt, strict_case=False)


def test_u0092_in_existing_name_is_renamed():
    # cas réel, Queens of the Stone Age/Villains : caractère invisible U+0092
    # (octets C2 92) devant l'apostrophe, tag correct
    cur = Path("Queens of the Stone Age/Villains/01 Feet Don\u0092't Fail Me.m4a")
    t = tags(albumartist="Queens of the Stone Age", album="Villains",
             title="Feet Don't Fail Me", track=1)
    tgt = target_rel(t, ".m4a", "Compilations")
    assert tgt.name == "01 Feet Don't Fail Me.m4a"
    assert not is_in_place(cur, tgt, strict_case=False)


def test_raw_x92_byte_in_existing_name_is_renamed():
    # variante : octet \x92 brut (CP1252), nom non UTF-8
    name = os.fsdecode(b"03 Feet Don\x92t Fail Me.m4a")
    cur = Path("Queens of the Stone Age/Villains") / name
    t = tags(albumartist="Queens of the Stone Age", album="Villains",
             title="Feet Don’t Fail Me", track=3)
    tgt = target_rel(t, ".m4a", "Compilations")
    assert tgt.name == "03 Feet Don_t Fail Me.m4a"
    assert not is_in_place(cur, tgt, strict_case=False)


def test_extension_must_match():
    assert not is_in_place(Path("A/B/01 T.mp3"), Path("A/B/01 T.m4a"), strict_case=False)


# ---------- read_tags (vrais fichiers) ----------

@pytest.mark.parametrize("ext", [".m4a", ".mp3", ".flac"])
def test_read_tags_all_formats(tmp_path, make_track, ext):
    p = make_track(tmp_path / f"x{ext}", title="T", artist="A", albumartist="AA",
                   album="B", track=4, disc=2, disctotal=3, compilation=True)
    t = read_tags(p)
    assert (t["title"], t["artist"], t["albumartist"], t["album"]) == ("T", "A", "AA", "B")
    assert (t["track"], t["disc"], t["disctotal"], t["compilation"]) == (4, 2, 3, True)


def test_read_tags_without_compilation(tmp_path, make_track):
    assert read_tags(make_track(tmp_path / "x.m4a", title="T"))["compilation"] is False
