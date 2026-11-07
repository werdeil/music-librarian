from music_librarian.core.state import state_path
from music_librarian.lyrics import misses


def test_a_relative_state_folder_is_made_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = state_path("state")
    assert state == tmp_path / "state"
    assert misses.recorded(state) == {}
