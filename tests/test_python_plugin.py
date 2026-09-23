import configparser
import os
import subprocess
import sys
import urllib.request

import pytest
from typer.testing import CliRunner

from anbar.cli import app
from anbar.config import Config, PythonTarget, load_config
from anbar.kit import Kit
from anbar.plugins.python import PythonPlugin, user_pip_config_path
from anbar.plugins.python.index import dist_name, dist_version

from helpers import fake_pypi, make_wheel

runner = CliRunner()


def test_dist_name():
    assert dist_name("Django-4.2.16-py3-none-any.whl") == "django"
    assert dist_name("zope.interface-6.0.tar.gz") == "zope-interface"
    assert dist_name("my_pkg-name-1.0.zip") == "my-pkg-name"
    assert dist_version("my_pkg-name-1.0.zip") == "1.0"
    assert dist_name("README.txt") is None


def test_plan_prefers_lock_over_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["poetry-core"]\n[project]\nname="x"\nversion="1"\ndependencies=["flask>=2"]\n'
    )
    (tmp_path / "poetry.lock").write_text('[[package]]\nname = "flask"\nversion = "3.0.2"\n')
    plugin = PythonPlugin()
    cfg = Config()
    plan = plugin.plan(tmp_path, cfg, plugin.detect(tmp_path, cfg))
    specs = [i.data["spec"] for i in plan.items]
    assert "flask==3.0.2" in specs
    assert "flask>=2" not in specs
    assert "poetry-core" in specs
    assert {"pip", "setuptools", "wheel"} <= set(specs)


def test_plan_extra_and_exclude(tmp_path):
    (tmp_path / "requirements.txt").write_text("numpy\nrequests\n")
    (tmp_path / "anbar.toml").write_text(
        '[python]\nextra = ["gunicorn"]\nexclude = ["numpy"]\ninclude_build_tools = false\n'
    )
    cfg = load_config(tmp_path)
    plugin = PythonPlugin()
    plan = plugin.plan(tmp_path, cfg, plugin.detect(tmp_path, cfg))
    assert [i.data["spec"] for i in plan.items] == ["requests", "gunicorn"]


def test_detect_skips_vendored_dirs(tmp_path):
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "node_modules" / "x" / "requirements.txt").write_text("a\n")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "pyproject.toml").write_text("")
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "requirements-dev.txt").write_text("b\n")
    found = PythonPlugin().detect(tmp_path, Config())
    assert [p.name for p in found] == ["requirements-dev.txt"]


def test_pip_command_for_targets(tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    from anbar.network import Network
    from anbar.plugins.base import Context

    (tmp_path / "requirements.txt").write_text("numpy==1.26.4\n")
    cfg = Config()
    cfg.python.include_build_tools = False
    cfg.python.targets = [PythonTarget(platform="win_amd64", python_version="3.11")]
    cfg.mirrors.pypi = ["http://mirror.local/simple"]
    plugin = PythonPlugin()
    plan = plugin.plan(tmp_path, cfg, plugin.detect(tmp_path, cfg))
    kit = Kit.open(tmp_path / "kit", create=True)
    ctx = Context(project=tmp_path, config=cfg, net=Network(), run=fake_run)
    plugin.fetch(plan, kit, ctx)
    cmd = calls[0]
    assert cmd[cmd.index("--platform") + 1] == "win_amd64"
    assert cmd[cmd.index("--python-version") + 1] == "3.11"
    assert "--only-binary=:all:" in cmd
    assert cmd[cmd.index("--index-url") + 1] == "http://mirror.local/simple"
    assert "--trusted-host" in cmd
    # A second run re-resolves loose requirements only when something changed.
    calls.clear()
    plugin.fetch(plan, kit, ctx)
    assert calls == []


def test_unreachable_index_gives_hint(tmp_path):
    from anbar.errors import AnbarError
    from anbar.network import Network
    from anbar.plugins.base import Context

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "WARNING: Retrying ... Failed to establish a new connection")

    (tmp_path / "requirements.txt").write_text("six\n")
    cfg = Config()
    plugin = PythonPlugin()
    plan = plugin.plan(tmp_path, cfg, plugin.detect(tmp_path, cfg))
    ctx = Context(project=tmp_path, config=cfg, net=Network(), run=fake_run)
    with pytest.raises(AnbarError) as info:
        plugin.fetch(plan, Kit.open(tmp_path / "kit", create=True), ctx)
    assert "PyPI unreachable" in info.value.message
    assert "--mirror" in info.value.hint


def test_use_and_restore_pip_config(tmp_path):
    kit = Kit.open(tmp_path / "kit", create=True)
    wheel = make_wheel(kit.dir("python", "packages"), "demo", "1.0")
    kit.add(wheel, "python", "test")
    kit.save()
    pip_conf = user_pip_config_path()
    pip_conf.parent.mkdir(parents=True, exist_ok=True)
    original = "[global]\ntimeout = 3\nextra-index-url = https://pypi.org/simple\n"
    pip_conf.write_text(original)

    result = runner.invoke(app, ["use", str(tmp_path / "kit"), "--pypi-port", "4000"])
    assert result.exit_code == 0, result.output
    parser = configparser.ConfigParser()
    parser.read(pip_conf)
    assert parser.get("global", "index-url") == "http://127.0.0.1:4000/simple/"
    assert parser.get("global", "timeout") == "3"
    assert not parser.has_option("global", "extra-index-url")

    again = runner.invoke(app, ["use", str(tmp_path / "kit")])
    assert again.exit_code != 0  # already active

    result = runner.invoke(app, ["restore"])
    assert result.exit_code == 0, result.output
    assert pip_conf.read_text() == original


def test_pack_serve_install_offline(tmp_path):
    """End to end without internet: fake upstream -> pack -> serve -> pip install."""
    upstream = fake_pypi(tmp_path, [("demo-app", "1.0", ["demo-dep>=2"]), ("demo-dep", "2.1", [])])
    try:
        project = tmp_path / "project"
        project.mkdir()
        (project / "requirements.txt").write_text("demo-app==1.0\n")
        (project / "anbar.toml").write_text("[python]\ninclude_build_tools = false\n")
        kit_dir = tmp_path / "kit"
        result = runner.invoke(
            app, ["pack", str(project), "--out", str(kit_dir), "--mirror", f"pypi={upstream.url}/simple/"]
        )
        assert result.exit_code == 0, result.output
    finally:
        upstream.stop()  # the internet is gone now

    kit = Kit.open(kit_dir)
    names = sorted(a.path.rsplit("/", 1)[1] for a in kit.by_ecosystem("python"))
    assert names == ["demo_app-1.0-py3-none-any.whl", "demo_dep-2.1-py3-none-any.whl"]
    assert all(a.meta.get("requires_python") == ">=3.8" for a in kit.by_ecosystem("python"))

    [service] = PythonPlugin().serve(kit, Config(), "127.0.0.1", {"pypi": 0})
    service.start()
    try:
        page = urllib.request.urlopen(f"{service.url}demo-app/").read().decode()
        assert "#sha256=" in page and 'data-requires-python="&gt;=3.8"' in page
        target = tmp_path / "site"
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check",
             "--target", str(target), "--index-url", service.url, "demo-app"],
            capture_output=True, text=True, env={**os.environ, "PIP_CONFIG_FILE": os.devnull},
        )
        assert proc.returncode == 0, proc.stderr
        assert (target / "demo_app" / "__init__.py").exists()
        assert (target / "demo_dep" / "__init__.py").exists()
    finally:
        service.stop()
