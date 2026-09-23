"""The kit directory and its ``manifest.json``.

Layout::

    KIT_DIR/
      manifest.json
      python/  node/  docker/  models/  docs/

The manifest records every artifact with its source, sha256, size and
download date. It is versioned: ``FORMAT_VERSION`` is bumped whenever the
layout changes, and :func:`_migrate` upgrades older manifests in memory so
new releases can always read old kits.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from anbar import __version__
from anbar.errors import KitError

FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


@dataclass
class Artifact:
    """One file stored in the kit."""

    path: str  # relative to the kit root, always with forward slashes
    ecosystem: str
    source: str
    sha256: str
    size: int
    downloaded_at: str = field(default_factory=utcnow)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if not data["meta"]:
            del data["meta"]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Artifact":
        return cls(
            path=data["path"],
            ecosystem=data["ecosystem"],
            source=data.get("source", ""),
            sha256=data["sha256"],
            size=int(data["size"]),
            downloaded_at=data.get("downloaded_at", ""),
            meta=dict(data.get("meta") or {}),
        )


def _migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Upgrade a manifest written by an older release to ``FORMAT_VERSION``."""
    version = data.get("format_version")
    if version is None:
        raise KitError("manifest.json has no format_version; this does not look like an Anbar kit")
    if not isinstance(version, int) or version > FORMAT_VERSION:
        raise KitError(
            f"this kit uses format version {version}, but this Anbar only understands up to {FORMAT_VERSION}",
            "upgrade Anbar (the offline bundle in the release page works without internet)",
        )
    # Future migrations go here, e.g. ``if version == 1: ...; version = 2``.
    data["format_version"] = FORMAT_VERSION
    return data


class Kit:
    """A kit directory plus its manifest. Thread-safe for concurrent ``add``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self._lock = threading.Lock()
        self.created_at = utcnow()
        self.updated_at = self.created_at
        self.anbar_version = __version__
        self.projects: list[str] = []
        self.artifacts: dict[str, Artifact] = {}

    # -- construction -----------------------------------------------------

    @classmethod
    def open(cls, root: Path, create: bool = False) -> "Kit":
        kit = cls(root)
        manifest = kit.root / MANIFEST_NAME
        if manifest.exists():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise KitError(f"cannot read {manifest}: {exc}", "run `anbar verify` or re-create the kit") from exc
            data = _migrate(data)
            kit.created_at = data.get("created_at", kit.created_at)
            kit.updated_at = data.get("updated_at", kit.updated_at)
            kit.anbar_version = data.get("anbar_version", "unknown")
            kit.projects = list(data.get("projects", []))
            for item in data.get("artifacts", []):
                art = Artifact.from_dict(item)
                kit.artifacts[art.path] = art
        elif create:
            kit.root.mkdir(parents=True, exist_ok=True)
            kit.save()
        else:
            if not kit.root.exists():
                raise KitError(f"kit directory not found: {kit.root}", "create one with `anbar pack --out DIR`")
            raise KitError(f"{kit.root} is not an Anbar kit (no {MANIFEST_NAME})")
        return kit

    # -- paths ------------------------------------------------------------

    def dir(self, *parts: str) -> Path:
        path = self.root.joinpath(*parts)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def rel(self, path: Path) -> str:
        return Path(path).resolve().relative_to(self.root).as_posix()

    def abspath(self, rel: str) -> Path:
        return self.root / Path(*rel.split("/"))

    # -- artifacts ----------------------------------------------------------

    def add(
        self,
        path: Path,
        ecosystem: str,
        source: str,
        sha256: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Artifact:
        path = Path(path)
        art = Artifact(
            path=self.rel(path),
            ecosystem=ecosystem,
            source=source,
            sha256=sha256 or sha256_file(path),
            size=path.stat().st_size,
            meta=meta or {},
        )
        with self._lock:
            self.artifacts[art.path] = art
        return art

    def get(self, rel: str) -> Artifact | None:
        return self.artifacts.get(rel)

    def has_valid(self, path: Path, sha256: str | None = None) -> bool:
        """True if ``path`` is recorded in the manifest and still matches on disk.

        Only the size is compared here (cheap); ``anbar verify`` does full hashing.
        """
        art = self.artifacts.get(self.rel(path))
        if art is None or not path.exists():
            return False
        if sha256 and art.sha256 != sha256:
            return False
        return path.stat().st_size == art.size

    def remove(self, rel: str) -> None:
        with self._lock:
            self.artifacts.pop(rel, None)

    def by_ecosystem(self, ecosystem: str) -> Iterator[Artifact]:
        return (a for a in self.artifacts.values() if a.ecosystem == ecosystem)

    def add_project(self, project: Path) -> None:
        name = Path(project).resolve().name
        if name not in self.projects:
            self.projects.append(name)

    # -- persistence --------------------------------------------------------

    def save(self) -> None:
        with self._lock:
            self.updated_at = utcnow()
            data = {
                "format_version": FORMAT_VERSION,
                "anbar_version": __version__,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "projects": self.projects,
                "artifacts": [a.to_dict() for a in sorted(self.artifacts.values(), key=lambda a: a.path)],
            }
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self.root / (MANIFEST_NAME + ".tmp")
            tmp.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
            os.replace(tmp, self.root / MANIFEST_NAME)
