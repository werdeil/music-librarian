# Commands

Every command is a dry run unless `--apply` is passed, and every change it applies is journaled, so `undo` can reverse it. `music-librarian {command} --help` lists each command's options.

## `organize`

Files the library as `Album Artist/Album/NN Title.ext` (falling back to the artist), `Compilations/Album/…` when the compilation flag is set, and `D-NN Title` for multi-disc albums. Characters Apple Music replaces (`/ : ? " | ’ ´ * < > \` …) become `_`. Accent normalization (NFC/NFD), case-only differences and truncated names are tolerated so existing files are not renamed for nothing, unless a folder spelled exactly as the target already exists next to the variant: the variant's files then join it. Side files (`folder.jpg`, booklets, `.lrc`) follow their album, renamed `folder (2).jpg` when the name is already taken there; `.DS_Store` and `._*` are deleted. An album whose tracks disagree on the compilation flag is reported as a conflict and left alone. A title or an album takes one value, without `;`: a track holding several, or a `;`, is skipped, so `import` sends its item to `_failed/`, and `tag` and the dashboard refuse to write one. Any other field holding values that differ (`Walk Off The Earth` and `Walk Off the Earth` as artist) is reported as `MULTIPLE`, and the pass then needs checking, but the track is filed by its first value. `.m4a` files whose index (`moov` atom) comes after the audio are rewritten with the index first, without re-encoding, so Lyrion streams them as `alc` instead of transcoding them as `alcx`. `--since` only rescans folders changed since the last full pass. The full rules are in [naming-rules.md](naming-rules.md).

## `import`

Takes album folders or loose files dropped in the Inbox (FLAC, ALAC, MP3), converts FLAC to ALAC with ffmpeg, one file per CPU at a time (tags and artwork kept, duration checked with ffprobe), files them all-or-nothing, and enriches only the target folders. Originals go to `_done/YYYY-MM-DD/` and are purged after 7 days; items that cannot be filed go to `_failed/`.

## `enrich`

Runs over the whole library and only fills what is missing or off: genres aligned with a validated list (see below), ReplayGain measured with ffmpeg, the embedded cover when `folder.jpg` differs from it, a value held twice in a field (`Copain de soupe; Copain de soupe`, or a composer named twice) kept once (`--no-repeats` to leave them), and lyrics from LRCLIB and NetEase, plus Musixmatch if enabled. Lyrics are only written when the provider's title, artist and duration match the file. A track looked up in vain is not looked up again for 30 days (`LYRICS_RETRY_DAYS`), unless its artist, title or album changes or `enrich --retry` is given. A track a provider calls instrumental gets `[Instrumental]` as its lyrics, which keeps it from being looked up again; the dashboard marks one by hand from its lyrics page, or through the review of the tracks without lyrics. A dry run looks nothing up nor measures anything: it shows the genre, cover, repeated value and credit changes, and counts the albums to search on iTunes, the tracks to measure and the tracks whose lyrics to look up, with the time the pass applied would take. `--no-lyrics` leaves the lyrics out, by far the slowest part, and `--no-genres`, `--no-replaygain` and `--no-covers` the other tools; such a pass does not stand in for the weekly one. With `--apply`, a `PROGRESS` line after each batch of 20 folders tells how far each tool has got and how long it has left, and each tool ends on one summary of its counts and the time it took.

## `scan`

Reads the metadata of the whole library into a cache in the state folder (`metadata.db`), then prints the track count per genre. It first lists the library, then tells every 1,000 files read how many it has gone through out of the total, and the time left. `organize`, `enrich`, `import`, `split` and `dedupe` read the tags through that cache, so after one full read (about 20 minutes for 25,000 tracks on a hard disk) a pass only opens the files whose size or dates changed, and a dry run followed by `--apply` reads the library once. Files gone from the library are dropped from the cache by `scan` and by every complete `enrich` pass. `scan --force` reads every file again whatever the cache holds, as `enrich --rescan` does before enriching.

## `split`

Rewrites composer and artist tags such as `John Lennon/Paul McCartney` as `John Lennon; Paul McCartney`, the separator Lyrion splits on. A comma or slash is only split when the library vouches for it: a credit that is an album artist somewhere (`AC/DC`, `Earth, Wind & Fire`) is kept, a credit whose fragments are all names seen on their own is split, and any other one waits for a decision in `state/split-decisions.txt` (`split`, `keep`, or the names to write). `split --review` asks about those one at a time with a suggestion that Enter accepts, can stop and resume at any point, and skips the credits an earlier answer already settles. Album artists that look like a collaboration of known names (`Iggy Pop, Muse`) stay whole, but the dry run lists them as `GUARDED` and the review asks about them last, keeping them unless told to split. The artist is only rewritten on tracks with an album artist. `import` runs it on what it files and `enrich` on the whole library; details in [splitting.md](splitting.md).

## `dedupe`

Finds the names of one artist spelled several ways: the same once case, accents, punctuation and a leading `The` are set aside (`Beyonce` / `Beyoncé`), the same MusicBrainz id, a close spelling (`Mettalica`), or a partial name (`Bach` / `Johann Sebastian Bach`). `dedupe --review` asks about each group one at a time and keeps the answers in `state/artist-aliases.txt` (`Beyonce => Beyoncé`, or `John Lennon != Julian Lennon`), and `--apply` rewrites the variants in the tags. `import` and `enrich` then apply the aliases by themselves; details in [dedupe.md](dedupe.md).

## `watch`

Is the container's long-running process: it imports whatever lands in the Inbox once the copy has settled (and runs the import once a day, at startup then each new day, to purge `_done/`), and can run `organize --since` nightly (`--organize-at`) and `enrich --rescan` weekly (`--enrich-at "sun 03:00"`), so the metadata cache is read again in full once a week.

## `tag PATH… --set FIELD=VALUE`

Rewrites tag fields of tracks or of every track of a folder (title, artist, album artist, album, track and disc numbers and totals, disc name, grouping, year, genre, composer, compilation flag; an empty value removes the field; `replaygain=`, which takes no value, removes the ReplayGain tags, measured again by the next `enrich`), then files them where their new tags put them, the tag changes and the moves in one journal. The dry run lists both; a genre takes the validated list's spelling. `tag PATH… --disc-names` copies the grouping, the only one of the two Apple Music shows, into the disc name Lyrion shows at the head of each disc, on the albums of several discs that each hold a grouping of their own (`Jupiter` and `Mars` on *Stadium Arcadium*), for the tracks without a disc name; the other albums with a grouping are listed as `KEPT`.

## `cover FOLDER IMAGE`

Puts an image as an album's `folder.jpg`, converted to JPEG with ffmpeg when in another format, the previous one kept in the state folder, then embeds it into the album's tracks. The dry run says what it replaces and how many tracks it would embed it into; `undo` puts the previous `folder.jpg` back.

## `undo JOURNAL`

Replays a run's journal in reverse, moves and tag changes alike.

## Run history

Each pass keeps its output in the state folder, wherever it was started from (`watch`, `docker compose run` or a shell): `state/runs/<id>.log`, with the 200 latest passes listed in `state/runs.db` (command, start and end, exit code, summary).

## Genres

With a validated genre list, `import` and `enrich` rewrite each track's genre to the list's spelling. Copy [`genres.example.txt`](../genres.example.txt) to `state/genres.txt` and adapt it: one validated genre per line, optionally followed by a colon and the names that stand for it (`Rock Dur: hard rock, metal`). Names are compared without case, accents, spaces or punctuation, and a tag holding several genres (`Rock; Pop`) takes the first one the list knows. A genre the list does not know is left as is and reported as `UNKNOWN`: add it to the list, by hand or from the dashboard's Reviews page, and the next weekly `enrich` aligns it. Without a list, genres are left alone.

With `GENRE_LOOKUP=itunes`, an album whose genre the list cannot align, or that has none, is searched on the [iTunes Search API](https://performance-partners.apple.com/search-api), free and without a key: when the list knows Apple's genre for that album (the example maps Apple's names), its tracks take it, logged as `ITUNES`. Otherwise Apple's genre is shown next to `UNKNOWN`, so it can be added to the list. Answers are kept in `state/genre_lookup.json`; an album Apple does not have is searched again after 30 days. Choose the store with `ITUNES_COUNTRY` (`fr` for French genre names).

## Lyrics providers

[LRCLIB](https://lrclib.net) is an open database with a public API. NetEase Cloud Music and Musixmatch are reached through the endpoints of their own apps, which are not public APIs: they can change or block requests at any time, and using them may go against their terms of service. Musixmatch is therefore off by default; add it to `LYRICS_PROVIDERS` (e.g. `lrclib,musixmatch,netease`) if you accept that. Leave NetEase out the same way if you prefer.
