"""HTTP downloads: retries, resume, mirrors, proxies and checksum verification.

Only the standard library is used. SOCKS proxies need the optional ``PySocks``
package (``pipx inject anbar PySocks`` or ``pip install anbar[socks]``).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar

from anbar import __version__
from anbar.config import NetworkConfig, ProxyConfig
from anbar.errors import IntegrityError, NetworkError

USER_AGENT = f"anbar/{__version__}"
CHUNK = 1 << 16

T = TypeVar("T")
R = TypeVar("R")


def _proxy_with_env_credentials(url: str | None) -> str | None:
    """Inject credentials from the environment so they never live in files."""
    if not url:
        return url
    user = os.environ.get("ANBAR_PROXY_USER")
    password = os.environ.get("ANBAR_PROXY_PASSWORD", "")
    if not user:
        return url
    parts = urllib.parse.urlsplit(url)
    if "@" in parts.netloc:
        return url
    netloc = f"{urllib.parse.quote(user)}:{urllib.parse.quote(password)}@{parts.netloc}"
    return urllib.parse.urlunsplit(parts._replace(netloc=netloc))


def proxy_env(proxy: ProxyConfig) -> dict[str, str]:
    """Environment variables that make child processes (pip, docker) use the proxy."""
    env: dict[str, str] = {}
    http = _proxy_with_env_credentials(proxy.http)
    https = _proxy_with_env_credentials(proxy.https or proxy.http)
    socks = _proxy_with_env_credentials(proxy.socks)
    if socks and not (http or https):
        http = https = socks
    if http:
        env["HTTP_PROXY"] = env["http_proxy"] = http
    if https:
        env["HTTPS_PROXY"] = env["https_proxy"] = https
    if proxy.no_proxy:
        env["NO_PROXY"] = env["no_proxy"] = ",".join(proxy.no_proxy)
    return env


def verify_integrity(path: Path, sha256: str | None = None, integrity: str | None = None) -> str:
    """Check ``path`` against a hex sha256 and/or an SRI string; return its sha256."""
    hashes: dict[str, Any] = {"sha256": hashlib.sha256()}
    sri: list[tuple[str, str]] = []
    if integrity:
        for token in integrity.split():
            algo, _, value = token.partition("-")
            if algo in ("sha1", "sha256", "sha384", "sha512") and value:
                sri.append((algo, value))
                hashes.setdefault(algo, hashlib.new(algo))
    with path.open("rb") as fh:
        while True:
            block = fh.read(1 << 20)
            if not block:
                break
            for h in hashes.values():
                h.update(block)
    actual = hashes["sha256"].hexdigest()
    if sha256 and actual.lower() != sha256.lower():
        raise IntegrityError(f"sha256 mismatch for {path.name}: expected {sha256}, got {actual}")
    if sri:
        # SRI passes if any of the listed strongest hashes matches.
        if not any(base64.b64encode(hashes[a].digest()).decode() == v for a, v in sri):
            raise IntegrityError(f"integrity check failed for {path.name} ({sri[0][0]})")
    return actual


@dataclass
class DownloadResult:
    path: Path
    url: str
    sha256: str
    size: int
    skipped: bool = False


class Network:
    """A small HTTP client that honours Anbar's network and proxy settings."""

    def __init__(self, network: NetworkConfig | None = None, proxy: ProxyConfig | None = None) -> None:
        self.cfg = network or NetworkConfig()
        self.proxy = proxy or ProxyConfig()
        self._opener = self._build_opener()

    # -- setup ------------------------------------------------------------

    def ssl_context(self) -> ssl.SSLContext:
        cafile = self.cfg.ca_bundle or os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
        # Verification is always on; only the trust store can be changed.
        return ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()

    def _build_opener(self) -> urllib.request.OpenerDirector:
        handlers: list[Any] = [urllib.request.HTTPSHandler(context=self.ssl_context())]
        if self.proxy.socks:
            self._install_socks(self.proxy.socks)
        proxies = {}
        if self.proxy.http:
            proxies["http"] = _proxy_with_env_credentials(self.proxy.http)
        if self.proxy.https or self.proxy.http:
            proxies["https"] = _proxy_with_env_credentials(self.proxy.https or self.proxy.http)
        if proxies:
            if self.proxy.no_proxy:
                os.environ.setdefault("no_proxy", ",".join(self.proxy.no_proxy))
            handlers.append(urllib.request.ProxyHandler(proxies))
        return urllib.request.build_opener(*handlers)

    @staticmethod
    def _install_socks(url: str) -> None:
        try:
            import socks  # type: ignore[import-not-found]
        except ImportError as exc:
            raise NetworkError(
                "a SOCKS proxy is configured but PySocks is not installed",
                "run `pipx inject anbar PySocks` or `pip install anbar[socks]`",
            ) from exc
        parts = urllib.parse.urlsplit(_proxy_with_env_credentials(url) or url)
        kind = socks.SOCKS4 if parts.scheme.startswith("socks4") else socks.SOCKS5
        socks.set_default_proxy(
            kind,
            parts.hostname,
            parts.port or 1080,
            rdns=parts.scheme in ("socks5h", "socks4a"),
            username=urllib.parse.unquote(parts.username) if parts.username else None,
            password=urllib.parse.unquote(parts.password) if parts.password else None,
        )
        socket.socket = socks.socksocket  # type: ignore[misc]

    # -- requests ---------------------------------------------------------

    def open(self, url: str, headers: dict[str, str] | None = None, method: str = "GET"):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT, **(headers or {})})
        return self._opener.open(req, timeout=self.cfg.timeout)

    def _retry(self, func: Callable[[], T], what: str) -> T:
        delay = 1.0
        last: Exception | None = None
        for attempt in range(max(1, self.cfg.retries + 1)):
            try:
                return func()
            except urllib.error.HTTPError as exc:
                # Client errors other than 408/429 will not get better by retrying.
                if 400 <= exc.code < 500 and exc.code not in (408, 429):
                    raise
                last = exc
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                if isinstance(exc, IntegrityError):
                    raise
                last = exc
            if attempt < self.cfg.retries:
                time.sleep(delay)
                delay = min(delay * 2, 30)
        assert last is not None
        raise last

    def get_bytes(self, url: str, headers: dict[str, str] | None = None) -> bytes:
        def once() -> bytes:
            with self.open(url, headers) as resp:
                return resp.read()

        return self._retry(once, url)

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        return json.loads(self.get_bytes(url, {"Accept": "application/json", **(headers or {})}))

    def content_length(self, url: str) -> int | None:
        try:
            with self.open(url, method="HEAD") as resp:
                value = resp.headers.get("Content-Length")
                return int(value) if value else None
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def _download_once(self, url: str, dest: Path) -> None:
        part = dest.with_name(dest.name + ".part")
        offset = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        try:
            resp = self.open(url, headers)
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and offset:  # stale partial file larger than the resource
                part.unlink()
                return self._download_once(url, dest)
            raise
        with resp:
            status = getattr(resp, "status", 200)
            mode = "ab" if offset and status == 206 else "wb"
            with part.open(mode) as fh:
                while True:
                    block = resp.read(CHUNK)
                    if not block:
                        break
                    fh.write(block)
            expected = resp.headers.get("Content-Length")
        if expected is not None and mode == "wb" and part.stat().st_size != int(expected):
            raise OSError(f"incomplete download of {url}")
        os.replace(part, dest)

    def download(
        self,
        urls: str | Iterable[str],
        dest: Path,
        sha256: str | None = None,
        integrity: str | None = None,
    ) -> DownloadResult:
        """Download the first working URL of ``urls`` to ``dest``.

        Interrupted downloads resume from ``dest.part``. The result is
        verified against ``sha256`` / ``integrity`` when given; a corrupt
        download is deleted and the next mirror is tried.
        """
        candidates = [urls] if isinstance(urls, str) else list(urls)
        dest.parent.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []
        for url in candidates:
            try:
                self._retry(lambda: self._download_once(url, dest), url)
                digest = verify_integrity(dest, sha256, integrity)
                return DownloadResult(dest, url, digest, dest.stat().st_size)
            except IntegrityError as exc:
                dest.unlink(missing_ok=True)
                errors.append(f"{url}: {exc.message}")
            except (urllib.error.URLError, OSError) as exc:
                errors.append(f"{url}: {describe_error(exc)}")
        host = urllib.parse.urlsplit(candidates[0]).netloc if candidates else "?"
        raise NetworkError(
            f"could not download {dest.name} ({host} unreachable or returned an error)\n  " + "\n  ".join(errors),
            "configure an upstream mirror in [mirrors] of anbar.toml, pass --mirror, or set a proxy",
        )


def describe_error(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code} {exc.reason}"
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, ssl.SSLCertVerificationError):
            return f"TLS certificate verification failed ({reason.verify_message}); set [network] ca_bundle"
        return str(reason)
    return str(exc) or exc.__class__.__name__


def run_parallel(
    items: list[T],
    func: Callable[[T], R],
    workers: int,
    on_done: Callable[[T, R | None, BaseException | None], None] | None = None,
) -> list[tuple[T, R | None, BaseException | None]]:
    """Run ``func`` over ``items`` in a thread pool, collecting errors instead of stopping."""
    results: list[tuple[T, R | None, BaseException | None]] = []
    if not items:
        return results
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(func, item): item for item in items}
        for fut in as_completed(futures):
            item = futures[fut]
            try:
                res, err = fut.result(), None
            except Exception as exc:  # noqa: BLE001 - reported to the caller
                res, err = None, exc
            results.append((item, res, err))
            if on_done:
                on_done(item, res, err)
    return results
