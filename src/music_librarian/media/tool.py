"""ffmpeg and ffprobe, run with a time limit."""
import subprocess

PROBE_SECONDS = 60
FILE_SECONDS = 30 * 60
TIMED_OUT = -9


class TimedOut(subprocess.CalledProcessError):
    """The tool was killed once past its time limit."""

    def __init__(self, cmd, timeout):
        super().__init__(TIMED_OUT, cmd, "", f"{cmd[0]} timed out after {timeout:g} s")
        self.timeout = timeout

    def __str__(self):
        return self.stderr


def run(cmd, timeout, check=False, **kwargs):
    """subprocess.run(cmd, capture_output=True, text=True), the tool killed past `timeout`
    seconds: then a failed run (exit code TIMED_OUT, the reason in stderr), or TimedOut with `check`."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=check, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired:
        if check:
            raise TimedOut(cmd, timeout) from None
        return subprocess.CompletedProcess(cmd, TIMED_OUT, "", TimedOut(cmd, timeout).stderr)
