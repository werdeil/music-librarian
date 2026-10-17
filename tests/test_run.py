"""Passages complets sur une mini-bibliothèque : dry-run, apply, undo, --since."""
# pylint: disable=redefined-outer-name,unused-argument  # fixtures pytest
import json
import os
import time

import pytest

import music_ranger


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "Musique"
    root.mkdir()
    return root


@pytest.fixture
def run(tmp_path, lib):
    state = tmp_path / "state"

    def run(*args):
        return music_ranger.main(["--root", str(lib), "--state-dir", str(state), *args])

    run.state = state
    return run


def journals(run):
    return sorted((run.state / "journal").glob("*.jsonl"))


def files(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def test_dry_run_changes_nothing(lib, run, make_track, capsys):
    make_track(lib / "mauvais/endroit.m4a", title="T", artist="A", album="B", track=1)
    (lib / "mauvais/.DS_Store").write_bytes(b"x")
    before = files(lib)
    assert run() == 0
    assert files(lib) == before
    assert not run.state.exists()
    assert "DÉPLACER" in capsys.readouterr().out


def test_apply_then_undo(lib, run, make_track, capsys):
    make_track(lib / "vrac/1.m4a", title="Un", artist="A", album="B", track=1)
    make_track(lib / "vrac/2.m4a", title="Deux", artist="A", album="B", track=2)
    (lib / "vrac/folder.jpg").write_bytes(b"jpg")
    (lib / "vrac/._1.m4a").write_bytes(b"x")
    make_track(lib / "C/D/01 Rien.m4a", title="Rien", artist="C", album="D", track=1)

    assert run("--apply") == 0
    assert files(lib) == ["A/B/01 Un.m4a", "A/B/02 Deux.m4a", "A/B/folder.jpg", "C/D/01 Rien.m4a"]
    assert not (lib / "vrac").exists()

    [j] = journals(run)
    # commande d'annulation prête à copier, valable dans le conteneur
    assert f"music-ranger undo {j} --root {lib} --apply" in capsys.readouterr().out
    recs = [json.loads(line) for line in j.read_text().splitlines()]
    assert recs[0]["type"] == "meta"
    assert len([r for r in recs if r["type"] == "move"]) == 3

    # undo en dry-run : rien ne bouge
    assert run("--undo", str(j)) == 0
    assert "A/B/01 Un.m4a" in files(lib)

    assert run("--undo", str(j), "--apply") == 0
    # le parasite supprimé n'est pas restauré
    assert files(lib) == ["C/D/01 Rien.m4a", "vrac/1.m4a", "vrac/2.m4a", "vrac/folder.jpg"]
    assert not (lib / "A").exists()
    # l'annulation a son propre journal
    assert len(journals(run)) == 2


def test_undo_refuses_when_place_taken(lib, run, make_track):
    make_track(lib / "vrac/1.m4a", title="Un", artist="A", album="B", track=1)
    assert run("--apply") == 0
    [j] = journals(run)
    (lib / "vrac").mkdir()
    (lib / "vrac/1.m4a").write_bytes(b"autre")
    assert run("--undo", str(j), "--apply") == 2
    assert (lib / "A/B/01 Un.m4a").exists()


def test_x92_file_renamed_and_undone(lib, run, make_track):
    d = lib / "Queens of the Stone Age/Villains"
    bad = make_track(os.fsencode(d) + b"/03 Feet Don\x92t Fail Me.m4a",
                     title="Feet Don’t Fail Me", albumartist="Queens of the Stone Age",
                     album="Villains", track=3)
    assert run("--apply") == 0
    assert files(lib) == ["Queens of the Stone Age/Villains/03 Feet Don_t Fail Me.m4a"]
    [j] = journals(run)
    assert run("--undo", str(j), "--apply") == 0
    assert bad.exists()


def test_u0092_file_renamed_and_shown(lib, run, make_track, capsys):
    d = lib / "Queens of the Stone Age/Villains"
    make_track(d / "01 Feet Don\u0092't Fail Me.m4a", title="Feet Don't Fail Me",
               albumartist="Queens of the Stone Age", album="Villains", track=1)
    assert run("--apply") == 0
    assert files(lib) == ["Queens of the Stone Age/Villains/01 Feet Don't Fail Me.m4a"]
    assert "Don\\x92't" in capsys.readouterr().out


def test_compilation_conflict_keeps_album_together(lib, run, make_track, capsys):
    d = lib / "Divers/Best Of"
    make_track(d / "1.m4a", title="Un", artist="X", album="Best Of", track=1, compilation=True)
    make_track(d / "2.m4a", title="Deux", artist="Y", album="Best Of", track=2)
    (d / "folder.jpg").write_bytes(b"jpg")
    before = files(lib)
    assert run("--apply") == 2
    assert files(lib) == before
    assert "cpil incohérent : 1/2" in capsys.readouterr().out


def test_coherent_compilation(lib, run, make_track):
    for i, artist in enumerate(("X", "Y"), 1):
        make_track(lib / f"vrac/{i}.mp3", title=f"T{i}", artist=artist, album="Best Of",
                   track=i, compilation=True)
    assert run("--apply") == 0
    assert files(lib) == ["Compilations/Best Of/01 T1.mp3", "Compilations/Best Of/02 T2.mp3"]


def test_annexes_stay_when_a_track_is_left_behind(lib, run, make_track):
    make_track(lib / "vrac/1.m4a", title="Un", artist="A", album="B", track=1)
    make_track(lib / "vrac/2.m4a", artist="A", album="B", track=2)   # pas de titre
    (lib / "vrac/folder.jpg").write_bytes(b"jpg")
    assert run("--apply") == 2
    assert files(lib) == ["A/B/01 Un.m4a", "vrac/2.m4a", "vrac/folder.jpg"]


def test_multidisc_flac_from_inbox(tmp_path, lib, run, make_track):
    inbox = tmp_path / "Inbox/Album"
    make_track(inbox / "a.flac", title="Un", artist="A", album="B", track=1, disc=1, disctotal=2)
    make_track(inbox / "b.flac", title="Deux", artist="A", album="B", track=1, disc=2, disctotal=2)
    assert run(str(inbox), "--apply") == 0
    assert files(lib) == ["A/B/1-01 Un.flac", "A/B/2-01 Deux.flac"]
    assert inbox.exists()                 # dossier passé en argument : jamais supprimé
    assert not (run.state / "last_run").exists()   # passage partiel : pas de dernier passage


def test_target_conflict(lib, run, make_track):
    make_track(lib / "A/B/01 Un.m4a", title="Un", artist="A", album="B", track=1)
    make_track(lib / "vrac/autre.m4a", title="Un", artist="A", album="B", track=1)
    assert run("--apply") == 2
    assert (lib / "vrac/autre.m4a").exists()


def test_since_only_rescans_changed_folders(lib, run, make_track, capsys, monkeypatch):
    monkeypatch.setattr(music_ranger, "SINCE_MARGIN", 0.05)  # passages rapprochés
    make_track(lib / "A/B/01 Un.m4a", title="Un", artist="A", album="B", track=1)
    make_track(lib / "A/C/01 Deux.m4a", title="Deux", artist="A", album="C", track=1)
    time.sleep(0.2)  # plus anciens que la marge de last_run
    # premier passage : pas d'état, tout est traité
    assert run("--since", "--apply") == 0
    assert "pas de dernier passage" in capsys.readouterr().out
    assert (run.state / "last_run").exists()

    time.sleep(0.05)
    # tag modifié dans A/B (comme depuis le Mac) : l'album change
    from mutagen.mp4 import MP4  # pylint: disable=import-outside-toplevel
    f = MP4(lib / "A/B/01 Un.m4a")
    f["\xa9alb"] = ["Nouveau"]
    f.save()

    assert run("--since", "--apply") == 0
    out = capsys.readouterr().out
    assert "analysés : 1" in out          # A/C n'a pas été relu
    assert files(lib) == ["A/C/01 Deux.m4a", "A/Nouveau/01 Un.m4a"]

    # le déplacement a mis à jour la ctime : le fichier est relu une fois, en place
    # (même si la ctime, posée par une horloge plus grossière, précède de quelques
    # ms le début noté du passage : cas vu sur la CI)
    assert run("--since", "--apply") == 0
    assert "en place : 1" in capsys.readouterr().out

    # rien n'a changé depuis : rien n'est relu
    time.sleep(0.2)
    assert run("--since", "--apply") == 0
    capsys.readouterr()
    assert run("--since") == 0
    assert "analysés" not in capsys.readouterr().out


def test_last_run_keeps_a_margin(lib, run, make_track, monkeypatch):
    make_track(lib / "A/B/01 Un.m4a", title="Un", artist="A", album="B", track=1)
    monkeypatch.setattr(music_ranger.time, "time", lambda: 1000.0)
    assert run("--apply") == 0
    assert float((run.state / "last_run").read_text()) == 1000.0 - music_ranger.SINCE_MARGIN


def test_since_dry_run_does_not_advance(lib, run, make_track):
    make_track(lib / "A/B/01 Un.m4a", title="Un", artist="A", album="B", track=1)
    assert run("--since") == 0
    assert not (run.state / "last_run").exists()


def test_autoadd_folder_is_harmless(lib, run):
    d = lib / "Automatically Add to Music.localized/.localized"
    d.mkdir(parents=True)
    (d / "fr.strings").write_text("x")
    assert run("--apply") == 0
    assert (d / "fr.strings").exists()
