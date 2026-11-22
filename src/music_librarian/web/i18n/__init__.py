"""The dashboard's strings, in French and English with the same keys; the language is picked
per request from Accept-Language, English by default."""

from music_librarian.web.i18n.en import EN
from music_librarian.web.i18n.fr import FR

SUPPORTED = ("fr", "en")
DEFAULT_LANG = "en"

TRANSLATIONS = {"fr": FR, "en": EN}


def pick_lang(accept_languages):
    """Best supported language from a Werkzeug Accept-Language."""
    return accept_languages.best_match(SUPPORTED) or DEFAULT_LANG
