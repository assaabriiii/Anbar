"""A minimal, read-only npm registry over the kit's tarballs and packuments."""

from __future__ import annotations

import copy
import json
import re
import urllib.parse
from pathlib import Path

from anbar.server import Handler


def packument_path(root: Path, name: str) -> Path:
    return root / "packuments" / (name + ".json")


def tarball_path(root: Path, name: str, filename: str) -> Path:
    return root / "tarballs" / name / "-" / filename


def rewrite_packument(data: dict, base_url: str) -> dict:
    """Point every ``dist.tarball`` at this server."""
    data = copy.deepcopy(data)
    for version in (data.get("versions") or {}).values():
        dist = version.get("dist") or {}
        tarball = dist.get("tarball")
        name = version.get("name") or data.get("name")
        if tarball and name:
            filename = tarball.rsplit("/", 1)[-1].split("?")[0]
            dist["tarball"] = f"{base_url}/{name}/-/{filename}"
    return data


class NpmRegistryHandler(Handler):
    root: Path  # kit/node

    def _json(self, data, status: int = 200) -> None:
        body = json.dumps(data, separators=(",", ":")).encode("utf-8")
        self.send_bytes(body, "application/json", status)

    def _missing(self, name: str) -> None:
        self._json({"error": f"{name} is not in this Anbar kit"}, 404)

    def route(self) -> None:
        path = urllib.parse.unquote(self.path.split("?", 1)[0])
        if path in ("/-/ping", "/-/ping/"):
            return self._json({})
        if path.startswith("/-/"):
            return self._json({"error": "not supported by the offline registry"}, 404)
        path = path.lstrip("/")
        if not path or ".." in path.split("/") or "\\" in path:
            return self._json({"error": "not found"}, 404)

        # Tarball: name/-/file.tgz or @scope/name/-/file.tgz
        match = re.match(r"^((?:@[^/]+/)?[^/@]+)/-/([^/]+\.tgz)$", path)
        if match:
            return self.send_file(tarball_path(self.root, match.group(1), match.group(2)), "application/octet-stream")

        # Packument: name or @scope/name, optionally followed by /version-or-tag
        match = re.match(r"^((?:@[^/]+/)?[^/@]+)(?:/([^/]+))?/?$", path)
        if not match:
            return self._json({"error": "not found"}, 404)
        name, version = match.group(1), match.group(2)
        doc_path = packument_path(self.root, name)
        if not doc_path.is_file():
            return self._missing(name)
        data = rewrite_packument(json.loads(doc_path.read_text(encoding="utf-8")), self.base_url)
        if version is None:
            accept = self.headers.get("Accept", "")
            ctype = "application/vnd.npm.install-v1+json" if "install-v1" in accept else "application/json"
            body = json.dumps(data, separators=(",", ":")).encode("utf-8")
            return self.send_bytes(body, ctype)
        version = (data.get("dist-tags") or {}).get(version, version)
        manifest = (data.get("versions") or {}).get(version)
        if manifest is None:
            return self._missing(f"{name}@{version}")
        return self._json(manifest)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        path = self.path.split("?", 1)[0]
        if path.startswith("/-/npm/v1/security/"):
            # `npm install` runs an audit; answer "no advisories" instead of failing offline.
            return self._json({})
        self._json({"error": "the offline registry is read-only"}, 405)

    def do_PUT(self) -> None:  # noqa: N802
        self.do_POST()
