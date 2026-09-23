"""Ecosystem plugins. Add new ones to :data:`PLUGINS`."""

from __future__ import annotations

from anbar.errors import AnbarError
from anbar.plugins.base import Plugin

PLUGINS: dict[str, type[Plugin]] = {}


def register(cls: type[Plugin]) -> type[Plugin]:
    PLUGINS[cls.name] = cls
    return cls


def _load_builtin() -> None:
    # Imported for their @register side effect.
    import importlib

    for module in ("python", "node", "docker", "models", "docs"):
        try:
            importlib.import_module(f"anbar.plugins.{module}")
        except ModuleNotFoundError as exc:
            if exc.name != f"anbar.plugins.{module}":
                raise


def all_plugins() -> list[Plugin]:
    _load_builtin()
    return [cls() for cls in PLUGINS.values()]


def select_plugins(only: list[str] | None = None, skip: list[str] | None = None) -> list[Plugin]:
    plugins = all_plugins()
    names = {p.name for p in plugins}
    for name in (only or []) + (skip or []):
        if name not in names:
            raise AnbarError(f"unknown ecosystem '{name}'", f"choose from: {', '.join(sorted(names))}")
    if only:
        plugins = [p for p in plugins if p.name in only]
    if skip:
        plugins = [p for p in plugins if p.name not in skip]
    return plugins
