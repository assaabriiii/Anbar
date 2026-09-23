import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import anbar.plugins.docker as docker_mod
from anbar.cli import app
from anbar.config import Config, load_config
from anbar.kit import Kit
from anbar.plugins.docker import DockerPlugin, image_filename, mirror_refs, split_reference
from anbar.plugins.docker.parsers import normalize_ref, parse_compose, parse_dockerfile_text, substitute

runner = CliRunner()


def refs(result):
    return [i.ref for i in result.images]


def test_substitute():
    assert substitute("a${X:-b}c", {}) == ("abc", [])
    assert substitute("${X:-b}", {"X": "y"}) == ("y", [])
    assert substitute("${X:+alt}", {"X": "1"}) == ("alt", [])
    assert substitute("$X-${Y}", {"X": "1"}) == ("1-${Y}", ["Y"])
    assert substitute("$$HOME", {}) == ("$HOME", [])


def test_normalize_ref():
    assert normalize_ref("python") == "python:latest"
    assert normalize_ref("localhost:5000/app") == "localhost:5000/app:latest"
    assert normalize_ref("ghcr.io/a/b:1") == "ghcr.io/a/b:1"
    assert normalize_ref("x@sha256:abc") == "x@sha256:abc"


def test_dockerfile_multistage_and_args(fixtures):
    text = (fixtures / "docker-app" / "Dockerfile").read_text()
    result = parse_dockerfile_text(text, "Dockerfile")
    assert refs(result) == ["python:3.12-slim", "ghcr.io/astral-sh/uv:0.4.0", "nginx:1.27-alpine"]
    assert result.images[2].platform == "linux/amd64"
    override = parse_dockerfile_text(text, "Dockerfile", {"PYTHON_VERSION": "3.11"})
    assert refs(override)[0] == "python:3.11-slim"


def test_unresolvable_arg_warns(fixtures):
    text = (fixtures / "docker-app" / "worker" / "Dockerfile.worker").read_text()
    result = parse_dockerfile_text(text, "Dockerfile.worker")
    assert refs(result) == ["alpine:latest"]
    assert "BASE_TAG" in result.warnings[0] and "build_args" in result.warnings[0]
    fixed = parse_dockerfile_text(text, "Dockerfile.worker", {"BASE_TAG": "1.36"})
    assert refs(fixed) == ["busybox:1.36", "alpine:latest"]


def test_escape_directive_and_comments():
    text = "# escape=`\nFROM `\n  mcr.microsoft.com/windows/nanoserver:ltsc2022\n# comment\nRUN echo\n"
    assert refs(parse_dockerfile_text(text, "x")) == ["mcr.microsoft.com/windows/nanoserver:ltsc2022"]


def test_compose(fixtures, monkeypatch):
    monkeypatch.delenv("POSTGRES_VERSION", raising=False)
    monkeypatch.delenv("REDIS_TAG", raising=False)
    result = parse_compose(fixtures / "docker-app" / "compose.yaml", "compose.yaml")
    assert refs(result) == [
        "postgres:16",
        "redis:7.2",
        "rabbitmq@sha256:0000000000000000000000000000000000000000000000000000000000000000",
    ]
    assert [p.name for p in result.dockerfiles] == ["Dockerfile", "Dockerfile.worker"]


def test_plan_collects_everything(fixtures, monkeypatch):
    monkeypatch.delenv("POSTGRES_VERSION", raising=False)
    monkeypatch.delenv("REDIS_TAG", raising=False)
    project = fixtures / "docker-app"
    cfg = Config()
    cfg.docker.extra = ["traefik:v3.1"]
    cfg.docker.exclude = ["alpine"]
    plugin = DockerPlugin()
    plan = plugin.plan(project, cfg, plugin.detect(project, cfg))
    got = sorted(i.data["ref"] for i in plan.items)
    assert "traefik:v3.1" in got and "postgres:16" in got and "python:3.12-slim" in got
    assert "alpine:latest" not in got
    assert any("BASE_TAG" in w for w in plan.warnings)


def test_mirror_refs():
    assert split_reference("python:3.12") == ("docker.io", "library/python:3.12")
    assert split_reference("ghcr.io/a/b:1") == ("ghcr.io", "a/b:1")
    assert mirror_refs("python:3.12", ["https://docker.mirror.ir/", "ghcr.io=ghcr.mirror.ir"]) == [
        "docker.mirror.ir/library/python:3.12",
        "python:3.12",
    ]
    assert mirror_refs("ghcr.io/a/b:1", ["docker.mirror.ir", "ghcr.io=ghcr.mirror.ir"]) == [
        "ghcr.mirror.ir/a/b:1",
        "ghcr.io/a/b:1",
    ]
    assert image_filename("ghcr.io/a/b:1", "linux/amd64") == "ghcr.io_a_b_1__linux_amd64.tar"


class FakeDocker:
    """Just enough of the docker CLI for pack / use / restore."""

    def __init__(self, reachable=("docker.mirror.ir/",)):
        self.reachable = reachable
        self.images: dict[str, str] = {}
        self.calls: list[list[str]] = []

    def __call__(self, args, env=None, check=False):
        self.calls.append(args)
        ok = lambda out="": subprocess.CompletedProcess(args, 0, out, "")  # noqa: E731
        fail = lambda err: subprocess.CompletedProcess(args, 1, "", err)  # noqa: E731
        cmd = args[0]
        if cmd == "version":
            return ok("27.0.0")
        if cmd == "pull":
            ref = args[-1]
            if any(ref.startswith(prefix) for prefix in self.reachable):
                self.images[ref] = "sha256:" + str(abs(hash(ref.split("/")[-1])))
                return ok()
            return fail("Error response from daemon: Get https://registry-1.docker.io/v2/: i/o timeout")
        if cmd == "tag":
            self.images[args[2]] = self.images[args[1]]
            return ok()
        if cmd in ("rmi",) or args[:2] == ["image", "rm"]:
            ref = args[-1]
            return ok() if self.images.pop(ref, None) else fail("No such image")
        if cmd == "save":
            Path(args[2]).write_bytes(("tar of " + args[3]).encode())
            return ok()
        if args[:2] == ["image", "inspect"]:
            ref = args[-1]
            return ok(self.images[ref]) if ref in self.images else fail("No such image")
        if cmd == "load":
            content = Path(args[2]).read_text()
            ref = content.removeprefix("tar of ")
            self.images[ref] = "sha256:" + str(abs(hash(ref.split("/")[-1])))
            return ok(f"Loaded image: {ref}")
        return fail(f"unexpected {args}")


def test_pack_use_restore_with_fake_docker(tmp_path, monkeypatch):
    fake = FakeDocker()
    monkeypatch.setattr(docker_mod, "run_docker", fake)
    project = tmp_path / "project"
    project.mkdir()
    (project / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (project / "anbar.toml").write_text('[mirrors]\ndocker = ["docker.mirror.ir"]\n')
    kit_dir = tmp_path / "kit"

    result = runner.invoke(app, ["pack", str(project), "--out", str(kit_dir)])
    assert result.exit_code == 0, result.output
    assert ["pull", "--quiet", "docker.mirror.ir/library/python:3.12-slim"] in fake.calls
    assert ["tag", "docker.mirror.ir/library/python:3.12-slim", "python:3.12-slim"] in fake.calls
    kit = Kit.open(kit_dir)
    [art] = list(kit.by_ecosystem("docker"))
    assert art.meta["image"] == "python:3.12-slim"

    # incremental: nothing pulled the second time
    fake.calls.clear()
    assert runner.invoke(app, ["pack", str(project), "--out", str(kit_dir)]).exit_code == 0
    assert not any(c[0] == "pull" for c in fake.calls)

    # a fresh machine: the image is not in Docker yet
    fake.images.clear()
    result = runner.invoke(app, ["use", str(kit_dir)])
    assert result.exit_code == 0, result.output
    assert "python:3.12-slim" in fake.images
    result = runner.invoke(app, ["restore"])
    assert result.exit_code == 0, result.output
    assert "python:3.12-slim" not in fake.images


def test_unreachable_docker_hub(tmp_path, monkeypatch):
    monkeypatch.setattr(docker_mod, "run_docker", FakeDocker(reachable=()))
    project = tmp_path / "p"
    project.mkdir()
    (project / "Dockerfile").write_text("FROM python:3.12-slim\n")
    result = runner.invoke(app, ["pack", str(project), "--out", str(tmp_path / "kit")])
    assert result.exit_code == 1
    assert "--mirror docker=" in result.output


@pytest.mark.docker
@pytest.mark.network
def test_real_docker_roundtrip(tmp_path):
    import shutil

    if shutil.which("docker") is None or subprocess.run(["docker", "version"], capture_output=True).returncode:
        pytest.skip("Docker daemon not available")
    project = tmp_path / "p"
    project.mkdir()
    (project / "Dockerfile").write_text("FROM busybox:1.36\n")
    result = runner.invoke(app, ["pack", str(project), "--out", str(tmp_path / "kit"), "--only", "docker"])
    assert result.exit_code == 0, result.output
    assert list((tmp_path / "kit" / "docker" / "images").glob("busybox*.tar"))
