#!/usr/bin/env python3
"""Build the release artifacts, including the ones that install without internet.

Produces in ``dist/``:

* ``anbar-X.Y.Z-py3-none-any.whl`` and ``anbar-X.Y.Z.tar.gz``   (PyPI)
* ``anbar-X.Y.Z.pyz``                 single-file zipapp: ``python anbar.pyz --help``
* ``anbar-X.Y.Z-offline.zip``         wheels for Anbar and every dependency on
                                      Linux, macOS and Windows plus install scripts
* ``SHA256SUMS``

Usage: python scripts/build_release.py [--skip-bundle]
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipapp
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

# Only PyYAML ships compiled wheels; everything else is pure Python.
PLATFORMS = [
    "manylinux2014_x86_64",
    "manylinux2014_aarch64",
    "macosx_12_0_arm64",  # pip also accepts wheels for older macOS releases
    "macosx_12_0_x86_64",
    "win_amd64",
]
PYTHON_VERSIONS = ["3.10", "3.11", "3.12", "3.13"]

INSTALL_SH = """#!/bin/sh
# Install Anbar without internet access.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
WHEEL="$(ls "$HERE"/wheels/anbar-*.whl | head -n 1)"
if command -v pipx >/dev/null 2>&1; then
    pipx install "$WHEEL" --pip-args="--no-index --find-links=$HERE/wheels"
else
    python3 -m pip install --user --no-index --find-links="$HERE/wheels" "$WHEEL"
fi
echo "Installed. Run: anbar --help"
"""

INSTALL_PS1 = """# Install Anbar without internet access.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$wheel = Get-ChildItem "$here\\wheels\\anbar-*.whl" | Select-Object -First 1
if (Get-Command pipx -ErrorAction SilentlyContinue) {
    pipx install $wheel.FullName --pip-args="--no-index --find-links=$here\\wheels"
} else {
    py -m pip install --user --no-index --find-links="$here\\wheels" $wheel.FullName
}
Write-Host "Installed. Run: anbar --help"
"""

README_TXT = """Anbar {version} - offline installation bundle

Linux / macOS:  sh install.sh
Windows:        powershell -ExecutionPolicy Bypass -File install.ps1

Or by hand:     pip install --no-index --find-links wheels anbar

Requires Python 3.10 or newer. The optional extras (models, socks) are not
included; install them from a kit or with internet access.
"""


def run(cmd: list[str], **kwargs) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, **kwargs)


def version() -> str:
    text = (ROOT / "src" / "anbar" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__ = "([^"]+)"', text)
    assert match, "version not found"
    return match.group(1)


def build_dists(ver: str) -> Path:
    run([sys.executable, "-m", "build", "--outdir", str(DIST), str(ROOT)])
    wheel = DIST / f"anbar-{ver}-py3-none-any.whl"
    if not wheel.exists():
        sys.exit(f"expected {wheel}")
    return wheel


def build_zipapp(wheel: Path, ver: str) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "app"
        run([sys.executable, "-m", "pip", "install", "--quiet", "--no-compile", "--target", str(target),
             str(wheel), "tomli>=2.0", "PySocks>=1.7"])
        # Drop compiled extensions (PyYAML falls back to pure Python) and scripts.
        for pattern in ("*.so", "*.pyd", "*.dylib"):
            for path in target.rglob(pattern):
                path.unlink()
        shutil.rmtree(target / "bin", ignore_errors=True)
        for cache in list(target.rglob("__pycache__")):
            shutil.rmtree(cache, ignore_errors=True)
        (target / "__main__.py").write_text("from anbar.cli import main\n\nmain()\n", encoding="utf-8")
        out = DIST / f"anbar-{ver}.pyz"
        zipapp.create_archive(target, out, interpreter="/usr/bin/env python3", compressed=True)
    run([sys.executable, str(out), "--version"])
    return out


def build_bundle(wheel: Path, ver: str) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / f"anbar-{ver}-offline"
        wheels = root / "wheels"
        wheels.mkdir(parents=True)
        shutil.copy2(wheel, wheels)
        # Pure-Python dependencies once, then platform wheels for every target.
        run([sys.executable, "-m", "pip", "download", "--quiet", "--dest", str(wheels), "--only-binary=:all:",
             str(wheel), "tomli>=2.0", "PySocks>=1.7"])
        for platform in PLATFORMS:
            for py in PYTHON_VERSIONS:
                run([sys.executable, "-m", "pip", "download", "--quiet", "--dest", str(wheels),
                     "--only-binary=:all:", "--platform", platform, "--python-version", py,
                     "--implementation", "cp", str(wheel), "tomli>=2.0"])
        (root / "install.sh").write_text(INSTALL_SH, encoding="utf-8", newline="\n")
        (root / "install.ps1").write_text(INSTALL_PS1, encoding="utf-8", newline="\r\n")
        (root / "README.txt").write_text(README_TXT.format(version=ver), encoding="utf-8")
        shutil.copy2(ROOT / "LICENSE", root / "LICENSE")
        out = DIST / f"anbar-{ver}-offline.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    info = zipfile.ZipInfo.from_file(path, path.relative_to(root.parent).as_posix())
                    if path.name == "install.sh":
                        info.external_attr = 0o755 << 16
                    with path.open("rb") as fh:
                        zf.writestr(info, fh.read(), zipfile.ZIP_DEFLATED)
    return out


def checksums() -> None:
    lines = []
    for path in sorted(DIST.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS":
            lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    (DIST / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-bundle", action="store_true", help="do not build the multi-platform offline bundle")
    args = parser.parse_args()
    ver = version()
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()
    wheel = build_dists(ver)
    build_zipapp(wheel, ver)
    if not args.skip_bundle:
        build_bundle(wheel, ver)
    checksums()


if __name__ == "__main__":
    main()
