# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0]

First public release.

### Added

- `organize`: files a library the way Apple Music names it, tolerating Unicode normalization, case-only differences and truncated names, with side files following their album and inconsistent compilations held back ([naming rules](docs/naming-rules.md)).
- `import`: imports album folders and loose files from an Inbox, converts FLAC to ALAC with checks, and files each item all-or-nothing.
- `enrich`: fills in missing ReplayGain, embedded covers and lyrics (LRCLIB and NetEase, Musixmatch on request) over the whole library.
- `watch`: the container's process, importing new drops and running `organize --since` nightly and `enrich` weekly.
- `undo`: replays a pass's journal backwards.
- Optional carry-over of play counts and ratings into Lyrion Music Server's `persist.db` ([Lyrion](docs/lyrion.md)).
- Docker image built with the test suite, published as `ghcr.io/werdeil/music-librarian` for amd64 and arm64, and a compose file for the long-running container.

### Changed, for installs predating the public release

- The command `range` is now `organize`, and `watch --range-at` is now `--organize-at`. The old names are gone.
- The Inbox folders are `_done`, `_failed` and `_processing` (formerly `_traités`, `_erreurs` and `_en_cours`): rename them by hand before upgrading.
- `--enrich-at` takes English day names (`sun 03:00`); French ones are still accepted.
- Messages and help are in English.
- Without `MUSIC_LIBRARIAN_STATE`, the state folder is `~/.local/state/music-librarian` (it was next to the scripts).
- The compose file defaults `TZ` to `UTC`.
- Musixmatch is off by default: set `LYRICS_PROVIDERS=lrclib,musixmatch,netease` to keep it.

[Unreleased]: https://github.com/werdeil/music-librarian/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/werdeil/music-librarian/releases/tag/v0.1.0
