"""Loading and validating ``anbar.toml``.

Every section is optional; a project without ``anbar.toml`` gets the defaults.
Unknown keys are reported as warnings so typos do not go unnoticed.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from anbar.console import warn
from anbar.errors import ConfigError

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Python 3.10 only
    import tomli as tomllib

CONFIG_FILENAME = "anbar.toml"


@dataclass
class PythonTarget:
    """A platform / interpreter combination to download wheels for."""

    platform: str | None = None
    python_version: str | None = None
    implementation: str | None = None
    abi: str | None = None

    def label(self) -> str:
        parts = [self.platform or "native", f"py{self.python_version}" if self.python_version else ""]
        return "-".join(p for p in parts if p)


@dataclass
class PythonConfig:
    extra: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    targets: list[PythonTarget] = field(default_factory=list)
    include_build_tools: bool = True
    only_binary: bool = False


@dataclass
class NodeConfig:
    extra: list[str] = field(default_factory=list)


@dataclass
class DockerConfig:
    extra: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    platform: str | None = None
    build_args: dict[str, str] = field(default_factory=dict)
    registry: bool = False


@dataclass
class HuggingFaceModel:
    repo: str
    revision: str | None = None
    repo_type: str = "model"
    allow_patterns: list[str] | None = None
    ignore_patterns: list[str] | None = None


@dataclass
class UrlFile:
    url: str
    filename: str | None = None
    sha256: str | None = None
    name: str | None = None


@dataclass
class ModelsConfig:
    huggingface: list[HuggingFaceModel] = field(default_factory=list)
    urls: list[UrlFile] = field(default_factory=list)


@dataclass
class DocsEntry:
    name: str
    url: str
    sha256: str | None = None
    extract: bool = True


@dataclass
class MirrorsConfig:
    pypi: list[str] = field(default_factory=list)
    npm: list[str] = field(default_factory=list)
    docker: list[str] = field(default_factory=list)
    huggingface: list[str] = field(default_factory=list)


@dataclass
class ProxyConfig:
    http: str | None = None
    https: str | None = None
    socks: str | None = None
    no_proxy: list[str] = field(default_factory=list)

    def any(self) -> bool:
        return bool(self.http or self.https or self.socks)


@dataclass
class ServeConfig:
    host: str = "127.0.0.1"
    pypi_port: int = 3141
    npm_port: int = 4873
    files_port: int = 8765
    registry_port: int = 5000


@dataclass
class NetworkConfig:
    workers: int = 8
    retries: int = 4
    timeout: float = 60.0
    ca_bundle: str | None = None


@dataclass
class Config:
    python: PythonConfig = field(default_factory=PythonConfig)
    node: NodeConfig = field(default_factory=NodeConfig)
    docker: DockerConfig = field(default_factory=DockerConfig)
    models: ModelsConfig = field(default_factory=ModelsConfig)
    docs: list[DocsEntry] = field(default_factory=list)
    mirrors: MirrorsConfig = field(default_factory=MirrorsConfig)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    serve: ServeConfig = field(default_factory=ServeConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    ecosystems: list[str] | None = None
    path: Path | None = None


# --------------------------------------------------------------------------
# parsing helpers
# --------------------------------------------------------------------------


def _check_keys(section: str, data: dict[str, Any], allowed: set[str]) -> None:
    for key in data:
        if key not in allowed:
            warn(f"anbar.toml: unknown key '{key}' in [{section}] (ignored)")


def _str_list(section: str, key: str, value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"anbar.toml: [{section}] {key} must be a list of strings")
    return list(value)


def _table(section: str, value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"anbar.toml: [{section}] must be a table")
    return value


def _port(section: str, key: str, value: Any, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or not 0 <= value <= 65535:
        raise ConfigError(f"anbar.toml: [{section}] {key} must be a port number (0-65535)")
    return value


def _parse_python(data: dict[str, Any]) -> PythonConfig:
    _check_keys(
        "python",
        data,
        {"extra", "exclude", "targets", "platforms", "python_versions", "include_build_tools", "only_binary"},
    )
    targets: list[PythonTarget] = []
    for i, raw in enumerate(data.get("targets") or []):
        raw = _table(f"python.targets[{i}]", raw)
        _check_keys("python.targets", raw, {"platform", "python_version", "implementation", "abi"})
        targets.append(
            PythonTarget(
                platform=raw.get("platform"),
                python_version=str(raw["python_version"]) if raw.get("python_version") is not None else None,
                implementation=raw.get("implementation"),
                abi=raw.get("abi"),
            )
        )
    # Shorthand: the cross product of `platforms` and `python_versions`.
    platforms = _str_list("python", "platforms", data.get("platforms"))
    versions = [str(v) for v in (data.get("python_versions") or [])]
    if platforms or versions:
        for plat in platforms or [None]:
            for ver in versions or [None]:
                targets.append(PythonTarget(platform=plat, python_version=ver))
    return PythonConfig(
        extra=_str_list("python", "extra", data.get("extra")),
        exclude=_str_list("python", "exclude", data.get("exclude")),
        targets=targets,
        include_build_tools=bool(data.get("include_build_tools", True)),
        only_binary=bool(data.get("only_binary", False)),
    )


def _parse_models(data: dict[str, Any]) -> ModelsConfig:
    _check_keys("models", data, {"huggingface", "urls"})
    hf: list[HuggingFaceModel] = []
    for i, raw in enumerate(data.get("huggingface") or []):
        if isinstance(raw, str):
            raw = {"repo": raw}
        raw = _table(f"models.huggingface[{i}]", raw)
        _check_keys(
            "models.huggingface", raw, {"repo", "revision", "repo_type", "allow_patterns", "ignore_patterns"}
        )
        if "repo" not in raw:
            raise ConfigError(f"anbar.toml: [[models.huggingface]] entry {i + 1} is missing 'repo'")
        hf.append(
            HuggingFaceModel(
                repo=raw["repo"],
                revision=raw.get("revision"),
                repo_type=raw.get("repo_type", "model"),
                allow_patterns=raw.get("allow_patterns"),
                ignore_patterns=raw.get("ignore_patterns"),
            )
        )
    urls = [_parse_url_file(f"models.urls[{i}]", raw) for i, raw in enumerate(data.get("urls") or [])]
    return ModelsConfig(huggingface=hf, urls=urls)


def _parse_url_file(section: str, raw: Any) -> UrlFile:
    if isinstance(raw, str):
        raw = {"url": raw}
    raw = _table(section, raw)
    _check_keys(section, raw, {"url", "filename", "sha256", "name"})
    if "url" not in raw:
        raise ConfigError(f"anbar.toml: {section} is missing 'url'")
    return UrlFile(url=raw["url"], filename=raw.get("filename"), sha256=raw.get("sha256"), name=raw.get("name"))


def _parse_docs(value: Any) -> list[DocsEntry]:
    entries: list[DocsEntry] = []
    for i, raw in enumerate(value or []):
        raw = _table(f"docs[{i}]", raw)
        _check_keys("docs", raw, {"name", "url", "sha256", "extract"})
        if "url" not in raw or "name" not in raw:
            raise ConfigError(f"anbar.toml: [[docs]] entry {i + 1} needs both 'name' and 'url'")
        name = str(raw["name"])
        if not name.replace("-", "").replace("_", "").replace(".", "").isalnum():
            raise ConfigError(f"anbar.toml: docs name '{name}' may only contain letters, digits, '-', '_' and '.'")
        entries.append(DocsEntry(name=name, url=raw["url"], sha256=raw.get("sha256"), extract=raw.get("extract", True)))
    return entries


def parse_config(data: dict[str, Any], path: Path | None = None) -> Config:
    """Build a :class:`Config` from already-parsed TOML data."""
    known = {"python", "node", "docker", "models", "docs", "mirrors", "proxy", "serve", "network", "ecosystems"}
    _check_keys("root", data, known)

    node = _table("node", data.get("node"))
    _check_keys("node", node, {"extra"})

    docker = _table("docker", data.get("docker"))
    _check_keys("docker", docker, {"extra", "exclude", "platform", "build_args", "registry"})
    build_args = _table("docker.build_args", docker.get("build_args"))

    mirrors = _table("mirrors", data.get("mirrors"))
    _check_keys("mirrors", mirrors, {"pypi", "npm", "docker", "huggingface"})

    proxy = _table("proxy", data.get("proxy"))
    _check_keys("proxy", proxy, {"http", "https", "socks", "no_proxy"})
    for key in ("http", "https", "socks"):
        value = proxy.get(key)
        if value and ("@" in value.split("//", 1)[-1].split("/", 1)[0]):
            warn(
                f"anbar.toml: [proxy] {key} contains credentials; prefer the "
                "ANBAR_PROXY_USER / ANBAR_PROXY_PASSWORD environment variables so they are not committed"
            )

    serve = _table("serve", data.get("serve"))
    _check_keys("serve", serve, {"host", "pypi_port", "npm_port", "files_port", "registry_port"})
    defaults = ServeConfig()

    network = _table("network", data.get("network"))
    _check_keys("network", network, {"workers", "retries", "timeout", "ca_bundle", "verify_tls"})
    if network.get("verify_tls") is False:
        raise ConfigError(
            "anbar.toml: disabling TLS verification is not supported",
            "set [network] ca_bundle to the certificate of your proxy or mirror instead",
        )

    ecosystems = data.get("ecosystems")
    if ecosystems is not None:
        ecosystems = _str_list("root", "ecosystems", ecosystems)

    return Config(
        python=_parse_python(_table("python", data.get("python"))),
        node=NodeConfig(extra=_str_list("node", "extra", node.get("extra"))),
        docker=DockerConfig(
            extra=_str_list("docker", "extra", docker.get("extra")),
            exclude=_str_list("docker", "exclude", docker.get("exclude")),
            platform=docker.get("platform"),
            build_args={str(k): str(v) for k, v in build_args.items()},
            registry=bool(docker.get("registry", False)),
        ),
        models=_parse_models(_table("models", data.get("models"))),
        docs=_parse_docs(data.get("docs")),
        mirrors=MirrorsConfig(
            pypi=_str_list("mirrors", "pypi", mirrors.get("pypi")),
            npm=_str_list("mirrors", "npm", mirrors.get("npm")),
            docker=_str_list("mirrors", "docker", mirrors.get("docker")),
            huggingface=_str_list("mirrors", "huggingface", mirrors.get("huggingface")),
        ),
        proxy=ProxyConfig(
            http=proxy.get("http"),
            https=proxy.get("https"),
            socks=proxy.get("socks"),
            no_proxy=_str_list("proxy", "no_proxy", proxy.get("no_proxy")),
        ),
        serve=ServeConfig(
            host=str(serve.get("host", defaults.host)),
            pypi_port=_port("serve", "pypi_port", serve.get("pypi_port"), defaults.pypi_port),
            npm_port=_port("serve", "npm_port", serve.get("npm_port"), defaults.npm_port),
            files_port=_port("serve", "files_port", serve.get("files_port"), defaults.files_port),
            registry_port=_port("serve", "registry_port", serve.get("registry_port"), defaults.registry_port),
        ),
        network=NetworkConfig(
            workers=int(network.get("workers", 8)),
            retries=int(network.get("retries", 4)),
            timeout=float(network.get("timeout", 60.0)),
            ca_bundle=network.get("ca_bundle"),
        ),
        ecosystems=ecosystems,
        path=path,
    )


def load_config(project: Path, explicit: Path | None = None) -> Config:
    """Load ``anbar.toml`` from ``explicit`` or the project root.

    Returns the default configuration when no file exists.
    """
    path = explicit if explicit is not None else project / CONFIG_FILENAME
    if not path.exists():
        if explicit is not None:
            raise ConfigError(f"config file not found: {path}")
        return Config()
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}", "check the syntax of anbar.toml") from exc
    return parse_config(data, path=path)
