"""The fields holding a value twice or several values: rewritten once, or reported."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import pytest
from mutagen.mp4 import MP4

from music_librarian import organize, repeats, scan
from music_librarian.core.log import Log
from music_librarian.media import fields
from music_librarian.organize import main as organize_main
from music_librarian.organize.undo import undo
from music_librarian.split.library import CreditOptions


@pytest.mark.parametrize("field, values, expected", [
    ("title", ["Copain de soupe", "Copain de soupe"], ("Copain de soupe", False)),
    ("title", ["Copain  de soupe", "Copain de soupe"], ("Copain  de soupe", False)),
    ("title", ["Toujours pizza", "Toujours pizza (Remix)"], (None, True)),
    ("artist", ["Walk Off The Earth", "Walk Off the Earth"], (None, True)),
    ("composer", ["Louis Bertignac; Téléphone; Louis Bertignac"], ("Louis Bertignac; Téléphone", False)),
    ("artist", ["A;A"], ("A", False)),
    ("artist", ["A; a"], (None, True)),
    ("artist", ["A; A; a"], ("A; a", True)),
    ("title", ["A; A"], (None, True)),                   # a title takes no separator
    ("album", ["A; B", "A; B"], ("A; B", True)),
    ("genre", ["Rock; Pop"], (None, False)),
    ("title", ["Help!"], (None, False)),
    ("title", [], (None, False)),
    ("title", ["", "Help!", " "], (None, False)),
])
def test_check(field, values, expected):
    assert repeats.check(field, values) == expected


def tag_m4a(path, **atoms):
    f = MP4(path)
    for name, values in atoms.items():
        f[fields.MP4_KEYS[name]] = values
    f.save()


@pytest.fixture
def lib(tmp_path, make_track):
    root = tmp_path / "Music"
    soup = make_track(root / "Salut C'est Cool/Mon premier EP/04 Copain de soupe.m4a", album="Mon premier EP",
                      albumartist="Salut C'est Cool", track=4)
    tag_m4a(soup, title=["Copain de soupe", "Copain de soupe"],
            artist=["Salut C'est Cool", "Salut C'est Cool"])
    pizza = make_track(root / "Salut C'est Cool/Mon premier EP/05 Toujours pizza.m4a", album="Mon premier EP",
                       albumartist="Salut C'est Cool", track=5)
    tag_m4a(pizza, title=["Toujours pizza", "Toujours pizza (Flavien Berger Remix)"])
    make_track(root / "Téléphone/Illimité/01 2000 Nuits.mp3", title="2000 Nuits", artist="Téléphone",
               albumartist="Téléphone", album="Illimité", track=1,
               composer="Louis Bertignac; Téléphone; Louis Bertignac")
    make_track(root / "A/B/01 Un.flac", title="Un", artist="A", albumartist="A", album="B", track=1)
    scan.run(root, tmp_path / "state", Log())
    return root


def run(lib, tmp_path, log, apply=False):
    return repeats.repeats_pass(CreditOptions(root=lib, apply=apply, state_dir=tmp_path / "state"), log)


SOUP = "Salut C'est Cool/Mon premier EP/04 Copain de soupe.m4a"
PIZZA = "Salut C'est Cool/Mon premier EP/05 Toujours pizza.m4a"
NUITS = "Téléphone/Illimité/01 2000 Nuits.mp3"


def test_dry_run_changes_nothing(lib, tmp_path):
    before = {p: fields.read(p) for p in lib.rglob("*.*")}
    lines = []
    assert run(lib, tmp_path, lines.append) == 0
    out = "\n".join(lines)
    assert f"REPEAT   title: 'Copain de soupe; Copain de soupe' → 'Copain de soupe'  {SOUP}" in out
    assert ("REPEAT   composer: 'Louis Bertignac; Téléphone; Louis Bertignac'"
            f" → 'Louis Bertignac; Téléphone'  {NUITS}") in out
    assert f"MULTIPLE title: 'Toujours pizza; Toujours pizza (Flavien Berger Remix)'  {PIZZA}" in out
    assert "tags to rewrite: 3" in out and "to decide: 1" in out
    assert {p: fields.read(p) for p in lib.rglob("*.*")} == before
    assert not (tmp_path / "state/journal").exists()


def test_apply_then_undo(lib, tmp_path):
    lines = []
    assert run(lib, tmp_path, lines.append, apply=True) == 0
    soup = fields.read(lib / SOUP)
    assert (soup["title"], soup["artist"]) == (["Copain de soupe"], ["Salut C'est Cool"])
    assert fields.read(lib / NUITS)["composer"] == ["Louis Bertignac; Téléphone"]
    assert fields.read(lib / PIZZA)["title"] == ["Toujours pizza", "Toujours pizza (Flavien Berger Remix)"]
    [journal] = (tmp_path / "state/journal").iterdir()
    args = type("Args", (), {"undo": str(journal), "apply": True})()
    assert undo(args, lib, tmp_path / "state", lambda _line: None) == 0
    assert fields.read(lib / SOUP)["title"] == ["Copain de soupe", "Copain de soupe"]
    assert fields.read(lib / NUITS)["composer"] == ["Louis Bertignac; Téléphone; Louis Bertignac"]


def test_organize_reports_the_credits_to_decide(tmp_path, make_track, capsys):
    root = tmp_path / "Music"
    p = make_track(root / "Walk Off The Earth/Fire In My Soul/01 Fire In My Soul.m4a",
                   title="Fire In My Soul", albumartist="Walk Off The Earth", album="Fire In My Soul",
                   track=1)
    tag_m4a(p, artist=["Walk Off The Earth", "Walk Off the Earth"])
    state = tmp_path / "state"
    assert organize_main(["--root", str(root), "--state-dir", str(state)]) == 2
    out = capsys.readouterr().out
    assert "MULTIPLE artist: 'Walk Off The Earth; Walk Off the Earth'  Walk Off The Earth/" in out
    assert "multiple values: 1" in out
    report = organize.organize_pass(organize.Options(root=root, state_dir=state), Log())
    assert (report.code, report.multiple) == (0, 1)        # an import is not refused for it


def test_organize_refuses_several_titles(tmp_path, make_track, capsys):
    root = tmp_path / "Music"
    p = make_track(root / "In/1.m4a", artist="A", albumartist="A", album="B", track=1)
    tag_m4a(p, title=["Un", "Un (Remix)"])
    make_track(root / "In/2.m4a", title="Movement 1; Largo", artist="A", albumartist="A", album="B", track=2)
    assert organize_main(["--root", str(root), "--state-dir", str(tmp_path / "state"), "--apply"]) == 2
    out = capsys.readouterr().out
    assert """SKIPPED  (title takes one value, without ";": 'Un; Un (Remix)')  In/1.m4a""" in out
    assert """SKIPPED  (title takes one value, without ";": 'Movement 1; Largo')  In/2.m4a""" in out
    assert p.exists() and not (root / "A").exists()
