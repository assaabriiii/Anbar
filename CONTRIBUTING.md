# Contributing to Anbar

**Thank you for being here.** Anbar exists because developers kept losing days of work every time the internet was shut down or filtered. Each improvement you make, whether it is a bug fix, a new ecosystem, a translation or one clear bug report, helps a developer somewhere keep working through the next shutdown.

You don't need to be an expert, and you don't need to live in Iran. Anyone who wants development tools to keep working offline is welcome here.

[فارسی](#راهنمای-کوتاه-فارسی) · [Code of Conduct](CODE_OF_CONDUCT.md) · [Security policy](SECURITY.md)

## Ways to help

- **Try it on a real filtered network and tell us what broke.** Real-world reports are the most valuable feedback we get. Please include the command, the full error and which registries were reachable.
- **Report bugs** with the [bug report template](https://github.com/assaabriiii/Anbar/issues/new/choose).
- **Share working mirrors.** Mirrors appear and disappear. If you know a reliable PyPI, npm, Docker Hub or Hugging Face mirror, open an issue or a PR against the mirror section of the READMEs.
- **Improve the docs and translations.** `README.md` and `README.fa.md` must say the same thing. If you change one, update the other, or mention in the PR that the translation still needs doing so someone else can pick it up.
- **Add an ecosystem.** apt, Go modules, Maven/Gradle, Cargo, Composer, RubyGems, conda and Flutter are all wanted. See [Writing a plugin](#writing-a-plugin).
- **Pick up an issue** labelled [`good first issue`](https://github.com/assaabriiii/Anbar/labels/good%20first%20issue) or [`help wanted`](https://github.com/assaabriiii/Anbar/labels/help%20wanted).

If you plan a larger change, open an issue first so we can agree on the approach before you invest time.

## Development setup

```bash
git clone https://github.com/assaabriiii/Anbar.git
cd Anbar
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev,models]"
anbar --help
```

Anbar supports Python 3.10 through 3.13 on Linux, macOS and Windows. Keep new code compatible with all of them.

## Running the tests

```bash
pytest -m "not network and not docker"   # fast, hermetic: no internet needed
pytest -m network                        # end to end against the real registries
pytest -m docker                         # needs a running Docker daemon
pytest                                   # everything
```

Most tests are **hermetic**. They start fake upstream registries on `127.0.0.1` (see `tests/helpers.py`), run `pack → serve → install` against them, and never touch the network. Please follow that pattern: every parser and plugin change needs a unit test, and behaviour that crosses the network should get a hermetic end-to-end test too.

Fixture projects live in `tests/fixtures/` (a Django app, a React app, a Docker project, and sample lock files for every supported format). If you add support for a new lock-file version, add a real example of it there.

The autouse fixture in `tests/conftest.py` points `HOME`, `APPDATA` and `ANBAR_HOME` at temporary directories, so tests can never change your real `pip.conf` or `.npmrc`.

## Project layout

```text
src/anbar/
├── cli.py              # Typer commands; output with Rich
├── config.py           # anbar.toml → dataclasses (unknown keys warn)
├── kit.py              # kit directory + versioned manifest.json
├── kitops.py           # verify, status, export, import
├── network.py          # downloads: retries, resume, mirrors, proxies, sha256/SRI
├── backup.py           # backups for `use`, exact undo for `restore`
├── server.py           # threaded stdlib HTTP servers
└── plugins/
    ├── base.py         # the Plugin interface
    ├── python/         # parsers, PEP 503 index, plugin
    ├── node/           # parsers, npm registry, plugin
    ├── docker/         # Dockerfile/compose parsers, plugin
    ├── models.py       # Hugging Face + URL files
    ├── docs.py         # documentation archives
    └── files.py        # shared static file server, safe extraction
```

## Writing a plugin

Each ecosystem is a subclass of `anbar.plugins.base.Plugin` with six steps:

| Method | Purpose |
| --- | --- |
| `detect(project, config)` | Return the manifest files this plugin understands. Use `walk_project()`, which skips `node_modules`, `.venv`, `.git` and similar folders. |
| `plan(project, config, manifests)` | Parse the manifests and config into a `Plan` of `PlanItem`s. This step must not use the network, because `scan --offline` relies on it. |
| `estimate(plan, ctx)` *(optional)* | Fill in `PlanItem.size` using the network. Best effort and fast; give up early when offline. |
| `fetch(plan, kit, ctx)` | Download into `kit.dir("<ecosystem>", ...)`, record every file with `kit.add(...)`, and skip files that `kit.has_valid(path)` already accepts. Return a `FetchResult`. |
| `serve(kit, config, host, ports)` | Return `Service` objects (usually `HTTPService` with a `Handler` subclass). |
| `configure(ctx, changes)` | Point the tools at the local service. Write files **only** through `changes.write(...)`, which backs them up first, and set environment variables with `changes.set_env(...)`. |
| `restore(record)` *(optional)* | Undo side effects that are not file edits (for example Docker images you loaded), using what you saved with `changes.record(...)`. |

Register the plugin with the `@register` decorator and add its module name to `_load_builtin()` in `src/anbar/plugins/__init__.py`. A minimal skeleton:

```python
from anbar.plugins import register
from anbar.plugins.base import FetchResult, Plan, PlanItem, Plugin, walk_project


@register
class GoPlugin(Plugin):
    name = "go"
    title = "Go"

    def detect(self, project, config):
        return [p for p in walk_project(project) if p.name == "go.sum"]

    def plan(self, project, config, manifests):
        plan = Plan(self.name, manifests=manifests)
        for manifest in manifests:
            for module, version in parse_go_sum(manifest):
                plan.items.append(PlanItem(module, version, source=manifest.name))
        return plan

    def fetch(self, plan, kit, ctx):
        result = FetchResult()
        for item in plan.items:
            dest = kit.dir("go", "cache", "download", item.name, "@v") / f"{item.version}.zip"
            if kit.has_valid(dest):
                result.skipped += 1
                continue
            res = ctx.net.download(mirror_urls(item, ctx.config), dest)
            kit.add(dest, self.name, res.url, sha256=res.sha256)
            result.downloaded += 1
        return result
```

Rules that every plugin must follow:

- **Never disable TLS verification**, and never write credentials (tokens, passwords, authenticated URLs) into the kit or into logs.
- Downloads go through `ctx.net` (retries, resume, proxies, CA bundle, checksums), or through the ecosystem's own tool running with `proxy_env()`.
- Support upstream mirrors in fallback order, and when everything is unreachable, raise an `AnbarError` whose hint explains what to try, for example ``"try `--mirror go=URL`"``.
- Report problems for single items in `FetchResult.failed` and keep going. One bad package must not throw away a two-hour download.
- Add parser unit tests, a hermetic end-to-end test and a fixture project.
- Document the ecosystem in both READMEs.

## Code style

- Match the surrounding code: type hints, `from __future__ import annotations`, small functions, and comments that explain *why* rather than *what*.
- Keep runtime dependencies minimal. The standard library is preferred, and a new dependency needs a good reason because it must also fit in the offline bundle.
- Error messages are part of the UI. Say what failed and what the user can do about it.
- Keep lines within 120 characters.

## Commits and pull requests

We use [Conventional Commits](https://www.conventionalcommits.org/):

```text
feat(node): support pnpm lockfile v10
fix(python): keep extras when parsing Poetry dependencies
docs: add a LAN sharing example
test(docker): cover ARG defaults that reference other ARGs
chore: bump actions/setup-python
```

Common types are `feat`, `fix`, `docs`, `test`, `refactor`, `perf` and `chore`. The scope is optional and is usually the ecosystem.

Before you open a pull request:

1. Run `pytest -m "not network and not docker"`. It should pass.
2. Add or update tests and fixtures.
3. Update `README.md` **and** `README.fa.md` if behaviour or options changed, or say in the PR that the Persian text still needs updating.
4. Add a line under "Unreleased" in `CHANGELOG.md`.

Keep pull requests focused. Several small PRs are reviewed faster than one large one.

## Releasing (maintainers)

1. Update `__version__` in `src/anbar/__init__.py` and move the "Unreleased" entries in `CHANGELOG.md` under the new version.
2. Commit with `chore: release vX.Y.Z` and push a tag `vX.Y.Z`.
3. The release workflow builds the wheel, sdist, zipapp and offline bundle, attaches them to a GitHub release and publishes to PyPI (trusted publishing through the `pypi` environment).

You can build the same artifacts locally with `python scripts/build_release.py`.

---

## راهنمای کوتاه فارسی

<div dir="rtl">

**از اینکه اینجا هستید ممنونیم.** انبار ساخته شد چون توسعه‌دهنده‌ها با هر قطعی یا فیلترینگ اینترنت روزهای کاری‌شان را از دست می‌دادند. هر مشارکتی، چه رفع یک باگ باشد، چه یک اکوسیستم تازه، چه ترجمه یا یک گزارش باگ دقیق، به یک توسعه‌دهندهٔ دیگر کمک می‌کند تا در قطعی بعدی هم به کارش ادامه دهد.

- اگر انبار را روی شبکهٔ فیلترشدهٔ واقعی امتحان کردید، نتیجه را در یک Issue بنویسید: دستوری که اجرا کردید، متن کامل خطا، و اینکه کدام رجیستری‌ها در دسترس بودند.
- اگر میرور معتبری برای PyPI، npm، داکر یا Hugging Face می‌شناسید، معرفی‌اش کنید.
- `README.md` و `README.fa.md` باید همیشه یک چیز را بگویند. اگر یکی را تغییر دادید، دیگری را هم به‌روز کنید، یا در PR بنویسید که ترجمه هنوز مانده است.
- پیام‌های commit با قالب [Conventional Commits](https://www.conventionalcommits.org/) نوشته می‌شوند (`feat:`، `fix:`، `docs:`، `test:`، `chore:`).
- پیش از ارسال PR، دستور `pytest -m "not network and not docker"` را اجرا کنید.

سؤال دارید؟ یک Issue باز کنید. فارسی یا انگلیسی، هر دو خوب است.

</div>
