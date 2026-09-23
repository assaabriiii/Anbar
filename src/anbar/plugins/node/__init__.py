"""Node ecosystem: npm / yarn / pnpm lock files, served as a read-only npm registry."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
from pathlib import Path
from typing import Any

import yaml

from anbar.config import Config
from anbar.errors import AnbarError, NetworkError
from anbar.kit import Kit
from anbar.network import describe_error, run_parallel
from anbar.plugins import register
from anbar.plugins.base import Changes, Context, FetchResult, Plan, PlanItem, Plugin, Service, UseContext, walk_project
from anbar.plugins.node.parsers import (
    NPM_REGISTRY,
    PARSERS,
    NodePackage,
    default_tarball,
    is_default_registry,
    split_name_version,
)
from anbar.plugins.node.registry import NpmRegistryHandler, packument_path, tarball_path
from anbar.server import HTTPService, make_handler

ABBREVIATED = "application/vnd.npm.install-v1+json; q=1.0, application/json; q=0.8"


def _version_key(version: str) -> tuple:
    main, _, pre = version.partition("-")
    nums = tuple(int(x) if x.isdigit() else 0 for x in main.split("."))
    return nums + ((1,) if not pre else (0, pre))


def _escape(name: str) -> str:
    return name.replace("/", "%2f") if name.startswith("@") else name


@register
class NodePlugin(Plugin):
    name = "node"
    title = "Node"

    def detect(self, project: Path, config: Config) -> list[Path]:
        return [p for p in walk_project(project) if p.name in PARSERS]

    def plan(self, project: Path, config: Config, manifests: list[Path]) -> Plan:
        plan = Plan(self.name, manifests=list(manifests))
        seen: set[str] = set()
        for manifest in manifests:
            try:
                label = manifest.resolve().relative_to(project.resolve()).as_posix()
            except ValueError:
                label = manifest.name
            result = PARSERS[manifest.name](manifest, label)
            plan.warnings += result.warnings
            for pkg in result.packages:
                if pkg.key in seen:
                    continue
                seen.add(pkg.key)
                plan.items.append(_item(pkg, label))
        for spec in config.node.extra:
            name, version = split_name_version(spec)
            if spec.startswith("@") and "@" not in spec[1:]:
                name, version = spec, ""
            plan.items.append(
                PlanItem(name=name, version=version or None, source="anbar.toml", data={"extra": True, "spec": spec})
            )
        return plan

    # -- estimate / fetch -------------------------------------------------------

    def estimate(self, plan: Plan, ctx: Context) -> None:
        items = [i for i in plan.items if "tarball" in i.data]
        if not items:
            return
        old = ctx.net.cfg.retries
        ctx.net.cfg.retries = 0
        try:
            first = items[0]
            first.size = ctx.net.content_length(first.data["tarball"])
            if first.size is None:
                return  # registry unreachable; keep typical sizes
            for item, size, _ in run_parallel(
                items[1:], lambda i: ctx.net.content_length(i.data["tarball"]), ctx.config.network.workers
            ):
                item.size = size
        finally:
            ctx.net.cfg.retries = old

    def _registries(self, config: Config) -> list[str]:
        return [m.rstrip("/") for m in config.mirrors.npm] or [NPM_REGISTRY]

    def _candidates(self, url: str, config: Config) -> list[str]:
        """Mirror URLs for a tarball, in fallback order, ending with the original."""
        urls = []
        if is_default_registry(url):
            path = urllib.parse.urlsplit(url).path
            urls = [m.rstrip("/") + path for m in config.mirrors.npm]
        if url not in urls:
            urls.append(url)
        return urls

    def _resolve_extras(self, plan: Plan, ctx: Context, result: FetchResult) -> list[PlanItem]:
        """Turn ``name@version`` / ``name@tag`` extras into concrete tarballs."""
        resolved = []
        for item in [i for i in plan.items if i.data.get("extra")]:
            try:
                doc = self._get_packument(item.name, ctx)
            except (NetworkError, urllib.error.URLError, OSError) as exc:
                result.failed.append(f"{item.data['spec']}: {exc}")
                continue
            wanted = item.version or "latest"
            version = (doc.get("dist-tags") or {}).get(wanted, wanted)
            info = (doc.get("versions") or {}).get(version)
            if info is None:
                result.failed.append(
                    f"{item.data['spec']}: version '{wanted}' not found (use an exact version or a dist-tag)"
                )
                continue
            dist = info.get("dist") or {}
            pkg = NodePackage(item.name, version, dist.get("tarball") or default_tarball(item.name, version),
                              dist.get("integrity"))
            resolved.append(_item(pkg, "anbar.toml"))
        return resolved

    def _get_packument(self, name: str, ctx: Context) -> dict[str, Any]:
        errors = []
        for registry in self._registries(ctx.config):
            try:
                return ctx.net.get_json(f"{registry}/{_escape(name)}", {"Accept": ABBREVIATED})
            except (urllib.error.URLError, OSError, ValueError) as exc:
                errors.append(f"{urllib.parse.urlsplit(registry).netloc}: {describe_error(exc)}")
        raise NetworkError(f"cannot fetch metadata for {name} ({'; '.join(errors)})",
                           "npm registry unreachable — try `--mirror npm=https://mirror.example`")

    def fetch(self, plan: Plan, kit: Kit, ctx: Context) -> FetchResult:
        result = FetchResult()
        root = kit.dir("node")
        items = [i for i in plan.items if "tarball" in i.data]
        items += self._resolve_extras(plan, ctx, result)
        ctx.progress.start("npm tarballs", len(items))

        def fetch_one(item: PlanItem) -> bool:
            pkg = NodePackage(item.name, item.version or "", item.data["tarball"], item.data.get("integrity"))
            dest = tarball_path(root, pkg.name, pkg.filename)
            if not ctx.refresh and kit.has_valid(dest):
                return False
            res = ctx.net.download(self._candidates(pkg.tarball, ctx.config), dest, integrity=pkg.integrity)
            kit.add(dest, self.name, res.url, sha256=res.sha256,
                    meta={"package": pkg.name, "version": pkg.version, "integrity": pkg.integrity or ""})
            return True

        unreachable = 0
        for item, downloaded, exc in run_parallel(items, fetch_one, ctx.config.network.workers,
                                                  on_done=lambda *_: ctx.progress.advance()):
            if exc is not None:
                if isinstance(exc, NetworkError) and "unreachable" in exc.message:
                    unreachable += 1
                result.failed.append(f"{item.name}@{item.version}: {getattr(exc, 'message', exc)}")
            elif downloaded:
                result.downloaded += 1
            else:
                result.skipped += 1
        ctx.progress.finish()
        if items and unreachable == len(items):
            raise AnbarError("npm registry unreachable: no tarball could be downloaded",
                             "try an upstream mirror: `--mirror npm=https://mirror.example` or [mirrors] npm")

        # Packuments: one per package name, trimmed to the versions in the kit.
        by_name: dict[str, list[PlanItem]] = {}
        for item in items:
            dest = tarball_path(root, item.name, f"{item.name.split('/')[-1]}-{item.version}.tgz")
            if dest.exists():
                by_name.setdefault(item.name, []).append(item)
        ctx.progress.start("npm metadata", len(by_name))

        def fetch_packument(name: str) -> None:
            path = packument_path(root, name)
            wanted = {i.version for i in by_name[name] if i.version}
            existing = _read_json(path)
            if existing and not ctx.refresh and wanted <= set((existing.get("versions") or {}).keys()):
                return
            try:
                full = self._get_packument(name, ctx)
            except NetworkError as exc:
                full = {}
                result.warnings.append(f"{exc.message}; using lock file data instead")
            doc = _trim(name, full, existing, by_name[name])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
            kit.add(path, self.name, f"{self._registries(ctx.config)[0]}/{_escape(name)}", meta={"packument": name})

        for name, _, exc in run_parallel(sorted(by_name), fetch_packument, ctx.config.network.workers,
                                         on_done=lambda *_: ctx.progress.advance()):
            if exc is not None:
                result.failed.append(f"{name} (metadata): {exc}")
        ctx.progress.finish()
        return result

    # -- serve / configure ----------------------------------------------------

    def serve(self, kit: Kit, config: Config, host: str, ports: dict[str, int]) -> list[Service]:
        root = kit.root / "node"
        if not (root / "packuments").is_dir():
            return []
        handler = make_handler(NpmRegistryHandler, root=root)
        return [HTTPService("npm registry", handler, host, ports["npm"], "/")]

    def configure(self, ctx: UseContext, changes: Changes) -> list[str]:
        registry = ctx.base(ctx.npm_port) + "/"
        host = urllib.parse.urlsplit(registry).hostname or "127.0.0.1"
        home = Path.home()
        notes = []

        npmrc = home / ".npmrc"
        lines = [ln for ln in (changes.read(npmrc) or "").splitlines() if not re.match(r"^\s*registry\s*=", ln)]
        lines.append(f"registry={registry}")
        changes.write(npmrc, "\n".join(lines) + "\n")
        notes.append(f"npm/pnpm: registry = {registry} ({npmrc})")

        yarnrc = home / ".yarnrc"
        lines = [ln for ln in (changes.read(yarnrc) or "").splitlines() if not re.match(r"^\s*registry\s", ln)]
        lines.append(f'registry "{registry}"')
        changes.write(yarnrc, "\n".join(lines) + "\n")
        notes.append(f"yarn 1: registry ({yarnrc})")

        berry = home / ".yarnrc.yml"
        try:
            data = yaml.safe_load(changes.read(berry) or "") or {}
        except yaml.YAMLError as exc:
            raise AnbarError(f"cannot parse {berry}: {exc}", "fix or move the file, then run `anbar use` again")
        data["npmRegistryServer"] = registry.rstrip("/")
        whitelist = list(data.get("unsafeHttpWhitelist") or [])
        if host not in whitelist:
            whitelist.append(host)
        data["unsafeHttpWhitelist"] = whitelist
        changes.write(berry, yaml.safe_dump(data, sort_keys=False))
        notes.append(f"yarn 2+: npmRegistryServer ({berry})")
        return notes


def _item(pkg: NodePackage, source: str) -> PlanItem:
    return PlanItem(
        name=pkg.name,
        version=pkg.version,
        source=source,
        data={"tarball": pkg.tarball, "integrity": pkg.integrity},
    )


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _trim(name: str, full: dict[str, Any], existing: dict[str, Any] | None, items: list[PlanItem]) -> dict[str, Any]:
    """Keep only versions present in the kit, merging with what earlier packs stored."""
    versions: dict[str, Any] = dict((existing or {}).get("versions") or {})
    upstream = full.get("versions") or {}
    for item in items:
        version = item.version or ""
        if version in upstream:
            versions[version] = upstream[version]
        elif version not in versions:
            # Synthesised from the lock file: enough for installs driven by a lock file.
            dist = {"tarball": item.data["tarball"]}
            if item.data.get("integrity"):
                dist["integrity"] = item.data["integrity"]
            versions[version] = {"name": name, "version": version, "dist": dist}
    ordered = sorted(versions, key=_version_key)
    tags = {}
    latest = (full.get("dist-tags") or {}).get("latest") or ((existing or {}).get("dist-tags") or {}).get("latest")
    stable = [v for v in ordered if "-" not in v] or ordered
    tags["latest"] = latest if latest in versions else stable[-1]
    for tag, version in (full.get("dist-tags") or {}).items():
        if version in versions:
            tags[tag] = version
    return {"name": name, "dist-tags": tags, "versions": {v: versions[v] for v in ordered}}
