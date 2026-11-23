# pylint: disable=redefined-outer-name  # pytest fixtures
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from music_librarian.deezer import client as client_module
from music_librarian.deezer.client import Client
from music_librarian.deezer.config import Config

FIXTURES = Path(__file__).parent / "fixtures"
# Dummy, deliberately non-random values: the real ones are never needed by the tests.
PRE_KEY = b"AAAAAAAAAAAAAAAA"
IV = bytes.fromhex("0001020304050607")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """No test of the Deezer download opens a connection: Deezer is always simulated."""
    real = socket.socket.connect

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError(f"a test tried to reach the network: {address}")
        return real(sock, address)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(client_module, "_last", [float("-inf")])


@pytest.fixture
def cfg():
    return Config(arl="dummy-arl", pre_key=PRE_KEY, iv=IV)


class Response:
    """What requests answers, as far as the Deezer modules read it."""

    def __init__(self, status=200, body=b"", pieces=None, error=None):
        self.status_code = status
        self.content = body if isinstance(body, bytes) else body.encode()
        self.pieces, self.error, self.closed = pieces, error, False

    @property
    def text(self):
        return self.content.decode()

    def json(self):
        return json.loads(self.content)

    def iter_content(self, _size):
        yield from self.pieces if self.pieces is not None else [self.content]
        if self.error:
            raise self.error

    def close(self):
        self.closed = True


def answer(value, status=200):
    """A JSON response."""
    return Response(status, json.dumps(value))


def page(state):
    """A Deezer page embedding `state`, as its pages do."""
    return Response(200, "<html><script>window.__DZR_APP_STATE__ = " + json.dumps(state)
                    + "</script></html>")


class Session:
    """Answers each URL (without its query) with the responses queued for it, the last one repeated;
    a queued exception is raised instead, a function is called with the request's arguments.
    `sent` keeps every request."""

    def __init__(self, routes=None):
        self.routes = {url: list(r) if isinstance(r, list) else [r] for url, r in (routes or {}).items()}
        self.sent = []

    def request(self, method, url, **kwargs):
        self.sent.append(dict(method=method, url=url, **kwargs))
        queue = self.routes.get(url)
        if not queue:
            raise AssertionError(f"unexpected request: {method} {url}")
        found = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(found, BaseException):
            raise found
        return found(**kwargs) if callable(found) else found


@pytest.fixture
def deezer():
    """deezer(routes) -> a Client on a fake session, whose clock does not wait."""
    def make(routes=None):
        session = Session(routes)
        return Client("dummy-arl", session=session, clock=lambda: 0.0, sleep=lambda _s: None)
    return make


@pytest.fixture
def http():
    """The fake responses: http.Response, http.answer(json), http.page(state)."""
    return SimpleNamespace(Response=Response, answer=answer, page=page, fixture=fixture)


def fixture(name):
    """A saved page of tests/deezer/fixtures, as a response."""
    return Response(200, (FIXTURES / name).read_bytes())
