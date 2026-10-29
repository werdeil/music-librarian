"""dedupe over a generated library: report, review, apply, undo, import."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import pytest

from music_librarian import dedupe
from music_librarian.dedupe import aliases
from music_librarian.media import credit_tags
from music_librarian.organize import main as organize_main


@pytest.fixture
def lib(tmp_path, make_track):
    root = tmp_path / "Music"
    for i in range(3):
        make_track(root / f"Beyoncé/Lemonade/0{i} T.m4a", title="T", artist="Beyoncé",
                   albumartist="Beyoncé")
    make_track(root / "Beyonce/Single/01 T.mp3", title="T", artist="Beyonce", albumartist="Beyonce")
    make_track(root / "Metallica/Black/01 T.flac", title="T", artist="Metallica",
               albumartist="Metallica", composer="James Hetfield; Lars Ulrich")
    make_track(root / "Metallica/Black/02 T.flac", title="T", artist="Mettalica", albumartist="Metallica")
    make_track(root / "Lennon/Imagine/01 T.m4a", title="T", artist="John Lennon", albumartist="John Lennon")
    make_track(root / "Lennon/Valotte/01 T.m4a", title="T", artist="Julian Lennon",
               albumartist="Julian Lennon")
    return root


def run(lib, tmp_path, *a):
    return dedupe.main(["--root", str(lib), "--state-dir", str(tmp_path / "state"), *a])


def entries(tmp_path):
    return aliases.load(tmp_path / "state" / aliases.FILE, print)


def test_dry_run_reports_the_groups(lib, tmp_path, capsys):
    assert run(lib, tmp_path) == 0
    out = capsys.readouterr().out
    assert "SIMILAR  Beyoncé (6) · Beyonce (2)  [same name once normalized]" in out
    assert "SIMILAR  Metallica (3) · Mettalica (1)  [close spelling]" in out
    assert "Julian" not in out
    assert not (tmp_path / "state" / aliases.FILE).exists()


def test_review_then_apply_and_undo(lib, tmp_path, answers, capsys):
    answers("", "d")                                                 # Beyoncé; Metallica != Mettalica
    assert run(lib, tmp_path, "--review") == 0
    assert entries(tmp_path) == ({"beyonce": "Beyoncé"}, {frozenset({"metallica", "mettalica"})})
    assert "groups left: 0" in capsys.readouterr().out

    assert run(lib, tmp_path, "--apply") == 0
    single = credit_tags.read(lib / "Beyonce/Single/01 T.mp3")
    assert (single["artist"], single["albumartist"]) == (["Beyoncé"], ["Beyoncé"])
    assert "album artists: 1 renamed, filed by the next organize pass" in capsys.readouterr().out
    assert credit_tags.read(lib / "Metallica/Black/02 T.flac")["artist"] == ["Mettalica"]

    assert run(lib, tmp_path) == 0
    assert "SIMILAR" not in capsys.readouterr().out

    journal = next((tmp_path / "state" / "journal").glob("*.jsonl"))
    assert organize_main(["--root", str(lib), "--state-dir", str(tmp_path / "state"),
                          "--undo", str(journal), "--apply"]) == 0
    assert credit_tags.read(lib / "Beyonce/Single/01 T.mp3")["artist"] == ["Beyonce"]


def test_review_pick_edit_undo_and_resume(lib, tmp_path, answers):
    answers("2", "u", "e", "Beyoncé Knowles", "q")
    run(lib, tmp_path, "--review")
    assert entries(tmp_path)[0] == {"beyoncé": "Beyoncé Knowles", "beyonce": "Beyoncé Knowles"}
    answers("1")
    run(lib, tmp_path, "--review")                                   # resumes at Metallica
    assert entries(tmp_path)[0]["mettalica"] == "Metallica"


def test_alias_inside_a_list_of_names(lib, tmp_path, make_track):
    track = make_track(lib / "X/Y/01 T.m4a", title="T", albumartist="X", composer="Beyonce; Jay-Z")
    aliases.update(tmp_path / "state" / aliases.FILE, add=[("Beyonce", "=>", "Beyoncé")])
    assert run(lib, tmp_path, "--apply", str(track.parent)) == 0
    assert credit_tags.read(track)["composer"] == ["Beyoncé; Jay-Z"]


def test_bad_alias_line_reported(lib, tmp_path, capsys):
    path = tmp_path / "state" / aliases.FILE
    path.parent.mkdir()
    path.write_text("Beyonce Beyoncé\n", encoding="utf-8")
    run(lib, tmp_path)
    assert f"ERROR    {path}:1: expected 'VARIANT => NAME'" in capsys.readouterr().out
