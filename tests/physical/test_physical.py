"""physical over a generated library, the Discogs collection stood in for."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import pytest

from music_librarian import physical
from music_librarian.editions.common import ProviderUnavailable
from music_librarian.media import fields
from music_librarian.organize import main as organize_main
from music_librarian.physical import collection, marks
from music_librarian.physical.matching import album_key, pair_key

AIR, DEBUT = album_key("Air", "Moon Safari (Remastered)"), album_key("Björk", "Debut")
RADIOHEAD = album_key("Radiohead", "OK Computer OKNOTOK 1997 2017")


@pytest.fixture
def lib(tmp_path, make_track):
    root = tmp_path / "Music"
    for i in (1, 2):
        make_track(root / f"Air/Moon Safari/0{i} T.m4a", title="T", artist="Air", albumartist="Air",
                   album="Moon Safari (Remastered)")
        make_track(root / f"Björk/Debut/0{i} T.flac", title="T", artist="Björk", albumartist="Björk",
                   album="Debut")
    fields.write(root / "Björk/Debut/01 T.flac", "comment", ["PHYSICAL"])   # marked in the library
    make_track(root / "Radiohead/OK Computer OKNOTOK/01 T.mp3", title="T", artist="Radiohead",
               albumartist="Radiohead", album="OK Computer OKNOTOK 1997 2017")
    return root


def release(rid, artist, title, year, fmt):
    return dict(id=rid, artist=artist, artists=[artist], title=title, year=year, formats=[fmt],
                instances=[[1, int(rid) * 10]])


@pytest.fixture
def discogs(monkeypatch):
    """The collection stood in for: `held` its releases, `asked` the changes asked of it."""
    held = [release("11", "Air", "Moon Safari", "1998", "Vinyl"),
            release("22", "Radiohead", "OK Computer", "1997", "CD"),
            release("33", "Daft Punk", "Homework", "1997", "CD")]
    asked = []
    monkeypatch.setattr(collection, "fetch", lambda: ("me", list(held)))
    monkeypatch.setattr(collection, "add", lambda user, rid: asked.append(("add", user, rid)))
    monkeypatch.setattr(collection, "remove", lambda user, r: asked.append(("remove", user, r["id"])))
    return held, asked


def run(lib, tmp_path, *a):
    return physical.main(["--root", str(lib), "--state-dir", str(tmp_path / "state"), *a])


def comments(folder):
    return {p.name: fields.read_field(p, "comment") for p in sorted(folder.iterdir())}


def test_dry_run_reads_discogs_and_changes_nothing(lib, tmp_path, discogs, capsys):
    assert run(lib, tmp_path) == 0
    out = capsys.readouterr().out
    assert "PHYSICAL Air/Moon Safari\n         ← Discogs 11: Air – Moon Safari (Vinyl, 1998)" in out
    assert "CONFLICT Björk/Debut  (marked PHYSICAL, not in the Discogs collection" in out
    assert "REVIEW   Radiohead/OK Computer OKNOTOK\n         ~ Discogs 22: Radiohead – OK Computer" in out
    assert "ABSENT   Discogs 33: Daft Punk – Homework (CD, 1997)" in out
    assert "to mark: 1" in out and "conflicts: 1" in out and "ids to write: 2" in out
    assert comments(lib / "Air/Moon Safari") == {"01 T.m4a": [], "02 T.m4a": []}
    assert discogs[1] == [] and marks.load(tmp_path / "state")["synced"] == {}
    assert [r["id"] for r in collection.kept(tmp_path / "state")["releases"]] == ["11", "22", "33"]


def test_a_release_added_or_taken_out_on_discogs_reaches_the_tags(lib, tmp_path, discogs, capsys):
    held, asked = discogs
    assert run(lib, tmp_path, "--apply") == 0
    assert comments(lib / "Air/Moon Safari") == {"01 T.m4a": ["PHYSICAL"], "02 T.m4a": ["PHYSICAL"]}
    assert fields.read_field(lib / "Air/Moon Safari/01 T.m4a", "discogs_release") == ["11"]
    synced = marks.load(tmp_path / "state")["synced"]
    assert synced[AIR] is True and DEBUT not in synced and asked == []
    assert "marked: 1" in capsys.readouterr().out

    held.pop(0)
    fields.write(lib / "Air/Moon Safari/02 T.m4a", "comment", ["Signed PHYSICAL"])
    assert run(lib, tmp_path, "--apply") == 0
    assert "UNMARK   Air/Moon Safari  (not in the Discogs collection)" in capsys.readouterr().out
    assert comments(lib / "Air/Moon Safari") == {"01 T.m4a": [], "02 T.m4a": ["Signed"]}
    assert marks.load(tmp_path / "state")["synced"][AIR] is False and asked == []

    journal = max((tmp_path / "state/journal").glob("*.jsonl"), key=lambda p: p.stat().st_mtime_ns)
    assert organize_main(["--root", str(lib), "--state-dir", str(tmp_path / "state"), "--undo", str(journal),
                          "--apply"]) == 0
    assert comments(lib / "Air/Moon Safari") == {"01 T.m4a": ["PHYSICAL"], "02 T.m4a": ["Signed PHYSICAL"]}


def test_an_album_unmarked_in_the_library_leaves_the_collection(lib, tmp_path, discogs, capsys):
    held, asked = discogs
    assert run(lib, tmp_path, "--apply") == 0
    for p in (lib / "Air/Moon Safari").iterdir():
        fields.write(p, "comment", [])
    assert run(lib, tmp_path) == 0
    assert "REMOVE   Air/Moon Safari\n         → Discogs 11: Air – Moon Safari (Vinyl, 1998)" in \
        capsys.readouterr().out and asked == []
    assert run(lib, tmp_path, "--apply") == 0
    assert asked == [("remove", "me", "11")] and "taken out of Discogs: 1" in capsys.readouterr().out
    held.pop(0)
    assert run(lib, tmp_path, "--apply") == 0
    assert asked == [("remove", "me", "11")] and "REMOVE" not in capsys.readouterr().out


def test_an_album_marked_in_the_library_joins_the_collection(lib, tmp_path, discogs, capsys):
    _, asked = discogs
    state = tmp_path / "state"
    marks.update(state, "conflicts", {DEBUT: "add"})
    marks.update(state, "answers", {pair_key("22", RADIOHEAD): False})
    assert run(lib, tmp_path, "--apply") == 0
    assert "EDITION  Björk/Debut  (marked PHYSICAL, no Discogs id" in capsys.readouterr().out and asked == []

    marks.update(state, "editions", {DEBUT: "77"})
    assert run(lib, tmp_path, "--apply") == 0
    assert asked == [("add", "me", "77")] and "added to Discogs: 1" in capsys.readouterr().out
    assert comments(lib / "Björk/Debut") == {"01 T.flac": ["PHYSICAL"], "02 T.flac": ["PHYSICAL"]}
    assert fields.read_field(lib / "Björk/Debut/02 T.flac", "discogs_release") == ["77"]
    held = marks.load(state)
    assert held["synced"][DEBUT] is True and DEBUT not in held["editions"] and DEBUT not in held["conflicts"]

    for p in (lib / "Radiohead/OK Computer OKNOTOK").iterdir():   # synced unmarked, then marked with its id
        fields.write(p, "comment", ["PHYSICAL"])
        fields.write(p, "discogs_release", ["99"])
    assert run(lib, tmp_path, "--apply") == 0
    assert asked[-1] == ("add", "me", "99")


def test_a_conflict_answered_drop_unmarks(lib, tmp_path, discogs, capsys):
    marks.update(tmp_path / "state", "conflicts", {DEBUT: "drop"})
    marks.update(tmp_path / "state", "answers",
                 {pair_key("22", album_key("Radiohead", "OK Computer OKNOTOK 1997 2017")): True})
    assert run(lib, tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    assert "UNMARK   Björk/Debut  (answered on the dashboard)" in out
    assert "PHYSICAL Radiohead/OK Computer OKNOTOK" in out and discogs[1] == []
    assert comments(lib / "Björk/Debut") == {"01 T.flac": [], "02 T.flac": []}


def test_an_empty_collection_unmarks_nothing(lib, tmp_path, discogs, capsys):
    assert run(lib, tmp_path, "--apply") == 0
    discogs[0].clear()
    assert run(lib, tmp_path, "--apply") == 0
    assert "WARNING  empty collection: 1 album left marked" in capsys.readouterr().out
    assert comments(lib / "Air/Moon Safari")["01 T.m4a"] == ["PHYSICAL"]


@pytest.mark.usefixtures("discogs")
def test_a_refused_change_is_tried_again(lib, tmp_path, monkeypatch, capsys):
    assert run(lib, tmp_path, "--apply") == 0
    for p in (lib / "Air/Moon Safari").iterdir():
        fields.write(p, "comment", [])

    def refused(_user, _release):
        raise ProviderUnavailable("HTTP 500")
    monkeypatch.setattr(collection, "remove", refused)
    assert run(lib, tmp_path, "--apply") == 2
    assert "ERROR    (Discogs: HTTP 500)  Air/Moon Safari" in capsys.readouterr().out
    assert marks.load(tmp_path / "state")["synced"][AIR] is True


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
    pages = {1: [{"id": 1, "instance_id": 101, "folder_id": 1,
                  "basic_information": {"id": 1, "title": "Debut", "year": 1993,
                                        "artists": [{"name": "Björk (2)", "anv": "", "join": ""}],
                                        "formats": [{"name": "CD"}, {"name": "CD"}]}}],
             2: [{"id": 2, "instance_id": 202, "folder_id": 5,
                  "basic_information": {"id": 2, "title": "Duets", "year": 0,
                                        "artists": [{"name": "Simon", "join": "&"},
                                                    {"name": "Garfunkel", "join": ""}],
                                        "formats": [{"name": "Vinyl"}]}},
                 {"id": 1, "instance_id": 102, "folder_id": 7,
                  "basic_information": {"id": 1, "title": "Debut"}}]}

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
    assert releases == [dict(id="1", artist="Björk", artists=["Björk"], instances=[[1, 101], [7, 102]],
                             title="Debut", year="1993", formats=["CD"]),
                        dict(id="2", artist="Simon & Garfunkel", artists=["Simon", "Garfunkel"],
                             instances=[[5, 202]], title="Duets", year="", formats=["Vinyl"])]


def test_add_and_remove_ask_discogs(monkeypatch):
    sent = []

    def request(method, url, headers=None, **_kwargs):
        sent.append((method, url.removeprefix(collection.API), headers["Authorization"]))
        return Answer({}, 201 if method == "POST" else 204)
    monkeypatch.setattr("requests.request", request)
    monkeypatch.setattr("music_librarian.editions.discogs.INTERVAL", 0)
    monkeypatch.setenv("DISCOGS_TOKEN", "t0k")
    collection.add("me", "77")
    collection.remove("me", {"id": "1", "instances": [[1, 101], [7, 102]]})
    token = "Discogs token=t0k"
    assert sent == [("POST", "/users/me/collection/folders/1/releases/77", token),
                    ("DELETE", "/users/me/collection/folders/1/releases/1/instances/101", token),
                    ("DELETE", "/users/me/collection/folders/7/releases/1/instances/102", token)]
    monkeypatch.setattr("requests.request", lambda *a, **k: Answer({}, 404))
    with pytest.raises(ProviderUnavailable, match="404"):
        collection.add("me", "0")


def test_fetch_without_a_token_or_refused(monkeypatch):
    with pytest.raises(collection.CollectionError, match="DISCOGS_TOKEN"):
        collection.fetch()
    monkeypatch.setenv("DISCOGS_TOKEN", "bad")
    monkeypatch.setattr("requests.get", lambda *a, **k: Answer({}, 401))
    with pytest.raises(collection.CollectionError, match="refused the token"):
        collection.fetch()


@pytest.mark.usefixtures("discogs")
def test_an_album_asked_about_keeps_its_mark_until_answered(lib, tmp_path, capsys):
    for p in (lib / "Radiohead/OK Computer OKNOTOK").iterdir():
        fields.write(p, "comment", ["PHYSICAL"])
    marks.update(tmp_path / "state", "synced", {RADIOHEAD: True})
    assert run(lib, tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    assert "REVIEW   Radiohead/OK Computer OKNOTOK" in out and "UNMARK   Radiohead" not in out
    assert comments(lib / "Radiohead/OK Computer OKNOTOK") == {"01 T.mp3": ["PHYSICAL"]}
