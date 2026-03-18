import os
import socket

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from ncai_app.config import (
    UPLOAD_DIR,
    get_server_host,
    get_server_port,
    get_waitress_threads,
)
from ncai_app.routes import register_routes

try:
    from waitress import serve
except ImportError:
    serve = None


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.config["JSON_AS_ASCII"] = False
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024

os.makedirs(UPLOAD_DIR, exist_ok=True)
register_routes(app)


def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


def print_server_urls(host: str, port: int) -> None:
    local_ip = get_local_ip()

    print("=" * 60)
    print("NCAI server is starting")
    print(f"Local URL:   http://127.0.0.1:{port}")
    print(f"LAN URL:     http://{local_ip}:{port}")
    if host not in {"0.0.0.0", "::"}:
        print(f"Bind Host:   {host}")
    else:
        print("Bind Host:   0.0.0.0 (all network interfaces)")
    print("=" * 60)


def run_server() -> None:
    host = get_server_host()
    port = get_server_port()

    print_server_urls(host, port)

    if serve is not None:
        serve(app, host=host, port=port, threads=get_waitress_threads())
        return

    print("waitress is not installed. Falling back to Flask development server.")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    run_server()
