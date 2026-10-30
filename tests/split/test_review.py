"""split --review: one credit at a time, stopped and resumed at will."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import pytest

from music_librarian import split
from music_librarian.media import credit_tags
from music_librarian.split import decisions


@pytest.fixture
def lib(tmp_path, make_track):
    root = tmp_path / "Music"
    for i in range(3):
        make_track(root / f"AC_DC/Back in Black/0{i} T.m4a", title="T", artist="AC/DC",
                   albumartist="AC/DC", composer="Angus Young/Malcolm Young")
    make_track(root / "AC_DC/Powerage/01 Riff Raff.m4a", title="Riff Raff", artist="AC/DC",
               albumartist="AC/DC", composer="Angus Young, Malcolm Young, Bon Scott")
    make_track(root / "AC_DC/Highway/01 Highway.m4a", title="Highway", artist="AC/DC",
               albumartist="AC/DC", composer="Bon Scott")
    make_track(root / "Bach/Suites/01 Prelude.flac", title="Prelude", albumartist="Pablo Casals",
               composer="Bach, Johann Sebastian")
    make_track(root / "Bach/Suites/02 Allemande.flac", title="Allemande", albumartist="Pablo Casals",
               composer="Bach, Johann Sebastian")
    return root


def run(lib, tmp_path, *a):
    return split.main(["--root", str(lib), "--state-dir", str(tmp_path / "state"), *a])


def decided(tmp_path):
    return decisions.load(tmp_path / "state" / decisions.FILE, print)


def test_an_answer_settles_the_credits_it_vouches_for(lib, tmp_path, answers, capsys):
    answers("")                                                      # Enter: the suggested split
    assert run(lib, tmp_path, "--review") == 0
    out = capsys.readouterr().out
    assert "REVIEW   [1/3] Angus Young/Malcolm Young  (3 tracks)" in out
    assert "Angus Young, Malcolm Young, Bon Scott" not in out        # all its names known now
    assert "REVIEW   [2/3] Bach, Johann Sebastian" in out
    assert decided(tmp_path) == {"angus young/malcolm young": "split"}
    assert "settled by answers: 1" in out and "awaiting decision: 1" in out
    assert credit_tags.read(lib / "AC_DC/Back in Black/00 T.m4a")["composer"] == \
        ["Angus Young/Malcolm Young"]                                # review never writes tags


def test_resume_where_it_stopped(lib, tmp_path, answers, capsys):
    answers("s", "q")
    run(lib, tmp_path, "--review")
    answers("")
    run(lib, tmp_path, "--review")
    out = capsys.readouterr().out.split("Review summary")[1]
    assert "Angus Young/Malcolm Young" not in out
    assert decided(tmp_path) == {"angus young/malcolm young": "split",
                                 "bach, johann sebastian": "keep"}
    assert run(lib, tmp_path, "--apply") == 0
    assert credit_tags.read(lib / "AC_DC/Powerage/01 Riff Raff.m4a")["composer"] == \
        ["Angus Young; Malcolm Young; Bon Scott"]
    assert credit_tags.read(lib / "Bach/Suites/01 Prelude.flac")["composer"] == ["Bach, Johann Sebastian"]


def test_edit_later_and_undo(lib, tmp_path, answers):
    answers("k", "u", "e", "Angus Young; Malcolm Young", "n", "x", "e", "", "k")
    run(lib, tmp_path, "--review")
    assert decided(tmp_path) == {"angus young/malcolm young": "Angus Young; Malcolm Young"}


def test_answers_replace_the_pending_lines(lib, tmp_path, answers):
    run(lib, tmp_path, "--apply")
    answers("k")
    run(lib, tmp_path, "--review")
    lines = (tmp_path / "state" / decisions.FILE).read_text(encoding="utf-8").splitlines()
    assert "Angus Young/Malcolm Young => keep" in lines
    assert len([line for line in lines if "=>" in line and not line.startswith("#")]) == 3


def test_needs_a_terminal(lib, tmp_path, capsys):
    assert run(lib, tmp_path, "--review") == 2
    assert "ERROR    --review asks questions" in capsys.readouterr().out


@pytest.fixture
def collab(lib, make_track):
    for name in ("Iggy Pop", "Muse"):
        make_track(lib / f"{name}/A/01 T.m4a", title="T", artist=name, albumartist=name)
    for i in range(2):
        make_track(lib / f"Iggy Pop, Muse/Single/0{i} T.m4a", title="T", artist="Iggy Pop, Muse",
                   albumartist="Iggy Pop, Muse")
    return lib


def test_dry_run_shows_the_guarded_credits(collab, tmp_path, capsys):
    assert run(collab, tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    assert "GUARDED  Iggy Pop, Muse  (2 tracks): an album artist" in out
    assert "protected: 1" in out
    assert "Iggy Pop, Muse" not in (tmp_path / "state" / decisions.FILE).read_text(encoding="utf-8")


def test_guarded_credits_come_last_and_enter_keeps_them(collab, tmp_path, answers, capsys):
    answers("", "", "")
    run(collab, tmp_path, "--review")
    out = capsys.readouterr().out
    assert "REVIEW   [4/4] Iggy Pop, Muse  (2 tracks)  album artist, kept unless split here" in out
    assert decided(tmp_path)["iggy pop, muse"] == "keep"


def test_a_guarded_credit_split_in_review(collab, tmp_path, answers):
    answers("n", "n", "n", "s")
    run(collab, tmp_path, "--review")
    assert run(collab, tmp_path, "--apply") == 0
    single = credit_tags.read(collab / "Iggy Pop, Muse/Single/00 T.m4a")
    assert (single["artist"], single["albumartist"]) == (["Iggy Pop; Muse"], ["Iggy Pop, Muse"])


def test_undone_answer_leaves_no_line(tmp_path):
    path = tmp_path / decisions.FILE
    decisions.record(path, "Iggy Pop, Muse", "split")
    decisions.record(path, "Iggy Pop, Muse", None)
    assert not decisions.load(path, print)
