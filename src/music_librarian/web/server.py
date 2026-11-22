"""The dashboard's HTTP server, run in a thread of watch."""
import threading

from waitress import create_server

from music_librarian.web.app import Context, create_app
from music_librarian.web.uploads import MAX_FILE


def serve(ctx: Context, listen, log):
    """Start serving the dashboard on `listen` (host, port) in a daemon thread; return the
    server, for its close()."""
    host, port = listen
    server = create_server(create_app(ctx, build_now=True), host=host, port=port, threads=4,
                           max_request_body_size=MAX_FILE)
    threading.Thread(target=server.run, name="web", daemon=True).start()
    log(f"WEB      dashboard on http://{host}:{server.effective_port}")
    return server
