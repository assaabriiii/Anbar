"""Find base images in Dockerfiles and compose files."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_VAR_RE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-+?])([^}]*))?\}|([A-Za-z_][A-Za-z0-9_]*))")


@dataclass(frozen=True)
class ImageRef:
    ref: str
    platform: str | None = None


@dataclass
class DockerParseResult:
    images: list[ImageRef] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dockerfiles: list[Path] = field(default_factory=list)  # referenced by compose `build:`


def is_dockerfile(name: str) -> bool:
    lower = name.lower()
    return (
        lower in ("dockerfile", "containerfile")
        or lower.endswith(".dockerfile")
        or lower.startswith(("dockerfile.", "containerfile."))
    )


def is_compose_file(name: str) -> bool:
    lower = name.lower()
    return bool(re.fullmatch(r"(docker-)?compose(\.[\w.-]+)?\.ya?ml", lower))


def normalize_ref(ref: str) -> str:
    """Add ``:latest`` when a reference has neither tag nor digest."""
    if "@" in ref:
        return ref
    last = ref.rsplit("/", 1)[-1]
    return ref if ":" in last else ref + ":latest"


def substitute(text: str, variables: dict[str, str]) -> tuple[str, list[str]]:
    """Expand ``$VAR``, ``${VAR}``, ``${VAR:-default}``, ``${VAR:+alt}``; return unresolved names."""
    missing: list[str] = []

    def repl(match: re.Match) -> str:
        name = match.group(1) or match.group(4)
        op, arg = match.group(2), match.group(3) or ""
        value = variables.get(name)
        if op in (":-", "-"):
            if value is None or (op == ":-" and value == ""):
                return substitute(arg, variables)[0]
            return value
        if op in (":+", "+"):
            return substitute(arg, variables)[0] if value else ""
        if op in (":?", "?"):
            if not value:
                missing.append(name)
                return match.group(0)
            return value
        if value is None:
            missing.append(name)
            return match.group(0)
        return value

    return _VAR_RE.sub(repl, text.replace("$$", "\x00")).replace("\x00", "$"), missing


def _instructions(text: str) -> list[tuple[int, str]]:
    """Split a Dockerfile into (line number, instruction) with continuations joined."""
    escape = "\\"
    match = re.match(r"^\s*#\s*escape\s*=\s*(\S)", text, re.IGNORECASE)
    if match:
        escape = match.group(1)
    result: list[tuple[int, str]] = []
    buf, start = "", 0
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if not buf and (not line.strip() or line.lstrip().startswith("#")):
            continue
        if buf and line.lstrip().startswith("#"):
            continue  # comments inside continuations are ignored
        if not buf:
            start = number
        if line.endswith(escape):
            buf += line[:-1] + " "
            continue
        buf += line
        result.append((start, buf.strip()))
        buf = ""
    if buf:
        result.append((start, buf.strip()))
    return result


def parse_dockerfile(path: Path, label: str, build_args: dict[str, str] | None = None) -> DockerParseResult:
    result = DockerParseResult()
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        result.warnings.append(f"{label}: cannot read ({exc})")
        return result
    return parse_dockerfile_text(text, label, build_args)


def parse_dockerfile_text(text: str, label: str, build_args: dict[str, str] | None = None) -> DockerParseResult:
    result = DockerParseResult()
    args: dict[str, str] = {}
    overrides = dict(build_args or {})
    stages: set[str] = set()
    seen_from = False
    for number, instruction in _instructions(text):
        keyword, _, rest = instruction.partition(" ")
        keyword = keyword.upper()
        if keyword == "ARG" and not seen_from:
            # Only ARGs declared before the first FROM can be used in FROM lines.
            for decl in rest.split():
                name, eq, default = decl.partition("=")
                if name in overrides:
                    args[name] = overrides[name]
                elif eq:
                    args[name] = substitute(default.strip("\"'"), args)[0]
            continue
        if keyword == "FROM":
            seen_from = True
            tokens = rest.split()
            platform = None
            while tokens and tokens[0].startswith("--"):
                flag = tokens.pop(0)
                if flag.startswith("--platform="):
                    platform = substitute(flag.split("=", 1)[1], args)[0]
            if not tokens:
                continue
            image = tokens[0]
            if len(tokens) >= 3 and tokens[1].upper() == "AS":
                stages.add(tokens[2].lower())
            _add_image(result, label, number, image, args, stages, platform, instruction)
            continue
        if keyword in ("COPY", "ADD") and "--from=" in rest:
            match = re.search(r"--from=(\S+)", rest)
            if match:
                source = match.group(1)
                if not source.isdigit():
                    _add_image(result, label, number, source, args, stages, None, instruction)
    return result


def _add_image(result, label, number, image, args, stages, platform, instruction) -> None:
    resolved, missing = substitute(image, args)
    if missing:
        result.warnings.append(
            f"{label}:{number}: cannot resolve ${{{missing[0]}}} in '{instruction}'; "
            f"set it in [docker] build_args of anbar.toml or give the ARG a default"
        )
        return
    if not resolved or resolved.lower() in stages or resolved.lower() == "scratch":
        return
    if platform and "$" in platform:
        platform = None  # e.g. $BUILDPLATFORM: the native platform
    result.images.append(ImageRef(normalize_ref(resolved), platform))


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def parse_compose(path: Path, label: str, use_environ: bool = True) -> DockerParseResult:
    result = DockerParseResult()
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        result.warnings.append(f"{label}: cannot parse ({exc})")
        return result
    variables = _read_env_file(Path(path).parent / ".env")
    if use_environ:
        variables.update(os.environ)
    services = data.get("services") if isinstance(data, dict) else None
    if not isinstance(services, dict):
        return result
    for name, service in services.items():
        if not isinstance(service, dict):
            continue
        build = service.get("build")
        if build is not None:
            # The image name of a built service is a local tag, not something to pull.
            context = build if isinstance(build, str) else (build.get("context") or ".")
            dockerfile = "Dockerfile" if isinstance(build, str) else (build.get("dockerfile") or "Dockerfile")
            context = substitute(str(context), variables)[0]
            dockerfile = substitute(str(dockerfile), variables)[0]
            if "://" not in context:
                candidate = (Path(path).parent / context / dockerfile).resolve()
                if candidate.is_file():
                    result.dockerfiles.append(candidate)
            continue
        image = service.get("image")
        if not image:
            continue
        resolved, missing = substitute(str(image), variables)
        if missing:
            result.warnings.append(
                f"{label}: service '{name}' image '{image}' uses unset variable ${{{missing[0]}}}; "
                "add it to .env or give it a default"
            )
            continue
        platform = service.get("platform")
        result.images.append(ImageRef(normalize_ref(resolved), str(platform) if platform else None))
    return result
