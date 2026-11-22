"""dates: the edition's year, alone, the one date a track holds."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import pytest
from mutagen.id3 import ID3, TORY, TRDA, TYER
from mutagen.mp4 import MP4, MP4FreeForm

from music_librarian import dates, scan
from music_librarian.core.log import Log
from music_librarian.media import fields
from music_librarian.organize.undo import undo
from music_librarian.split.library import CreditOptions


@pytest.mark.parametrize("value, year", [("2011", True), ("0000", False), ("2011-05-02", False),
                                         ("11", False), ("", False), ("année", False)])
def test_is_year(value, year):
    assert dates.is_year(value) is year


@pytest.mark.parametrize("years, found", [
    (["2011", "2011"], ()),
    (["2011", ""], (dates.MISSING,)),
    (["2011-05-02", "2011"], (dates.FORMAT,)),
    ([" ", "abc"], (dates.MISSING, dates.FORMAT)),
])
def test_problems(years, found):
    assert dates.problems(years) == found


@pytest.mark.parametrize("value, year", [("2011", "2011"), ("2011-05-02", "2011"), ("2011-05", "2011"),
                                         ("2011-05-02T07:00:00Z", "2011"), ("0000", None),
                                         ("0000-01-01", None), ("May 2011", None), ("", None)])
def test_cut(value, year):
    assert dates.cut(value) == year


@pytest.mark.parametrize("tags, found", [
    ({"date": ["2011"]}, (None, False, None)),
    ({"date": ["2011-05-02"]}, ("2011", False, None)),
    ({"DATE": ["2011-05-02"], "YEAR": ["2011"]}, ("2011", True, None)),
    ({"year": ["2011-05-02"]}, ("2011", False, None)),
    ({"date": ["2011"], "originaldate": ["1973"]}, (None, True, None)),
    ({"date": ["2011"], "year": ["2015"]}, (None, True, None)),
    ({"trda": ["1972"]}, (None, True, dates.MISSING)),
    ({"date": ["mai 2011"]}, (None, False, dates.FORMAT)),
])
def test_findings(tags, found):
    assert dates.findings({"tags": tags}) == found


@pytest.fixture
def lib(tmp_path, make_track):
    root = tmp_path / "Music"
    m4a = make_track(root / "A/B/01 Un.m4a", title="Un", artist="A", album="B", track=1)
    f = MP4(m4a)
    f["\xa9day"] = ["2011-05-02T07:00:00Z"]
    f["----:com.apple.iTunes:ORIGINALDATE"] = [MP4FreeForm(b"1973-03-01")]
    f.save()
    mp3 = make_track(root / "A/B/02 Deux.mp3", title="Deux", artist="A", album="B", track=2)
    t = ID3(mp3)
    for frame in (TYER(encoding=3, text="2011"), TORY(encoding=3, text="1973"),
                  TRDA(encoding=3, text="1972-06-10")):
        t.add(frame)
    t.save(mp3, v2_version=3)
    make_track(root / "A/B/03 Trois.flac", title="Trois", artist="A", album="B", track=3,
               extra={"date": "2011", "year": "1973", "recordingdate": "1972"})
    make_track(root / "A/C/01 Rien.flac", title="Rien", artist="A", album="C", track=1,
               extra={"date": "vers 2011"})
    make_track(root / "A/C/02 Vide.flac", title="Vide", artist="A", album="C", track=2)
    scan.run(root, tmp_path / "state", Log())
    return root


def run(lib, tmp_path, log, apply=False):
    return dates.dates_pass(CreditOptions(root=lib, apply=apply, state_dir=tmp_path / "state"), log)


def years(lib):
    return {p.name: (fields.read(p)["year"], fields.other_dates(p)) for p in sorted(lib.rglob("*.*"))}


def test_dry_run_changes_nothing(lib, tmp_path):
    before = years(lib)
    lines = []
    assert run(lib, tmp_path, lines.append) == 0
    out = "\n".join(lines)
    assert "DATE     year → '2011'  A/B/01 Un.m4a" in out
    assert "DATE     removed: originaldate 1973-03-01  A/B/01 Un.m4a" in out
    assert "DATE     removed: originaldate 1973; trda 1972-06-10  A/B/02 Deux.mp3" in out
    assert "DATE     removed: recordingdate 1972; year 1973  A/B/03 Trois.flac" in out
    assert "INVALID  year: 'vers 2011'  A/C/01 Rien.flac" in out
    assert "years to cut: 1" in out and "dates to remove: 3" in out
    assert "without a year: 1" in out and "not a year: 1" in out
    assert years(lib) == before
    assert not (tmp_path / "state/journal").exists()


def test_apply_then_undo(lib, tmp_path):
    before = years(lib)
    assert run(lib, tmp_path, lambda _line: None, apply=True) == 0
    assert years(lib) == {"01 Rien.flac": (["vers 2011"], []), "01 Un.m4a": (["2011"], []),
                          "02 Deux.mp3": (["2011"], []), "02 Vide.flac": ([], []),
                          "03 Trois.flac": (["2011"], [])}
    [journal] = (tmp_path / "state/journal").iterdir()
    args = type("Args", (), {"undo": str(journal), "apply": True})()
    lines = []
    undo(args, lib, tmp_path / "state", lines.append)
    assert "TRDA 1972-06-10 cannot be written back" in "\n".join(lines)
    back = years(lib)
    assert back["02 Deux.mp3"] == (["2011"], ["TDOR=1973"])
    assert {k: v for k, v in back.items() if k != "02 Deux.mp3"} == \
        {k: v for k, v in before.items() if k != "02 Deux.mp3"}
