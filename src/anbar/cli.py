"""Command-line interface."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

import typer
from rich.table import Table

from anbar import __version__
from anbar.config import Config, load_config
from anbar.console import console, err_console, human_size, warn
from anbar.errors import AnbarError
from anbar.network import Network
from anbar.plugins import select_plugins
from anbar.plugins.base import Context, Plan, Plugin

app = typer.Typer(
    name="anbar",
    help="Build offline development kits: download every dependency now, install offline later.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)

ConfigOpt = typer.Option(None, "--config", "-c", help="Path to anbar.toml (default: PROJECT/anbar.toml).")
OnlyOpt = typer.Option(None, "--only", help="Only handle these ecosystems (repeatable).")
SkipOpt = typer.Option(None, "--skip", help="Skip these ecosystems (repeatable).")
MirrorOpt = typer.Option(
    None,
    "--mirror",
    "-m",
    help="Upstream mirror as ECOSYSTEM=URL (pypi, npm, docker, huggingface); tried before [mirrors] entries. Repeatable.",
)

# Rough per-item sizes used when the real size is unknown (e.g. with --offline).
TYPICAL_SIZE = {"python": 1_500_000, "node": 80_000, "docker": 150_000_000, "models": None, "docs": 15_000_000}


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"anbar {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(False, "--version", callback=_version_callback, is_eager=True, help="Show version."),
) -> None:
    """Anbar (انبار, "storehouse") keeps your projects buildable through internet shutdowns."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _load(project: Path, config_path: Optional[Path]) -> Config:
    project = project.resolve()
    if not project.is_dir():
        raise AnbarError(f"project directory not found: {project}")
    return load_config(project, config_path)


def _apply_mirrors(config: Config, mirrors: Optional[List[str]]) -> None:
    for spec in reversed(mirrors or []):
        eco, sep, url = spec.partition("=")
        eco = eco.strip().lower()
        if not sep or not url:
            raise AnbarError(f"invalid --mirror '{spec}'", "use ECOSYSTEM=URL, e.g. --mirror pypi=https://mirror.example/simple")
        target = getattr(config.mirrors, eco, None)
        if target is None:
            raise AnbarError(f"unknown mirror ecosystem '{eco}'", "use one of: pypi, npm, docker, huggingface")
        target.insert(0, url.strip())


def _plugins(config: Config, only: Optional[List[str]], skip: Optional[List[str]]) -> list[Plugin]:
    return select_plugins(only or config.ecosystems, skip)


def _make_plans(project: Path, config: Config, plugins: list[Plugin]) -> list[tuple[Plugin, Plan]]:
    plans = []
    for plugin in plugins:
        manifests = plugin.detect(project, config)
        plan = plugin.plan(project, config, manifests)
        if plan.items or plan.manifests or plan.warnings:
            plans.append((plugin, plan))
    return plans


def _rel(path: Path, project: Path) -> str:
    try:
        return path.resolve().relative_to(project.resolve()).as_posix()
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


@app.command()
def scan(
    path: Path = typer.Argument(Path("."), help="Project directory."),
    config_path: Optional[Path] = ConfigOpt,
    only: Optional[List[str]] = OnlyOpt,
    skip: Optional[List[str]] = SkipOpt,
    offline: bool = typer.Option(False, "--offline", help="Do not query registries for exact sizes."),
    show_all: bool = typer.Option(False, "--all", "-a", help="List every item instead of the first 25 per ecosystem."),
) -> None:
    """Detect manifests and show what `pack` would download, with an estimated size."""
    project = path.resolve()
    config = _load(project, config_path)
    plans = _make_plans(project, config, _plugins(config, only, skip))
    if not plans:
        console.print("[yellow]Nothing found.[/yellow] No supported manifests and nothing listed in anbar.toml.")
        console.print("Supported: requirements*.txt, pyproject.toml, poetry.lock, Pipfile.lock, uv.lock, "
                      "package-lock.json, yarn.lock, pnpm-lock.yaml, Dockerfile, compose files, anbar.toml.")
        return

    ctx = Context(project=project, config=config, net=Network(config.network, config.proxy), online=not offline)
    if not offline:
        with console.status("Estimating download sizes..."):
            for plugin, plan in plans:
                try:
                    plugin.estimate(plan, ctx)
                except Exception as exc:  # noqa: BLE001 - estimates are best effort
                    warn(f"{plugin.title}: could not estimate sizes ({exc}); using typical sizes")

    summary = Table(title="Summary", show_lines=False)
    summary.add_column("Ecosystem")
    summary.add_column("Manifests", justify="right")
    summary.add_column("Items", justify="right")
    summary.add_column("Estimated size", justify="right")
    grand_total = 0
    approximate = False

    for plugin, plan in plans:
        console.rule(f"[bold]{plugin.title}")
        for manifest in plan.manifests:
            console.print(f"  [dim]manifest[/dim] {_rel(manifest, project)}")
        for message in plan.warnings:
            warn(message)
        table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
        table.add_column("Item")
        table.add_column("Version")
        table.add_column("From", style="dim")
        table.add_column("Size", justify="right")
        shown = plan.items if show_all else plan.items[:25]
        for item in shown:
            size = human_size(item.size) if item.size is not None else "[dim]?[/dim]"
            table.add_row(item.name, item.version or "", item.source, size)
        if plan.items:
            console.print(table)
        if len(shown) < len(plan.items):
            console.print(f"  [dim]... and {len(plan.items) - len(shown)} more (use --all)[/dim]")

        total = plan.estimated_size
        typical = TYPICAL_SIZE.get(plugin.name)
        if plan.unknown_sizes:
            approximate = True
            if typical:
                total += plan.unknown_sizes * typical
        grand_total += total
        label = ("≈ " if plan.unknown_sizes else "") + human_size(total)
        if plan.unknown_sizes and not typical:
            label += f" + {plan.unknown_sizes} unknown"
        summary.add_row(plugin.title, str(len(plan.manifests)), str(len(plan.items)), label)

    console.print()
    console.print(summary)
    prefix = "≈ " if approximate else ""
    console.print(f"[bold]Total estimated download: {prefix}{human_size(grand_total)}[/bold]")
    if any(p.name == "python" for p, _ in plans):
        console.print("[dim]Python sizes cover direct requirements; transitive dependencies are resolved during pack.[/dim]")


def main() -> None:
    try:
        app()
    except AnbarError as exc:
        err_console.print(f"[red]error:[/red] {exc.message}")
        if exc.hint:
            err_console.print(f"[cyan]hint:[/cyan] {exc.hint}")
        sys.exit(1)
    except KeyboardInterrupt:
        err_console.print("[yellow]interrupted[/yellow] (partial downloads are kept and will resume)")
        sys.exit(130)
