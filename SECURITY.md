# Security policy

Anbar downloads software and serves it to other tools, so security bugs matter here. Thank you for helping keep its users safe.

## Supported versions

Security fixes go into the latest release. Because kits are often used where upgrading is hard, fixes are also backported to the previous minor release when that is practical.

## Reporting a vulnerability

**Please don't open a public issue for security problems.** Instead, report them privately through GitHub's [private vulnerability reporting](https://github.com/assaabriiii/Anbar/security/advisories/new).

Please include:

- the version of Anbar, your operating system and your Python version
- what an attacker could do, and under which conditions (for example a malicious mirror, a crafted kit archive, or a user on the same LAN)
- steps to reproduce, or a proof of concept

You'll get an acknowledgement within a week. We'll work with you on a fix and a disclosure date, and credit you in the release notes unless you'd rather stay anonymous.

## Security design

These properties are intentional. Breaking any of them counts as a vulnerability:

- **TLS verification is never disabled.** The only way to change trust is to add a CA bundle (`[network] ca_bundle`).
- **No credentials in kits.** Proxy credentials come from `ANBAR_PROXY_USER` / `ANBAR_PROXY_PASSWORD`, and Hugging Face tokens from `HF_TOKEN`. Anbar never writes them to a kit, a manifest or a log. Authenticated index URLs in `requirements.txt` are ignored.
- **Integrity checks.** Downloads are verified against the hashes published by the registry (pip index hashes, npm SRI) or the `sha256` configured in `anbar.toml`. Every artifact's sha256 is recorded in `manifest.json` and can be checked again with `anbar verify`.
- **Safe imports.** `anbar import` and archive extraction reject absolute paths, `..` components, device files, and links that point outside the target directory. A `.sha256` sidecar is checked when it is present.
- **Local-only by default.** `anbar serve` listens on `127.0.0.1` unless `--host` or `[serve] host` says otherwise, and the servers are read-only.
- **Reversible configuration.** `anbar use` backs up every file before changing it, and `anbar restore` restores the original bytes.

## Things to keep in mind

- A kit is only as trustworthy as the machine and the mirrors that built it. Before you use a kit from someone else, check it with `anbar verify`, and compare the export's `.sha256` through a channel you trust.
- Third-party mirrors can serve different content from the official registries. Pin versions in lock files (which carry hashes), and set `sha256` for URL downloads.
- When you share a kit on the LAN with `--host 0.0.0.0`, anyone on that network can read it.
