# To do

## Release dates

### The rule

Every player should show the year of the edition owned, not of the first release.

1. Each track holds the edition's date, in the tag Picard calls `date`: `©day` in MP4, `TDRC` in MP3, `DATE` in FLAC.
2. That date is a year alone, `YYYY`.
3. Every track of an album holds the same one. The dashboard's inconsistent albums already check it, comparing whole values, and stay as they are.
4. A track holds no other date: no original date (`TDOR`, `TORY`, `ORIGINALDATE`, `ORIGINALYEAR`, as an MP4 freeform or a `TXXX`), no recording date (`TRDA`, `RECORDINGDATE`), no FLAC `YEAR` beside `DATE`.

### Why rule 4

Tested with Lyrion 9.1.1 on files tagged as Picard does (edition 2011, original 1973):

| File | Year Lyrion shows |
|---|---|
| MP4, `©day` and a freeform `ORIGINALDATE` | 2011 |
| MP3 v2.4, `TDRC` and `TDOR` | 1973 |
| MP3 v2.3, `TYER` and `TORY` | 1973 |
| MP3, `TDRC` and `TXXX:ORIGINALDATE` | 2011 |
| FLAC, `DATE` and `ORIGINALDATE` | 1973 |
| FLAC, `DATE` and `RECORDINGDATE` | 2011 |

Lyrion keeps one year per track, the album taking the year of the last track scanned, and prefers the original date in MP3 and FLAC; nothing configures that. Apple Music reads only `©day` and `TDRC`. Writing a track turns an MP3 into ID3v2.4, which drops `TRDA`.

### Steps

- [x] **Read the missing tags.** The metadata cache also reads the MP4 freeform tags and the MP3 `TXXX` frames, so that rule 4 can be checked in every format. Bump `metadata_cache.VERSION`: the whole library is read again once.
- [x] **Report.** The dashboard lists the albums without an edition date or with one that is not `YYYY`, fixed from the album's page.
- [ ] **Fix in `enrich`.** A tool of its own, run on every pass: the other date tags removed, a `YYYY-MM-DD` date or Apple's `YYYY-MM-DDThh:mm:ssZ` cut down to `YYYY`. A dry run lists the changes; the values removed go to the journal so that `undo` writes them back, as the ReplayGain tags do.
- [ ] **Look the edition up, by hand only.** A button on every album's page asks the providers for the album's editions and writes nothing: each with its year, country, label, number of tracks and a link, those matching the album's number of tracks and durations first, a release MBID or a barcode in the tags naming the edition exactly. Picking a year puts it in the album form's year field, applied as a `tag` pass. `enrich` never looks anything up. The providers live in a package of their own, one module each, ordered by an environment variable, as the lyrics providers are. Their outcomes are found, nothing found, several editions of different years, or unreachable.
  - [ ] MusicBrainz first: no key, one request a second, the existing `USER_AGENT` as its contact.
  - [ ] Update the network rule in CLAUDE.md, both READMEs and `docs/web.md`: the edition lookup is a second exception, beside the lookup of one track's lyrics.

### Later

- [ ] iTunes as a provider: already used for the genre, and the edition Apple Music knows, though its `releaseDate` is often the original date.
- [ ] Discogs as a provider: the most precise on editions, but it needs a token and allows 60 requests a minute.
- [ ] Deezer as a provider: no key, but its date is often that of the digital release.
- [ ] `media/convert.py` copies the FLAC's MusicBrainz ids into MP4 freeform tags named in capitals (`MUSICBRAINZ_ALBUMID`), where Picard and mutagen expect `MusicBrainz Album Id`: a converted file's release MBID is not read back, and the lookup could not use it.
