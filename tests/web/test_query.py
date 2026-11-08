"""The library's search, track by track (web/query.py)."""
import pytest
from werkzeug.datastructures import MultiDict

from music_librarian.web import query


def track(name="01 Song.m4a", flags=(), duration=200.0, **tags):
    snap = {"tags": {k: v if isinstance(v, list) else [v] for k, v in tags.items()}, "duration": duration}
    return query.track_of("Artist/Album", name, snap, flags)


SONG = track(title="Café Noir", artist=["Ané", "Bob"], composer="Ané; Bob", date="1994-05-01",
             tracknumber="3/12", discnumber="1/2", isrc="FR123", flags=("lyrics", "cover"))


@pytest.mark.parametrize("text, kept", [
    ("title:contains:cafe", True), ("title:contains:thé", False), ("title:lacks:cafe", False),
    ("title:is:CAFÉ NOIR", True), ("title:isnot:café", True), ("title:starts:caf", True),
    ("artist:is:bob", True), ("artist:several", True), ("composer:several", True), ("title:several", False),
    ("genre:empty", True), ("genre:filled", False), ("any:contains:ané", True),
    ("folder:starts:artist/", True),
    ("tag.isrc:is:fr123", True), ("tag.label:empty", True),
    ("year:eq:1994", True), ("year:gt:1990", True), ("year:lt:1990", False),
    ("year:between:1990:1999", True), ("track:eq:3", True), ("disc:gt:1", False),
    ("duration:between:3:3,5", True), ("duration:filled", True),
    ("lyrics:yes", True), ("cover:no", False), ("replaygain:no", True), ("compilation:yes", False),
    ("format:is:M4A", True), ("format:isnot:m4a", False),
])
def test_condition(text, kept):
    condition = query.parse(text)
    assert condition is not None and str(condition) == text and condition.keeps(SONG) is kept


@pytest.mark.parametrize("text", ["title", "nope:is:x", "title:eq:1", "year:contains:x", "year:eq:soon",
                                  "title:contains:", "year:between:1990", "tag:filled", "tag.:filled",
                                  "lyrics:maybe"])
def test_not_a_condition(text):
    assert query.parse(text) is None


def test_no_number_kept_by_no_comparison():
    bare = track()
    assert not query.parse("year:lt:3000").keeps(bare) and query.parse("year:empty").keeps(bare)


def test_search_keeps_every_condition_or_any():
    conditions = (query.parse("title:contains:cafe"), query.parse("genre:filled"))
    assert not query.Search(conditions).keeps(SONG) and query.Search(conditions, either=True).keeps(SONG)
    assert query.Search().keeps(SONG)


def test_from_args():
    search = query.from_args(MultiDict([("c", "genre:empty"), ("c", "genre:empty"), ("c", "bad"),
                                        ("f", "tag"), ("k", "ISRC"), ("o", "filled"), ("v", ""), ("w", ""),
                                        ("match", "any")]))
    assert [str(c) for c in search.conditions] == ["genre:empty", "tag.isrc:filled"] and search.either
    assert search.args() == {"c": ["genre:empty", "tag.isrc:filled"], "match": "any"}
    assert query.from_args(MultiDict([("only", "lyrics")])).preset == "lyrics"
    assert query.from_args(MultiDict([("only", "lyrics"), ("c", "title:filled")])).preset is None
    assert query.from_args(MultiDict([("match", "any")])).args() == {}


def test_presets_and_columns():
    assert SONG.presets == {"genre", "replaygain"}
    search = query.Search((query.parse("title:filled"), query.parse("year:filled"), query.parse("year:gt:1")))
    assert search.columns() == ["year"]
    assert query.cell(SONG, "artist") == "Ané; Bob" and query.cell(SONG, "cover") is True
    assert query.cell(SONG, "duration") == "3:20" and query.cell(SONG, "genre") is None
    assert query.cell(SONG, "tag.isrc") == "FR123" and query.cell(SONG, "format") == "m4a"
