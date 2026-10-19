"""Output of the commands: stdout, plus journald with --syslog."""
import unicodedata


class Log:
    """stdout, plus journald (tag music-librarian) with --syslog."""

    def __init__(self, use_syslog=False, ident="music-librarian"):
        self.syslog = None
        self.ident = ident
        if use_syslog:
            import syslog  # pylint: disable=import-outside-toplevel  # only with --syslog
            self.syslog = syslog

    def __call__(self, msg=""):
        # invisible control characters (e.g. U+0092) shown escaped
        msg = "".join(f"\\x{ord(c):02x}" if unicodedata.category(c) == "Cc" and c != "\n"
                      else c for c in msg)
        print(msg, flush=True)   # in order with the subcommands' output
        if self.syslog and msg.strip():
            line = " ".join(part.strip() for part in msg.splitlines() if part.strip())
            # reopened on every line: import and range each have their own tag
            self.syslog.openlog(self.ident)
            # a non-UTF-8 file name (e.g. \x92) must not crash the log
            self.syslog.syslog(line.encode("utf-8", "backslashreplace").decode())
