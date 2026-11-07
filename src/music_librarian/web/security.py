"""What the dashboard refuses before any page, a name it is not reached by and a form without
the page's token, and the headers of every response."""
import hmac
import ipaddress
import secrets

from flask import abort, request

from music_librarian.web.common import texts

LOCAL_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".internal")


def host_name(host):
    """The name or address of a Host header, its port and an IPv6 address's brackets aside."""
    if host.startswith("["):
        return host[1:].partition("]")[0]
    return host.rpartition(":")[0] if host.count(":") == 1 else host


def local_host(name):
    """Whether no public DNS can point `name` at the dashboard: an address, a name without a dot,
    or under a suffix kept for local networks. A DNS rebinding page is refused by its name."""
    name = name.lower().rstrip(".")
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return "." not in name or name.endswith(LOCAL_SUFFIXES)


def register(app, ctx):
    """Add the checks to the app, ahead of any other before_request; return the forms' token."""
    token = secrets.token_urlsafe(16)

    @app.before_request
    def known_host():
        name = host_name(request.host).lower().rstrip(".")
        if not local_host(name) and name not in ctx.hosts:
            return app.response_class(texts()["host_refused"].format(host=name), 403,
                                      mimetype="text/plain; charset=utf-8")
        return None

    @app.before_request
    def same_origin_forms():
        if request.method == "POST" and not hmac.compare_digest(request.form.get("csrf", ""), token):
            abort(403)

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        return response

    return token
