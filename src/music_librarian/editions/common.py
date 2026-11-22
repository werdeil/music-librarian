"""What the edition providers share: how they fail."""


class ProviderUnavailable(Exception):
    """A provider could not be reached, as opposed to having no edition to give."""
