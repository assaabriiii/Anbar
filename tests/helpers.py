"""Helpers for hermetic tests: fake wheels and a fake upstream PyPI."""

from __future__ import annotations

import base64
import hashlib
import zipfile
from pathlib import Path

from anbar.kit import Kit
from anbar.plugins.python.index import SimpleIndex, SimpleIndexHandler
from anbar.server import HTTPService, make_handler


def make_wheel(directory: Path, name: str, version: str, requires: list[str] | None = None) -> Path:
    """Write a minimal, installable pure-Python wheel."""
    dist = f"{name.replace('-', '_')}-{version}"
    path = directory / f"{dist}-py3-none-any.whl"
    files = {
        f"{name.replace('-', '_')}/__init__.py": f"__version__ = '{version}'\n",
        f"{dist}.dist-info/METADATA": "Metadata-Version: 2.1\n"
        f"Name: {name}\nVersion: {version}\nRequires-Python: >=3.8\n"
        + "".join(f"Requires-Dist: {r}\n" for r in (requires or [])),
        f"{dist}.dist-info/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record_lines = []
    for fname, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content.encode()).digest()).rstrip(b"=").decode()
        record_lines.append(f"{fname},sha256={digest},{len(content.encode())}")
    record_lines.append(f"{dist}.dist-info/RECORD,,")
    files[f"{dist}.dist-info/RECORD"] = "\n".join(record_lines) + "\n"
    with zipfile.ZipFile(path, "w") as zf:
        for fname, content in files.items():
            zf.writestr(fname, content)
    return path


def fake_pypi(tmp_path: Path, wheels: list[tuple[str, str, list[str]]]) -> HTTPService:
    """Start a PEP 503 index serving the given (name, version, requires) wheels."""
    root = tmp_path / "upstream"
    kit = Kit.open(root, create=True)
    packages = kit.dir("python", "packages")
    for name, version, requires in wheels:
        make_wheel(packages, name, version, requires)
    handler = make_handler(SimpleIndexHandler, index=SimpleIndex(kit, packages))
    service = HTTPService("upstream", handler, "127.0.0.1", 0)
    service.start()
    return service
