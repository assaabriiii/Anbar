"""Offline documentation: HTML archives (zip / tar) listed in anbar.toml, served as static files.

Archives are kept in ``docs/archives/<name>/`` (tracked in the manifest) and
extracted to ``docs/site/<name>/``, which is derived data and can always be
rebuilt from the archive.
"""

from __future__ import annotations

from pathlib import Path

from anbar.config import Config
from anbar.errors import AnbarError
from anbar.kit import Kit
from anbar.network import run_parallel
from anbar.plugins import register
from anbar.plugins.base import Changes, Context, FetchResult, Plan, PlanItem, Plugin, Service, UseContext
from anbar.plugins.files import extract_archive, filename_from_url, is_archive, shared_file_server


def site_dir(kit: Kit, name: str) -> Path:
    return kit.root / "docs" / "site" / name


def ensure_extracted(kit: Kit) -> None:
    """Rebuild ``docs/site`` from the archives when it is missing (e.g. after import)."""
    for art in kit.by_ecosystem("docs"):
        name = art.meta.get("name")
        if art.meta.get("extracted") and name and not site_dir(kit, name).is_dir():
            extract_archive(kit.abspath(art.path), site_dir(kit, name))


@register
class DocsPlugin(Plugin):
    name = "docs"
    title = "Docs"

    def detect(self, project: Path, config: Config) -> list[Path]:
        return []  # documentation packages are listed explicitly in anbar.toml

    def plan(self, project: Path, config: Config, manifests: list[Path]) -> Plan:
        plan = Plan(self.name)
        for entry in config.docs:
            plan.items.append(
                PlanItem(
                    name=entry.name,
                    source="anbar.toml",
                    data={"url": entry.url, "sha256": entry.sha256, "extract": entry.extract},
                )
            )
        return plan

    def estimate(self, plan: Plan, ctx: Context) -> None:
        for item in plan.items:
            item.size = ctx.net.content_length(item.data["url"])

    def fetch(self, plan: Plan, kit: Kit, ctx: Context) -> FetchResult:
        result = FetchResult()
        ctx.progress.start("Documentation", len(plan.items))

        def fetch_one(item: PlanItem) -> bool:
            filename = filename_from_url(item.data["url"])
            archive = kit.dir("docs", "archives", item.name) / filename
            extract = bool(item.data["extract"]) and is_archive(filename)
            target = site_dir(kit, item.name)
            if not ctx.refresh and kit.has_valid(archive, item.data.get("sha256")):
                if extract and not target.is_dir():
                    extract_archive(archive, target)
                return False
            res = ctx.net.download(item.data["url"], archive, sha256=item.data.get("sha256"))
            if extract:
                extract_archive(archive, target)
            else:
                target.mkdir(parents=True, exist_ok=True)
                link = target / filename
                link.unlink(missing_ok=True)
                try:
                    link.hardlink_to(archive)
                except OSError:
                    import shutil

                    shutil.copy2(archive, link)
            kit.add(archive, self.name, res.url, sha256=res.sha256,
                    meta={"name": item.name, "extracted": extract})
            return True

        for item, downloaded, exc in run_parallel(plan.items, fetch_one, ctx.config.network.workers,
                                                  on_done=lambda *_: ctx.progress.advance()):
            if exc is not None:
                result.failed.append(f"{item.name}: {exc}")
            elif downloaded:
                result.downloaded += 1
            else:
                result.skipped += 1
        ctx.progress.finish()
        return result

    def serve(self, kit: Kit, config: Config, host: str, ports: dict[str, int]) -> list[Service]:
        try:
            ensure_extracted(kit)
        except AnbarError as exc:
            from anbar.console import warn

            warn(f"docs: {exc}")
        site = kit.root / "docs" / "site"
        if not site.is_dir():
            return []
        return [shared_file_server(kit, host, ports["files"], "docs", site)]

    def configure(self, ctx: UseContext, changes: Changes) -> list[str]:
        if ctx.kit is None:
            return []
        names = sorted({a.meta.get("name") for a in ctx.kit.by_ecosystem(self.name) if a.meta.get("name")})
        if not names:
            return []
        return [f"docs: {', '.join(names)} at {ctx.base(ctx.files_port)}/docs/ (while `anbar serve` runs)"]
