"""Command-line interface."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import List, Optional

import typer
from rich.progress import BarColumn, MofNCompleteColumn, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.progress import Progress as RProgress
from rich.table import Table

from anbar import __version__
from anbar.config import Config, load_config
from anbar.console import console, err_console, human_size, warn
from anbar.errors import AnbarError
from anbar.backup import Changes, load_state, restore_files
from anbar.kit import Kit
from anbar.network import Network
from anbar.plugins import all_plugins, select_plugins
from anbar.plugins.base import Context, Plan, Plugin, Progress, UseContext

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


# ---------------------------------------------------------------------------
# pack
# ---------------------------------------------------------------------------


class RichProgress(Progress):
    """Progress bars for pack."""

    def __init__(self) -> None:
        super().__init__()
        self._bar = RProgress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=False,
        )
        self._task: Optional[int] = None

    def start(self, description: str, total: Optional[int]) -> None:
        with self._lock:
            if self._task is not None:
                self._bar.update(self._task, visible=True)
            self._task = self._bar.add_task(description, total=total)
            self._bar.start()

    def advance(self, amount: int = 1) -> None:
        with self._lock:
            if self._task is not None:
                self._bar.advance(self._task, amount)

    def log(self, message: str) -> None:
        self._bar.console.print(message)

    def finish(self) -> None:
        with self._lock:
            if self._task is not None:
                task = self._bar.tasks[-1]
                if task.total is not None:
                    self._bar.update(self._task, completed=task.total)
            self._bar.stop()
            self._task = None


@app.command()
def pack(
    path: Path = typer.Argument(Path("."), help="Project directory."),
    out: Path = typer.Option(Path("anbar-kit"), "--out", "-o", help="Kit directory to create or update."),
    config_path: Optional[Path] = ConfigOpt,
    only: Optional[List[str]] = OnlyOpt,
    skip: Optional[List[str]] = SkipOpt,
    mirror: Optional[List[str]] = MirrorOpt,
    refresh: bool = typer.Option(False, "--refresh", help="Re-check everything upstream instead of skipping what the kit has."),
    workers: Optional[int] = typer.Option(None, "--workers", "-j", help="Parallel downloads (default 8)."),
) -> None:
    """Download everything the project needs into KIT_DIR (incremental and resumable)."""
    project = path.resolve()
    config = _load(project, config_path)
    _apply_mirrors(config, mirror)
    if workers:
        config.network.workers = workers
    plans = _make_plans(project, config, _plugins(config, only, skip))
    if not plans:
        raise AnbarError("nothing to pack: no supported manifests and nothing listed in anbar.toml",
                         "run `anbar scan` to see what Anbar detects")
    kit = Kit.open(out, create=True)
    kit.add_project(project)
    net = Network(config.network, config.proxy)
    failures: list[str] = []
    summary = Table(title=f"Kit: {kit.root}")
    summary.add_column("Ecosystem")
    summary.add_column("New files", justify="right")
    summary.add_column("Skipped (up to date)", justify="right")
    summary.add_column("Failed", justify="right")
    try:
        for plugin, plan in plans:
            console.rule(f"[bold]{plugin.title}")
            for message in plan.warnings:
                warn(message)
            ctx = Context(project=project, config=config, net=net, progress=RichProgress(), refresh=refresh)
            try:
                result = plugin.fetch(plan, kit, ctx)
            except AnbarError as exc:
                ctx.progress.finish()
                err_console.print(f"[red]{plugin.title} failed:[/red] {exc.message}")
                if exc.hint:
                    err_console.print(f"[cyan]hint:[/cyan] {exc.hint}")
                failures.append(f"{plugin.title}: {exc.message}")
                summary.add_row(plugin.title, "-", "-", "all")
                continue
            finally:
                kit.save()
            for message in result.warnings:
                warn(message)
            for item in result.failed:
                err_console.print(f"[red]failed:[/red] {item}")
            failures += [f"{plugin.title}: {f}" for f in result.failed]
            summary.add_row(plugin.title, str(result.downloaded), str(result.skipped), str(len(result.failed)))
    finally:
        kit.save()
    console.print()
    console.print(summary)
    total = sum(a.size for a in kit.artifacts.values())
    console.print(f"Kit size: [bold]{human_size(total)}[/bold] in {len(kit.artifacts)} files")
    if failures:
        err_console.print(f"[red]{len(failures)} item(s) failed.[/red] Everything else is in the kit; "
                          "run the same command again to retry only what is missing.")
        raise typer.Exit(1)
    console.print("[green]Done.[/green] Next: `anbar serve " + str(out) + "` and `anbar use " + str(out) + "`.")


# ---------------------------------------------------------------------------
# serve / use / restore / env
# ---------------------------------------------------------------------------


def _ports(config: Config, pypi_port, npm_port, files_port, registry_port) -> dict[str, int]:
    return {
        "pypi": pypi_port if pypi_port is not None else config.serve.pypi_port,
        "npm": npm_port if npm_port is not None else config.serve.npm_port,
        "files": files_port if files_port is not None else config.serve.files_port,
        "registry": registry_port if registry_port is not None else config.serve.registry_port,
    }


def _config_for_kit(config_path: Optional[Path]) -> Config:
    return load_config(Path.cwd(), config_path)


PypiPortOpt = typer.Option(None, "--pypi-port", help="Port of the PyPI index (default 3141).")
NpmPortOpt = typer.Option(None, "--npm-port", help="Port of the npm registry (default 4873).")
FilesPortOpt = typer.Option(None, "--files-port", help="Port of the models/docs file server (default 8765).")
RegistryPortOpt = typer.Option(None, "--registry-port", help="Port of the optional Docker registry (default 5000).")


@app.command()
def serve(
    kit_dir: Path = typer.Argument(..., help="Kit directory."),
    host: Optional[str] = typer.Option(None, "--host", help="Address to listen on; use 0.0.0.0 to share on the LAN."),
    pypi_port: Optional[int] = PypiPortOpt,
    npm_port: Optional[int] = NpmPortOpt,
    files_port: Optional[int] = FilesPortOpt,
    registry_port: Optional[int] = RegistryPortOpt,
    config_path: Optional[Path] = ConfigOpt,
    only: Optional[List[str]] = OnlyOpt,
    skip: Optional[List[str]] = SkipOpt,
) -> None:
    """Start local mirrors (PyPI, npm, files, optional Docker registry) for a kit."""
    config = _config_for_kit(config_path)
    kit = Kit.open(kit_dir)
    bind = host or config.serve.host
    ports = _ports(config, pypi_port, npm_port, files_port, registry_port)
    services: list = []
    try:
        for plugin in select_plugins(only, skip):
            if not plugin.has_content(kit):
                continue
            for service in plugin.serve(kit, config, bind, ports):
                service.start()
                services.append(service)
        if not services:
            raise AnbarError("this kit has nothing to serve", "fill it with `anbar pack --out " + str(kit_dir) + "`")
        table = Table(title="Anbar is serving " + str(kit.root))
        table.add_column("Service")
        table.add_column("URL")
        for service in services:
            table.add_row(service.name, service.url)
        console.print(table)
        if bind in ("0.0.0.0", "::"):
            console.print("Teammates can run: [bold]anbar use --host <this machine's IP>[/bold]")
        console.print("Press Ctrl+C to stop.")
        stop = threading.Event()
        try:
            while not stop.wait(0.5):
                pass
        except KeyboardInterrupt:
            console.print("Stopping...")
    finally:
        for service in services:
            try:
                service.stop()
            except Exception as exc:  # noqa: BLE001
                warn(f"could not stop {service.name}: {exc}")


@app.command()
def use(
    kit_dir: Optional[Path] = typer.Argument(None, help="Kit directory (optional when using a teammate's server via --host)."),
    host: Optional[str] = typer.Option(None, "--host", help="Host running `anbar serve` (default 127.0.0.1)."),
    pypi_port: Optional[int] = PypiPortOpt,
    npm_port: Optional[int] = NpmPortOpt,
    files_port: Optional[int] = FilesPortOpt,
    registry_port: Optional[int] = RegistryPortOpt,
    config_path: Optional[Path] = ConfigOpt,
    only: Optional[List[str]] = OnlyOpt,
    skip: Optional[List[str]] = SkipOpt,
    force: bool = typer.Option(False, "--force", help="Restore a previous `anbar use` first, then apply again."),
) -> None:
    """Point pip, npm/yarn/pnpm, Docker and Hugging Face at the local mirrors (backs up every file)."""
    if load_state() is not None:
        if not force:
            raise AnbarError("Anbar is already active", "run `anbar restore` first, or pass --force")
        _do_restore(quiet=True)
    config = _config_for_kit(config_path)
    kit = Kit.open(kit_dir) if kit_dir else None
    ports = _ports(config, pypi_port, npm_port, files_port, registry_port)
    host_value = host or (config.serve.host if config.serve.host not in ("0.0.0.0", "::") else "127.0.0.1")
    ctx = UseContext(kit=kit, config=config, host=host_value, pypi_port=ports["pypi"], npm_port=ports["npm"],
                     files_port=ports["files"], registry_port=ports["registry"])
    changes = Changes(kit=str(kit.root) if kit else None)
    notes: list[tuple[str, str]] = []
    try:
        for plugin in select_plugins(only, skip):
            if kit is not None and not plugin.has_content(kit):
                continue
            if kit is None and plugin.name not in ("python", "node"):
                continue
            for note in plugin.configure(ctx, changes):
                notes.append((plugin.title, note))
    finally:
        env_files = changes.write_env_files()
        changes.save()
    if not notes:
        (changes.home / "state.json").unlink(missing_ok=True)
        raise AnbarError("nothing to configure: the kit is empty")
    table = Table(title="Configured")
    table.add_column("Ecosystem")
    table.add_column("Change")
    for title, note in notes:
        table.add_row(title, note)
    console.print(table)
    if env_files:
        console.print("Some tools read environment variables. Load them in your shell:")
        console.print(f"  bash/zsh:   [bold]source {env_files[0]}[/bold]   (or: eval \"$(anbar env)\")")
        console.print(f"  fish:       [bold]source {env_files[1]}[/bold]")
        console.print(f"  PowerShell: [bold]. {env_files[2]}[/bold]")
        console.print(f"  cmd.exe:    [bold]call {env_files[3]}[/bold]")
    console.print("Undo everything with [bold]anbar restore[/bold].")


def _do_restore(quiet: bool = False) -> None:
    report = restore_files()
    notes = []
    for plugin in all_plugins():
        records = report.records.get(plugin.name)
        if records:
            notes += plugin.restore({"records": records})
    if quiet:
        return
    for path in report.restored:
        console.print(f"restored  {path}")
    for path in report.removed:
        console.print(f"removed   {path}")
    for note in notes:
        console.print(note)
    for path in report.modified_since:
        warn(f"{path} was edited after `anbar use`; those edits were replaced by the original file")
    console.print("[green]All changes made by `anbar use` were undone.[/green] "
                  "Open a new shell (or unset the variables) to drop the environment settings.")


@app.command()
def restore() -> None:
    """Undo `anbar use` exactly, from the backups."""
    _do_restore()


@app.command("env")
def env_cmd(
    shell: str = typer.Option("sh", "--shell", help="sh, fish, powershell or cmd."),
) -> None:
    """Print the environment variables set by `anbar use` (for eval in your shell)."""
    state = load_state()
    if state is None or not state.env:
        raise AnbarError("no environment to export", "run `anbar use KIT_DIR` first")
    for key, value in state.env.items():
        if shell == "fish":
            print(f"set -gx {key} '{value}'")
        elif shell in ("powershell", "pwsh"):
            print(f"$env:{key} = '{value}'")
        elif shell == "cmd":
            print(f'set "{key}={value}"')
        else:
            print(f"export {key}='{value}'")


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
