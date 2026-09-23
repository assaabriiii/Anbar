# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-09-23

### Added

- `anbar scan`: detects manifests and estimates the download size.
- `anbar pack`: incremental, resumable and parallel downloads with retries, sha256/SRI verification and upstream mirror fallback, through HTTP or SOCKS proxies.
- `anbar serve`: a PEP 503 index, a read-only npm registry, a static file server for models and docs, and an optional `registry:2`.
- `anbar use` / `anbar restore`: configures pip, uv, npm, yarn (1 and 2+), pnpm, Docker and Hugging Face with backups, and undoes the changes exactly.
- `anbar env`: prints the environment variables set by `use` for `eval`.
- `anbar verify`, `anbar status`, `anbar export` and `anbar import`: integrity checks, freshness report, and checksummed archives that merge into existing kits.
- Python: `requirements*.txt`, `pyproject.toml` (PEP 621, dependency groups, Poetry), `poetry.lock`, `Pipfile.lock`, `uv.lock`; cross-platform and cross-version wheel downloads.
- Node: `package-lock.json` v1–v3, `npm-shrinkwrap.json`, `yarn.lock` (classic and Berry), `pnpm-lock.yaml` v5, v6 and v9.
- Docker: Dockerfiles (multi-stage, `ARG`, `--platform`, `COPY --from`) and compose files (`.env` interpolation, `build:` contexts).
- Models: Hugging Face repositories with an HF-compatible cache, and arbitrary weight files.
- Docs: offline documentation archives, served as static sites.
- Versioned kit format (`format_version: 1`).
- Release artifacts that install offline: a zipapp and a multi-platform wheel bundle.

[Unreleased]: https://github.com/assaabriiii/Anbar/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/assaabriiii/Anbar/releases/tag/v0.1.0
