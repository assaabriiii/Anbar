"""Models: Hugging Face repositories and arbitrary weight files (YOLO, OpenVINO, ...).

Hugging Face repos are stored in a regular ``huggingface_hub`` cache inside
the kit, so ``HF_HOME=KIT/models/hf`` plus ``HF_HUB_OFFLINE=1`` is all that
libraries such as transformers or diffusers need. Access tokens are read from
the environment (``HF_TOKEN``) by ``huggingface_hub`` and are never written
to the kit.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from anbar.config import Config, HuggingFaceModel
from anbar.errors import AnbarError
from anbar.kit import Kit
from anbar.network import proxy_env, run_parallel
from anbar.plugins import register
from anbar.plugins.base import Changes, Context, FetchResult, Plan, PlanItem, Plugin, Service, UseContext
from anbar.plugins.files import filename_from_url, shared_file_server

HF_DEFAULT_ENDPOINT = "https://huggingface.co"


def _hf_key(model: HuggingFaceModel) -> str:
    data = [model.repo, model.revision, model.repo_type, model.allow_patterns, model.ignore_patterns]
    return hashlib.sha256(json.dumps(data).encode()).hexdigest()[:16]


def _matches(name: str, allow: list[str] | None, ignore: list[str] | None) -> bool:
    if allow and not any(fnmatch.fnmatch(name, p) for p in allow):
        return False
    return not (ignore and any(fnmatch.fnmatch(name, p) for p in ignore))


@contextmanager
def _env(values: dict[str, str]) -> Iterator[None]:
    old = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _import_hf():
    try:
        import huggingface_hub
    except ImportError as exc:
        raise AnbarError(
            "Hugging Face models are configured but huggingface_hub is not installed",
            "run `pipx inject anbar huggingface_hub` or `pip install anbar[models]`",
        ) from exc
    return huggingface_hub


@register
class ModelsPlugin(Plugin):
    name = "models"
    title = "Models"

    def detect(self, project: Path, config: Config) -> list[Path]:
        return []  # models are listed explicitly in anbar.toml

    def plan(self, project: Path, config: Config, manifests: list[Path]) -> Plan:
        plan = Plan(self.name)
        for model in config.models.huggingface:
            plan.items.append(
                PlanItem(
                    name=model.repo,
                    version=model.revision,
                    source="anbar.toml (huggingface)",
                    data={"kind": "hf", "model": model},
                )
            )
        for entry in config.models.urls:
            filename = entry.filename or filename_from_url(entry.url)
            plan.items.append(
                PlanItem(
                    name=entry.name or filename,
                    source="anbar.toml (url)",
                    data={"kind": "url", "url": entry.url, "filename": filename, "sha256": entry.sha256,
                          "subdir": entry.name},
                )
            )
        return plan

    def estimate(self, plan: Plan, ctx: Context) -> None:
        for item in plan.items:
            if item.data["kind"] == "url":
                item.size = ctx.net.content_length(item.data["url"])
                continue
            try:
                hf = _import_hf()
            except AnbarError:
                continue
            model: HuggingFaceModel = item.data["model"]
            endpoint = (ctx.config.mirrors.huggingface or [HF_DEFAULT_ENDPOINT])[0]
            try:
                api = hf.HfApi(endpoint=endpoint)
                info = api.repo_info(model.repo, repo_type=model.repo_type, revision=model.revision,
                                     files_metadata=True)
                item.size = sum(
                    (s.size or 0)
                    for s in (info.siblings or [])
                    if _matches(s.rfilename, model.allow_patterns, model.ignore_patterns)
                )
            except Exception:  # noqa: BLE001 - estimates are best effort
                item.size = None

    def fetch(self, plan: Plan, kit: Kit, ctx: Context) -> FetchResult:
        result = FetchResult()
        files_dir = kit.dir("models", "files")
        ctx.progress.start("Models", len(plan.items))
        url_items = [i for i in plan.items if i.data["kind"] == "url"]
        hf_items = [i for i in plan.items if i.data["kind"] == "hf"]

        def fetch_url(item: PlanItem) -> bool:
            sub = item.data.get("subdir")
            dest = (files_dir / sub if sub else files_dir) / item.data["filename"]
            if not ctx.refresh and kit.has_valid(dest, item.data.get("sha256")):
                return False
            res = ctx.net.download(item.data["url"], dest, sha256=item.data.get("sha256"))
            kit.add(dest, self.name, res.url, sha256=res.sha256, meta={"kind": "url"})
            return True

        for item, downloaded, exc in run_parallel(url_items, fetch_url, ctx.config.network.workers,
                                                  on_done=lambda *_: ctx.progress.advance()):
            if exc is not None:
                result.failed.append(f"{item.name}: {exc}")
            elif downloaded:
                result.downloaded += 1
            else:
                result.skipped += 1

        for item in hf_items:
            try:
                count = self._fetch_hf(item.data["model"], kit, ctx)
            except AnbarError as exc:
                result.failed.append(f"{item.name}: {exc}")
            else:
                if count is None:
                    result.skipped += 1
                else:
                    result.downloaded += count
            ctx.progress.advance()
        ctx.progress.finish()
        return result

    def _fetch_hf(self, model: HuggingFaceModel, kit: Kit, ctx: Context) -> int | None:
        key = _hf_key(model)
        existing = [a for a in kit.by_ecosystem(self.name) if a.meta.get("hf_key") == key]
        if not ctx.refresh and existing and all(kit.abspath(a.path).exists() for a in existing):
            return None
        hf = _import_hf()
        hub_dir = kit.dir("models", "hf", "hub")
        endpoints = ctx.config.mirrors.huggingface + [HF_DEFAULT_ENDPOINT]
        errors = []
        env = proxy_env(ctx.config.proxy)
        env["HF_HUB_DISABLE_TELEMETRY"] = "1"
        if ctx.config.network.ca_bundle:
            env["REQUESTS_CA_BUNDLE"] = env["SSL_CERT_FILE"] = ctx.config.network.ca_bundle
        for endpoint in dict.fromkeys(e.rstrip("/") for e in endpoints):
            try:
                with _env(env):
                    snapshot = hf.snapshot_download(
                        repo_id=model.repo,
                        repo_type=model.repo_type,
                        revision=model.revision,
                        cache_dir=str(hub_dir),
                        allow_patterns=model.allow_patterns,
                        ignore_patterns=model.ignore_patterns,
                        endpoint=endpoint,
                    )
                break
            except Exception as exc:  # noqa: BLE001 - huggingface_hub raises many types
                errors.append(f"{endpoint}: {type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}")
        else:
            raise AnbarError(
                "; ".join(errors),
                "Hugging Face unreachable? try `--mirror huggingface=https://hf-mirror.example`; "
                "for gated models export HF_TOKEN (it is never stored in the kit)",
            )
        repo_dir = Path(snapshot).parent.parent  # .../models--org--name/snapshots/<rev>
        count = 0
        for path in sorted(repo_dir.rglob("*")):
            if path.is_symlink() or not path.is_file() or path.name.endswith(".lock") or ".no_exist" in path.parts:
                continue
            rel = kit.rel(path)
            if kit.get(rel) is None or kit.get(rel).size != path.stat().st_size:
                count += 1
            kit.add(path, self.name, f"{model.repo}@{model.revision or 'main'}",
                    meta={"kind": "hf", "repo": model.repo, "hf_key": key})
        return count

    def serve(self, kit: Kit, config: Config, host: str, ports: dict[str, int]) -> list[Service]:
        files_dir = kit.root / "models" / "files"
        if not files_dir.is_dir() or not any(files_dir.iterdir()):
            return []
        return [shared_file_server(kit, host, ports["files"], "models", files_dir)]

    def configure(self, ctx: UseContext, changes: Changes) -> list[str]:
        notes = []
        if ctx.kit is not None and (ctx.kit.root / "models" / "hf").is_dir():
            hf_home = str(ctx.kit.root / "models" / "hf")
            changes.set_env("HF_HOME", hf_home)
            changes.set_env("HF_HUB_OFFLINE", "1")
            changes.set_env("TRANSFORMERS_OFFLINE", "1")
            notes.append(f"Hugging Face: HF_HOME={hf_home}, HF_HUB_OFFLINE=1 (environment script)")
        files_dir = ctx.kit.root / "models" / "files" if ctx.kit else None
        if files_dir is not None and files_dir.is_dir():
            notes.append(f"weight files: {files_dir} (also served at {ctx.base(ctx.files_port)}/models/)")
        return notes
