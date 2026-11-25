"""The collection's releases matched to the library's albums (physical/matching.py)."""
from music_librarian.physical import matching
from music_librarian.physical.matching import ANSWER, ID, NAME, LibraryAlbum, match, pair_key


def release(rid, artist, title, artists=None, year="", formats=("CD",)):
    return dict(id=rid, artist=artist, artists=artists or [artist], title=title, year=year,
                formats=list(formats))


def test_keys_set_aside_what_editions_add():
    assert matching.title_key("Abbey Road (Remastered 2009)") == matching.title_key("Abbey Road")
    assert matching.title_key("[Deluxe] Lemonade") == "lemonade"
    assert matching.title_key("Kind Of Blue = Kind Of Blue") == "kind of blue"
    assert matching.artist_key("The Beatles") == matching.artist_key("Beatles")
    assert matching.artist_key("Prince (2)") == "prince"
    assert matching.artist_key("Simon & Garfunkel") == matching.artist_key("Simon and Garfunkel")
    assert matching.artist_key("Various Artists") == matching.artist_key("Various")
    assert matching.artist_key("Beyoncé") == "beyonce"


def test_by_id_then_by_name_whatever_the_edition():
    albums = [LibraryAlbum("Beatles/Abbey Road", "The Beatles", "Abbey Road (2019 Mix)"),
              LibraryAlbum("Daft Punk/Discovery", "Daft Punk", "Discovery", {"9"}),
              LibraryAlbum("Daft Punk/Homework", "Daft Punk", "Homework")]
    found = match([release("1", "Beatles, The", "Abbey Road", artists=["The Beatles"]),
                   release("9", "Daft Punk", "Discovery (Remastered)")], albums, {})
    assert found.found == {"Beatles/Abbey Road": (found.found["Beatles/Abbey Road"][0], NAME),
                           "Daft Punk/Discovery": (found.found["Daft Punk/Discovery"][0], ID)}
    assert not found.doubtful and not found.absent


def test_close_albums_are_asked_then_answered():
    album = LibraryAlbum("Radiohead/OK Computer OKNOTOK", "Radiohead", "OK Computer OKNOTOK 1997 2017")
    r = release("5", "Radiohead", "OK Computer")
    asked = match([r], [album], {})
    assert asked.doubtful == [(r, album)] and not asked.found and not asked.absent
    yes = match([r], [album], {pair_key("5", album.key): True})
    assert yes.found == {album.folder: (r, ANSWER)} and not yes.doubtful
    no = match([r], [album], {pair_key("5", album.key): False})
    assert not no.found and not no.doubtful and no.absent == [r]


def test_a_release_of_no_album_is_absent():
    air = LibraryAlbum("Air/Moon Safari", "Air", "Moon Safari")
    found = match([release("7", "Björk", "Debut")], [air], {})
    assert [r["id"] for r in found.absent] == ["7"] and not found.found
