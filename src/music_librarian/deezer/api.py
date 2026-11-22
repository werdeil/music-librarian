"""What Deezer says of an album, a playlist and a track, and where a track's stream is."""
import json
from dataclasses import dataclass, field

from music_librarian.deezer.client import DeezerError

API = "https://api.deezer.com"
SITE = "https://www.deezer.com/de"
GET_URL = "https://media.deezer.com/v1/get_url"
GW = "https://www.deezer.com/ajax/gw-light.php"
TOKEN_EXPIRED = "1001"
FORMATS = ("FLAC", "MP3_320", "MP3_128")
# The pages carry their data as a script assigning it; Deezer has no public API for the track tokens.
STATE_START, STATE_END = "window.__DZR_APP_STATE__ = ", "</script>"


@dataclass
class Track:  # pylint: disable=too-many-instance-attributes  # one per field of Deezer's
    song_id: str
    title: str = ""
    version: str = ""
    artists: list = field(default_factory=list)
    album: str = ""
    album_id: str = ""
    album_picture: str = ""
    track_number: str = ""
    disc_number: str = ""
    isrc: str = ""
    copyright: str = ""
    physical_release_date: str = ""
    composers: list = field(default_factory=list)
    track_token: str = field(default="", repr=False)
    fallback: "Track | None" = None

    @classmethod
    def from_state(cls, data):
        """The track of a page's state (its DATA, or an entry of an album's SONGS)."""
        fallback = data.get("FALLBACK")
        return cls(song_id=_text(data.get("SNG_ID")), title=_text(data.get("SNG_TITLE")),
                   version=_text(data.get("VERSION")),
                   artists=[_text(a.get("ART_NAME")) for a in data.get("ARTISTS") or []
                            if isinstance(a, dict) and a.get("ART_NAME")],
                   album=_text(data.get("ALB_TITLE")), album_id=_text(data.get("ALB_ID")),
                   album_picture=_text(data.get("ALB_PICTURE")),
                   track_number=_text(data.get("TRACK_NUMBER")), disc_number=_text(data.get("DISK_NUMBER")),
                   isrc=_text(data.get("ISRC")), copyright=_text(data.get("COPYRIGHT")),
                   physical_release_date=_text(data.get("PHYSICAL_RELEASE_DATE")),
                   composers=_composers(data.get("SNG_CONTRIBUTORS")),
                   track_token=_text(data.get("TRACK_TOKEN")),
                   fallback=cls.from_state(fallback) if isinstance(fallback, dict) else None)


@dataclass
class Album:
    album_id: str
    title: str = ""
    artist: str = ""
    nb_tracks: int = 0
    nb_discs: int = 0
    release_date: str = ""
    cover_xl: str = ""
    genres: list = field(default_factory=list)

    @classmethod
    def from_api(cls, data):
        genres = ((data.get("genres") or {}).get("data")) or []
        return cls(album_id=_text(data.get("id")), title=_text(data.get("title")),
                   artist=_text((data.get("artist") or {}).get("name")),
                   nb_tracks=_number(data.get("nb_tracks")), nb_discs=_number(data.get("nb_discs")),
                   release_date=_text(data.get("release_date")), cover_xl=_text(data.get("cover_xl")),
                   genres=[_text(g.get("name")) for g in genres
                           if isinstance(g, dict) and _text(g.get("name"))])


@dataclass
class Playlist:
    playlist_id: str
    title: str = ""
    track_ids: list = field(default_factory=list)


@dataclass
class Stream:
    """Where to download a track: its format, its URL, and the recording whose key decrypts it,
    another than the track's own when Deezer serves its fallback (`fallback`)."""
    format: str
    url: str = field(repr=False)
    song_id: str
    fallback: bool = False


def _text(value):
    return "" if value is None else str(value).strip()


def _number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _composers(contributors):
    """SNG_CONTRIBUTORS comes as one object, or as a list of them of which the first counts."""
    if isinstance(contributors, list):
        contributors = contributors[0] if contributors else {}
    if not isinstance(contributors, dict):
        return []
    return [_text(c) for c in contributors.get("composer") or [] if _text(c)]


def app_state(html):
    """The state a Deezer page embeds. Raises DeezerError."""
    start = html.find(STATE_START)
    if start < 0:
        raise DeezerError("no app state in the page")
    end = html.find(STATE_END, start)
    if end < 0:
        raise DeezerError("no end to the app state in the page")
    try:
        state, _ = json.JSONDecoder().raw_decode(html[start + len(STATE_START):end].strip())
    except ValueError as exc:
        raise DeezerError("the app state is not JSON") from exc
    if not isinstance(state, dict):
        raise DeezerError("the app state is not an object")
    return state


def pick_best(medias):
    """The media of the best format FORMATS ranks, whatever order Deezer gives them in."""
    def rank(media):
        name = str(media.get("format") or "").upper()
        return FORMATS.index(name) if name in FORMATS else len(FORMATS)
    return min(medias, key=rank)


def stream_url(media):
    """The URL of a media's stream, from Akamai when offered; a bare hash is built into the CDN's URL."""
    sources = media.get("sources") or []
    if not sources:
        raise DeezerError("no stream source in the media")
    url = next((s.get("url") for s in sources if s.get("provider") == "ak"), None) or sources[0].get("url")
    if not url:
        raise DeezerError("no stream source in the media")
    if not url.startswith("http"):
        flac = str(media.get("format") or "").upper().startswith("FLAC")
        url = (f"https://cdns-preview-d.dzcdn.net/stream/c-{ord(url[0]) % 10}/{url}-"
               f"{'9' if flac else '3'}.{'flac' if flac else 'mp3'}")
    return url


def disc_total(album, tracks):
    """The album's number of discs: the API's, else the highest disc number of its tracks."""
    return album.nb_discs or max((_number(t.disc_number) for t in tracks), default=0)


class TokenExpired(DeezerError):
    """get_url refused the license token as expired."""


class ArlRefused(DeezerError):
    """Deezer takes the arl for no account."""


def user_license_token(client):
    """The license token of the account whose arl `client` sends, as the web player gets it before it
    plays. Raises ArlRefused when Deezer takes the arl for no account, DeezerError otherwise."""
    params = {"method": "deezer.getUserData", "input": "3", "api_version": "1.0", "api_token": "null"}
    response = client.get(GW, params=params)
    if response.status_code != 200:
        raise DeezerError(f"user data: HTTP {response.status_code}")
    try:
        user = (response.json().get("results") or {}).get("USER") or {}
    except (ValueError, AttributeError) as exc:
        raise DeezerError("user data: not JSON") from exc
    if str(user.get("USER_ID") or "0") == "0":
        raise ArlRefused("the arl is refused by Deezer: take the arl cookie of a logged-in session again")
    token = _text((user.get("OPTIONS") or {}).get("license_token"))
    if not token:
        raise DeezerError("user data: no license token")
    return token


class Api:
    """Deezer's metadata and streams, through `client` (client.Client); `license_token` is the one
    get_url and the playlist API ask for, and `renew()`, when given, a fresh one once it expires."""

    def __init__(self, client, license_token="", renew=None):
        self.client = client
        self._license_token = license_token
        self._renew = renew

    def __repr__(self):
        return "Api()"

    def _ok(self, response, what):
        if response.status_code != 200:
            raise DeezerError(f"{what}: HTTP {response.status_code}")
        return response

    def _json(self, url, what, **kwargs):
        response = self._ok(self.client.get(url, **kwargs), what)
        try:
            data = response.json()
        except ValueError as exc:
            raise DeezerError(f"{what}: not JSON") from exc
        if not isinstance(data, dict):
            raise DeezerError(f"{what}: not an object")
        if "error" in data:
            error = data["error"] if isinstance(data["error"], dict) else {}
            raise DeezerError(f"{what}: {error.get('message') or 'error'}")
        return data

    def _page(self, kind, item_id):
        return app_state(self._ok(self.client.get(f"{SITE}/{kind}/{item_id}"), f"{kind} {item_id}").text)

    def album(self, album_id):
        """The album as the public API describes it. Raises DeezerError."""
        return Album.from_api(self._json(f"{API}/album/{album_id}", f"album {album_id}"))

    def album_tracks(self, album_id):
        """The tracks of the album's page, in its order. Raises DeezerError."""
        songs = (self._page("album", album_id).get("SONGS") or {})
        data = songs.get("data") if isinstance(songs, dict) else None
        if not isinstance(data, list):
            raise DeezerError(f"album {album_id}: no SONGS in the page")
        return [Track.from_state(song) for song in data if isinstance(song, dict)]

    def track(self, track_id):
        """The track of its page. Raises DeezerError."""
        data = self._page("track", track_id).get("DATA")
        if not isinstance(data, dict):
            raise DeezerError(f"track {track_id}: no DATA in the page")
        return Track.from_state(data)

    def playlist(self, playlist_id):
        """The playlist's title and track ids, from the public API, else from its page.
        Raises DeezerError."""
        try:
            data = self._json(f"{API}/playlist/{playlist_id}", f"playlist {playlist_id}",
                              params={"access_token": self._license_token})
        except DeezerError:
            data = {}
        ids = self._api_track_ids(data.get("tracks") or {}, playlist_id)
        if ids:
            return Playlist(playlist_id, _text(data.get("title")), ids)
        state = self._page("playlist", playlist_id)
        found = _track_list(state)
        if found is None:
            raise DeezerError(f"playlist {playlist_id}: no track list in the page")
        ids = [_text(t.get("id") or t.get("SNG_ID")) for t in found if isinstance(t, dict)]
        title = _text(data.get("title")) or _playlist_title(state)
        return Playlist(playlist_id, title, [i for i in ids if i])

    def _api_track_ids(self, tracks, playlist_id):
        """The ids of a playlist's tracks, the pages after the first followed through `next`."""
        ids, seen = [], set()
        while isinstance(tracks, dict):
            ids += [_text(t.get("id")) for t in tracks.get("data") or []
                    if isinstance(t, dict) and _text(t.get("id"))]
            following = tracks.get("next")
            if not following or not str(following).startswith(API + "/") or following in seen:
                break
            seen.add(following)
            tracks = self._json(following, f"playlist {playlist_id}")
        return ids

    def resolve(self, track_token):
        """(format, url) of the best stream get_url offers for a track token, the license token renewed
        when it has expired; a renewal that fails is not tried again. Raises DeezerError."""
        try:
            return self._resolve(track_token)
        except TokenExpired:
            renew, self._renew = self._renew, None
            if renew is None:
                raise
            self._license_token = renew()
            self._renew = renew
            return self._resolve(track_token)

    def _resolve(self, track_token):
        body = {"license_token": self._license_token,
                "media": [{"type": "FULL", "formats": [{"cipher": "BF_CBC_STRIPE", "format": f}
                                                       for f in FORMATS]}],
                "track_tokens": [track_token]}
        try:
            media = self._media(self.client.post(GET_URL, json=body))
        except DeezerError as exc:
            kind = TokenExpired if isinstance(exc, TokenExpired) else DeezerError
            raise kind(f"no playable format among {'/'.join(FORMATS)} ({exc})") from exc
        best = pick_best(media)
        return str(best.get("format")), stream_url(best)

    @staticmethod
    def _media(response):
        try:
            answer = response.json()
        except ValueError:
            answer = None
        if response.status_code != 200:
            errors = (answer or {}).get("errors") if isinstance(answer, dict) else None
            if errors:
                kind = TokenExpired if str(errors[0].get("code")) == TOKEN_EXPIRED else DeezerError
                raise kind(f"error {errors[0].get('code')}: {errors[0].get('message')}")
            raise DeezerError(f"HTTP {response.status_code}")
        data = (answer or {}).get("data") if isinstance(answer, dict) else None
        if not data:
            raise DeezerError("empty data")
        if data[0].get("errors"):
            error = data[0]["errors"][0]
            kind = TokenExpired if str(error.get("code")) == TOKEN_EXPIRED else DeezerError
            raise kind(error.get("message") or "error")
        if not data[0].get("media"):
            raise DeezerError("no media available")
        return data[0]["media"]

    def stream(self, track):
        """The stream of `track`, or of its fallback recording when its own has none.
        Raises DeezerError, the track's own error when the fallback fails too."""
        try:
            return Stream(*self.resolve(track.track_token), song_id=track.song_id)
        except DeezerError:
            fallback = track.fallback
            if fallback is None or not fallback.track_token:
                raise
            try:
                found = self.resolve(fallback.track_token)
            except DeezerError:
                pass
            else:
                return Stream(*found, song_id=fallback.song_id, fallback=True)
            raise


def _track_list(state):
    """The first list in the state whose first entry looks like a track (an id or SNG_ID)."""
    if isinstance(state, dict):
        values = state.values()
    elif isinstance(state, list):
        if state and isinstance(state[0], dict) and ("id" in state[0] or "SNG_ID" in state[0]):
            return state
        values = state
    else:
        return None
    for value in values:
        found = _track_list(value)
        if found is not None:
            return found
    return None


def _playlist_title(state):
    """The PLAYLIST's title in the state, else the first TITLE or title found."""
    if isinstance(state, dict):
        playlist = state.get("PLAYLIST")
        if isinstance(playlist, dict):
            title = playlist.get("TITLE") or playlist.get("title")
            if isinstance(title, str):
                return title
        for key in ("TITLE", "title"):
            if isinstance(state.get(key), str):
                return state[key]
        values = state.values()
    elif isinstance(state, list):
        values = state
    else:
        return ""
    for value in values:
        title = _playlist_title(value)
        if title:
            return title
    return ""
