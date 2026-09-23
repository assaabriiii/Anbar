import json
import os
import shutil
import subprocess
import urllib.request

import pytest
import yaml
from typer.testing import CliRunner

from anbar.cli import app
from anbar.config import Config
from anbar.kit import Kit
from anbar.plugins.node import NodePlugin, _trim
from anbar.plugins.base import PlanItem

from helpers import fake_npm_registry

runner = CliRunner()


def _lock(project, packages):
    entries = {"": {"name": "app", "version": "1.0.0", "dependencies": {n: v for n, v, _ in packages[:1]}}}
    for name, version, deps in packages:
        entries[f"node_modules/{name}"] = {
            "version": version,
            "dependencies": deps,
            "resolved": f"https://registry.npmjs.org/{name}/-/{name.split('/')[-1]}-{version}.tgz",
        }
    (project / "package.json").write_text(json.dumps(
        {"name": "app", "version": "1.0.0", "dependencies": {n: v for n, v, _ in packages[:1]}}
    ))
    (project / "package-lock.json").write_text(json.dumps(
        {"name": "app", "version": "1.0.0", "lockfileVersion": 3, "requires": True, "packages": entries}
    ))


def test_trim_keeps_only_kit_versions():
    full = {
        "name": "x",
        "dist-tags": {"latest": "3.0.0", "next": "2.0.0"},
        "versions": {v: {"name": "x", "version": v, "dist": {"tarball": "t"}} for v in ("1.0.0", "2.0.0", "3.0.0")},
    }
    items = [PlanItem("x", "2.0.0", data={"tarball": "t"}), PlanItem("x", "1.0.0", data={"tarball": "t"})]
    doc = _trim("x", full, None, items)
    assert list(doc["versions"]) == ["1.0.0", "2.0.0"]
    assert doc["dist-tags"] == {"latest": "2.0.0", "next": "2.0.0"}


def test_pack_serve_install_offline(tmp_path):
    packages = [("hello-dep", "1.0.0", {"@demo/util": "^2.0.0"}), ("@demo/util", "2.1.0", {})]
    upstream = fake_npm_registry(tmp_path, packages)
    project = tmp_path / "project"
    project.mkdir()
    _lock(project, packages)
    kit_dir = tmp_path / "kit"
    try:
        result = runner.invoke(app, ["pack", str(project), "--out", str(kit_dir), "--mirror", f"npm={upstream.url}"])
        assert result.exit_code == 0, result.output
        # Second pack is incremental.
        again = runner.invoke(app, ["pack", str(project), "--out", str(kit_dir), "--mirror", f"npm={upstream.url}"])
        assert again.exit_code == 0
        assert "Node" in again.output
    finally:
        upstream.stop()

    kit = Kit.open(kit_dir)
    paths = sorted(a.path for a in kit.by_ecosystem("node"))
    assert "node/tarballs/@demo/util/-/util-2.1.0.tgz" in paths
    assert "node/packuments/hello-dep.json" in paths

    [service] = NodePlugin().serve(kit, Config(), "127.0.0.1", {"npm": 0})
    service.start()
    try:
        doc = json.load(urllib.request.urlopen(service.url + "@demo%2futil"))
        assert doc["versions"]["2.1.0"]["dist"]["tarball"].startswith(service.url.rstrip("/"))
        assert json.load(urllib.request.urlopen(service.url + "-/ping")) == {}
        manifest = json.load(urllib.request.urlopen(service.url + "hello-dep/latest"))
        assert manifest["version"] == "1.0.0"
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(service.url + "not-in-kit")

        npm = shutil.which("npm")
        if npm:
            env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
            env["npm_config_cache"] = str(tmp_path / "npm-cache")
            proc = subprocess.run(
                [npm, "install", "--registry", service.url, "--no-audit", "--no-fund", "--ignore-scripts"],
                cwd=project, capture_output=True, text=True, env=env, shell=os.name == "nt",
            )
            assert proc.returncode == 0, proc.stderr
            assert (project / "node_modules" / "@demo" / "util" / "index.js").exists()
    finally:
        service.stop()


def test_use_and_restore_npm_configs(tmp_path):
    kit = Kit.open(tmp_path / "kit", create=True)
    f = kit.dir("node", "packuments") / "x.json"
    f.write_text("{}")
    kit.add(f, "node", "test")
    kit.save()
    home = os.path.expanduser("~")
    npmrc = os.path.join(home, ".npmrc")
    with open(npmrc, "w") as fh:
        fh.write("registry=https://registry.npmjs.org/\n@corp:registry=https://npm.corp/\n")

    result = runner.invoke(app, ["use", str(tmp_path / "kit"), "--npm-port", "4900", "--host", "10.0.0.5"])
    assert result.exit_code == 0, result.output
    content = open(npmrc).read()
    assert "registry=http://10.0.0.5:4900/" in content
    assert "@corp:registry=https://npm.corp/" in content
    berry = yaml.safe_load(open(os.path.join(home, ".yarnrc.yml")))
    assert berry["unsafeHttpWhitelist"] == ["10.0.0.5"]

    assert runner.invoke(app, ["restore"]).exit_code == 0
    assert open(npmrc).read() == "registry=https://registry.npmjs.org/\n@corp:registry=https://npm.corp/\n"
    assert not os.path.exists(os.path.join(home, ".yarnrc.yml"))
    assert not os.path.exists(os.path.join(home, ".yarnrc"))
