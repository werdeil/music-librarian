"""The dashboard's HTTP server, run in a thread of watch."""
import threading

from waitress import create_server

from music_librarian.web.app import Context, create_app


def serve(ctx: Context, listen, log):
    """Start serving the dashboard on `listen` (host, port) in a daemon thread; return the
    server, for its close()."""
    host, port = listen
    server = create_server(create_app(ctx), host=host, port=port, threads=4)
    threading.Thread(target=server.run, name="web", daemon=True).start()
    log(f"WEB      dashboard on http://{host}:{server.effective_port}")
    return server
