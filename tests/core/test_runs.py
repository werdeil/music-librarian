"""Run history (core/runs.py): what is kept of each pass, and of which passes."""
# pylint: disable=redefined-outer-name  # pytest fixtures
import pytest

from music_librarian import cli, organize
from music_librarian.core import runs
from music_librarian.core.lock import EXIT_LOCKED
from music_librarian.core.log import Log


@pytest.fixture
def state(tmp_path):
    return tmp_path / "state"


def a_pass(log, code=2):
    def fn():
        log("[DRY-RUN] root: /music")
        log("MOVE     /music/a.m4a\n         → /music/A/B/01 a.m4a")
        log("\n--- Summary ---")
        log(f"{'scanned':>20}: 1")
        log(f"{'to move':>20}: 1")
        return code
    return fn


def test_pass_kept_with_its_output_and_summary(state, capsys):
    log = Log()
    assert runs.recorded(a_pass(log), log, state, "organize", False) == 2
    [run] = runs.recent(state)
    assert (run["command"], run["origin"], run["apply"], run["code"]) == ("organize", "cli", False, 2)
    assert run["started"] <= run["ended"]
    assert run["summary"] == ["scanned: 1", "to move: 1"]
    text = runs.log_path(state, run["id"]).read_text(encoding="utf-8")
    assert text == capsys.readouterr().out
    assert log.tee is None


def test_summary_is_the_last_section_with_entries(state):
    log = Log()

    def fn():
        log("\n--- Summary ---")
        log(f"{'to move':>20}: 3")
        log("\n--- Import summary ---")
        log(f"{'imported':>20}: 1")
        log("\n--- Tracks per genre ---")
        log(f"{12:>8}  Rock")
        return 0

    runs.recorded(fn, log, state, "import", True, origin="watch")
    [run] = runs.recent(state)
    assert (run["summary"], run["origin"], run["apply"]) == (["imported: 1"], "watch", True)


def test_pass_that_found_the_lock_taken_not_kept(state):
    log = Log()
    assert runs.recorded(a_pass(log, EXIT_LOCKED), log, state, "enrich", True) == EXIT_LOCKED
    assert runs.recent(state) == []
    assert not list((state / runs.LOGS).iterdir())


def test_crash_kept_with_its_traceback(state):
    log = Log()

    def boom():
        log("first line")
        raise RuntimeError("oups")

    with pytest.raises(RuntimeError):
        runs.recorded(boom, log, state, "enrich", True)
    [run] = runs.recent(state)
    assert run["code"] is None and run["ended"] is not None
    text = runs.log_path(state, run["id"]).read_text(encoding="utf-8")
    assert text.startswith("first line\nERROR    enrich interrupted:\nTraceback")
    assert "RuntimeError: oups" in text


def test_oldest_runs_dropped(state, monkeypatch):
    monkeypatch.setattr(runs, "KEEP", 3)
    log = Log()
    for _ in range(5):
        runs.recorded(lambda: 0, log, state, "scan", False)
    assert [r["id"] for r in runs.recent(state)] == [5, 4, 3]
    assert sorted(p.name for p in (state / runs.LOGS).iterdir()) == ["3.log", "4.log", "5.log"]


def test_non_utf8_name_kept_escaped(state):
    log = Log()
    name = "/music/" + b"caf\x92.m4a".decode("utf-8", "surrogateescape")
    runs.recorded(lambda: log(f"MOVE     {name}") or 0, log, state, "organize", False)
    [run] = runs.recent(state)
    assert runs.log_path(state, run["id"]).read_text(encoding="utf-8") == "MOVE     /music/caf\\udc92.m4a\n"


def test_child_log_writes_to_the_same_run(state):
    log = Log()

    def fn():
        log.child("music-librarian")("MOVE     from organize, under import")
        return 0

    runs.recorded(fn, log, state, "import", True)
    [run] = runs.recent(state)
    assert "from organize, under import" in runs.log_path(state, run["id"]).read_text(encoding="utf-8")


def test_no_history_yet(state):
    assert runs.recent(state) == []
    assert not state.exists()


def test_commands_recorded(tmp_path, make_track):
    lib, state = tmp_path / "Musique", tmp_path / "state"
    make_track(lib / "vrac/1.m4a", title="Un", artist="A", album="B", track=1)
    assert organize.main(["--root", str(lib), "--state-dir", str(state)]) == 0
    assert organize.main(["--root", str(lib), "--state-dir", str(state), "--apply"]) == 0
    [journal] = (state / "journal").glob("*.jsonl")
    assert cli.main(["undo", str(journal), "--root", str(lib), "--state-dir", str(state)]) == 0
    undone, applied, dry = runs.recent(state)
    assert [(r["command"], r["apply"]) for r in (dry, applied, undone)] == \
        [("organize", False), ("organize", True), ("undo", False)]
    assert "to move: 1" in dry["summary"]
    assert f"journal: {journal}" in applied["summary"]
