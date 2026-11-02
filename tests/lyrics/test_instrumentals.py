"""lyrics.instrumentals: the answers of the dashboard's review, written by a tag pass."""
from music_librarian import tag
from music_librarian.core.log import Log
from music_librarian.lyrics import instrumentals
from music_librarian.media import tags


def test_answers_kept_in_order_once(tmp_path):
    instrumentals.add(tmp_path, "A/01.m4a")
    instrumentals.add(tmp_path, "A/02.m4a")
    instrumentals.add(tmp_path, "A/01.m4a")
    assert instrumentals.load(tmp_path) == ["A/01.m4a", "A/02.m4a"]
    instrumentals.remove(tmp_path, "A/01.m4a")
    assert instrumentals.load(tmp_path) == ["A/02.m4a"]
    assert instrumentals.load(tmp_path / "none") == []


def test_pass_writes_the_mark_and_drops_what_it_settled(tmp_path, make_track, capsys):
    root, state = tmp_path / "Music", tmp_path / "state"
    bare = make_track(root / "A/B/01 T.m4a", title="T", artist="A", albumartist="A", album="B", track=1)
    sung = make_track(root / "A/B/02 U.m4a", title="U", artist="A", albumartist="A", album="B", track=2)
    tags.write_lyrics(sung, "la la")
    for rel in ("A/B/01 T.m4a", "A/B/02 U.m4a", "A/B/03 gone.m4a"):
        instrumentals.add(state, rel)
    assert instrumentals.run(tag.Options(root=root, state_dir=state), Log()) == 0
    out = capsys.readouterr().out
    assert "SKIPPED  (gone, or has lyrics since)  A/B/02 U.m4a" in out and "A/B/03 gone.m4a" in out
    assert not tags.has_lyrics(bare) and len(instrumentals.load(state)) == 3
    assert instrumentals.run(tag.Options(root=root, state_dir=state, apply=True), Log()) == 0
    assert tags.read_lyrics(bare) == "[Instrumental]" and tags.read_lyrics(sung) == "la la"
    assert instrumentals.load(state) == [] and "MOVE" not in capsys.readouterr().out
