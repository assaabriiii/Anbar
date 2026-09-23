"""Docker ecosystem: base images from Dockerfiles and compose files, saved as tarballs."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from anbar.config import Config
from anbar.errors import AnbarError
from anbar.kit import Kit
from anbar.network import proxy_env, run_parallel
from anbar.plugins import register
from anbar.plugins.base import Changes, Context, FetchResult, Plan, PlanItem, Plugin, Service, UseContext, walk_project
from anbar.plugins.docker.parsers import (
    ImageRef,
    is_compose_file,
    is_dockerfile,
    normalize_ref,
    parse_compose,
    parse_dockerfile,
)

REGISTRY_IMAGE = "registry:2"
REGISTRY_CONTAINER = "anbar-registry"


def run_docker(args: list[str], env: dict[str, str] | None = None, check: bool = False) -> subprocess.CompletedProcess:
    """Run the docker CLI. Tests replace this function."""
    exe = shutil.which("docker")
    if exe is None:
        raise AnbarError("the docker command was not found", "install Docker (or Docker Desktop) and try again")
    import os

    full_env = {**os.environ, **(env or {})}
    proc = subprocess.run([exe, *args], capture_output=True, text=True, env=full_env)
    if check and proc.returncode != 0:
        raise AnbarError(f"docker {args[0]} failed: {_last_line(proc.stderr)}")
    return proc


def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    return lines[-1] if lines else "unknown error"


def split_reference(ref: str) -> tuple[str, str]:
    """Split an image reference into (registry, repository[:tag|@digest])."""
    first, sep, rest = ref.partition("/")
    if sep and ("." in first or ":" in first or first == "localhost"):
        return first, rest
    if not sep:
        return "docker.io", f"library/{ref}"
    return "docker.io", ref


def mirror_refs(ref: str, mirrors: list[str]) -> list[str]:
    """Candidate references to pull, mirrors first, the original last.

    A plain mirror entry (``docker.mirror.example``) proxies Docker Hub. Use
    ``ghcr.io=ghcr.mirror.example`` to mirror another registry.
    """
    registry, path = split_reference(ref)
    out: list[str] = []
    for entry in mirrors:
        entry = re.sub(r"^https?://", "", entry.strip()).rstrip("/")
        if "=" in entry:
            upstream, _, host = entry.partition("=")
            if upstream.strip() == registry:
                out.append(f"{host.strip()}/{path}")
        elif registry in ("docker.io", "registry-1.docker.io", "index.docker.io"):
            out.append(f"{entry}/{path}")
    out.append(ref)
    return out


def image_filename(ref: str, platform: str | None) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", ref)
    if platform:
        name += "__" + re.sub(r"[^A-Za-z0-9._-]+", "_", platform)
    return name + ".tar"


def check_daemon() -> None:
    proc = run_docker(["version", "--format", "{{.Server.Version}}"])
    if proc.returncode != 0:
        raise AnbarError(
            "Docker is installed but the daemon is not reachable: " + _last_line(proc.stderr),
            "start Docker Desktop or the docker service (`sudo systemctl start docker`)",
        )


@register
class DockerPlugin(Plugin):
    name = "docker"
    title = "Docker"

    def detect(self, project: Path, config: Config) -> list[Path]:
        return [p for p in walk_project(project) if is_dockerfile(p.name) or is_compose_file(p.name)]

    def plan(self, project: Path, config: Config, manifests: list[Path]) -> Plan:
        plan = Plan(self.name, manifests=list(manifests))
        images: dict[ImageRef, str] = {}
        pending = list(manifests)
        parsed: set[Path] = set()
        while pending:
            manifest = pending.pop(0)
            if manifest.resolve() in parsed:
                continue
            parsed.add(manifest.resolve())
            label = _label(manifest, project)
            if is_compose_file(manifest.name):
                result = parse_compose(manifest, label)
                for dockerfile in result.dockerfiles:
                    if dockerfile not in parsed:
                        pending.append(dockerfile)
                        if dockerfile not in plan.manifests:
                            plan.manifests.append(dockerfile)
            else:
                result = parse_dockerfile(manifest, label, config.docker.build_args)
            plan.warnings += result.warnings
            for image in result.images:
                images.setdefault(image, label)
        for extra in config.docker.extra:
            images.setdefault(ImageRef(normalize_ref(extra)), "anbar.toml")
        if config.docker.registry:
            images.setdefault(ImageRef(REGISTRY_IMAGE), "anbar.toml ([docker] registry)")
        excluded = {normalize_ref(e) for e in config.docker.exclude}
        for image, source in images.items():
            if image.ref in excluded:
                continue
            platform = image.platform or config.docker.platform
            name, _, tag = image.ref.rpartition(":") if "@" not in image.ref else (image.ref, "", "")
            plan.items.append(
                PlanItem(
                    name=name or image.ref,
                    version=tag or None,
                    source=source,
                    data={"ref": image.ref, "platform": platform},
                )
            )
        return plan

    def fetch(self, plan: Plan, kit: Kit, ctx: Context) -> FetchResult:
        result = FetchResult()
        if not plan.items:
            return result
        images_dir = kit.dir("docker", "images")
        todo = []
        for item in plan.items:
            dest = images_dir / image_filename(item.data["ref"], item.data["platform"])
            if not ctx.refresh and kit.has_valid(dest):
                result.skipped += 1
            else:
                todo.append(item)
        if not todo:
            return result
        check_daemon()
        env = proxy_env(ctx.config.proxy)
        ctx.progress.start("Docker images", len(todo))

        def pull_and_save(item: PlanItem) -> None:
            ref, platform = item.data["ref"], item.data["platform"]
            errors = []
            pulled_as = None
            for candidate in mirror_refs(ref, ctx.config.mirrors.docker):
                args = ["pull", "--quiet"] + (["--platform", platform] if platform else []) + [candidate]
                proc = run_docker(args, env)
                if proc.returncode == 0:
                    pulled_as = candidate
                    break
                errors.append(f"{candidate}: {_last_line(proc.stderr)}")
            if pulled_as is None:
                raise AnbarError(
                    "; ".join(errors),
                    "Docker Hub unreachable? add a mirror with `--mirror docker=docker.mirror.example`",
                )
            if pulled_as != ref:
                run_docker(["tag", pulled_as, ref], check=True)
                run_docker(["rmi", "--no-prune", pulled_as])  # drop the mirror tag, keep the image
            dest = images_dir / image_filename(ref, platform)
            tmp = dest.with_name(dest.name + ".part")
            proc = run_docker(["save", "-o", str(tmp), ref])
            if proc.returncode != 0:
                tmp.unlink(missing_ok=True)
                raise AnbarError(f"docker save {ref} failed: {_last_line(proc.stderr)}")
            tmp.replace(dest)
            inspect = run_docker(["image", "inspect", "--format", "{{.Id}}", ref])
            meta: dict[str, Any] = {"image": ref, "pulled_from": pulled_as}
            if platform:
                meta["platform"] = platform
            if inspect.returncode == 0:
                meta["id"] = inspect.stdout.strip()
            kit.add(dest, self.name, source=f"docker pull {pulled_as}", meta=meta)

        workers = min(4, ctx.config.network.workers)
        for item, _, exc in run_parallel(todo, pull_and_save, workers, on_done=lambda *_: ctx.progress.advance()):
            if exc is None:
                result.downloaded += 1
            else:
                result.failed.append(f"{item.data['ref']}: {exc}")
        ctx.progress.finish()
        return result

    # -- serve: optional local registry ------------------------------------------

    def serve(self, kit: Kit, config: Config, host: str, ports: dict[str, int]) -> list[Service]:
        if not config.docker.registry:
            return []
        registry_tar = next(
            (a for a in kit.by_ecosystem(self.name) if a.meta.get("image") == normalize_ref(REGISTRY_IMAGE)), None
        )
        if registry_tar is None:
            from anbar.console import warn

            warn("[docker] registry = true but registry:2 is not in the kit; run `anbar pack` again with it enabled")
            return []
        return [RegistryService(kit, host, ports["registry"])]

    # -- use / restore -----------------------------------------------------------

    def configure(self, ctx: UseContext, changes: Changes) -> list[str]:
        if ctx.kit is None:
            return []
        check_daemon()
        loaded, present = [], []
        for art in sorted(ctx.kit.by_ecosystem(self.name), key=lambda a: a.path):
            ref = art.meta.get("image")
            if not ref:
                continue
            exists = run_docker(["image", "inspect", "--format", "{{.Id}}", ref])
            if exists.returncode == 0 and (not art.meta.get("id") or exists.stdout.strip() == art.meta["id"]):
                present.append(ref)
                continue
            proc = run_docker(["load", "-i", str(ctx.kit.abspath(art.path))])
            if proc.returncode != 0:
                raise AnbarError(f"docker load of {art.path} failed: {_last_line(proc.stderr)}",
                                 f"run `anbar verify {ctx.kit.root}` to check the kit")
            if exists.returncode != 0:
                loaded.append(ref)
        changes.record(self.name, {"loaded": loaded})
        notes = [f"loaded {len(loaded)} image(s) into Docker" + (f", {len(present)} already present" if present else "")]
        notes.append("Dockerfiles and compose files now build offline (use `--pull=never` / `pull_policy: never`)")
        return notes

    def restore(self, record: dict[str, Any]) -> list[str]:
        loaded = [ref for r in record.get("records", []) for ref in r.get("loaded", [])]
        if not loaded:
            return []
        notes = []
        for ref in loaded:
            proc = run_docker(["image", "rm", ref])
            if proc.returncode == 0:
                notes.append(f"removed image {ref} (loaded by `anbar use`)")
            else:
                notes.append(f"kept image {ref}: {_last_line(proc.stderr)}")
        return notes


class RegistryService(Service):
    """Runs registry:2 from the kit and pushes every kit image into it."""

    name = "Docker registry"

    def __init__(self, kit: Kit, host: str, port: int) -> None:
        self.kit = kit
        self.host = host
        self.port = port

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.host in ("0.0.0.0", "") else self.host
        return f"{host}:{self.port} (docker pull {host}:{self.port}/IMAGE)"

    def start(self) -> None:
        check_daemon()
        images = [a for a in self.kit.by_ecosystem("docker") if a.meta.get("image")]
        for art in images:
            if run_docker(["image", "inspect", art.meta["image"]]).returncode != 0:
                run_docker(["load", "-i", str(self.kit.abspath(art.path))], check=True)
        run_docker(["rm", "-f", REGISTRY_CONTAINER])
        data_dir = self.kit.dir("docker", "registry-data")
        bind = "" if self.host in ("0.0.0.0", "") else f"{self.host}:"
        run_docker(
            ["run", "-d", "--rm", "--name", REGISTRY_CONTAINER, "-p", f"{bind}{self.port}:5000",
             "-v", f"{data_dir}:/var/lib/registry", REGISTRY_IMAGE],
            check=True,
        )
        for art in images:
            ref = art.meta["image"]
            if ref == normalize_ref(REGISTRY_IMAGE):
                continue
            _, path = split_reference(ref)
            path = path.removeprefix("library/")
            local = f"127.0.0.1:{self.port}/{path}"
            run_docker(["tag", ref, local], check=True)
            run_docker(["push", "--quiet", local], check=True)
            run_docker(["rmi", "--no-prune", local])

    def stop(self) -> None:
        run_docker(["stop", REGISTRY_CONTAINER])


def _label(path: Path, project: Path) -> str:
    try:
        return path.resolve().relative_to(project.resolve()).as_posix()
    except ValueError:
        return str(path)
