# Naming rules

`organize` gives every audio file (`.m4a`, `.mp3`, `.flac`) the path Apple Music would give it, relative to the library root. The rules live in [`organize/naming.py`](../src/music_librarian/organize/naming.py).

## Target path

```
<Album Artist>/<Album>/<NN> <Title>.<ext>
Compilations/<Album>/<NN> <Title>.<ext>        compilation flag set
<Album Artist>/<Album>/<D>-<NN> <Title>.<ext>  multi-disc album
```

- The top folder is the album artist, else the artist, else `Unknown Artist`. With the compilation flag it is `Compilations`, or the name given with `--compilations-dir`.
- The album folder is the album tag, else `Unknown Album`.
- `NN` is the track number on two digits. It is left out when the track has no number.
- The disc prefix `D-` is added when the album has more than one disc (disc total above 1) or the disc number is above 1. A missing disc number then counts as 1.
- The extension is lowercased.
- A file without a title tag is skipped and reported.

## Tags read

| | Album artist | Artist | Album | Title | Track | Disc | Compilation |
|---|---|---|---|---|---|---|---|
| MP4 (`.m4a`) | `aART` | `©ART` | `©alb` | `©nam` | `trkn` | `disk` | `cpil` |
| MP3 | `TPE2` | `TPE1` | `TALB` | `TIT2` | `TRCK` | `TPOS` | `TCMP` = `1` |
| FLAC | `albumartist`, `album artist` | `artist` | `album` | `title` | `tracknumber` | `discnumber`, `disctotal`, `totaldiscs` | `compilation` = `1` |

`3/12` forms are understood for track and disc numbers.

## Characters

Each name is normalized to Unicode NFC, then:

- `/ : ? " | * < > \` and the typographic quotes and accent `’ ‘ “ ” ´` become `_`. Apple Music was seen replacing `/ : ? " | ’ ´`; the others are replaced by analogy.
- A leading `.` becomes `_`, except in a title that follows a track number: Apple Music only replaces it at the start of the whole file name.
- A trailing `.` in a folder name becomes `_`.
- Leading and trailing spaces are dropped, and an empty name becomes `_`.

## When a file counts as in place

A file whose current path already matches its target is not moved, even when the two differ in ways Apple Music itself produces:

- **Unicode normalization**: NFC and NFD forms of the same name (macOS writes NFD) are equal.
- **Case**: names that only differ in case are equal, unless `--strict-case` is passed.
- **Truncation**: Apple Music cuts long names. A current name of at least 20 characters that the target name starts with is accepted.

The extension must match, case aside.

These tolerances only keep a file where it is. When a folder of its target path also exists exactly as written, next to the variant the file sits in (`Walk of the Earth/` beside `Walk Of The Earth/`), the file joins it, so two folders that only differ in case, normalization or truncation become one. A folder with no such twin stays as it is.

## Around the audio files

- **Side files** (`folder.jpg`, booklets, `.lrc`, anything that is not audio) follow their album, but only when every track of the folder goes to the same target folder and none stayed behind because of a conflict or missing tags. A side file whose target already exists is a conflict.
- **Junk files** (`.DS_Store`, `Thumbs.db`, `desktop.ini`, AppleDouble `._*` files) are deleted.
- **Emptied folders** are removed, up to the library root and the folders given as arguments, which are always kept.
- **Inconsistent compilations**: tracks of one folder and one album that disagree on the compilation flag would be split between `Compilations/` and an artist folder. The whole album is reported as a conflict and left alone.
- **MP4 index**: an `.m4a` file whose `moov` atom comes after `mdat` is typed `alcx` by Lyrion (`mp4x` for AAC) and transcoded rather than streamed. The pass rewrites it in place with `moov` first, before the moves: the audio packets are copied, never re-encoded, and the rewrite is kept only if the packets, the tags, the codec and the duration match the original. The rewrite is not journaled, since there is nothing to undo; a failed check leaves the file as it was and reports an error. Lyrion picks the new type up on its next rescan.
- **Conflicts**: two files with the same target, or a target already taken by another file, are reported and not moved.
