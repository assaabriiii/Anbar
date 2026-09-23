"""Backups for ``anbar use`` and their exact undo in ``anbar restore``.

Every file ``anbar use`` touches is copied to ``$ANBAR_HOME/backups/<stamp>/``
first (or recorded as "did not exist"). ``anbar restore`` puts every file back
byte for byte, deletes files that did not exist before, and removes the
environment scripts Anbar generated.

``ANBAR_HOME`` defaults to ``~/.anbar``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from anbar.errors import AnbarError

STATE_FILE = "state.json"


def anbar_home() -> Path:
    env = os.environ.get("ANBAR_HOME")
    return Path(env).expanduser() if env else Path.home() / ".anbar"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class FileChange:
    path: str
    existed: bool
    backup: str | None
    written_sha256: str
    mode: int | None = None


@dataclass
class State:
    created_at: str
    kit: str | None
    files: list[FileChange] = field(default_factory=list)
    records: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    env_files: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "created_at": self.created_at,
                "kit": self.kit,
                "files": [f.__dict__ for f in self.files],
                "records": self.records,
                "env": self.env,
                "env_files": self.env_files,
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, text: str) -> "State":
        data = json.loads(text)
        return cls(
            created_at=data["created_at"],
            kit=data.get("kit"),
            files=[FileChange(**f) for f in data.get("files", [])],
            records=data.get("records", {}),
            env=data.get("env", {}),
            env_files=data.get("env_files", []),
        )


def load_state(home: Path | None = None) -> State | None:
    path = (home or anbar_home()) / STATE_FILE
    if not path.exists():
        return None
    return State.from_json(path.read_text(encoding="utf-8"))


class Changes:
    """Collects the edits made by ``anbar use`` and backs up originals first."""

    def __init__(self, kit: str | None, home: Path | None = None) -> None:
        self.home = home or anbar_home()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.backup_dir = self.home / "backups" / stamp
        self.state = State(created_at=stamp, kit=kit)
        self._by_path: dict[str, FileChange] = {}

    # -- file edits ---------------------------------------------------------

    @staticmethod
    def read(path: Path) -> str | None:
        try:
            return Path(path).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def write(self, path: Path, content: str) -> None:
        """Write ``content`` to ``path``, backing up the original the first time."""
        path = Path(path).expanduser().absolute()
        key = str(path)
        data = content.encode("utf-8")
        change = self._by_path.get(key)
        if change is None:
            existed = path.exists()
            backup = None
            mode = None
            if existed:
                self.backup_dir.mkdir(parents=True, exist_ok=True)
                backup_path = self.backup_dir / f"{len(self._by_path):03d}-{path.name}"
                shutil.copy2(path, backup_path)
                backup = str(backup_path)
                mode = path.stat().st_mode & 0o777
            change = FileChange(path=key, existed=existed, backup=backup, written_sha256="", mode=mode)
            self._by_path[key] = change
            self.state.files.append(change)
            # Persist after every backup so a crash mid-way is still restorable.
            self.save()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        change.written_sha256 = _sha(data)

    # -- other side effects --------------------------------------------------

    def record(self, plugin: str, data: dict[str, Any]) -> None:
        self.state.records.setdefault(plugin, []).append(data)

    def set_env(self, name: str, value: str) -> None:
        self.state.env[name] = value

    def save(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        tmp = self.home / (STATE_FILE + ".tmp")
        tmp.write_text(self.state.to_json(), encoding="utf-8")
        os.replace(tmp, self.home / STATE_FILE)

    def write_env_files(self) -> list[Path]:
        """Write shell snippets that export the collected environment variables."""
        if not self.state.env:
            return []
        env_dir = self.home / "env"
        env_dir.mkdir(parents=True, exist_ok=True)
        files = {
            "anbar.sh": "".join(f"export {k}={_sh_quote(v)}\n" for k, v in self.state.env.items()),
            "anbar.fish": "".join(f"set -gx {k} {_sh_quote(v)}\n" for k, v in self.state.env.items()),
            "anbar.ps1": "".join(f"$env:{k} = '{v.replace(chr(39), chr(39) * 2)}'\n" for k, v in self.state.env.items()),
            "anbar.bat": "".join(f'set "{k}={v}"\n' for k, v in self.state.env.items()),
        }
        written = []
        for name, content in files.items():
            path = env_dir / name
            path.write_text(content, encoding="utf-8")
            written.append(path)
        self.state.env_files = [str(p) for p in written]
        return written


def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


@dataclass
class RestoreReport:
    restored: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    modified_since: list[str] = field(default_factory=list)
    records: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


def restore_files(home: Path | None = None) -> RestoreReport:
    """Undo every file change recorded in the state file."""
    home = home or anbar_home()
    state = load_state(home)
    if state is None:
        raise AnbarError("nothing to restore: `anbar use` has not been run (or was already undone)")
    report = RestoreReport(records=state.records)
    for change in reversed(state.files):
        path = Path(change.path)
        if path.exists() and _sha(path.read_bytes()) != change.written_sha256:
            report.modified_since.append(change.path)
        if change.existed:
            if not change.backup or not Path(change.backup).exists():
                raise AnbarError(
                    f"backup of {change.path} is missing ({change.backup})",
                    f"inspect {home / 'backups'} and restore the file by hand",
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(change.backup, path)
            if change.mode is not None:
                os.chmod(path, change.mode)
            report.restored.append(change.path)
        else:
            if path.exists():
                path.unlink()
            report.removed.append(change.path)
    for env_file in state.env_files:
        Path(env_file).unlink(missing_ok=True)
    (home / STATE_FILE).unlink()
    return report
