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
    ("title", ["A; A"], (None, False)),                  # a title is not a list of names
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


def test_organize_reports_the_values_to_decide(lib, tmp_path, capsys):
    assert organize_main(["--root", str(lib), "--state-dir", str(tmp_path / "state")]) == 2
    out = capsys.readouterr().out
    assert f"MULTIPLE title: 'Toujours pizza; Toujours pizza (Flavien Berger Remix)'  {PIZZA}" in out
    assert "multiple values: 1" in out and "Copain de soupe'" not in out
    report = organize.organize_pass(organize.Options(root=lib, state_dir=tmp_path / "state"), Log())
    assert (report.code, report.multiple) == (0, 1)        # an import is not refused for it
