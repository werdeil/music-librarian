"""Tag fields read and written in each format (media/fields.py)."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import pytest
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4

from music_librarian.media import fields
from music_librarian.organize.scan import read_tags

FORMATS = (".m4a", ".mp3", ".flac")


@pytest.fixture
def track(make_track, tmp_path):
    def make(ext, **tags):
        base = dict(title="Halo", artist="Beyoncé", albumartist="Beyoncé", album="I Am… Sasha Fierce",
                    track=3, disc=1, disctotal=2, composer="Ryan Tedder")
        base.update(tags)
        return make_track(tmp_path / f"t{ext}", **base)
    return make


@pytest.mark.parametrize("ext", FORMATS)
def test_read(track, ext):
    values = fields.read(track(ext))
    assert values["title"] == ["Halo"] and values["albumartist"] == ["Beyoncé"]
    assert (values["track"], values["disc"], values["disctotal"]) == (["3"], ["1"], ["2"])
    assert values["composer"] == ["Ryan Tedder"] and values["compilation"] == [] and values["year"] == []


@pytest.mark.parametrize("ext", FORMATS)
@pytest.mark.parametrize("name, value", [
    ("title", "Single Ladies"), ("artist", "Beyoncé & Jay-Z"), ("albumartist", "Various"),
    ("album", "4"), ("year", "2011"), ("genre", "R&B"), ("composer", "The-Dream"),
    ("albumartist_mbid", "859d0860-d480-4efd-970c-c05d5f1776b8"),
])
def test_text_fields_round_trip(track, ext, name, value):
    p = track(ext)
    fields.write(p, name, [value])
    assert fields.read(p)[name] == [value]
    fields.write(p, name, [])
    assert fields.read(p)[name] == []


@pytest.mark.parametrize("ext", FORMATS)
def test_number_and_its_total_kept_apart(track, ext):
    p = track(ext)
    fields.write(p, "tracktotal", ["12"])
    fields.write(p, "track", ["5"])
    fields.write(p, "disc", ["2"])
    values = fields.read(p)
    assert (values["track"], values["tracktotal"], values["disc"], values["disctotal"]) == \
        (["5"], ["12"], ["2"], ["2"])
    fields.write(p, "disctotal", [])
    assert fields.read(p)["disctotal"] == [] and fields.read(p)["disc"] == ["2"]
    tags = read_tags(p)
    assert (tags["track"], tags["disc"], tags["disctotal"]) == (5, 2, None)


@pytest.mark.parametrize("ext", FORMATS)
def test_compilation_flag(track, ext):
    p = track(ext)
    fields.write(p, "compilation", ["yes"])
    assert fields.read(p)["compilation"] == ["1"] and read_tags(p)["compilation"]
    fields.write(p, "compilation", ["0"])
    assert fields.read(p)["compilation"] == [] and not read_tags(p)["compilation"]


def test_formats_own_tags(track):
    m4a, mp3, flac = (track(ext) for ext in FORMATS)
    for p in (m4a, mp3, flac):
        fields.write(p, "genre", ["Pop"])
        fields.write(p, "year", ["2008"])
    assert MP4(m4a).tags["\xa9gen"] == ["Pop"] and MP4(m4a).tags["\xa9day"] == ["2008"]
    assert MP3(mp3).tags["TCON"].genres == ["Pop"] and str(MP3(mp3).tags["TDRC"].text[0]) == "2008"
    assert FLAC(flac)["genre"] == ["Pop"] and FLAC(flac)["date"] == ["2008"]


def test_flac_variants_read_then_replaced(track):
    p = track(".flac", albumartist=None, extra={"album artist": "Beyonce", "tracknumber": "3/12"})
    values = fields.read(p)
    assert (values["albumartist"], values["track"], values["tracktotal"]) == (["Beyonce"], ["3"], ["12"])
    fields.write(p, "albumartist", ["Beyoncé"])
    fields.write(p, "track", ["4"])
    tags = FLAC(p)
    assert "album artist" not in tags and tags["albumartist"] == ["Beyoncé"]
    assert tags["tracknumber"] == ["4"] and tags["tracktotal"] == ["12"]


def test_file_without_tags(tmp_path, templates):
    p = tmp_path / "bare.mp3"
    p.write_bytes(templates[".mp3"].read_bytes())
    MP3(p).delete()
    assert fields.read(p)["title"] == []
    fields.write(p, "title", ["Halo"])
    assert fields.read(p)["title"] == ["Halo"]


def test_format_not_handled(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("not audio", encoding="utf-8")
    with pytest.raises(fields.FieldError):
        fields.read(p)


@pytest.mark.parametrize("name, values, written", [
    ("track", [" 07 "], ["7"]), ("disc", [""], []), ("compilation", ["TRUE"], ["1"]),
    ("compilation", ["no"], []), ("title", ["  Halo "], ["Halo"]),
])
def test_check(name, values, written):
    assert fields.check(name, values) == written


@pytest.mark.parametrize("name, values", [
    ("track", ["0"]), ("track", ["three"]), ("disctotal", ["2", "3"]), ("compilation", ["maybe"]),
    ("rating", ["5"]),
])
def test_check_refuses(name, values):
    with pytest.raises(fields.FieldError):
        fields.check(name, values)
