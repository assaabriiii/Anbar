"""The interface every ecosystem plugin implements.

A plugin moves through six steps:

``detect``     find the manifests it understands in a project
``plan``       turn manifests + config into a list of items to download
``fetch``      download planned items into the kit (incremental, verified)
``serve``      return services that expose the kit's content locally
``configure``  point the ecosystem's tools at those services (``anbar use``)
``restore``    undo anything ``configure`` did that is not a plain file edit

New ecosystems (apt, Go modules, Maven, ...) subclass :class:`Plugin` and are
added to :data:`anbar.plugins.PLUGINS`.
"""

from __future__ import annotations

import abc
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from anbar.config import Config
from anbar.kit import Kit
from anbar.network import Network

# Directories that never contain manifests we care about.
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__",
    ".tox", ".nox", ".mypy_cache", ".pytest_cache", "site-packages", "dist", "build",
    ".next", ".nuxt", "bower_components", ".idea", ".vscode",
}


def walk_project(root: Path, max_depth: int = 6) -> Iterator[Path]:
    """Yield files under ``root``, skipping vendored and VCS directories."""
    root = Path(root)
    base_depth = len(root.resolve().parts)
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).resolve().parts) - base_depth
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".anbar") and not _is_kit(Path(dirpath, d))
        )
        if depth >= max_depth:
            dirnames[:] = []
        for name in sorted(filenames):
            yield Path(dirpath, name)


def _is_kit(path: Path) -> bool:
    """A kit living inside the project must not be scanned as project files."""
    try:
        with (path / "manifest.json").open("rb") as fh:
            return b"format_version" in fh.read(256)
    except OSError:
        return False


@dataclass
class PlanItem:
    """Something a plugin intends to download."""

    name: str
    version: str | None = None
    source: str = ""  # manifest (relative path) or "anbar.toml"
    size: int | None = None  # estimated bytes, if known
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.name}=={self.version}" if self.version else self.name


@dataclass
class Plan:
    ecosystem: str
    manifests: list[Path] = field(default_factory=list)
    items: list[PlanItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def estimated_size(self) -> int:
        return sum(i.size or 0 for i in self.items)

    @property
    def unknown_sizes(self) -> int:
        return sum(1 for i in self.items if i.size is None)


@dataclass
class FetchResult:
    downloaded: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class Progress:
    """Minimal progress sink; the CLI plugs a Rich progress bar in here."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def start(self, description: str, total: int | None) -> None: ...

    def advance(self, amount: int = 1) -> None: ...

    def log(self, message: str) -> None: ...

    def finish(self) -> None: ...


@dataclass
class Context:
    """Everything a plugin needs while fetching."""

    project: Path
    config: Config
    net: Network
    progress: Progress = field(default_factory=Progress)
    refresh: bool = False
    online: bool = True
    run: Callable[..., Any] | None = None  # subprocess runner, replaceable in tests


class Service(abc.ABC):
    """A long-running local server started by ``anbar serve``."""

    name: str = "service"

    @property
    @abc.abstractmethod
    def url(self) -> str: ...

    @abc.abstractmethod
    def start(self) -> None: ...

    @abc.abstractmethod
    def stop(self) -> None: ...


@dataclass
class UseContext:
    """Where the local services live, for ``anbar use``."""

    kit: Kit | None
    config: Config
    host: str
    pypi_port: int
    npm_port: int
    files_port: int
    registry_port: int

    def base(self, port: int) -> str:
        host = self.host
        if host in ("0.0.0.0", "::", ""):
            host = "127.0.0.1"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{port}"


class Plugin(abc.ABC):
    name: str = ""
    title: str = ""

    @abc.abstractmethod
    def detect(self, project: Path, config: Config) -> list[Path]:
        """Return manifest files in ``project`` that this plugin understands."""

    @abc.abstractmethod
    def plan(self, project: Path, config: Config, manifests: list[Path]) -> Plan:
        """Parse manifests and config into a :class:`Plan` (no network access)."""

    def estimate(self, plan: Plan, ctx: Context) -> None:
        """Optionally fill in ``PlanItem.size`` using the network."""

    @abc.abstractmethod
    def fetch(self, plan: Plan, kit: Kit, ctx: Context) -> FetchResult:
        """Download everything in ``plan`` into ``kit``; skip what is already there."""

    def serve(self, kit: Kit, config: Config, host: str, ports: dict[str, int]) -> list[Service]:
        """Return services exposing this ecosystem's part of the kit."""
        return []

    def configure(self, ctx: UseContext, changes: "Changes") -> list[str]:
        """Point tools at the local services. Returns human-readable notes."""
        return []

    def restore(self, record: dict[str, Any]) -> list[str]:
        """Undo non-file side effects recorded by ``configure``. Returns notes."""
        return []

    def has_content(self, kit: Kit) -> bool:
        return any(True for _ in kit.by_ecosystem(self.name))


# Imported late to avoid a cycle: backup.Changes only needs the kit module.
from anbar.backup import Changes  # noqa: E402
