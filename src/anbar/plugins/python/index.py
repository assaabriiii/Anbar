"""A read-only PEP 503 "simple" index over the kit's Python packages."""

from __future__ import annotations

import html
import re
import tarfile
import zipfile
from pathlib import Path

from anbar.kit import Kit
from anbar.plugins.python.parsers import normalize
from anbar.server import Handler

SDIST_EXTS = (".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".zip")
DIST_EXTS = (".whl",) + SDIST_EXTS


def dist_name(filename: str) -> str | None:
    """Return the normalised project name of a wheel or sdist filename."""
    if filename.endswith(".whl"):
        return normalize(filename.split("-")[0])
    for ext in SDIST_EXTS:
        if filename.endswith(ext):
            stem = filename[: -len(ext)]
            match = re.match(r"^(.+?)-(\d[^-]*)$", stem)
            return normalize(match.group(1)) if match else None
    return None


def dist_version(filename: str) -> str | None:
    if filename.endswith(".whl"):
        parts = filename.split("-")
        return parts[1] if len(parts) >= 5 else None
    for ext in SDIST_EXTS:
        if filename.endswith(ext):
            match = re.match(r"^(.+?)-(\d[^-]*)$", filename[: -len(ext)])
            return match.group(2) if match else None
    return None


def read_requires_python(path: Path) -> str | None:
    """Extract ``Requires-Python`` from a wheel's METADATA or an sdist's PKG-INFO."""
    try:
        text = None
        if path.name.endswith(".whl"):
            with zipfile.ZipFile(path) as zf:
                for name in zf.namelist():
                    if name.count("/") == 1 and name.endswith(".dist-info/METADATA"):
                        text = zf.read(name).decode("utf-8", "replace")
                        break
        elif path.name.endswith(".zip"):
            with zipfile.ZipFile(path) as zf:
                for name in zf.namelist():
                    if name.count("/") == 1 and name.endswith("/PKG-INFO"):
                        text = zf.read(name).decode("utf-8", "replace")
                        break
        elif ".tar" in path.name or path.name.endswith(".tgz"):
            with tarfile.open(path) as tf:
                for member in tf:
                    if member.name.count("/") == 1 and member.name.endswith("/PKG-INFO"):
                        fh = tf.extractfile(member)
                        if fh:
                            text = fh.read().decode("utf-8", "replace")
                        break
        if not text:
            return None
        for line in text.split("\n\n", 1)[0].splitlines():
            if line.lower().startswith("requires-python:"):
                return line.split(":", 1)[1].strip() or None
    except (OSError, zipfile.BadZipFile, tarfile.TarError, EOFError):
        return None
    return None


class SimpleIndex:
    """Snapshot of the packages directory, grouped by project."""

    def __init__(self, kit: Kit, packages_dir: Path) -> None:
        self.kit = kit
        self.packages_dir = packages_dir
        self.projects: dict[str, list[tuple[str, str | None, str | None]]] = {}
        self.refresh()

    def refresh(self) -> None:
        projects: dict[str, list[tuple[str, str | None, str | None]]] = {}
        if self.packages_dir.is_dir():
            for path in sorted(self.packages_dir.iterdir()):
                if not path.is_file() or not path.name.endswith(DIST_EXTS):
                    continue
                name = dist_name(path.name)
                if not name:
                    continue
                art = self.kit.get(self.kit.rel(path))
                sha = art.sha256 if art else None
                requires = (art.meta.get("requires_python") if art else None) if art else None
                projects.setdefault(name, []).append((path.name, sha, requires))
        self.projects = projects


class SimpleIndexHandler(Handler):
    index: SimpleIndex

    def route(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", ""):
            self.send_response(302)
            self.send_header("Location", "/simple/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path in ("/simple", "/simple/"):
            links = "".join(
                f'<a href="/simple/{name}/">{html.escape(name)}</a><br>\n' for name in sorted(self.index.projects)
            )
            return self._html("Simple index", links)
        match = re.match(r"^/simple/([^/]+)/?$", path)
        if match:
            name = normalize(match.group(1))
            if name != match.group(1) or not path.endswith("/"):
                self.send_response(301)
                self.send_header("Location", f"/simple/{name}/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            files = self.index.projects.get(name)
            if not files:
                return self.not_found(f"{name} is not in this kit")
            links = []
            for filename, sha, requires in files:
                href = f"/packages/{filename}" + (f"#sha256={sha}" if sha else "")
                attr = f' data-requires-python="{html.escape(requires, quote=True)}"' if requires else ""
                links.append(f'<a href="{href}"{attr}>{html.escape(filename)}</a><br>\n')
            return self._html(f"Links for {name}", "".join(links))
        if path.startswith("/packages/"):
            filename = path[len("/packages/"):]
            if "/" in filename or filename.startswith("."):
                return self.not_found()
            return self.send_file(self.index.packages_dir / filename)
        self.not_found()

    def _html(self, title: str, body: str) -> None:
        page = (
            "<!DOCTYPE html>\n<html><head><meta name=\"pypi:repository-version\" content=\"1.0\">"
            f"<title>{html.escape(title)}</title></head><body>\n<h1>{html.escape(title)}</h1>\n{body}</body></html>\n"
        )
        self.send_text(page, content_type="text/html; charset=utf-8")
