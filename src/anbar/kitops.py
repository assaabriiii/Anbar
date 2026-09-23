"""Kit-level operations: verify, status, export and import."""

from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable

from anbar.errors import AnbarError, KitError
from anbar.kit import MANIFEST_NAME, Artifact, Kit, sha256_file

# Paths that are derived from tracked files or are runtime state, relative to the kit root.
DERIVED_PREFIXES = ("docs/site/", "docker/registry-data/")


def _is_ignored(rel: str) -> bool:
    if rel == MANIFEST_NAME or rel.endswith(".part") or rel.endswith(".tmp"):
        return True
    if any(part.startswith(".") for part in rel.split("/")):
        return True
    return rel.startswith(DERIVED_PREFIXES)


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


@dataclass
class VerifyReport:
    ok: int = 0
    missing: list[str] = field(default_factory=list)
    corrupt: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)
    partial: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return not self.missing and not self.corrupt


def verify_kit(kit: Kit, quick: bool = False, on_bytes: Callable[[int], None] | None = None) -> VerifyReport:
    report = VerifyReport()
    for art in sorted(kit.artifacts.values(), key=lambda a: a.path):
        path = kit.abspath(art.path)
        if not path.is_file():
            report.missing.append(art.path)
            if on_bytes:
                on_bytes(art.size)
            continue
        size = path.stat().st_size
        if size != art.size:
            report.corrupt.append(f"{art.path} (size {size}, expected {art.size})")
            if on_bytes:
                on_bytes(art.size)
            continue
        if not quick and _sha256(path, on_bytes) != art.sha256:
            report.corrupt.append(f"{art.path} (sha256 mismatch)")
            continue
        if quick and on_bytes:
            on_bytes(art.size)
        report.ok += 1
    tracked = set(kit.artifacts)
    for path in kit.root.rglob("*"):
        if path.is_dir() or path.is_symlink():
            continue
        rel = path.relative_to(kit.root).as_posix()
        if rel.endswith(".part"):
            report.partial.append(rel)
        elif rel not in tracked and not _is_ignored(rel):
            report.untracked.append(rel)
    return report


def _sha256(path: Path, on_bytes: Callable[[int], None] | None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(1 << 20)
            if not block:
                break
            digest.update(block)
            if on_bytes:
                on_bytes(len(block))
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@dataclass
class EcosystemStatus:
    name: str
    files: int = 0
    size: int = 0
    oldest: str | None = None
    newest: str | None = None
    details: list[str] = field(default_factory=list)

    def age_days(self, now: datetime | None = None) -> float | None:
        if not self.newest:
            return None
        now = now or datetime.now(timezone.utc)
        try:
            newest = datetime.fromisoformat(self.newest)
        except ValueError:
            return None
        if newest.tzinfo is None:
            newest = newest.replace(tzinfo=timezone.utc)
        return (now - newest).total_seconds() / 86400


def _details(name: str, arts: list[Artifact]) -> list[str]:
    if name == "python":
        from anbar.plugins.python.index import dist_name

        projects = {dist_name(a.path.rsplit("/", 1)[-1]) for a in arts}
        return [f"{len(projects - {None})} projects"]
    if name == "node":
        tarballs = [a for a in arts if a.path.endswith(".tgz")]
        names = {a.meta.get("package") for a in tarballs}
        return [f"{len(tarballs)} versions of {len(names)} packages"]
    if name == "docker":
        return sorted(a.meta.get("image", a.path) for a in arts)
    if name == "models":
        repos = sorted({a.meta["repo"] for a in arts if a.meta.get("repo")})
        files = sorted(a.path.rsplit("/", 1)[-1] for a in arts if a.meta.get("kind") == "url")
        return repos + files
    if name == "docs":
        return sorted({a.meta.get("name", a.path) for a in arts})
    return []


def kit_status(kit: Kit) -> list[EcosystemStatus]:
    groups: dict[str, list[Artifact]] = {}
    for art in kit.artifacts.values():
        groups.setdefault(art.ecosystem, []).append(art)
    result = []
    for name in sorted(groups):
        arts = groups[name]
        dates = sorted(a.downloaded_at for a in arts if a.downloaded_at)
        result.append(
            EcosystemStatus(
                name=name,
                files=len(arts),
                size=sum(a.size for a in arts),
                oldest=dates[0] if dates else None,
                newest=dates[-1] if dates else None,
                details=_details(name, arts),
            )
        )
    return result


# ---------------------------------------------------------------------------
# export / import
# ---------------------------------------------------------------------------


def _compression(path: Path) -> str:
    name = path.name.lower()
    if name.endswith((".tar.gz", ".tgz")):
        return "gz"
    if name.endswith((".tar.xz", ".txz")):
        return "xz"
    if name.endswith((".tar.bz2", ".tbz2")):
        return "bz2"
    if name.endswith(".tar"):
        return ""
    raise AnbarError(f"unsupported archive name: {path.name}", "use .tar (fastest), .tar.gz or .tar.xz")


def export_kit(kit: Kit, dest: Path, on_file: Callable[[str, int], None] | None = None) -> str:
    """Write the kit to a tar archive and a ``.sha256`` sidecar; return the archive's sha256."""
    mode = _compression(dest)
    dest = dest.resolve()
    try:
        dest.relative_to(kit.root)
        raise AnbarError("the export file cannot be inside the kit directory")
    except ValueError:
        pass
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    top = kit.root.name
    kit.save()
    with tarfile.open(tmp, "w:" + mode if mode else "w") as tf:
        tf.add(kit.root / MANIFEST_NAME, arcname=f"{top}/{MANIFEST_NAME}")
        for path in sorted(kit.root.rglob("*")):
            rel = path.relative_to(kit.root).as_posix()
            if rel == MANIFEST_NAME or path.is_dir() or rel.endswith((".part", ".tmp")):
                continue
            if rel.startswith(DERIVED_PREFIXES):
                continue
            tf.add(path, arcname=f"{top}/{rel}", recursive=False)
            if on_file:
                on_file(rel, 0 if path.is_symlink() else path.stat().st_size)
    digest = sha256_file(tmp)
    os.replace(tmp, dest)
    dest.with_name(dest.name + ".sha256").write_text(f"{digest}  {dest.name}\n", encoding="utf-8")
    return digest


def _check_member(member: tarfile.TarInfo, top: str) -> str:
    name = member.name.replace("\\", "/")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != top:
        raise KitError(f"refusing to import: unsafe path in archive: {member.name}")
    if member.issym() or member.islnk():
        target = PurePosixPath(member.linkname.replace("\\", "/"))
        resolved = (path.parent / target) if member.issym() else target
        parts: list[str] = []
        for part in resolved.parts:
            if part == "..":
                if not parts:
                    raise KitError(f"refusing to import: link escapes the kit: {member.name}")
                parts.pop()
            elif part != ".":
                parts.append(part)
        if target.is_absolute() or not parts or parts[0] != top:
            raise KitError(f"refusing to import: link escapes the kit: {member.name}")
    elif not (member.isfile() or member.isdir()):
        raise KitError(f"refusing to import: unsupported member type: {member.name}")
    return name


@dataclass
class ImportResult:
    kit: Kit
    merged: bool
    added: int


def import_kit(archive: Path, dest: Path | None, check_sidecar: bool = True) -> ImportResult:
    archive = archive.resolve()
    if not archive.is_file():
        raise AnbarError(f"file not found: {archive}")
    sidecar = archive.with_name(archive.name + ".sha256")
    if check_sidecar and sidecar.is_file():
        expected = sidecar.read_text(encoding="utf-8").split()[0]
        if sha256_file(archive) != expected:
            raise KitError(
                f"{archive.name} does not match {sidecar.name}; the copy is damaged",
                "copy the file again (USB drives and flaky networks corrupt large files)",
            )
    try:
        tf = tarfile.open(archive)
    except (tarfile.TarError, OSError) as exc:
        raise KitError(f"{archive.name} is not a readable tar archive: {exc}") from exc
    with tf:
        members = tf.getmembers()
        if not members:
            raise KitError(f"{archive.name} is empty")
        top = PurePosixPath(members[0].name.replace("\\", "/")).parts[0]
        for member in members:
            _check_member(member, top)
        if f"{top}/{MANIFEST_NAME}" not in {m.name.replace("\\", "/") for m in members}:
            raise KitError(f"{archive.name} is not an Anbar kit (no {MANIFEST_NAME})")
        target = (dest or Path.cwd() / top).resolve()
        staging = target.parent / f".{target.name}.importing"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        try:
            if hasattr(tarfile, "data_filter"):
                tf.extractall(staging, members=members, filter="fully_trusted")  # validated above
            else:
                tf.extractall(staging, members=members)  # noqa: S202 - validated above
        except (tarfile.TarError, OSError) as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise KitError(f"cannot extract {archive.name}: {exc}") from exc
    incoming = Kit.open(staging / top)  # also checks the format version

    if not (target / MANIFEST_NAME).exists():
        if target.exists() and any(target.iterdir()):
            shutil.rmtree(staging, ignore_errors=True)
            raise AnbarError(f"{target} exists and is not an Anbar kit", "choose another directory with --to")
        if target.exists():
            target.rmdir()
        shutil.move(str(staging / top), str(target))
        shutil.rmtree(staging, ignore_errors=True)
        return ImportResult(Kit.open(target), merged=False, added=len(incoming.artifacts))

    # Merge into an existing kit: newer artifacts win, nothing is deleted.
    existing = Kit.open(target)
    added = 0
    for rel, art in incoming.artifacts.items():
        current = existing.get(rel)
        if current is not None and current.sha256 == art.sha256 and existing.abspath(rel).exists():
            continue
        src = incoming.abspath(rel)
        dst = existing.abspath(rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        shutil.move(str(src), str(dst))
        existing.artifacts[rel] = art
        added += 1
    # Symlinks (Hugging Face snapshots) and untracked state files.
    for path in sorted(incoming.root.rglob("*")):
        rel = path.relative_to(incoming.root).as_posix()
        dst = existing.root / rel
        if rel == MANIFEST_NAME or path.is_dir() or dst.exists() or dst.is_symlink():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dst))
    for project in incoming.projects:
        if project not in existing.projects:
            existing.projects.append(project)
    existing.save()
    shutil.rmtree(staging, ignore_errors=True)
    return ImportResult(existing, merged=True, added=added)
