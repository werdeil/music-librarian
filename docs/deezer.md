# Downloading from Deezer

`music-librarian deezer` downloads an album or a playlist from Deezer into the Inbox, where `import` converts it to ALAC, files it, enriches it, splits its credits and unifies its artists like any other drop. It can be asked for from the command line, from the dashboard and from a Chrome extension, both through the queue of `watch`. It is a rewrite in Python of [deezer-music-download](https://github.com/werdeil/deezer-music-download), whose Deezer API calls, stream decryption and tag writing it ports as they are; the filing and the conversion stay those of music-librarian.

## Limits

- **For personal use, with a paid Deezer account.** Downloading may break Deezer's terms of use: the feature is off by default, and it is up to whoever turns it on to check that their use is allowed.
- **No key is provided.** The `arl` cookie and the license token come from your own Deezer session; the stream decryption key (`pre_key`) and its IV are not published here and have to be found on your own. None of them is ever written to the repository, the image, the logs or the run history.
- **Deezer's private API can change** (the state embedded in its pages, `get_url`) without notice; each failure is reported on its own, so what changed shows.

## Decisions

| # | Topic | Decision |
|---|---|---|
| D1 | Language | A rewrite in Python, no Go subprocess nor binary in the image. |
| D2 | Activation | Off until `DEEZER_ARL`, `DEEZER_PRE_KEY` and `DEEZER_IV` are all set: the command then exits with a clear error, and the dashboard shows nothing of it. |
| D3 | Dry run | Without `--apply`, only the metadata is read (album, playlist, tracks): `get_url` is not called and nothing is downloaded; the format is shown as "best of FLAC/MP3_320/MP3_128". This is the one dry run that reaches the network. |
| D4 | Missing tracks | An incomplete album is still dropped, its summary listing `unavailable: N tracks` with their titles. An album without any track is not dropped. `--complete-only` refuses an incomplete album. |
| D5 | Lock | A download writes only to a hidden folder of the Inbox and does not take the `--apply` lock. From the dashboard and the extension it goes through the `Requests` queue of `watch`, and holds it while it runs. |
| D6 | Extension | A route of its own, guarded by `DEEZER_EXTENSION_KEY`, with CORS open to `DEEZER_EXTENSION_ORIGIN` (`chrome-extension://<id>`) only. The route only queues a request. |
| D7 | Tags | As deezer-music-download writes them: the same tags, the artists joined by `, `, Deezer's genre, `folder.jpg` dropped and embedded into every track. Without a genre from Deezer, none is written (the label is not used in its place). `enrich` then aligns the genre with the validated list, and `split` splits the artists it can vouch for. |
| D8 | Git history | The history of deezer-music-download is not carried over; that repository is archived with a link to this page. |
| D9 | Blowfish | `pycryptodome` (`Crypto.Cipher.Blowfish`), pinned in `requirements.txt` and `pyproject.toml`. |

## Configuration

| Variable | Meaning |
|---|---|
| `DEEZER_ARL` | The `arl` cookie of a logged-in Deezer session. Required. |
| `DEEZER_PRE_KEY` | The 16-byte key the per-track Blowfish key is derived from. Required. |
| `DEEZER_IV` | The Blowfish IV, 16 hexadecimal characters. Required. |
| `DEEZER_LICENSE_TOKEN` | The license token `get_url` and the playlist API ask for. Needed to download, unless the request carries its own, as the extension's does; a dry run goes without it. |
| `DEEZER_EXTENSION_KEY` | The key the extension sends as `Authorization: Bearer …`. Without it, the extension's routes answer 404. |
| `DEEZER_EXTENSION_ORIGIN` | The extension's origin, `chrome-extension://<id>`, the only one its routes accept. |

A value that is malformed is reported by its name, never shown. With the compose file, set them in `.env`: they reach the container empty otherwise, which leaves the download off.

### Finding the values

- **`arl`**: log in on deezer.com in a browser, then read the cookie named `arl` of `www.deezer.com` in its developer tools (Application, Cookies). It lasts several months; a new login gives a new one.
- **The license token**: the web player sends it with each `get_url` request, in the `license_token` field of the body (developer tools, Network, a request to `media.deezer.com/v1/get_url` while a track plays). The Chrome extension captures it by itself and sends it with each download. It expires: when every track fails with an `error <code>` from Deezer, take a fresh one.
- **`pre_key` and `iv`**: the key every track's Blowfish key is derived from, and the IV of the decryption. They are the same for every account and live in the code of Deezer's web player; this project does not publish them.

## Command line

```
music-librarian deezer album ID|URL... [--apply] [--complete-only] [--inbox INBOX] [--state-dir DIR]
music-librarian deezer playlist ID|URL... [--apply] [--complete-only] [--inbox INBOX] [--state-dir DIR]
```

An album or a playlist is given by its id or its address (`https://www.deezer.com/fr/album/302127`); a short `link.deezer.com` link is refused, its deezer.com address is to be given instead. The Inbox is `--inbox`, else `MUSIC_LIBRARIAN_INBOX`.

- **A dry run** prints `PLAN` and the number of tracks of each album or playlist, and changes nothing.
- **With `--apply`**, each track is announced `DOWNLOAD` with its format (`FLAC 24-bit/96kHz`), `FALLBACK` when its fallback recording is downloaded, `SKIPPED` with the reason when it is unavailable; each album moved into the Inbox is announced `ADDED`. A tag or cover that could not be written is a `WARNING`, the track kept.
- **`--complete-only`** leaves out an album, or a playlist, with any track unavailable.
- **The summary** (`--- Deezer summary ---`) counts the albums, the tracks, the unavailable ones by title, and the formats.

Exit codes: 0 OK, 1 failed (not configured, an album or a playlist not found, not one of its tracks downloaded), 2 tracks unavailable, 4 stopped before the end, what was waiting removed. Each pass is kept in the run history.

## On the dashboard

Once the download is configured, the overview's Inbox shows **Download from Deezer**: a deezer.com address or an id (the radio buttons say whether a bare id names an album or a playlist) and **only if every track is available** (`--complete-only`). **Download** queues the pass applied at once, as **Add to the Inbox** does, and opens its run's page, which follows it live; there is no preview. The form is disabled while `watch` runs without `--apply`, and until `DEEZER_LICENSE_TOKEN` is set, since the dashboard has no other token to give. A short link is refused with a word on what to give instead. Details of the routes in [web.md](web.md#downloading-from-deezer).

## The Chrome extension

The extension, in [`extensions/chrome`](../extensions/chrome), captures the license token while you browse deezer.com and sends the album or the playlist of the page you are on to the dashboard, then shows how many of its tracks are done.

1. Pick a key of your own, such as the output of `openssl rand -hex 32`, and set it as `DEEZER_EXTENSION_KEY`.
2. In Chrome, open `chrome://extensions`, turn on the developer mode, then **Load unpacked** and pick the `extensions/chrome` folder. Chrome shows the extension's id under its name.
3. Set `DEEZER_EXTENSION_ORIGIN` to `chrome-extension://<that id>`, then restart the container.
4. In the extension's popup, open the settings (⚙️), type the dashboard's address (`http://nas.local:8080`) and the key, and save: Chrome asks for access to that address, which the extension needs to reach it.

The badge then reads **Connected**, or **Connected, downloads off** when `watch` runs without `--apply` or the download is not configured. On a Deezer album or playlist page, the address and id are filled in; **Download** queues the pass. The extension's routes answer only its origin and its key, and only queue: see [web.md](web.md#downloading-from-deezer).

## Troubleshooting

- **`the Deezer download is off`**: one of `DEEZER_ARL`, `DEEZER_PRE_KEY`, `DEEZER_IV` is empty in the container (`docker compose exec music-librarian env | grep -c DEEZER_` counts them without showing them).
- **`no license token to download with`**: set `DEEZER_LICENSE_TOKEN`, or download from the extension, which sends its own.
- **`SKIPPED  <title>: no playable format among FLAC/MP3_320/MP3_128 (…)`**: `get_url` gave no stream for the track. Between the brackets, `error <code>: <message>` is Deezer's own refusal (a license token expired or of another account, a track not streamable in your country or with your subscription), `HTTP <status>` an answer without a reason, `empty data` or `no media available` a track offered in none of the formats asked. The track is counted unavailable; the album is still dropped, unless `--complete-only`.
- **`FALLBACK`**: the track has no rights of its own, and Deezer points at an equivalent recording, downloaded and decrypted in its place, as the web player does. Its tags stay the track's.
- **`unavailable: N tracks (…)`** in the summary, exit code 2: those tracks were skipped; the album reached the Inbox without them.
- **`WARNING  cover of …`** or **`WARNING  <title>: tags not written`**: the track was downloaded; `enrich` embeds `folder.jpg` later, and `import` refuses an item whose tags lack what filing needs, sending it to `_failed/`.
- **`no app state in the page`**, **`no SONGS in the page`**: Deezer changed its pages. The pages the tests read are in `tests/deezer/fixtures/`, to compare with what Deezer serves now.

## How a download runs

1. **The request** comes from the command line, or from the dashboard or the extension through the `Requests` queue of `watch`.
2. **The metadata is read**: the album or the playlist, then each of its tracks. A playlist's tracks each have their album read too, so their album artist, track total, year and genre are right.
3. **Each track is fetched** with `--apply`: its stream URL asked from `get_url` in the best format offered (FLAC, then MP3 320, then MP3 128), the stream downloaded and decrypted, its tags and cover written. A track offered in no format is tried again through its fallback track.
4. **The files are written** to `Inbox/.deezer-<id>/<Artist - Album>/NN - Title.flac` (or `.mp3`), with `folder.jpg`. The names do not matter: `organize` files the tracks by their tags.
5. **Each album folder is moved** into the Inbox once complete, by a rename on the same file system, so `import` never sees an album half written.
6. **`watch` imports it** as any drop, once it has settled: conversion, filing, enrichment, split and dedupe.

See [architecture.md](architecture.md) for how `watch` and `import` run, and [web.md](web.md) for the dashboard.
