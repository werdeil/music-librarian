"""physical over a generated library, the Discogs collection stood in for."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import json

import pytest

from music_librarian import physical
from music_librarian.editions.common import ProviderUnavailable
from music_librarian.media import fields
from music_librarian.organize import main as organize_main
from music_librarian.physical import collection, marks
from music_librarian.physical.matching import album_key, pair_key


@pytest.fixture
def lib(tmp_path, make_track):
    root = tmp_path / "Music"
    for i in (1, 2):
        make_track(root / f"Air/Moon Safari/0{i} T.m4a", title="T", artist="Air", albumartist="Air",
                   album="Moon Safari (Remastered)")
        make_track(root / f"Björk/Debut/0{i} T.flac", title="T", artist="Björk", albumartist="Björk",
                   album="Debut")
    make_track(root / "Radiohead/OK Computer OKNOTOK/01 T.mp3", title="T", artist="Radiohead",
               albumartist="Radiohead", album="OK Computer OKNOTOK 1997 2017")
    return root


RELEASES = [dict(id="11", artist="Air", artists=["Air"], title="Moon Safari", year="1998", formats=["Vinyl"]),
            dict(id="22", artist="Radiohead", artists=["Radiohead"], title="OK Computer", year="1997",
                 formats=["CD"]),
            dict(id="33", artist="Daft Punk", artists=["Daft Punk"], title="Homework", year="1997",
                 formats=["CD"])]


@pytest.fixture
def owned(monkeypatch):
    held = list(RELEASES)
    monkeypatch.setattr(collection, "fetch", lambda: ("me", held))
    return held


def run(lib, tmp_path, *a):
    return physical.main(["--root", str(lib), "--state-dir", str(tmp_path / "state"), *a])


def comments(folder):
    return {p.name: fields.read_field(p, "comment") for p in sorted(folder.iterdir())}


@pytest.mark.usefixtures("owned")
def test_dry_run_reads_discogs_and_writes_nothing(lib, tmp_path, capsys):
    assert run(lib, tmp_path) == 0
    out = capsys.readouterr().out
    assert "PHYSICAL Air/Moon Safari\n         ← Discogs 11: Air – Moon Safari (Vinyl, 1998)" in out
    assert "REVIEW   Radiohead/OK Computer OKNOTOK\n         ~ Discogs 22: Radiohead – OK Computer" in out
    assert "ABSENT   Discogs 33: Daft Punk – Homework (CD, 1997)" in out
    assert "to mark: 1" in out and "ids to write: 2" in out and "to review: 1" in out
    assert comments(lib / "Air/Moon Safari") == {"01 T.m4a": [], "02 T.m4a": []}
    kept = collection.kept(tmp_path / "state")
    assert kept["user"] == "me" and [r["id"] for r in kept["releases"]] == ["11", "22", "33"]
    assert not (tmp_path / "state" / physical.LAST_RUN).exists()


def test_apply_answer_unmark_and_undo(lib, tmp_path, owned, capsys):
    state = tmp_path / "state"
    marks.set_answer(state, pair_key("22", album_key("Radiohead", "OK Computer OKNOTOK 1997 2017")), True)
    assert run(lib, tmp_path, "--apply") == 0
    assert comments(lib / "Air/Moon Safari") == {"01 T.m4a": ["PHYSICAL"], "02 T.m4a": ["PHYSICAL"]}
    assert fields.read_field(lib / "Air/Moon Safari/01 T.m4a", "discogs_release") == ["11"]
    assert comments(lib / "Radiohead/OK Computer OKNOTOK") == {"01 T.mp3": ["PHYSICAL"]}
    assert comments(lib / "Björk/Debut") == {"01 T.flac": [], "02 T.flac": []}
    assert "marked: 2" in capsys.readouterr().out
    assert physical.last_run(state) is not None

    owned.pop(0)
    fields.write(lib / "Air/Moon Safari/02 T.m4a", "comment", ["Signed PHYSICAL"])
    assert run(lib, tmp_path, "--apply") == 0
    assert "UNMARK   Air/Moon Safari  (not in the Discogs collection)" in capsys.readouterr().out
    assert comments(lib / "Air/Moon Safari") == {"01 T.m4a": [], "02 T.m4a": ["Signed"]}
    assert fields.read_field(lib / "Air/Moon Safari/01 T.m4a", "discogs_release") == ["11"]

    journal = max((state / "journal").glob("*.jsonl"), key=lambda p: p.stat().st_mtime_ns)
    assert organize_main(["--root", str(lib), "--state-dir", str(state), "--undo", str(journal),
                          "--apply"]) == 0
    assert comments(lib / "Air/Moon Safari") == {"01 T.m4a": ["PHYSICAL"], "02 T.m4a": ["Signed PHYSICAL"]}


@pytest.mark.usefixtures("owned")
def test_marks_by_hand_win_over_discogs(lib, tmp_path, capsys):
    state = tmp_path / "state"
    marks.set_manual(state, album_key("Air", "Moon Safari (Remastered)"), False)
    marks.set_manual(state, album_key("Björk", "Debut"), True)
    assert run(lib, tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    assert "PHYSICAL Björk/Debut\n         ← no release  [marked by hand]" in out
    assert comments(lib / "Air/Moon Safari") == {"01 T.m4a": [], "02 T.m4a": []}
    assert comments(lib / "Björk/Debut") == {"01 T.flac": ["PHYSICAL"], "02 T.flac": ["PHYSICAL"]}
    assert fields.read_field(lib / "Björk/Debut/01 T.flac", "discogs_release") == []


def test_an_empty_collection_unmarks_nothing(lib, tmp_path, owned, capsys):
    assert run(lib, tmp_path, "--apply") == 0
    owned.clear()
    assert run(lib, tmp_path, "--apply") == 0
    assert "WARNING  empty collection: 1 album left marked" in capsys.readouterr().out
    assert comments(lib / "Air/Moon Safari")["01 T.m4a"] == ["PHYSICAL"]


def test_an_unreadable_collection_changes_nothing(lib, tmp_path, monkeypatch, capsys):
    def refused():
        raise ProviderUnavailable("HTTP 503")
    monkeypatch.setattr(collection, "fetch", refused)
    assert run(lib, tmp_path, "--apply") == 2
    assert "ERROR    the Discogs collection cannot be read: HTTP 503" in capsys.readouterr().out
    assert not (tmp_path / "state" / collection.FILE).exists()


class Answer:
    def __init__(self, payload, status=200):
        self.status_code, self.payload = status, payload

    def json(self):
        return self.payload


def test_fetch_reads_every_page_with_the_token(monkeypatch):
    asked = []
    pages = {1: [{"id": 1, "basic_information": {"id": 1, "title": "Debut", "year": 1993,
                                                  "artists": [{"name": "Björk (2)", "anv": "", "join": ""}],
                                                  "formats": [{"name": "CD"}, {"name": "CD"}]}}],
             2: [{"id": 2, "basic_information": {"id": 2, "title": "Duets", "year": 0,
                                                  "artists": [{"name": "Simon", "join": "&"},
                                                              {"name": "Garfunkel", "join": ""}],
                                                  "formats": [{"name": "Vinyl"}]}},
                 {"id": 1, "basic_information": {"id": 1, "title": "Debut"}}]}

    def get(url, params=None, headers=None, **_kwargs):
        asked.append((url, dict(params or {}), headers["Authorization"]))
        if url.endswith("/oauth/identity"):
            return Answer({"username": "me"})
        return Answer({"pagination": {"pages": 2}, "releases": pages[params["page"]]})
    monkeypatch.setattr("requests.get", get)
    monkeypatch.setattr("music_librarian.editions.discogs.INTERVAL", 0)
    monkeypatch.setenv("DISCOGS_TOKEN", "t0k")
    user, releases = collection.fetch()
    assert user == "me" and [a[2] for a in asked] == ["Discogs token=t0k"] * 3
    assert asked[1][0].endswith("/users/me/collection/folders/0/releases")
    assert releases == [dict(id="1", artist="Björk", artists=["Björk"], title="Debut", year="1993",
                             formats=["CD"]),
                        dict(id="2", artist="Simon & Garfunkel", artists=["Simon", "Garfunkel"],
                             title="Duets", year="", formats=["Vinyl"])]


def test_fetch_without_a_token_or_refused(monkeypatch):
    with pytest.raises(collection.CollectionError, match="DISCOGS_TOKEN"):
        collection.fetch()
    monkeypatch.setenv("DISCOGS_TOKEN", "bad")
    monkeypatch.setattr("requests.get", lambda *a, **k: Answer({}, 401))
    with pytest.raises(collection.CollectionError, match="refused the token"):
        collection.fetch()
    assert json.dumps(collection.kept(".")) == "null"
