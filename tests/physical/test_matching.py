"""The collection's releases matched to the library's albums (physical/matching.py)."""
from music_librarian.physical import matching
from music_librarian.physical.matching import ANSWER, ID, LINK, NAME, LibraryAlbum, match, pair_key


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
    albums = [LibraryAlbum("Beatles/Abbey Road", "The Beatles", "Abbey Road (Remastered 2009)"),
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


def test_a_remix_a_live_or_another_spelling_is_only_asked():
    rid = "6311701"
    for title in ("Origin of Symmetry (XX Anniversary RemiXX)", "Origin of Symmetry [Live]"):
        album = LibraryAlbum("Muse/x", "Muse", title)
        found = match([release(rid, "Muse", "Origin Of Symmetry")], [album], {})
        assert not found.found and found.doubtful == [(found.doubtful[0][0], album)]
    for artist, title, album_title in (("Zebra", "Mambo Punk", "Mambopunk"),
                                       ("Julien Doré", "Løve Live", "Love Live"),
                                       ("Aston Villa", "Aston Villa", "Aston Villa")):
        album = LibraryAlbum("x/y", "Astonvilla" if artist == "Aston Villa" else artist, album_title)
        found = match([release("1", artist, title)], [album], {})
        assert not found.found and len(found.doubtful) == 1, (artist, title)


def test_a_release_of_various_is_the_compilation_of_its_title():
    hits = LibraryAlbum("Compilations/Hôtel Costes 8", "Stéphane Pompougnac", "Hôtel Costes 8",
                        compilation=True)
    vintage = LibraryAlbum("Compilations/Vintage FG", "Daft Punk", "Vintage FG", compilation=True)
    other = LibraryAlbum("Moby/Vintage FG", "Moby", "Vintage FG")
    owned = [release("1", "Stéphane Pompougnac", "Hôtel Costes 8"), release("2", "Various", "Vintage FG")]
    found = match(owned, [hits, vintage, other], {})
    assert set(found.found) == {hits.folder, vintage.folder} and found.found[vintage.folder][1] == NAME


def test_a_release_linked_on_the_dashboard_is_its_album():
    albums = [LibraryAlbum("Compilations/Hotel Costes 8", "Various Artists", "Hôtel Costes 8",
                           compilation=True),
              LibraryAlbum("Daft Punk/Homework", "Daft Punk", "Homework")]
    costes = release("3", "Stéphane Pompougnac", "Hôtel Costes 8")
    assert match([costes], albums, {}).absent == [costes]
    found = match([costes, release("4", "Daft Punk", "Homework")], albums, {},
                  {"3": "Compilations/Hotel Costes 8", "4": "Daft Punk/Gone"})
    assert found.found["Compilations/Hotel Costes 8"] == (costes, LINK) and not found.absent
    assert found.found["Daft Punk/Homework"][1] == NAME
    assert match([costes], albums, {}, {"3": ""}).absent == [costes]
