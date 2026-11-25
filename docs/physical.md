# Physical albums

`physical` marks the albums of the library you also own on a physical medium (CD, vinyl, cassette…): those of your [Discogs](https://www.discogs.com) collection. The mark is a word in the tracks' comment, so that a virtual library of Lyrion can gather them (see [In Lyrion](#in-lyrion)). The code is in [`physical/`](../src/music_librarian/physical).

## Setting it up

Create a personal access token on discogs.com (Settings, Developers, **Generate new token**) and set it in `DISCOGS_TOKEN`, the variable the edition lookup of the dashboard already reads. The collection read is the one of the token's owner, every folder of it, whether the profile is public or not: no user name is needed.

```bash
docker compose run --rm music-librarian physical            # dry run
docker compose run --rm music-librarian physical --apply
```

To run it every week, add `--physical-at "sun 04:00"` (a day and a time, as `--enrich-at` takes them) to the `watch` command of `docker-compose.yml`. Like the weekly `enrich`, the slot is skipped when an applied pass already ran since (`state/physical_last_run`), and the first slot comes after the container starts, not at its start.

## What a physical album holds

- **`PHYSICAL` in the comment of each of its tracks**, as a word of its own: a comment already there is kept, the word added after it (`Signed copy PHYSICAL`) and taken out of it alone. The comment is the `©cmt` atom of an M4A, the `COMMENT` of a FLAC, the `COMM` frame without a description of an MP3 (iTunes' own `COMM` frames are left alone).
- **The id of its release** (`DISCOGS_RELEASE_ID`, the name Mp3tag's Discogs source writes) in the tracks that hold none. A pass never removes it nor replaces it, even once the release leaves the collection, since another tagger may have written it.

The two can also be set by hand: `tag PATH --set comment=…`, `tag PATH --set discogs_release=…`.

## Which album a release is

Each release of the collection is matched to an album of the library (one album per folder, its album artist, else its artist, and its album title):

1. **by its id**: the album whose tracks hold the release's id;
2. **by its name**: the album of the same artist and title once case, accents, punctuation, `&` / `+` / `and`, a leading `The`, Discogs' numbers of homonyms (`Prince (2)`), a translated title after ` = ` and whatever the titles hold in brackets are set aside. `Abbey Road (2019 Mix)` is `Abbey Road`: any edition of an album owned counts. `Various` and `Various Artists` are the same artist;
3. **by an answer**: an album only close to a release, one of the artist or the title the same and the other similar (`OK Computer` and `OK Computer OKNOTOK 1997 2017`), is a question for the dashboard's review, answered "same album" or "another album". Until it is answered, the album is left as it is.

A release several albums match marks each of them (an album and its deluxe edition, both in the library).

An album is physical when a release matches it, unless it was marked by hand on the dashboard: an album marked physical or not physical by hand keeps that mark whatever the collection holds. Any other album loses `PHYSICAL` once its release leaves the collection, but a collection Discogs answers empty unmarks nothing (`WARNING  empty collection`), in case it answered wrong.

## The output

```
DISCOGS  collection of me: 412 releases
PHYSICAL Air/Moon Safari
         ← Discogs 11: Air – Moon Safari (Vinyl, 1998)
UNMARK   Björk/Debut  (not in the Discogs collection)
DISCOGS  Daft Punk/Discovery  ← release 9 (12 tracks)
REVIEW   Radiohead/OK Computer OKNOTOK
         ~ Discogs 22: Radiohead – OK Computer (CD, 1997)
ABSENT   Discogs 33: Daft Punk – Homework (CD, 1997)
```

`PHYSICAL` is an album to mark, with the release that makes it physical and, in brackets, why when it is not the name (`its Discogs id`, `answered on the dashboard`, `marked by hand`); `UNMARK` one to unmark; `DISCOGS` one already marked whose tracks are to hold the id; `REVIEW` a release only close to an album, to answer on the dashboard; `ABSENT` a release no album of the library is. The summary counts the releases, the albums, those physical, those to mark or unmark, the ids to write, the questions left and the releases not in the library.

The dry run reads Discogs, its metadata only, and writes nothing in the library; it keeps the collection read in `state/physical-collection.json`, which the dashboard reads instead of asking Discogs. Every tag written goes through the journal: `music-librarian undo` restores it.

Exit codes: 0 = OK, 2 = collection unreadable (no token, a token refused, Discogs out of reach), unreadable files or failed writes, 3 = another pass is running, 4 = stopped before the end.

## On the dashboard

- **The library** badges the albums whose every track holds `PHYSICAL`, and its **Physical** filter keeps them; the advanced search tests it as **Physical**, under State.
- **An album's page** tells whether it is physical, and links the release it holds. Its three choices: **As the Discogs collection says**, **Physical, whatever Discogs says** and **Not physical, whatever Discogs says**. A choice by hand is kept in `state/physical.json` and written at once through `tag`, as any edit of the page; given back to Discogs, the album is marked or not by the next pass.
- **Reviews** asks about the releases only close to an album, one page at a time; each answer is kept in `state/physical.json` and applied by the next pass, which the review's button queues.

All of it reads the collection the last pass kept: the dashboard never asks Discogs for it.

## In Lyrion

The [Virtual Library Creator](https://github.com/AF-1/lms-virtuallibrarycreator) plugin builds a virtual library out of the tracks whose comment holds `PHYSICAL`:

1. Install Virtual Library Creator from Lyrion's plugins (Settings, Manage Plugins).
2. In its settings, create a virtual library with the **Comment** filter, the comment holding `PHYSICAL`, and a name (`Physical`).
3. Pick that library in Lyrion's library selector (Material Skin, or the web interface).

A virtual library is a list of tracks: an album shows whole in it when each of its tracks holds the word, which is what `physical` writes. Lyrion reads a comment changed without a move on its next scan of changed files: run one (Settings, Rescan, **Look for new and changed media files**), or wait for its scheduled one; see [lyrion.md](lyrion.md#asking-lyrion-for-a-scan).
