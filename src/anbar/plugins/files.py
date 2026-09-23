"""Helpers shared by the models and docs plugins: URL files, archives and one static file server."""

from __future__ import annotations

import posixpath
import shutil
import tarfile
import urllib.parse
import zipfile
from pathlib import Path, PurePosixPath

from anbar.errors import AnbarError
from anbar.kit import Kit
from anbar.server import Handler, HTTPService, make_handler

ARCHIVE_EXTS = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")


def filename_from_url(url: str) -> str:
    name = posixpath.basename(urllib.parse.urlsplit(url).path)
    return urllib.parse.unquote(name) or "download"


def is_archive(name: str) -> bool:
    return name.lower().endswith(ARCHIVE_EXTS)


def _safe_members(names: list[str]) -> str | None:
    """Validate archive member names; return a single common top-level dir if there is one."""
    tops = set()
    for name in names:
        path = PurePosixPath(name.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or (path.parts and ":" in path.parts[0]):
            raise AnbarError(f"refusing to extract unsafe archive member: {name}")
        if path.parts:
            tops.add(path.parts[0])
    if len(tops) == 1:
        top = next(iter(tops))
        if any(len(PurePosixPath(n.replace("\\", "/")).parts) > 1 for n in names):
            return top
    return None


def extract_archive(archive: Path, dest: Path) -> None:
    """Extract ``archive`` into ``dest``, stripping a single top-level directory."""
    tmp = dest.with_name(dest.name + ".extracting")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    name = archive.name.lower()
    try:
        if name.endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                top = _safe_members(zf.namelist())
                zf.extractall(tmp)
        else:
            with tarfile.open(archive) as tf:
                members = [m for m in tf.getmembers() if m.isfile() or m.isdir()]
                top = _safe_members([m.name for m in members])
                for member in members:
                    member.mode = 0o755 if member.isdir() else 0o644
                if hasattr(tarfile, "data_filter"):
                    tf.extractall(tmp, members=members, filter="data")
                else:  # Python < 3.12: members were validated above
                    tf.extractall(tmp, members=members)  # noqa: S202
    except (zipfile.BadZipFile, tarfile.TarError, OSError) as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        raise AnbarError(f"cannot extract {archive.name}: {exc}") from exc
    source = tmp / top if top else tmp
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(source), str(dest))
    shutil.rmtree(tmp, ignore_errors=True)


class FilesHandler(Handler):
    mounts: dict[str, Path]

    def route(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", ""):
            rows = "".join(f'<li><a href="/{name}/">{name}/</a></li>' for name in sorted(self.mounts))
            page = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Anbar kit</title></head>" \
                   f"<body><h1>Anbar kit</h1><ul>{rows}</ul></body></html>"
            return self.send_text(page, content_type="text/html; charset=utf-8")
        mount, _, rest = path.lstrip("/").partition("/")
        root = self.mounts.get(mount)
        if root is None:
            return self.not_found()
        if not rest and not path.endswith("/"):
            self.send_response(301)
            self.send_header("Location", f"/{mount}/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.serve_static(root, rest)


_SHARED: dict[tuple[str, str, int], HTTPService] = {}


def shared_file_server(kit: Kit, host: str, port: int, mount: str, directory: Path) -> HTTPService:
    """One file server per kit and port; plugins add their own mount point to it."""
    key = (str(kit.root), host, port)
    service = _SHARED.get(key)
    if service is None:
        handler = make_handler(FilesHandler, mounts={})
        service = HTTPService("File server (models, docs)", handler, host, port, "/")
        _SHARED[key] = service
    service.handler.mounts[mount] = directory  # type: ignore[attr-defined]
    return service
