"""The split command over a generated library, and undoing it."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import pytest

from music_librarian import scan, split
from music_librarian.core.lock import EXIT_STOPPED, STOP
from music_librarian.media import credit_tags, tags
from music_librarian.organize import main as organize_main
from music_librarian.split import decisions


@pytest.fixture
def lib(tmp_path, make_track):
    root = tmp_path / "Music"
    make_track(root / "AC_DC/Back in Black/01 Hells Bells.m4a", title="Hells Bells",
               artist="AC/DC", albumartist="AC/DC", composer="Angus Young/Malcolm Young")
    make_track(root / "The Beatles/Help!/01 Help!.mp3", title="Help!", artist="The Beatles",
               albumartist="The Beatles", composer="John Lennon/Paul McCartney")
    make_track(root / "John Lennon/Imagine/01 Imagine.flac", title="Imagine",
               artist="John Lennon", albumartist="John Lennon", composer="John Lennon")
    make_track(root / "Paul McCartney/Ram/01 Too Many People.m4a", title="Too Many People",
               artist="Paul McCartney", albumartist="Paul McCartney", composer="Paul McCartney")
    make_track(root / "Earth, Wind & Fire/All 'n All/01 Serpentine Fire.flac",
               title="Serpentine Fire", artist="Earth, Wind & Fire",
               albumartist="Earth, Wind & Fire", composer="Maurice White, Verdine White")
    make_track(root / "Compil/01 Duet.m4a", title="Duet", artist="John Lennon, Paul McCartney",
               albumartist="Various Artists", composer="Lennon/McCartney")
    make_track(root / "Loose/Duet.m4a", title="Duet", artist="John Lennon, Paul McCartney")
    return root


def run(lib, tmp_path, *a):
    return split.main(["--root", str(lib), "--state-dir", str(tmp_path / "state"), *a])


def composer(p):
    return credit_tags.read(p)["composer"]


def test_dry_run_changes_nothing(lib, tmp_path, capsys):
    before = {p: credit_tags.read(p) for p in lib.rglob("*.*")}
    assert run(lib, tmp_path) == 0
    out = capsys.readouterr().out
    assert "SPLIT    composer: John Lennon/Paul McCartney\n         → John Lennon; Paul McCartney" in out
    assert "REVIEW   Angus Young/Malcolm Young  (1 track(s))" in out
    assert {p: credit_tags.read(p) for p in lib.rglob("*.*")} == before
    state = tmp_path / "state"
    assert not (state / decisions.FILE).exists() and not (state / "journal").exists()


def test_apply(lib, tmp_path):
    assert run(lib, tmp_path, "--apply") == 0
    assert composer(lib / "The Beatles/Help!/01 Help!.mp3") == ["John Lennon; Paul McCartney"]
    duet = credit_tags.read(lib / "Compil/01 Duet.m4a")
    assert duet["artist"] == ["John Lennon; Paul McCartney"]
    assert duet["composer"] == ["Lennon/McCartney"]                 # waits for a decision
    ewf = credit_tags.read(lib / "Earth, Wind & Fire/All 'n All/01 Serpentine Fire.flac")
    assert (ewf["albumartist"], ewf["artist"], ewf["composer"]) == \
        (["Earth, Wind & Fire"], ["Earth, Wind & Fire"], ["Maurice White, Verdine White"])
    loose = credit_tags.read(lib / "Loose/Duet.m4a")
    assert loose["artist"] == ["John Lennon, Paul McCartney"]      # no album artist
    text = (tmp_path / "state" / decisions.FILE).read_text(encoding="utf-8")
    assert text.startswith("# ")
    assert "Angus Young/Malcolm Young => ?\n" in text and "Lennon/McCartney => ?\n" in text


def test_decisions_applied_and_not_asked_again(lib, tmp_path):
    run(lib, tmp_path, "--apply")
    path = tmp_path / "state" / decisions.FILE
    path.write_text(path.read_text(encoding="utf-8")
                    .replace("Angus Young/Malcolm Young => ?", "Angus Young/Malcolm Young => split")
                    .replace("Lennon/McCartney => ?", "Lennon/McCartney => John Lennon; Paul McCartney")
                    .replace("Maurice White, Verdine White => ?", "Maurice White, Verdine White => keep"),
                    encoding="utf-8")
    assert run(lib, tmp_path, "--apply") == 0
    assert composer(lib / "AC_DC/Back in Black/01 Hells Bells.m4a") == ["Angus Young; Malcolm Young"]
    assert composer(lib / "Compil/01 Duet.m4a") == ["John Lennon; Paul McCartney"]
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len([line for line in lines if not line.startswith("#")]) == 3


def test_bad_decision_line_reported(lib, tmp_path, capsys):
    path = tmp_path / "state" / decisions.FILE
    path.parent.mkdir()
    path.write_text("Angus Young/Malcolm Young split\n", encoding="utf-8")
    run(lib, tmp_path)
    assert f"ERROR    {path}:1: expected 'CREDIT => DECISION'" in capsys.readouterr().out


def test_partial_pass_knows_the_names_of_the_cache(lib, tmp_path, make_track):
    scan.run(lib, tmp_path / "state", print)
    new = make_track(lib / "The Beatles/Abbey Road/01 Come Together.m4a", title="Come Together",
                     artist="The Beatles", albumartist="The Beatles",
                     composer="John Lennon, Paul McCartney")
    assert run(lib, tmp_path, "--apply", str(new.parent)) == 0
    assert composer(new) == ["John Lennon; Paul McCartney"]


def test_undo(lib, tmp_path, capsys):
    run(lib, tmp_path, "--apply")
    journal = next((tmp_path / "state" / "journal").glob("*.jsonl"))
    help_mp3 = lib / "The Beatles/Help!/01 Help!.mp3"
    assert organize_main(["--root", str(lib), "--state-dir", str(tmp_path / "state"),
                          "--undo", str(journal), "--apply"]) == 0
    assert composer(help_mp3) == ["John Lennon/Paul McCartney"]
    assert credit_tags.read(lib / "Compil/01 Duet.m4a")["artist"] == ["John Lennon, Paul McCartney"]
    assert "tags to restore: 2" in capsys.readouterr().out


def test_undo_refuses_a_tag_changed_since(lib, tmp_path, capsys):
    run(lib, tmp_path, "--apply")
    journal = next((tmp_path / "state" / "journal").glob("*.jsonl"))
    help_mp3 = lib / "The Beatles/Help!/01 Help!.mp3"
    credit_tags.write(help_mp3, "composer", "Someone Else")
    assert organize_main(["--root", str(lib), "--state-dir", str(tmp_path / "state"),
                          "--undo", str(journal), "--apply"]) == 2
    assert composer(help_mp3) == ["Someone Else"]
    assert "REFUSED  (composer changed since)" in capsys.readouterr().out


def test_stop(lib, tmp_path):
    STOP.set()
    assert run(lib, tmp_path, "--apply") == EXIT_STOPPED
    assert composer(lib / "The Beatles/Help!/01 Help!.mp3") == ["John Lennon/Paul McCartney"]


def test_unchanged_files_come_from_the_cache(lib, tmp_path, monkeypatch):
    run(lib, tmp_path)
    help_mp3 = lib / "The Beatles/Help!/01 Help!.mp3"
    credit_tags.write(help_mp3, "composer", "John Lennon, Paul McCartney")
    read = []
    real = tags.read_snapshot
    monkeypatch.setattr(tags, "read_snapshot", lambda p: read.append(p) or real(p))
    assert run(lib, tmp_path, "--apply") == 0
    assert read == [help_mp3]
    assert composer(help_mp3) == ["John Lennon; Paul McCartney"]
