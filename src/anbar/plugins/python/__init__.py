"""Python ecosystem: pip / uv / poetry projects, served as a PEP 503 index."""

from __future__ import annotations

import configparser
import hashlib
import io
import json
import os
import statistics
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Any

from anbar.config import Config, PythonTarget
from anbar.errors import AnbarError
from anbar.kit import Kit
from anbar.network import proxy_env, run_parallel
from anbar.plugins import register
from anbar.plugins.base import (
    Changes,
    Context,
    FetchResult,
    Plan,
    PlanItem,
    Plugin,
    Service,
    UseContext,
    walk_project,
)
from anbar.plugins.python.index import DIST_EXTS, SimpleIndex, SimpleIndexHandler, read_requires_python
from anbar.plugins.python.parsers import (
    LOCK_PARSERS,
    Requirement,
    is_requirements_file,
    normalize,
    parse_manifest,
    parse_pyproject,
    requirement_name,
)
from anbar.server import HTTPService, make_handler

DEFAULT_INDEX = "https://pypi.org/simple"
BUILD_TOOLS = ["pip", "setuptools", "wheel"]
STATE_FILE = ".anbar-state.json"
CHUNK = 25

_NETWORK_MARKERS = (
    "Failed to establish a new connection",
    "Could not fetch URL",
    "ProxyError",
    "SSLError",
    "ConnectTimeoutError",
    "Read timed out",
    "Temporary failure in name resolution",
    "Name or service not known",
    "getaddrinfo failed",
    "Network is unreachable",
    "Max retries exceeded",
)


def _pip_error(stderr: str) -> str:
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip().startswith("ERROR:")]
    if lines:
        return lines[-1][len("ERROR:"):].strip()
    tail = [ln for ln in stderr.strip().splitlines() if ln.strip()]
    return tail[-1] if tail else "pip failed"


def _is_network_failure(stderr: str) -> bool:
    return any(marker in stderr for marker in _NETWORK_MARKERS)


def user_pip_config_path() -> Path:
    """The per-user pip config file pip reads with the highest priority."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "pip" / "pip.ini"
    if sys.platform == "darwin":
        lib = Path.home() / "Library" / "Application Support" / "pip"
        if lib.is_dir():
            return lib / "pip.conf"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "pip" / "pip.conf"


@register
class PythonPlugin(Plugin):
    name = "python"
    title = "Python"

    # -- detect / plan --------------------------------------------------------

    def detect(self, project: Path, config: Config) -> list[Path]:
        found = []
        for path in walk_project(project):
            name = path.name
            if name in LOCK_PARSERS or name == "pyproject.toml" or is_requirements_file(name):
                found.append(path)
        return found

    def plan(self, project: Path, config: Config, manifests: list[Path]) -> Plan:
        plan = Plan(self.name, manifests=list(manifests))
        requirements: list[Requirement] = []
        constraints: list[str] = []
        dirs_with_lock = {m.parent for m in manifests if m.name in LOCK_PARSERS}
        for manifest in manifests:
            label = _label(manifest, project)
            if manifest.name == "pyproject.toml" and manifest.parent in dirs_with_lock:
                # The lock file already pins the dependency tree; only build requirements are new.
                result = parse_pyproject(manifest, label)
                result.requirements = [r for r in result.requirements if _is_build_req(manifest, r)]
            else:
                result = parse_manifest(manifest, label)
            requirements += result.requirements
            constraints += [str(c) for c in result.constraints]
            plan.warnings += result.warnings

        py = config.python
        requirements += [Requirement(spec, "anbar.toml") for spec in py.extra]
        if requirements and py.include_build_tools:
            requirements += [Requirement(spec, "build tools") for spec in BUILD_TOOLS]

        excluded = {normalize(e) for e in py.exclude}
        seen: set[str] = set()
        for req in requirements:
            if req.name in excluded:
                continue
            key = req.spec.replace(" ", "").lower()
            if key in seen:
                continue
            seen.add(key)
            plan.items.append(
                PlanItem(
                    name=req.name,
                    version=req.pinned_version,
                    source=req.source,
                    data={"spec": req.spec, "locked": req.locked},
                )
            )
        # Lock files win over loose specs for the same project.
        locked_names = {i.name for i in plan.items if i.data["locked"]}
        plan.items = [
            i for i in plan.items if i.data["locked"] or i.name not in locked_names or i.source == "anbar.toml"
        ]
        plan.data["constraints"] = sorted(set(constraints))
        return plan

    # -- estimate ---------------------------------------------------------------

    def estimate(self, plan: Plan, ctx: Context) -> None:
        indexes = ctx.config.mirrors.pypi or [DEFAULT_INDEX]
        json_base = _json_api_base(indexes)
        if not json_base or not plan.items:
            return
        n_targets = max(1, len(ctx.config.python.targets))

        def one(item: PlanItem) -> int | None:
            name = requirement_name(item.data["spec"]) or item.name
            url = f"{json_base}/{name}/{item.version}/json" if item.version else f"{json_base}/{name}/json"
            data = ctx.net.get_json(url)
            return _pick_size(data.get("urls") or [], n_targets)

        # Fail fast when the API is unreachable instead of timing out per item.
        first = plan.items[0]
        old_retries = ctx.net.cfg.retries
        ctx.net.cfg.retries = 0
        try:
            first.size = one(first)
            for item, size, err in run_parallel(plan.items[1:], one, ctx.config.network.workers):
                if err is None:
                    item.size = size
        finally:
            ctx.net.cfg.retries = old_retries

    # -- fetch ----------------------------------------------------------------

    def fetch(self, plan: Plan, kit: Kit, ctx: Context) -> FetchResult:
        result = FetchResult()
        if not plan.items:
            return result
        packages = kit.dir("python", "packages")
        state_path = kit.dir("python") / STATE_FILE
        state = _load_json(state_path, {"locked": [], "loose": {}})
        done_locked = set(state["locked"])
        cfg = ctx.config
        targets = cfg.python.targets or [PythonTarget()]
        indexes = cfg.mirrors.pypi or [DEFAULT_INDEX]
        constraints = plan.data.get("constraints", [])
        locked = [i for i in plan.items if i.data["locked"]]
        loose = [i for i in plan.items if not i.data["locked"]]

        before = _snapshot(packages)
        total_steps = len(targets) * (len(locked) + (1 if loose else 0))
        ctx.progress.start("Python packages", total_steps)

        for target in targets:
            label = target.label()
            # 1. Locked requirements: exact pins, no dependency resolution needed.
            todo = [i for i in locked if ctx.refresh or f"{label}|{i.data['spec']}" not in done_locked]
            ctx.progress.advance(len(locked) - len(todo))
            result.skipped += len(locked) - len(todo)
            if todo:
                ok, failed = self._download_batches(todo, packages, target, indexes, ctx, no_deps=True)
                for item in ok:
                    done_locked.add(f"{label}|{item.data['spec']}")
                result.failed += failed
            # 2. Loose requirements: pip resolves the transitive closure in one go.
            if loose:
                specs = sorted(i.data["spec"] for i in loose)
                key = hashlib.sha256(json.dumps([specs, constraints, indexes]).encode()).hexdigest()
                previous = state["loose"].get(label)
                if previous == key and not ctx.refresh and _all_present(kit, packages):
                    result.skipped += len(loose)
                else:
                    error = self._pip_with_fallback(specs, constraints, packages, target, indexes, ctx, False)
                    if error is None:
                        state["loose"][label] = key
                    else:
                        # Isolate the failing requirement(s) so the rest still lands in the kit.
                        ctx.progress.log(f"[yellow]resolving {len(specs)} requirements together failed ({error}); "
                                         "retrying one by one[/yellow]")
                        outcomes = run_parallel(
                            loose,
                            lambda item: self._pip_with_fallback(
                                [item.data["spec"]], constraints, packages, target, indexes, ctx, False
                            ),
                            cfg.network.workers,
                        )
                        for item, err_msg, exc in outcomes:
                            if exc is not None or err_msg:
                                result.failed.append(f"{item.data['spec']} [{label}]: {err_msg or exc}")
                ctx.progress.advance(1)

        state["locked"] = sorted(done_locked)
        _save_json(state_path, state)

        after = _snapshot(packages)
        new_files = [name for name in after if before.get(name) != after[name]]
        for name in sorted(new_files):
            path = packages / name
            meta: dict[str, Any] = {}
            requires = read_requires_python(path)
            if requires:
                meta["requires_python"] = requires
            kit.add(path, self.name, source=f"{indexes[0]} ({name})", meta=meta)
            result.downloaded += 1
        # Files pip reported as "already downloaded" but that are not tracked yet.
        for name in after:
            if name not in new_files and kit.get(kit.rel(packages / name)) is None:
                kit.add(packages / name, self.name, source=indexes[0], meta=_meta(packages / name))
                result.downloaded += 1
        ctx.progress.finish()
        return result

    def _download_batches(self, items, packages, target, indexes, ctx, no_deps):
        """Download locked items in parallel chunks; isolate failures per item."""
        chunks = [items[i : i + CHUNK] for i in range(0, len(items), CHUNK)]
        ok: list[PlanItem] = []
        failed: list[str] = []
        platform_skips: list[str] = []

        def run_chunk(chunk: list[PlanItem]) -> tuple[list[PlanItem], list[str]]:
            specs = [i.data["spec"] for i in chunk]
            error = self._pip_with_fallback(specs, [], packages, target, indexes, ctx, no_deps)
            if error is None:
                ctx.progress.advance(len(chunk))
                return chunk, []
            good, bad = [], []
            for item in chunk:
                err = self._pip_with_fallback([item.data["spec"]], [], packages, target, indexes, ctx, no_deps)
                if err is not None and target.platform:
                    # No wheel for the target platform: fall back to the sdist.
                    err = self._pip_with_fallback(
                        [item.data["spec"]], [], packages, target, indexes, ctx, no_deps, allow_sdist=True
                    )
                ctx.progress.advance(1)
                if err is None:
                    good.append(item)
                elif target.platform and "No matching distribution" in err:
                    platform_skips.append(f"{item.data['spec']} has no build for {target.label()} (platform-specific?)")
                else:
                    bad.append(f"{item.data['spec']} [{target.label()}]: {err}")
            return good, bad

        for _, res, exc in run_parallel(chunks, run_chunk, ctx.config.network.workers):
            if exc is not None:
                raise exc
            good, bad = res
            ok += good
            failed += bad
        for message in platform_skips:
            ctx.progress.log(f"[yellow]skipped:[/yellow] {message}")
        return ok, failed

    def _pip_with_fallback(self, specs, constraints, packages, target, indexes, ctx, no_deps, allow_sdist=False):
        """Run ``pip download`` against each upstream index in order. Returns an error or None."""
        errors = []
        all_network = True
        for index in indexes:
            proc = self._run_pip(specs, constraints, packages, target, index, ctx, no_deps, allow_sdist)
            if proc.returncode == 0:
                return None
            network = _is_network_failure(proc.stderr)
            all_network = all_network and network
            errors.append(f"{_host(index)}: {'unreachable' if network else _pip_error(proc.stderr)}")
        if all_network:
            raise AnbarError(
                "PyPI unreachable (" + "; ".join(errors) + ")",
                "try an upstream mirror: `--mirror pypi=https://mirror.example/simple` or [mirrors] pypi "
                "in anbar.toml, or set a proxy in [proxy]",
            )
        return "; ".join(errors)

    def _run_pip(self, specs, constraints, packages, target, index, ctx, no_deps, allow_sdist):
        cfg = ctx.config
        cmd = [
            sys.executable, "-m", "pip", "download",
            "--dest", str(packages),
            "--disable-pip-version-check", "--no-input", "--progress-bar", "off",
            "--retries", str(cfg.network.retries),
            "--timeout", str(int(cfg.network.timeout)),
            "--index-url", index,
        ]
        parts = urllib.parse.urlsplit(index)
        if parts.scheme == "http" and parts.hostname:
            cmd += ["--trusted-host", parts.hostname]
        if cfg.network.ca_bundle:
            cmd += ["--cert", cfg.network.ca_bundle]
        if cfg.proxy.https or cfg.proxy.http:
            cmd += ["--proxy", proxy_env(cfg.proxy).get("HTTPS_PROXY", "")]
        if target.platform or target.python_version or target.implementation or target.abi:
            if target.platform:
                cmd += ["--platform", target.platform]
            if target.python_version:
                cmd += ["--python-version", target.python_version]
            if target.implementation:
                cmd += ["--implementation", target.implementation]
            if target.abi:
                cmd += ["--abi", target.abi]
            if not allow_sdist:
                cmd += ["--only-binary=:all:"]
        elif cfg.python.only_binary:
            cmd += ["--only-binary=:all:"]
        if no_deps or allow_sdist:
            cmd += ["--no-deps"]
        for constraint in constraints:
            cmd += ["-c", constraint]
        cmd += specs
        env = {k: v for k, v in os.environ.items() if not k.startswith("PIP_")}
        # Ignore the user's pip config: after `anbar use` it points at the local mirror.
        env["PIP_CONFIG_FILE"] = os.devnull
        env.update(proxy_env(cfg.proxy))
        runner = ctx.run or subprocess.run
        try:
            return runner(cmd, capture_output=True, text=True, env=env)
        except FileNotFoundError as exc:  # pragma: no cover - sys.executable always exists
            raise AnbarError(f"cannot run pip: {exc}") from exc

    # -- serve / configure ---------------------------------------------------

    def serve(self, kit: Kit, config: Config, host: str, ports: dict[str, int]) -> list[Service]:
        packages = kit.root / "python" / "packages"
        if not packages.is_dir():
            return []
        index = SimpleIndex(kit, packages)
        handler = make_handler(SimpleIndexHandler, index=index)
        return [HTTPService("PyPI index", handler, host, ports["pypi"], "/simple/")]

    def configure(self, ctx: UseContext, changes: Changes) -> list[str]:
        index_url = ctx.base(ctx.pypi_port) + "/simple/"
        host = urllib.parse.urlsplit(index_url).hostname or "127.0.0.1"
        path = user_pip_config_path()
        parser = configparser.ConfigParser(interpolation=None)
        original = changes.read(path)
        if original:
            try:
                parser.read_string(original)
            except configparser.Error as exc:
                raise AnbarError(f"cannot parse {path}: {exc}", "fix or move the file, then run `anbar use` again")
        for section in ("global", "install", "download"):
            if parser.has_section(section):
                for key in ("extra-index-url", "extra_index_url", "find-links", "find_links"):
                    parser.remove_option(section, key)
        if not parser.has_section("global"):
            parser.add_section("global")
        parser.set("global", "index-url", index_url)
        trusted = parser.get("global", "trusted-host", fallback="").split()
        if host not in trusted:
            trusted.append(host)
        parser.set("global", "trusted-host", " ".join(trusted))
        for section in ("install", "download"):
            if parser.has_option(section, "index-url"):
                parser.set(section, "index-url", index_url)
        buf = io.StringIO()
        parser.write(buf)
        changes.write(path, buf.getvalue())
        changes.set_env("UV_DEFAULT_INDEX", index_url)
        changes.set_env("UV_INDEX_URL", index_url)
        notes = [f"pip: index-url = {index_url} ({path})", "uv: UV_DEFAULT_INDEX (environment script)"]
        if os.environ.get("PIP_INDEX_URL"):
            notes.append("[yellow]PIP_INDEX_URL is set in your environment and overrides pip.conf; unset it[/yellow]")
        return notes


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _label(path: Path, project: Path) -> str:
    try:
        return path.resolve().relative_to(project.resolve()).as_posix()
    except ValueError:
        return path.name


def _is_build_req(manifest: Path, req: Requirement) -> bool:
    try:
        import tomllib as _toml  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover
        import tomli as _toml  # type: ignore[no-redef]
    try:
        data = _toml.loads(manifest.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    return req.spec in ((data.get("build-system") or {}).get("requires") or [])


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc or url


def _json_api_base(indexes: list[str]) -> str | None:
    for index in indexes:
        parts = urllib.parse.urlsplit(index)
        if parts.netloc in ("pypi.org", "pypi.python.org"):
            return "https://pypi.org/pypi"
        path = parts.path.rstrip("/")
        if path.endswith("/simple"):
            return urllib.parse.urlunsplit(parts._replace(path=path[: -len("simple")] + "pypi"))
    return None


def _pick_size(urls: list[dict[str, Any]], n_targets: int) -> int | None:
    wheels = [u for u in urls if u.get("packagetype") == "bdist_wheel" and u.get("size")]
    universal = [u for u in wheels if u["filename"].endswith("-none-any.whl")]
    if universal:
        return int(universal[0]["size"])
    if wheels:
        return int(statistics.median(u["size"] for u in wheels)) * n_targets
    sdists = [u for u in urls if u.get("packagetype") == "sdist" and u.get("size")]
    return int(sdists[0]["size"]) if sdists else None


def _snapshot(directory: Path) -> dict[str, int]:
    return {
        p.name: p.stat().st_size
        for p in directory.iterdir()
        if p.is_file() and p.name.endswith(DIST_EXTS)
    }


def _meta(path: Path) -> dict[str, Any]:
    requires = read_requires_python(path)
    return {"requires_python": requires} if requires else {}


def _all_present(kit: Kit, packages: Path) -> bool:
    for art in kit.by_ecosystem("python"):
        if not kit.abspath(art.path).exists():
            return False
    return True


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

