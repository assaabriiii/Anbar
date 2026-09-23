"""Shared Rich console and small output helpers."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

console = Console()
err_console = Console(stderr=True)


def warn(message: str) -> None:
    err_console.print(f"[yellow]warning:[/yellow] {escape(message)}")


def info(message: str) -> None:
    console.print(message)


def human_size(num: float | int | None) -> str:
    if num is None:
        return "?"
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"  # pragma: no cover
