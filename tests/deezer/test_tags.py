import shutil

import pytest

from music_librarian.deezer import tags
from music_librarian.deezer.api import Album, Track
from music_librarian.media import tags as media_tags
from music_librarian.media.artwork import image_size
from music_librarian.media.metadata_cache import MetadataCache

TRACK = Track("101", title="Song", version="(Live)", artists=["Zoé", "Alpha"], album="Fake Album",
              track_number="2", disc_number="1", isrc="FRXXX0000001", copyright="(P) Fake Records",
              physical_release_date="2001-02-03", composers=["Jane Doe", "John Roe"],
              album_picture="0123456789abcdef")
ALBUM = Album("302127", "Fake Album", "Alpha", nb_tracks=10, release_date="2020-03-04",
              genres=["Pop", "Rock"])


def test_fields():
    assert tags.fields(TRACK, ALBUM, [TRACK, Track("102", disc_number="2")]) == {
        "title": "Song (Live)", "album": "Fake Album", "artist": "Alpha, Zoé", "albumartist": "Alpha",
        "composer": "Jane Doe, John Roe", "tracknumber": "2", "tracktotal": "10", "discnumber": "1",
        "disctotal": "2", "date": "2020", "isrc": "FRXXX0000001", "copyright": "(P) Fake Records",
        "genre": "Pop, Rock"}


def test_the_year_alone_from_the_album_else_the_track():
    assert tags.year(ALBUM, TRACK) == "2020"
    assert tags.year(Album("1"), TRACK) == "2001"
    assert tags.year(Album("1", release_date="0000-00-00"), Track("1")) == "0000"
    assert tags.year(Album("1"), Track("1", physical_release_date="unknown")) == ""


def test_no_genre_without_one_from_deezer():
    assert "genre" not in tags.fields(TRACK, Album("1", "Fake Album", "Alpha"))


def test_cover_url():
    assert tags.cover_url(Album("1", cover_xl="https://cdn/xl.jpg"), TRACK) == "https://cdn/xl.jpg"
    assert tags.cover_url(Album("1"), TRACK) == \
        "https://cdn-images.dzcdn.net/images/cover/0123456789abcdef/1000x1000-000000-80-0-0.jpg"
    assert tags.cover_url(Album("1"), Track("1")) == ""


@pytest.mark.parametrize("ext", [".flac", ".mp3"])
def test_written_tags_read_back_by_the_library(ext, templates, image, tmp_path):
    path = tmp_path / f"02 - Song{ext}"
    shutil.copy(templates[ext], path)
    tags.write(path, tags.fields(TRACK, ALBUM, [TRACK]))
    tags.embed_cover(path, image("folder.jpg", 32, 24))

    snap = media_tags.read_snapshot(path)
    with MetadataCache(tmp_path / "state") as cache:
        assert cache.snapshot(path) == snap
    found = {k: v[0] for k, v in snap["tags"].items()}
    assert {k: found.get(k) for k in ("title", "artist", "albumartist", "album", "composer", "date", "genre",
                                      "isrc", "copyright")} == {
        "title": "Song (Live)", "artist": "Alpha, Zoé", "albumartist": "Alpha", "album": "Fake Album",
        "composer": "Jane Doe, John Roe", "date": "2020", "genre": "Pop, Rock", "isrc": "FRXXX0000001",
        "copyright": "(P) Fake Records"}
    if ext == ".flac":
        assert (found["tracknumber"], found["tracktotal"], found["discnumber"], found["disctotal"]) == \
            ("2", "10", "1", "1")
    else:
        assert (found["tracknumber"], found["discnumber"]) == ("2/10", "1/1")
    assert snap["cover"]
    assert image_size(media_tags.read_cover(path)) == ("jpeg", 32, 24)


def test_a_file_that_cannot_be_tagged(tmp_path):
    path = tmp_path / "broken.flac"
    path.write_bytes(b"not audio")
    with pytest.raises(tags.TagWriteError):
        tags.write(path, {"title": "x"})
    with pytest.raises(tags.TagWriteError):
        tags.embed_cover(path, b"\xff\xd8")
