"""Parsers for ``package-lock.json``, ``yarn.lock`` (classic and Berry) and ``pnpm-lock.yaml``."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

NPM_REGISTRY = "https://registry.npmjs.org"
_DEFAULT_HOSTS = ("registry.npmjs.org", "registry.yarnpkg.com", "registry.npmjs.com")


@dataclass(frozen=True)
class NodePackage:
    name: str
    version: str
    tarball: str
    integrity: str | None = None

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"

    @property
    def filename(self) -> str:
        return f"{self.name.split('/')[-1]}-{self.version}.tgz"


@dataclass
class NodeParseResult:
    packages: list[NodePackage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def default_tarball(name: str, version: str, registry: str = NPM_REGISTRY) -> str:
    return f"{registry.rstrip('/')}/{name}/-/{name.split('/')[-1]}-{version}.tgz"


def is_default_registry(url: str) -> bool:
    return any(f"//{host}/" in url for host in _DEFAULT_HOSTS)


def _is_remote(url: str | None) -> bool:
    return bool(url) and url.startswith(("https://", "http://"))


def split_name_version(spec: str) -> tuple[str, str]:
    """Split ``@scope/name@1.2.3`` into its name and version (or range)."""
    at = spec.rfind("@")
    if at <= 0:
        return spec, ""
    return spec[:at], spec[at + 1 :]


# ---------------------------------------------------------------------------
# package-lock.json / npm-shrinkwrap.json
# ---------------------------------------------------------------------------


def parse_package_lock(path: Path, label: str | None = None) -> NodeParseResult:
    label = label or Path(path).name
    result = NodeParseResult()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result.warnings.append(f"{label}: cannot parse ({exc})")
        return result

    packages = data.get("packages")
    if isinstance(packages, dict) and packages:
        for key, info in packages.items():
            if not key or "node_modules/" not in key or not isinstance(info, dict):
                continue
            if info.get("link") or info.get("inBundle") or info.get("bundled"):
                continue
            name = info.get("name") or key.rsplit("node_modules/", 1)[1]
            _add(result, label, name, info.get("version"), info.get("resolved"), info.get("integrity"))
    else:
        _walk_v1(result, label, data.get("dependencies") or {})
    return result


def _walk_v1(result: NodeParseResult, label: str, deps: dict) -> None:
    for name, info in deps.items():
        if not isinstance(info, dict):
            continue
        if not info.get("bundled"):
            version = info.get("version", "")
            if version.startswith("npm:"):  # alias: "npm:real-name@1.0.0"
                name, version = split_name_version(version[4:])
            _add(result, label, name, version, info.get("resolved"), info.get("integrity"))
        _walk_v1(result, label, info.get("dependencies") or {})


def _add(result: NodeParseResult, label: str, name: str, version: str | None, resolved: str | None,
         integrity: str | None) -> None:
    if not version:
        return
    if resolved and not _is_remote(resolved):
        if resolved.startswith(("git", "github:")):
            result.warnings.append(f"{label}: git dependency {name} is skipped")
        return  # file: / link: / workspace packages are part of the project
    if not resolved:
        if re.match(r"^\d", version):
            resolved = default_tarball(name, version)
        else:
            return
    result.packages.append(NodePackage(name, version, resolved, integrity))


# ---------------------------------------------------------------------------
# yarn.lock
# ---------------------------------------------------------------------------


def parse_yarn_lock(path: Path, label: str | None = None) -> NodeParseResult:
    label = label or Path(path).name
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return NodeParseResult(warnings=[f"{label}: cannot read ({exc})"])
    if "__metadata:" in text:
        return _parse_yarn_berry(text, label)
    return _parse_yarn_classic(text, label)


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _parse_yarn_classic(text: str, label: str) -> NodeParseResult:
    result = NodeParseResult()
    entry: dict[str, str] = {}
    specs: list[str] = []

    def flush() -> None:
        if not specs or "version" not in entry:
            return
        first = specs[0]
        if "@npm:" in first:  # alias: alias@npm:real-name@^1.0.0
            name, _ = split_name_version(first.split("@npm:", 1)[1])
        else:
            name, _ = split_name_version(first)
        resolved = entry.get("resolved")
        integrity = entry.get("integrity")
        if resolved and "#" in resolved:
            resolved, sha1 = resolved.split("#", 1)
            if not integrity and re.fullmatch(r"[0-9a-f]{40}", sha1):
                import base64

                integrity = "sha1-" + base64.b64encode(bytes.fromhex(sha1)).decode()
        _add(result, label, name, entry["version"], resolved, integrity)

    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if not raw.startswith(" "):
            flush()
            entry = {}
            specs = [_unquote(s) for s in raw.rstrip(":").split(",")]
            continue
        line = raw.strip()
        if raw.startswith("    "):
            continue  # nested dependency lists
        key, _, value = line.partition(" ")
        if key.endswith(":"):
            continue  # "dependencies:" block header
        entry[_unquote(key)] = _unquote(value)
    flush()
    return result


def _parse_yarn_berry(text: str, label: str) -> NodeParseResult:
    result = NodeParseResult()
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        result.warnings.append(f"{label}: cannot parse ({exc})")
        return result
    for key, info in data.items():
        if key == "__metadata" or not isinstance(info, dict):
            continue
        resolution = str(info.get("resolution", ""))
        name, ref = split_name_version(resolution)
        if not ref.startswith("npm:"):
            if ref.startswith(("git", "https://github")) or "github.com" in ref:
                result.warnings.append(f"{label}: git dependency {name} is skipped")
            continue  # workspace:, patch:, file:, link:, portal:
        version = ref[4:]
        if version.startswith("https://") or version.startswith("http://"):
            result.packages.append(NodePackage(name, str(info.get("version", "")), version))
            continue
        # Berry's checksum covers its own zip cache, not the tarball, so it cannot be verified here.
        result.packages.append(NodePackage(name, version, default_tarball(name, version)))
    return result


# ---------------------------------------------------------------------------
# pnpm-lock.yaml
# ---------------------------------------------------------------------------


def _pnpm_key(key: str) -> tuple[str, str] | None:
    key = key.lstrip("/")
    key = re.sub(r"\(.*\)$", "", key)  # v6+/v9 peer suffix: name@1.0.0(react@18.0.0)
    v5 = re.match(r"^((?:@[^/]+/)?[^/@]+)/(\d[^/]*)$", key)  # v5: name/1.0.0 or @scope/name/1.0.0_peer
    if v5:
        name, version = v5.group(1), v5.group(2)
    elif "@" in key[1:]:
        name, version = split_name_version(key)
    else:
        return None
    version = version.split("_", 1)[0]
    if not name or not version:
        return None
    return name, version


def parse_pnpm_lock(path: Path, label: str | None = None) -> NodeParseResult:
    label = label or Path(path).name
    result = NodeParseResult()
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        result.warnings.append(f"{label}: cannot parse ({exc})")
        return result
    for key, info in (data.get("packages") or {}).items():
        if not isinstance(info, dict):
            continue
        resolution = info.get("resolution") or {}
        if resolution.get("directory") or resolution.get("type") == "directory":
            continue
        if resolution.get("repo") or resolution.get("type") == "git":
            result.warnings.append(f"{label}: git dependency {key} is skipped")
            continue
        parsed = _pnpm_key(str(key))
        name = info.get("name") or (parsed[0] if parsed else None)
        version = str(info.get("version") or (parsed[1] if parsed else ""))
        if not name or not version:
            continue
        tarball = resolution.get("tarball")
        if tarball and not _is_remote(tarball):
            continue
        if not re.match(r"^\d", version) and not tarball:
            continue
        result.packages.append(
            NodePackage(name, version, tarball or default_tarball(name, version), resolution.get("integrity"))
        )
    return result


PARSERS = {
    "package-lock.json": parse_package_lock,
    "npm-shrinkwrap.json": parse_package_lock,
    "yarn.lock": parse_yarn_lock,
    "pnpm-lock.yaml": parse_pnpm_lock,
}
