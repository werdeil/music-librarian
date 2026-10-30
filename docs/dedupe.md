# Unifying artist names

`dedupe` finds the names of one artist spelled several ways (`Beyoncé` / `Beyonce`, `The Beatles` / `Beatles`, `Mettalica` / `Metallica`) and rewrites the variants in the tags, so Lyrion shows a single artist. The code is in [`dedupe/`](../src/music_librarian/dedupe). Nothing is merged without an answer: the command only proposes, and the answers live in a file.

## Which names are compared

The album artist, artist and composer tags of every track, cut on `;` as Lyrion does (see [splitting.md](splitting.md)). These are the names Lyrion builds its artist list from.

## Which names are grouped

Two names are grouped when they are:

- **the same once normalized**: case, accents, punctuation and spaces, typographic quotes, `&` / `+` / `and` / `et`, a leading `The` and the `Last, First` order set aside (`R.E.M.` / `REM`, `J.S. Bach` / `J. S. Bach`, `Bach, Johann Sebastian` / `Johann Sebastian Bach`);
- **credited with the same MusicBrainz artist id** (`MusicBrainz Artist Id`, `MusicBrainz Album Artist Id`), when the tag credits a single name;
- **a close spelling**: names of 6 letters or more, one typo apart once normalized: a letter missing, added or changed, or two neighbouring letters swapped (`Mettalica` / `Metallica`);
- **a partial name**: a single word that ends exactly one longer name (`Bach` / `Johann Sebastian Bach`; `Young` is left alone when there are `Angus Young` and `Malcolm Young`).

Names credited with different MusicBrainz ids are homonyms and never grouped, and neither are names the aliases file declares different. A name linked to two others puts the three in one group.

## The aliases file

`state/artist-aliases.txt` (or `--aliases FILE`) holds one answer per line:

```
Beyonce => Beyoncé
Mettalica => Metallica
John Lennon != Julian Lennon
```

- `VARIANT => NAME` rewrites the variant as the name. Aliases follow each other (`A => B` and `B => C` make `A` a `C`).
- `NAME != OTHER` keeps the two names apart: the pair is never proposed again.

Names match case and spaces aside. Lines starting with `#` are comments.

## Reviewing the groups

`music-librarian dedupe` (dry run) lists the groups the file does not settle yet and the tags it would rewrite. `music-librarian dedupe --review` asks about the groups one at a time, the largest first:

```
REVIEW   [1/87] same name once normalized
         1  The Beatles  (212 tags)
         2  Beatles  (14 tags)
         [Enter] → The Beatles · 1-2 pick · e edit · d different · n later · u undo · q quit >
```

- **Enter** keeps the suggested spelling: the most used one, the `First Last` and longer one on a tie, and a name of several words when a partial name is involved. **1-n** picks another one, **e** types a new one.
- **d** declares the names of the group different. **n** leaves the group for later, **u** takes back the previous answer, **q** (or Ctrl-D) stops.

Each answer is written to the aliases file at once, so a review can stop at any point and resume where it left off. A review only writes the aliases file. In a terminal it runs with `docker compose run --rm music-librarian dedupe --review` in Docker; the dashboard asks the same questions on its Reviews page (see [web.md](web.md)).

## Rewriting the tags

`music-librarian dedupe --apply` rewrites each variant in the tags, within a list of names too (`Beyonce; Jay-Z` becomes `Beyoncé; Jay-Z`). A tag that already holds several values is left alone. Every change goes through the journal and `music-librarian undo` restores it.

A renamed album artist changes the folder its albums belong in: the next `organize` pass (nightly with `watch --organize-at`) moves them, and the Lyrion history follows if the carry-over is on.

The aliases apply by themselves after that:

- `import` applies them to what it files, after the split (`--no-dedupe` to skip it);
- `enrich` applies them to the whole library, after the split (`--no-dedupe` to skip it).

## Reading the library

`split` and `dedupe` read the tags through the metadata cache (`state/metadata.db`, filled by `scan` and every enrichment pass), so after one full read a pass, a review included, only opens the files whose size or dates changed.
