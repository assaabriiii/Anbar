"""Small threaded HTTP servers built on the standard library."""

from __future__ import annotations

import html
import mimetypes
import os
import posixpath
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from anbar import __version__
from anbar.plugins.base import Service


class Handler(BaseHTTPRequestHandler):
    """Base handler with helpers; subclasses implement ``route``."""

    server_version = f"anbar/{__version__}"
    protocol_version = "HTTP/1.1"
    quiet = True

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        if not self.quiet:
            super().log_message(format, *args)

    # -- helpers ------------------------------------------------------------

    @property
    def base_url(self) -> str:
        host = self.headers.get("Host") or "{}:{}".format(*self.server.server_address[:2])
        return f"http://{host}"

    def send_bytes(self, body: bytes, content_type: str, status: int = 200, extra: dict[str, str] | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_text(self, text: str, status: int = 200, content_type: str = "text/plain; charset=utf-8"):
        self.send_bytes(text.encode("utf-8"), content_type, status)

    def not_found(self, what: str = "not found") -> None:
        self.send_text(f"{what}\n", HTTPStatus.NOT_FOUND)

    def send_file(self, path: Path, content_type: str | None = None) -> None:
        if not path.is_file():
            return self.not_found()
        size = path.stat().st_size
        ctype = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        status = 200
        if rng and rng.startswith("bytes=") and size:
            first, _, last = rng[6:].split(",")[0].partition("-")
            try:
                if first:
                    start = int(first)
                    end = int(last) if last else size - 1
                else:
                    start = max(0, size - int(last))
                end = min(end, size - 1)
                if start > end:
                    raise ValueError
                status = 206
            except ValueError:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        length = end - start + 1 if size else 0
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD" or not length:
            return
        with path.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                block = fh.read(min(1 << 16, remaining))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)

    def send_directory_listing(self, directory: Path, url_path: str) -> None:
        entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        rows = []
        if url_path.rstrip("/"):
            rows.append('<li><a href="../">../</a></li>')
        for entry in entries:
            if entry.name.startswith("."):
                continue
            name = entry.name + ("/" if entry.is_dir() else "")
            rows.append(f'<li><a href="{urllib.parse.quote(name)}">{html.escape(name)}</a></li>')
        title = html.escape(url_path or "/")
        body = (
            f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{title}</title></head>"
            f"<body><h1>{title}</h1><ul>{''.join(rows)}</ul></body></html>"
        )
        self.send_text(body, content_type="text/html; charset=utf-8")

    def serve_static(self, root: Path, rel: str) -> None:
        """Serve ``rel`` below ``root`` safely (no path traversal)."""
        rel = posixpath.normpath(urllib.parse.unquote(rel)).lstrip("/")
        if rel in (".", ""):
            rel = ""
        if rel.startswith("..") or "\\" in rel or "\x00" in rel:
            return self.not_found()
        target = (root / rel).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            return self.not_found()
        if target.is_dir():
            if not self.path.split("?")[0].endswith("/"):
                self.send_response(HTTPStatus.MOVED_PERMANENTLY)
                self.send_header("Location", self.path.split("?")[0] + "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            index = target / "index.html"
            if index.is_file():
                return self.send_file(index)
            return self.send_directory_listing(target, "/" + rel)
        return self.send_file(target)

    # -- dispatch -----------------------------------------------------------

    def route(self) -> None:
        self.not_found()

    def do_GET(self) -> None:  # noqa: N802
        try:
            self.route()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()


class HTTPService(Service):
    """Runs a :class:`Handler` subclass in a background thread."""

    def __init__(self, name: str, handler: type[Handler], host: str, port: int, path: str = "") -> None:
        self.name = name
        self.path = path
        self.handler = handler
        self.host = host
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host = self.host
        if host in ("0.0.0.0", ""):
            host = "127.0.0.1"
        if ":" in host:
            host = f"[{host}]"
        return f"http://{host}:{self.port}{self.path}"

    def start(self) -> None:
        server_cls = ThreadingHTTPServer
        if ":" in self.host:
            import socket

            class V6(ThreadingHTTPServer):
                address_family = socket.AF_INET6

            server_cls = V6
        try:
            self._server = server_cls((self.host, self.port), self.handler)
        except OSError as exc:
            from anbar.errors import AnbarError

            raise AnbarError(
                f"cannot start {self.name} on {self.host}:{self.port}: {exc.strerror or exc}",
                "pick another port with the --*-port options or [serve] in anbar.toml",
            ) from exc
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, name=self.name, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


def make_handler(base: type[Handler], **attrs) -> type[Handler]:
    """Create a handler subclass bound to per-service attributes (kit paths etc.)."""
    attrs.setdefault("quiet", os.environ.get("ANBAR_LOG_REQUESTS", "") == "")
    return type(base.__name__, (base,), attrs)
