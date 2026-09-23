# Anbar

**Offline development kit builder.** Download everything your project needs while the internet works, and keep installing, building and running it when the internet stops working.

[![CI](https://github.com/assaabriiii/Anbar/actions/workflows/ci.yml/badge.svg)](https://github.com/assaabriiii/Anbar/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)

[فارسی](README.fa.md) · [Contributing](CONTRIBUTING.md) · [Changelog](CHANGELOG.md) · [Security](SECURITY.md)

---

*Anbar* (انبار) means "storehouse" in Persian.

Developers in Iran regularly deal with internet shutdowns and heavy filtering that block PyPI, npm, Docker Hub, Hugging Face and documentation sites for days at a time. Anbar scans a project, downloads every dependency it needs into a portable **kit** directory, and later serves that kit as local mirrors. Your tools (`pip`, `uv`, `npm`, `yarn`, `pnpm`, `docker`, `transformers`, …) keep working as if nothing happened.

A kit is an ordinary folder. Copy it to a USB drive or serve it over the LAN, and one person with internet access can supply a whole team.

## Contents

- [Features](#features)
- [Installation](#installation)
- [5-minute quickstart](#5-minute-quickstart)
- [Commands](#commands)
- [Supported ecosystems](#supported-ecosystems)
- [Configuration reference](#configuration-reference)
- [Upstream mirrors and proxies](#upstream-mirrors-and-proxies)
- [Sharing a kit with your team](#sharing-a-kit-with-your-team)
- [Kit format](#kit-format)
- [Security](#security)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

## Features

- **One command to scan, one to pack.** Anbar finds `requirements*.txt`, `pyproject.toml`, `poetry.lock`, `Pipfile.lock`, `uv.lock`, `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, Dockerfiles and compose files on its own.
- **Complete dependency trees.** It includes transitive Python dependencies, every package in your JS lock file, and every base image, including multi-stage builds and `COPY --from` images.
- **Incremental, resumable, parallel.** Running `pack` again downloads only what changed. Interrupted downloads continue where they stopped, failed requests are retried, and every file is checked against its sha256 or SRI hash.
- **Real local mirrors.** You get a PEP 503 index for pip and uv, a read-only npm registry for npm, yarn and pnpm, a static file server for model weights and docs, and an optional `registry:2` for Docker.
- **Reversible setup.** `anbar use` configures your tools and backs up every file it touches first. `anbar restore` puts every file back byte for byte.
- **Built for filtered networks.** Anbar can download through upstream mirrors in fallback order, and through HTTP or SOCKS proxies. Error messages suggest what to try next.
- **Cross-platform kits.** Download wheels for other machines too, for example when you develop on Windows and deploy to Linux.
- **Easy to move.** `export` and `import` handle checksummed archives for USB or LAN transfer, and importing an archive into an existing kit adds only what is new.
- **Offline itself.** Every release includes a single-file zipapp and a wheel bundle that install without internet access.

## Installation

Anbar needs Python 3.10 or newer.

```bash
pipx install anbar                     # recommended
# or
pip install --user anbar
```

Optional extras:

```bash
pipx inject anbar huggingface_hub      # Hugging Face models   (pip install "anbar[models]")
pipx inject anbar PySocks              # SOCKS proxies         (pip install "anbar[socks]")
```

To install the latest development version from source:

```bash
pipx install git+https://github.com/assaabriiii/Anbar.git
```

### Installing Anbar without internet

Each [GitHub release](https://github.com/assaabriiii/Anbar/releases) includes two files that install without internet access. Keep a copy of them on your USB drive:

| File | How to use it |
| --- | --- |
| `anbar-X.Y.Z.pyz` | A single file. Just run it: `python anbar-X.Y.Z.pyz --help` |
| `anbar-X.Y.Z-offline.zip` | Wheels for Linux, macOS and Windows (Python 3.10–3.13). Unzip it, then run `sh install.sh` or `powershell -File install.ps1` |

## 5-minute quickstart

On a machine **with** internet access:

```bash
cd my-project

anbar scan                      # 1. see what would be downloaded, and how big it is
anbar pack --out ~/anbar-kit    # 2. download everything into the kit
anbar verify ~/anbar-kit        # 3. optional: re-hash every file
```

Later, **without** internet access (on the same machine or another one):

```bash
anbar serve ~/anbar-kit         # 4. start the local mirrors (keep this terminal open)
```

In a second terminal:

```bash
anbar use ~/anbar-kit           # 5. point pip / npm / yarn / pnpm / docker / HF at the kit
source ~/.anbar/env/anbar.sh    #    only needed for uv and Hugging Face (see the printed hint)

pip install -r requirements.txt # works offline
npm ci                          # works offline
docker compose build            # base images were loaded into Docker

anbar restore                   # 6. when the internet is back, undo everything exactly
```

For a small Django + React project with a Dockerfile, `anbar scan` shows:

```text
──────────────────────────── Python ────────────────────────────
  manifest requirements.txt
Item        Version  From                  Size
django      4.2.16   requirements.txt    7.6 MB
pip                  build tools         1.7 MB
...
                     Summary
┏━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━┓
┃ Ecosystem ┃ Manifests ┃ Items ┃ Estimated size ┃
┡━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━┩
│ Python    │         1 │     4 │        10.2 MB │
│ Node      │         1 │     5 │         1.1 MB │
│ Docker    │         2 │     2 │     ≈ 286.1 MB │
└───────────┴───────────┴───────┴────────────────┘
Total estimated download: ≈ 297.4 MB
```

Sizes marked `≈` are typical sizes for items whose exact size can't be found without downloading them, such as Docker images.

## Commands

| Command | What it does |
| --- | --- |
| `anbar scan [PATH]` | Detect manifests and show what `pack` would download, with an estimated size. `--offline` skips the registry lookups used for exact sizes. `--all` lists every item. |
| `anbar pack [PATH] --out KIT_DIR` | Download everything into the kit. It is incremental, resumable, parallel (`-j N`) and verified. `--mirror ECO=URL` adds an upstream mirror, and `--refresh` checks everything upstream again. |
| `anbar serve KIT_DIR` | Start the local mirrors. `--host 0.0.0.0` shares them with your LAN. The ports are set with `--pypi-port`, `--npm-port`, `--files-port` and `--registry-port`. |
| `anbar use [KIT_DIR]` | Point pip, uv, npm, yarn, pnpm, Docker and Hugging Face at the mirrors, backing up every file first. Use `--host IP` to rely on a teammate's `anbar serve`. |
| `anbar restore` | Undo `anbar use` exactly, from the backups. |
| `anbar env [--shell sh\|fish\|powershell\|cmd]` | Print the environment variables that `use` set, so you can run `eval "$(anbar env)"`. |
| `anbar verify KIT_DIR` | Check every file against its sha256 and size in the manifest. `--quick` compares sizes only. |
| `anbar status KIT_DIR` | Show the contents, sizes and freshness of each ecosystem. `--stale-days N` sets when an ecosystem counts as stale. |
| `anbar export KIT_DIR --to FILE.tar` | Write the kit to one archive (`.tar`, `.tar.gz` or `.tar.xz`) plus a `.sha256` file next to it. |
| `anbar import FILE.tar [--to DIR]` | Check the archive and unpack it safely. If `DIR` already holds a kit, only new files are added. |

`scan`, `pack`, `serve` and `use` also accept `--only ECOSYSTEM` and `--skip ECOSYSTEM` (repeatable). Every command has `--help`.

## Supported ecosystems

| Ecosystem | Detected from | Stored as | Served as | `anbar use` configures |
| --- | --- | --- | --- | --- |
| **Python** | `requirements*.txt` (with `-r`/`-c`), `pyproject.toml` (PEP 621, dependency groups, Poetry, build requires), `poetry.lock`, `Pipfile.lock`, `uv.lock` | wheels and sdists (`pip download`, transitive) | PEP 503 simple index, `http://127.0.0.1:3141/simple/` | user `pip.conf`/`pip.ini` (`index-url`, `trusted-host`); `UV_DEFAULT_INDEX` for uv |
| **Node** | `package-lock.json` (v1–v3), `npm-shrinkwrap.json`, `yarn.lock` (classic and Berry), `pnpm-lock.yaml` (v5, v6, v9) | tarballs, and packuments trimmed to the versions in the kit | read-only npm registry, `http://127.0.0.1:4873/` | `~/.npmrc`, `~/.yarnrc`, `~/.yarnrc.yml` |
| **Docker** | `FROM` in every Dockerfile / Containerfile (multi-stage, `ARG` defaults, `--platform`), `COPY --from=image`, `image:` in compose files (with `.env` interpolation) | `docker save` tarballs | optional local `registry:2` on port 5000 | `docker load` of every image (removed again by `restore`) |
| **Models** | `[[models.huggingface]]` and `[[models.urls]]` in `anbar.toml` | a Hugging Face cache (`HF_HOME` layout), plain files | file server, `http://127.0.0.1:8765/models/` | `HF_HOME`, `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` |
| **Docs** | `[[docs]]` in `anbar.toml` | zip or tar archives, extracted | file server, `http://127.0.0.1:8765/docs/` | – |

Lock files take priority. When a folder has both `pyproject.toml` and `poetry.lock` (or `uv.lock`), the pinned versions from the lock file are used.

Environment variables can't be changed in your running shell, so `anbar use` writes them to `~/.anbar/env/anbar.sh` (plus `.fish`, `.ps1` and `.bat` versions). Load that file in your shell, or run `eval "$(anbar env)"`.

## Configuration reference

Every setting is optional. Anbar reads `anbar.toml` from the project root, or from the path given with `--config`. `serve` and `use` read `anbar.toml` from the current directory, which is only needed for custom ports. The full annotated example is in [`anbar.example.toml`](anbar.example.toml).

```toml
ecosystems = ["python", "node", "docker", "models", "docs"]   # default: all

[python]
extra = ["gunicorn==23.0.0"]          # packages that are not in any manifest
exclude = ["pywin32"]                 # never download these
include_build_tools = true            # pip, setuptools, wheel (needed to build sdists offline)
only_binary = false                   # wheels only
platforms = ["manylinux2014_x86_64", "win_amd64"]   # combined with every python_version
python_versions = ["3.11", "3.12"]

[node]
extra = ["typescript@5.6.3", "pnpm@latest"]

[docker]
extra = ["redis:7.4-alpine"]
exclude = []
platform = "linux/amd64"
build_args = { PYTHON_VERSION = "3.12" }   # values for ARGs used in FROM lines
registry = false                           # also keep and serve registry:2

[[models.huggingface]]
repo = "sentence-transformers/all-MiniLM-L6-v2"
revision = "main"
allow_patterns = ["*.json", "*.safetensors", "*.txt"]

[[models.urls]]
url = "https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8n.pt"
name = "yolo"          # optional sub-folder
sha256 = "..."         # optional, recommended

[[docs]]
name = "django"
url = "https://example.com/django-docs-5.1-en.zip"

[mirrors]              # used by `pack`, tried in order
pypi = ["https://mirror.example/simple", "https://pypi.org/simple"]
npm = ["https://npm-mirror.example"]
docker = ["docker-mirror.example", "ghcr.io=ghcr-mirror.example"]
huggingface = ["https://hf-mirror.example"]

[proxy]                # used by `pack`
http = "http://127.0.0.1:8080"
https = "http://127.0.0.1:8080"
socks = "socks5h://127.0.0.1:1080"
no_proxy = ["localhost", "127.0.0.1"]

[serve]
host = "127.0.0.1"
pypi_port = 3141
npm_port = 4873
files_port = 8765
registry_port = 5000

[network]
workers = 8
retries = 4
timeout = 60
ca_bundle = "/path/to/ca.pem"
```

| Key | Default | Meaning |
| --- | --- | --- |
| `ecosystems` | all | Ecosystems to handle. `--only` / `--skip` override it for a single run. |
| `python.extra` / `python.exclude` | `[]` | Extra PEP 508 requirements to download / project names to skip. |
| `python.platforms`, `python.python_versions`, `[[python.targets]]` | native only | Download wheels for other platforms and Python versions. Every combination is fetched, and targets never fall back to building from source. |
| `python.include_build_tools` | `true` | Also keep `pip`, `setuptools` and `wheel`. |
| `python.only_binary` | `false` | Never download sdists for the native target. |
| `node.extra` | `[]` | `name@version` or `name@dist-tag`. Dependencies of extras aren't resolved; add real dependencies to `package.json` and your lock file. |
| `docker.extra` / `docker.exclude` | `[]` | Extra images to pull / images to skip. |
| `docker.platform` | native | Default `--platform` for pulls. A `FROM --platform=` line overrides it. |
| `docker.build_args` | `{}` | Values for global `ARG`s used in `FROM`. Anbar warns about every tag it can't resolve. |
| `docker.registry` | `false` | Keep `registry:2` in the kit. `anbar serve` then runs it and pushes every image into it. |
| `models.huggingface[]` | – | `repo`, `revision`, `repo_type`, `allow_patterns`, `ignore_patterns`. |
| `models.urls[]` | – | `url`, `filename`, `name`, `sha256`. |
| `docs[]` | – | `name`, `url`, `sha256`, `extract` (default `true`). |
| `mirrors.*` | `[]` | Upstream mirrors for `pack`, tried in order. See below. |
| `proxy.*` | none | Proxy for `pack`. Put credentials in `ANBAR_PROXY_USER` / `ANBAR_PROXY_PASSWORD`, not in this file. |
| `serve.*` | see above | Bind address and ports for `serve`, and the ports `use` points to. |
| `network.workers` / `retries` / `timeout` | 8 / 4 / 60 | Parallel downloads, retries (with exponential backoff) and timeout in seconds. |
| `network.ca_bundle` | system store | Extra CA certificate for a proxy or mirror. There is no option to disable TLS verification. |

## Upstream mirrors and proxies

When the public registries are blocked, mirrors inside the country often still work. List them under `[mirrors]`, or pass them for a single run with `--mirror`:

```bash
anbar pack --out kit \
  --mirror pypi=https://mirror.example/simple \
  --mirror npm=https://npm-mirror.example \
  --mirror docker=docker-mirror.example
```

Mirrors are tried **in order**, and the next one is used when a download fails or doesn't match its checksum. The public registry is only used when no mirror is configured, or when you add it to the list yourself (as the last entry is a good idea).

- **pypi**: any PEP 503 simple index URL.
- **npm**: a registry base URL. Tarball URLs in lock files are rewritten to point at the mirror.
- **docker**: a plain host name mirrors Docker Hub (`python:3.12` is pulled as `HOST/library/python:3.12` and re-tagged). Use `REGISTRY=HOST` for other registries, for example `ghcr.io=ghcr-mirror.example`.
- **huggingface**: an endpoint compatible with the Hugging Face Hub API.

Mirrors that Iranian developers use today include `https://mirror-pypi.runflare.com/simple` (PyPI), `https://mirror-npm.runflare.com` (npm) and `docker.arvancloud.ir` (Docker Hub). They are run by third parties, so their availability and content can change. Check the mirror's own documentation, and keep `sha256` checks on for files that matter.

## Sharing a kit with your team

One person with internet access can supply everyone else.

### Over a USB drive or file share

```bash
# the person with internet
anbar pack ./project --out team-kit
anbar export team-kit --to /media/usb/team-kit.tar     # also writes team-kit.tar.sha256

# everyone else (copy both files)
anbar import /media/usb/team-kit.tar --to ~/team-kit   # checks the sha256, unpacks safely
anbar serve ~/team-kit &
anbar use ~/team-kit
```

`.tar` is fastest. `.tar.gz` or `.tar.xz` make smaller files but take longer to create. When you import a newer export into an existing kit, only the new or changed files are added, so a weekly update is quick.

### Over the LAN

One machine serves the kit, and everyone else points at it. Teammates don't need a copy of the kit:

```bash
# the machine that has the kit (e.g. 192.168.1.10)
anbar serve ~/team-kit --host 0.0.0.0

# every teammate
anbar use --host 192.168.1.10          # pip, uv, npm, yarn, pnpm
pip install -r requirements.txt
```

For Docker images over the LAN, set `[docker] registry = true` before packing. `anbar serve` then starts `registry:2` from the kit and pushes every image into it. Teammates add `"insecure-registries": ["192.168.1.10:5000"]` to Docker's `daemon.json` and run `docker pull 192.168.1.10:5000/python:3.12-slim`. Models and docs are served at `http://192.168.1.10:8765/`.

### Keeping kits fresh

- Run `anbar pack` again whenever you have internet access. It only downloads what is new.
- `anbar status KIT` shows how old each part of the kit is, and marks stale parts.
- `anbar verify KIT` catches files damaged by a flaky USB drive or network copy.

### Kits for other machines

A developer on Windows packing for Linux servers:

```toml
[python]
platforms = ["manylinux2014_x86_64", "win_amd64"]
python_versions = ["3.12"]

[docker]
platform = "linux/amd64"
```

## Kit format

A kit is a single directory:

```text
KIT_DIR/
├── manifest.json          # every artifact: path, ecosystem, source, sha256, size, download date
├── python/packages/       # wheels and sdists
├── node/tarballs/         # <name>/-/<name>-<version>.tgz
├── node/packuments/       # registry metadata, trimmed to the versions in the kit
├── docker/images/         # docker save tarballs
├── models/hf/hub/         # Hugging Face cache (HF_HOME=models/hf)
├── models/files/          # weights downloaded from URLs
└── docs/archives/         # documentation archives (extracted to docs/site/)
```

`manifest.json` has a `format_version`. New releases of Anbar keep reading older kits and upgrade them in memory. A kit written by a newer Anbar is rejected with a clear message instead of being misread. See [docs/kit-format.md](docs/kit-format.md).

## Security

- TLS verification is **always on**. If your network intercepts TLS, set `[network] ca_bundle`. There is no switch to turn verification off.
- **No credentials are stored in the kit.** Proxy credentials come from `ANBAR_PROXY_USER` / `ANBAR_PROXY_PASSWORD`, and Hugging Face tokens from `HF_TOKEN`. Neither is written to disk by Anbar. Index URLs that contain credentials are never copied from `requirements.txt`.
- Every download is checked against the hash its registry publishes (pip's index hashes, npm SRI) or the `sha256` in `anbar.toml`, and every artifact's sha256 is recorded in the manifest.
- `import` refuses archives that contain absolute paths, `..`, or links pointing outside the kit.
- The local mirrors are read-only and listen on `127.0.0.1` unless you choose otherwise.

To report a vulnerability, see [SECURITY.md](SECURITY.md).

## Troubleshooting

| Message | What to do |
| --- | --- |
| `PyPI unreachable — try an upstream mirror` | Add a mirror with `--mirror pypi=URL` or `[mirrors] pypi`, or set `[proxy]`. |
| `npm registry unreachable` | Same, with `--mirror npm=URL`. |
| `Docker is installed but the daemon is not reachable` | Start Docker Desktop, or run `sudo systemctl start docker`. |
| `cannot resolve ${VAR} in 'FROM …'` | Give the `ARG` a default, or set it under `[docker] build_args`. |
| `TLS certificate verification failed` | Your network intercepts TLS. Set `[network] ca_bundle` to its certificate. |
| `Anbar is already active` | Run `anbar restore` first, or use `anbar use --force`. |
| `this kit uses format version N` | The kit was made by a newer Anbar. Upgrade using the offline bundle. |
| pip still reaches the internet | `PIP_INDEX_URL` in your environment overrides `pip.conf`. Unset it. |
| `uv` / `transformers` ignore the kit | Load the environment file printed by `anbar use`, or run `eval "$(anbar env)"`. |
| yarn 1 downloads from `registry.yarnpkg.com` | Run `yarn install --registry http://127.0.0.1:4873/`, or replace the host in `yarn.lock`. |

Set `ANBAR_LOG_REQUESTS=1` to log every request the local mirrors receive.

## Contributing

Contributions are very welcome, whether they are code, bug reports, translations, testing on real filtered networks, or mirror lists. Please read [CONTRIBUTING.md](CONTRIBUTING.md). Planned ecosystems include **apt**, **Go modules** and **Maven**, and each one is a self-contained plugin.

## License

[MIT](LICENSE) © The Anbar contributors
