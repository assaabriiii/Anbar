# Kit format

A kit is a directory that can be copied, archived or served as-is. This page describes format version **1**.

## Layout

```text
KIT_DIR/
├── manifest.json
├── python/
│   ├── packages/                 # wheels and sdists, flat
│   └── .anbar-state.json         # pack bookkeeping (which specs were resolved for which target)
├── node/
│   ├── tarballs/<name>/-/<basename>-<version>.tgz
│   └── packuments/<name>.json    # scoped packages: packuments/@scope/name.json
├── docker/
│   ├── images/<reference>[__<platform>].tar   # `docker save` output
│   └── registry-data/            # runtime data of `anbar serve` with registry:2 (not tracked)
├── models/
│   ├── hf/hub/models--<org>--<name>/...       # huggingface_hub cache layout; HF_HOME=models/hf
│   └── files/[<name>/]<filename>
└── docs/
    ├── archives/<name>/<file>    # tracked
    └── site/<name>/              # extracted from the archive (derived, rebuilt on demand)
```

Files and folders whose names start with a dot, `*.part` files (downloads in progress), `docs/site/` and `docker/registry-data/` are not artifacts. `verify` doesn't report them as untracked, and `export` leaves out `*.part`, `docs/site/` and `docker/registry-data/`.

## manifest.json

```json
{
  "format_version": 1,
  "anbar_version": "0.1.0",
  "created_at": "2026-09-23T10:00:00+00:00",
  "updated_at": "2026-09-23T10:05:00+00:00",
  "projects": ["my-project"],
  "artifacts": [
    {
      "path": "python/packages/Django-4.2.16-py3-none-any.whl",
      "ecosystem": "python",
      "source": "https://pypi.org/simple (Django-4.2.16-py3-none-any.whl)",
      "sha256": "…",
      "size": 8007621,
      "downloaded_at": "2026-09-23T10:01:02+00:00",
      "meta": { "requires_python": ">=3.8" }
    }
  ]
}
```

| Field | Meaning |
| --- | --- |
| `format_version` | Integer, bumped on every incompatible layout change. |
| `anbar_version` | Version of Anbar that last wrote the manifest. |
| `projects` | Names of the project directories packed into this kit. |
| `artifacts[].path` | Path relative to the kit root, always with `/`. |
| `artifacts[].ecosystem` | The plugin that owns the file (`python`, `node`, `docker`, `models`, `docs`). |
| `artifacts[].source` | Where it was downloaded from (URL, index, or `docker pull <ref>`). |
| `artifacts[].sha256` / `size` | Integrity data used by `verify`, `export` and `import`. |
| `artifacts[].downloaded_at` | UTC timestamp (ISO 8601), used by `status` for freshness. |
| `artifacts[].meta` | Plugin-specific data, for example `requires_python`, npm `integrity`, Docker `image` / `id` / `platform`, or Hugging Face `repo`. |

The manifest never contains credentials.

## Compatibility

- A newer Anbar must read every older `format_version`. Migrations live in `anbar.kit._migrate` and run in memory when a kit is opened. The upgraded manifest is written the next time the kit is saved.
- An older Anbar refuses kits with a higher `format_version` and says that Anbar needs upgrading, instead of misreading them.
- Adding optional fields (including new `meta` keys) does not change the format version.
