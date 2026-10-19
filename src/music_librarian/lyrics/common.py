"""What the lyrics providers share: who they say they are, and how they fail."""

USER_AGENT = "music-librarian (https://github.com/werdeil/music-librarian)"
# A browser-like UA avoids being blocked by the Musixmatch desktop endpoint or
# NetEase.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


class ProviderUnavailable(Exception):
    """A provider could not be reached, as opposed to having no match to give."""
