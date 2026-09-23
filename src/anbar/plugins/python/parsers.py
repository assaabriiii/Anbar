"""Parsers for Python dependency manifests.

Two kinds of results are produced:

* **locked** requirements (``name==version``) come from lock files that already
  contain the full dependency tree (``poetry.lock``, ``Pipfile.lock``,
  ``uv.lock``). They are downloaded with ``--no-deps``.
* **loose** requirements (any PEP 508 string) come from ``requirements*.txt``
  and ``pyproject.toml``. pip resolves their transitive dependencies.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def normalize(name: str) -> str:
    """PEP 503 name normalisation."""
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(spec: str) -> str | None:
    match = _NAME_RE.match(spec)
    return normalize(match.group(1)) if match else None


@dataclass
class Requirement:
    spec: str  # PEP 508 string, e.g. "django>=4.2" or "numpy==1.26.4"
    source: str  # manifest it came from
    locked: bool = False

    @property
    def name(self) -> str:
        return requirement_name(self.spec) or self.spec

    @property
    def pinned_version(self) -> str | None:
        match = re.match(r"^[A-Za-z0-9._-]+(?:\[[^\]]*\])?\s*===?\s*([^\s;,]+)\s*(?:;.*)?$", self.spec)
        return match.group(1) if match else None


@dataclass
class ParseResult:
    requirements: list[Requirement] = field(default_factory=list)
    constraints: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# requirements.txt
# ---------------------------------------------------------------------------


def _logical_lines(text: str) -> list[str]:
    lines: list[str] = []
    buf = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        buf += line
        lines.append(buf)
        buf = ""
    if buf:
        lines.append(buf)
    return lines


def _strip_comment(line: str) -> str:
    # A '#' starts a comment only at the line start or after whitespace (URLs may contain '#').
    return re.split(r"(?:^|\s)#", line, maxsplit=1)[0].strip()


def parse_requirements_txt(path: Path, label: str | None = None, _seen: set[Path] | None = None) -> ParseResult:
    path = Path(path)
    label = label or path.name
    seen = _seen if _seen is not None else set()
    result = ParseResult()
    resolved = path.resolve()
    if resolved in seen:
        return result
    seen.add(resolved)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        result.warnings.append(f"{label}: cannot read ({exc})")
        return result

    for line in _logical_lines(text):
        line = _strip_comment(line)
        if not line:
            continue
        if line.startswith(("-r ", "--requirement", "-r")) and not line.startswith("-r-"):
            target = re.sub(r"^(-r|--requirement)[=\s]*", "", line).strip()
            sub = parse_requirements_txt(path.parent / target, f"{label} -> {target}", seen)
            result.requirements += sub.requirements
            result.constraints += sub.constraints
            result.warnings += sub.warnings
            continue
        if line.startswith(("-c", "--constraint")):
            target = re.sub(r"^(-c|--constraint)[=\s]*", "", line).strip()
            result.constraints.append((path.parent / target).resolve())
            continue
        if line.startswith(("-e", "--editable")):
            target = re.sub(r"^(-e|--editable)[=\s]*", "", line).strip()
            if "://" in target and "#egg=" in target:
                result.warnings.append(f"{label}: editable VCS requirement '{target}' is skipped (not on an index)")
            # Local editable installs are the project itself: nothing to download.
            continue
        if line.startswith(("--index-url", "-i", "--extra-index-url", "--find-links", "-f")):
            result.warnings.append(
                f"{label}: '{line.split()[0]}' ignored; configure upstream indexes in [mirrors] pypi of anbar.toml"
            )
            continue
        if line.startswith("-"):
            # Global options such as --pre, --no-binary, --trusted-host.
            continue
        # Per-requirement options (--hash, --config-settings) follow the spec.
        spec = re.split(r"\s+--", line, maxsplit=1)[0].strip()
        if spec.startswith((".", "/", "file:")) or re.match(r"^[A-Za-z]:[\\/]", spec):
            continue  # local path
        if spec.startswith(("git+", "hg+", "svn+", "bzr+")):
            result.warnings.append(f"{label}: VCS requirement '{spec}' is skipped (vendor it or build a wheel)")
            continue
        if re.match(r"^https?://", spec):
            result.warnings.append(f"{label}: bare URL requirement '{spec}' is skipped; use 'name @ URL'")
            continue
        result.requirements.append(Requirement(spec=spec, source=label))
    return result


# ---------------------------------------------------------------------------
# pyproject.toml
# ---------------------------------------------------------------------------


def _poetry_constraint(name: str, value: Any) -> str | None:
    extras = ""
    markers = ""
    if isinstance(value, list):  # multiple-constraint dependencies: take all of them
        value = value[0] if value else "*"
    if isinstance(value, dict):
        if any(k in value for k in ("path", "git", "url", "file")):
            return None
        if value.get("extras"):
            extras = "[" + ",".join(value["extras"]) + "]"
        if value.get("markers"):
            markers = f"; {value['markers']}"
        value = value.get("version", "*")
    return f"{name}{extras}{poetry_version_to_pep440(str(value))}{markers}"


def poetry_version_to_pep440(constraint: str) -> str:
    """Translate Poetry's ``^`` / ``~`` / ``*`` syntax to PEP 440 specifiers."""
    out: list[str] = []
    for part in [p.strip() for p in constraint.split(",")]:
        if not part or part == "*":
            continue
        if " || " in part or "||" in part:
            return ""  # unions cannot be expressed; let pip pick the newest version
        if part.startswith("^"):
            version = part[1:].strip()
            nums = [int(x) if x.isdigit() else 0 for x in re.split(r"[.]", version)[:3]]
            while len(nums) < 3:
                nums.append(0)
            if nums[0] > 0:
                upper = f"{nums[0] + 1}.0.0"
            elif nums[1] > 0:
                upper = f"0.{nums[1] + 1}.0"
            else:
                upper = f"0.0.{nums[2] + 1}"
            out.append(f">={version},<{upper}")
        elif part.startswith("~") and not part.startswith("~="):
            version = part[1:].strip()
            nums = version.split(".")
            if len(nums) >= 2:
                upper = f"{nums[0]}.{int(nums[1]) + 1}.0" if nums[1].isdigit() else ""
            else:
                upper = f"{int(nums[0]) + 1}.0.0" if nums[0].isdigit() else ""
            out.append(f">={version}" + (f",<{upper}" if upper else ""))
        elif re.match(r"^[0-9]", part):
            if "*" in part:
                out.append("==" + part)
            else:
                out.append("==" + part)
        else:
            out.append(part.replace(" ", ""))
    return ",".join(out)


def parse_pyproject(path: Path, label: str | None = None) -> ParseResult:
    label = label or Path(path).name
    result = ParseResult()
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        result.warnings.append(f"{label}: cannot parse ({exc})")
        return result

    def add(spec: str) -> None:
        spec = spec.strip()
        if not spec:
            return
        if "@" in spec and re.search(r"@\s*(file:|\.|/)", spec):
            return  # local path dependency
        if re.search(r"@\s*git\+", spec):
            result.warnings.append(f"{label}: VCS dependency '{spec}' is skipped")
            return
        result.requirements.append(Requirement(spec=spec, source=label))

    project = data.get("project") or {}
    for spec in project.get("dependencies") or []:
        add(spec)
    for group in (project.get("optional-dependencies") or {}).values():
        for spec in group:
            add(spec)
    for group in (data.get("dependency-groups") or {}).values():
        for spec in group:
            if isinstance(spec, str):
                add(spec)

    poetry = (data.get("tool") or {}).get("poetry") or {}
    poetry_tables = [poetry.get("dependencies") or {}, poetry.get("dev-dependencies") or {}]
    for group in (poetry.get("group") or {}).values():
        poetry_tables.append(group.get("dependencies") or {})
    for table in poetry_tables:
        for name, value in table.items():
            if name.lower() == "python":
                continue
            spec = _poetry_constraint(name, value)
            if spec is None:
                continue
            add(spec)

    for spec in (data.get("build-system") or {}).get("requires") or []:
        add(spec)
    return result


# ---------------------------------------------------------------------------
# lock files
# ---------------------------------------------------------------------------


def parse_poetry_lock(path: Path, label: str | None = None) -> ParseResult:
    label = label or Path(path).name
    result = ParseResult()
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        result.warnings.append(f"{label}: cannot parse ({exc})")
        return result
    for pkg in data.get("package") or []:
        source = pkg.get("source") or {}
        if source.get("type") in ("git", "directory", "file", "url"):
            if source.get("type") == "git":
                result.warnings.append(f"{label}: git dependency '{pkg.get('name')}' is skipped")
            continue
        result.requirements.append(Requirement(f"{pkg['name']}=={pkg['version']}", label, locked=True))
    return result


def parse_uv_lock(path: Path, label: str | None = None) -> ParseResult:
    label = label or Path(path).name
    result = ParseResult()
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        result.warnings.append(f"{label}: cannot parse ({exc})")
        return result
    for pkg in data.get("package") or []:
        source = pkg.get("source") or {}
        if "registry" not in source:
            if "git" in source:
                result.warnings.append(f"{label}: git dependency '{pkg.get('name')}' is skipped")
            continue  # editable / virtual / directory / path: the project itself
        if not pkg.get("version"):
            continue
        result.requirements.append(Requirement(f"{pkg['name']}=={pkg['version']}", label, locked=True))
    return result


def parse_pipfile_lock(path: Path, label: str | None = None) -> ParseResult:
    label = label or Path(path).name
    result = ParseResult()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result.warnings.append(f"{label}: cannot parse ({exc})")
        return result
    for section in ("default", "develop"):
        for name, info in (data.get(section) or {}).items():
            if not isinstance(info, dict):
                continue
            if any(k in info for k in ("git", "path", "file", "editable")):
                if "git" in info:
                    result.warnings.append(f"{label}: git dependency '{name}' is skipped")
                continue
            version = str(info.get("version", "")).lstrip("=")
            if not version:
                result.requirements.append(Requirement(name, label))
                continue
            result.requirements.append(Requirement(f"{name}=={version}", label, locked=True))
    return result


LOCK_PARSERS = {
    "poetry.lock": parse_poetry_lock,
    "uv.lock": parse_uv_lock,
    "Pipfile.lock": parse_pipfile_lock,
}


def is_requirements_file(name: str) -> bool:
    lower = name.lower()
    return (lower.startswith("requirements") and lower.endswith((".txt", ".in"))) or (
        lower.endswith(".txt") and lower.startswith(("dev-requirements", "test-requirements"))
    )


def parse_manifest(path: Path, label: str) -> ParseResult:
    name = path.name
    if name in LOCK_PARSERS:
        return LOCK_PARSERS[name](path, label)
    if name == "pyproject.toml":
        return parse_pyproject(path, label)
    return parse_requirements_txt(path, label)
