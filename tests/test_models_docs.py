import hashlib
import http.server
import io
import threading
import urllib.request
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from anbar.backup import load_state
from anbar.cli import app
from anbar.errors import AnbarError
from anbar.kit import Kit
from anbar.plugins.files import extract_archive

runner = CliRunner()


def _docs_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("python-3.12-docs-html/index.html", "<h1>Python docs</h1>")
        zf.writestr("python-3.12-docs-html/library/os.html", "<h1>os</h1>")
    return buf.getvalue()


WEIGHTS = b"\x00weights" * 1000
FILES = {"/yolov8n.pt": WEIGHTS, "/python-docs.zip": _docs_zip(), "/manual.pdf": b"%PDF-1.4 fake"}


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = FILES.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_HEAD = do_GET


@pytest.fixture
def upstream():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_url_models_and_docs(tmp_path, upstream):
    project = tmp_path / "project"
    project.mkdir()
    sha = hashlib.sha256(WEIGHTS).hexdigest()
    (project / "anbar.toml").write_text(f"""
[[models.urls]]
url = "{upstream}/yolov8n.pt"
sha256 = "{sha}"
name = "yolo"

[[docs]]
name = "python"
url = "{upstream}/python-docs.zip"

[[docs]]
name = "manual"
url = "{upstream}/manual.pdf"
""")
    kit_dir = tmp_path / "kit"
    result = runner.invoke(app, ["scan", str(project)])
    assert result.exit_code == 0 and "yolo" in result.output
    result = runner.invoke(app, ["pack", str(project), "--out", str(kit_dir)])
    assert result.exit_code == 0, result.output
    assert (kit_dir / "models" / "files" / "yolo" / "yolov8n.pt").read_bytes() == WEIGHTS
    assert (kit_dir / "docs" / "site" / "python" / "library" / "os.html").exists()
    assert (kit_dir / "docs" / "site" / "manual" / "manual.pdf").exists()

    from anbar.config import Config
    from anbar.plugins.docs import DocsPlugin
    from anbar.plugins.models import ModelsPlugin

    kit = Kit.open(kit_dir)
    services = ModelsPlugin().serve(kit, Config(), "127.0.0.1", {"files": 0})
    services += DocsPlugin().serve(kit, Config(), "127.0.0.1", {"files": 0})
    assert services[0] is services[1]  # one shared file server
    server = services[0]
    server.start()
    try:
        base = server.url.rstrip("/")
        assert b"Python docs" in urllib.request.urlopen(f"{base}/docs/python/").read()
        assert urllib.request.urlopen(f"{base}/models/yolo/yolov8n.pt").read() == WEIGHTS
        req = urllib.request.Request(f"{base}/models/yolo/yolov8n.pt", headers={"Range": "bytes=0-3"})
        assert urllib.request.urlopen(req).read() == WEIGHTS[:4]
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"{base}/docs/..%2f..%2fmanifest.json")
    finally:
        server.stop()


def test_bad_sha_fails(tmp_path, upstream):
    project = tmp_path / "project"
    project.mkdir()
    (project / "anbar.toml").write_text(f'[[models.urls]]\nurl = "{upstream}/yolov8n.pt"\nsha256 = "{"0" * 64}"\n')
    result = runner.invoke(app, ["pack", str(project), "--out", str(tmp_path / "kit")])
    assert result.exit_code == 1
    assert "sha256 mismatch" in result.output


def test_unsafe_archive_rejected(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../evil.txt", "x")
    with pytest.raises(AnbarError, match="unsafe"):
        extract_archive(archive, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()


def fake_snapshot_download(repo_id, cache_dir, revision=None, **kwargs):
    repo = Path(cache_dir) / ("models--" + repo_id.replace("/", "--"))
    blob = repo / "blobs" / "abc123"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_text('{"model_type": "bert"}')
    (repo / "refs").mkdir(exist_ok=True)
    (repo / "refs" / "main").write_text("deadbeef")
    snap = repo / "snapshots" / "deadbeef"
    snap.mkdir(parents=True, exist_ok=True)
    link = snap / "config.json"
    if not link.exists():
        try:
            link.symlink_to(Path("..") / ".." / "blobs" / "abc123")
        except OSError:
            link.write_text(blob.read_text())
    fake_snapshot_download.calls += 1
    return str(snap)


fake_snapshot_download.calls = 0


def test_huggingface_pack_and_use(tmp_path, monkeypatch):
    hf = pytest.importorskip("huggingface_hub")
    monkeypatch.setattr(hf, "snapshot_download", fake_snapshot_download)
    fake_snapshot_download.calls = 0
    project = tmp_path / "project"
    project.mkdir()
    (project / "anbar.toml").write_text('[[models.huggingface]]\nrepo = "org/tiny"\nallow_patterns = ["*.json"]\n')
    kit_dir = tmp_path / "kit"
    assert runner.invoke(app, ["pack", str(project), "--out", str(kit_dir)]).exit_code == 0
    assert runner.invoke(app, ["pack", str(project), "--out", str(kit_dir)]).exit_code == 0
    assert fake_snapshot_download.calls == 1  # second pack is a no-op
    kit = Kit.open(kit_dir)
    paths = sorted(a.path for a in kit.by_ecosystem("models"))
    assert "models/hf/hub/models--org--tiny/blobs/abc123" in paths

    result = runner.invoke(app, ["use", str(kit_dir)])
    assert result.exit_code == 0, result.output
    env = load_state().env
    assert env["HF_HOME"] == str(kit.root / "models" / "hf")
    assert env["HF_HUB_OFFLINE"] == "1"
    exported = runner.invoke(app, ["env"])
    assert "export HF_HUB_OFFLINE='1'" in exported.output
    assert runner.invoke(app, ["restore"]).exit_code == 0


@pytest.mark.network
def test_real_huggingface_tiny_model(tmp_path):
    pytest.importorskip("huggingface_hub")
    project = tmp_path / "project"
    project.mkdir()
    (project / "anbar.toml").write_text(
        '[[models.huggingface]]\nrepo = "hf-internal-testing/tiny-random-bert"\nallow_patterns = ["config.json"]\n'
    )
    result = runner.invoke(app, ["pack", str(project), "--out", str(tmp_path / "kit")])
    assert result.exit_code == 0, result.output

    from huggingface_hub import hf_hub_download

    path = hf_hub_download(
        "hf-internal-testing/tiny-random-bert", "config.json",
        cache_dir=str(tmp_path / "kit" / "models" / "hf" / "hub"), local_files_only=True,
    )
    assert Path(path).exists()
