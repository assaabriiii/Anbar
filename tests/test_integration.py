"""End-to-end tests against the real registries (pack -> serve -> install offline).

They need internet access and are skipped with ``-m "not network"``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from anbar.cli import app
from anbar.config import Config
from anbar.kit import Kit
from anbar.plugins.python import PythonPlugin

runner = CliRunner()


def _copy(fixtures, name, tmp_path):
    dest = tmp_path / name
    shutil.copytree(fixtures / name, dest)
    return dest


@pytest.mark.network
def test_django_app_offline(fixtures, tmp_path):
    project = _copy(fixtures, "django-app", tmp_path)
    kit_dir = tmp_path / "kit"
    result = runner.invoke(app, ["pack", str(project), "--out", str(kit_dir), "--only", "python"])
    assert result.exit_code == 0, result.output
    verify = runner.invoke(app, ["verify", str(kit_dir)]) if "verify" in [c.name for c in app.registered_commands] else None
    if verify is not None:
        assert verify.exit_code == 0, verify.output

    kit = Kit.open(kit_dir)
    [service] = PythonPlugin().serve(kit, Config(), "127.0.0.1", {"pypi": 0})
    service.start()
    try:
        target = tmp_path / "site"
        env = {**os.environ, "PIP_CONFIG_FILE": os.devnull}
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            env.pop(key, None)  # prove nothing comes from the internet
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check",
             "--target", str(target), "--index-url", service.url, "-r", str(project / "requirements.txt")],
            capture_output=True, text=True, env=env,
        )
        assert proc.returncode == 0, proc.stderr
        check = subprocess.run(
            [sys.executable, "manage.py", "check"],
            cwd=project, capture_output=True, text=True, env={**env, "PYTHONPATH": str(target)},
        )
        assert check.returncode == 0, check.stderr
    finally:
        service.stop()
