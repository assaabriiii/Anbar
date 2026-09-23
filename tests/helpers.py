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


def make_npm_tarball(directory: Path, name: str, version: str, dependencies: dict | None = None) -> Path:
    """Write an npm package tarball (package/ prefix, like `npm pack`)."""
    import io
    import json
    import tarfile

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name.split('/')[-1]}-{version}.tgz"
    files = {
        "package/package.json": json.dumps(
            {"name": name, "version": version, "main": "index.js", "dependencies": dependencies or {}}
        ),
        "package/index.js": f"module.exports = {json.dumps(name + '@' + version)};\n",
    }
    with tarfile.open(path, "w:gz") as tf:
        for fname, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(fname)
            info.size = len(data)
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(data))
    return path


def fake_npm_registry(tmp_path: Path, packages: list[tuple[str, str, dict]]) -> HTTPService:
    """Start an upstream npm registry serving the given (name, version, dependencies)."""
    import json

    from anbar.network import verify_integrity  # noqa: F401 - keeps helpers self-contained
    from anbar.plugins.node.registry import NpmRegistryHandler

    root = tmp_path / "upstream-npm"
    docs: dict[str, dict] = {}
    for name, version, deps in packages:
        tgz = make_npm_tarball(root / "tarballs" / name / "-", name, version, deps)
        integrity = "sha512-" + base64.b64encode(hashlib.sha512(tgz.read_bytes()).digest()).decode()
        doc = docs.setdefault(name, {"name": name, "dist-tags": {}, "versions": {}})
        doc["versions"][version] = {
            "name": name,
            "version": version,
            "dependencies": deps,
            "dist": {"tarball": f"https://registry.npmjs.org/{name}/-/{tgz.name}", "integrity": integrity},
        }
        doc["dist-tags"]["latest"] = version
    for name, doc in docs.items():
        path = root / "packuments" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc))
    service = HTTPService("upstream npm", make_handler(NpmRegistryHandler, root=root), "127.0.0.1", 0)
    service.start()
    return service
