import base64
import hashlib
import http.server
import threading

import pytest

from anbar.config import NetworkConfig
from anbar.errors import IntegrityError, NetworkError
from anbar.network import Network, run_parallel, verify_integrity

PAYLOAD = b"x" * 200_000


class _Handler(http.server.BaseHTTPRequestHandler):
    hits: dict = {}

    def log_message(self, *args):
        pass

    def do_GET(self):
        _Handler.hits[self.path] = _Handler.hits.get(self.path, 0) + 1
        if self.path == "/flaky" and _Handler.hits[self.path] == 1:
            self.send_response(503)
            self.end_headers()
            return
        if self.path in ("/file", "/flaky"):
            start = 0
            rng = self.headers.get("Range")
            if rng:
                start = int(rng.split("=")[1].split("-")[0])
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}")
            else:
                self.send_response(200)
            body = PAYLOAD[start:]
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


@pytest.fixture
def server():
    _Handler.hits = {}
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def fast_net():
    return Network(NetworkConfig(retries=2, timeout=5))


def test_download_and_verify(server, tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    sha = hashlib.sha256(PAYLOAD).hexdigest()
    res = fast_net().download(f"{server}/file", tmp_path / "f.bin", sha256=sha)
    assert res.sha256 == sha and res.size == len(PAYLOAD)


def test_resume_partial(server, tmp_path):
    part = tmp_path / "f.bin.part"
    part.write_bytes(PAYLOAD[:1000])
    fast_net().download(f"{server}/file", tmp_path / "f.bin")
    assert (tmp_path / "f.bin").read_bytes() == PAYLOAD
    assert not part.exists()


def test_retry_then_success(server, tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    fast_net().download(f"{server}/flaky", tmp_path / "f.bin")
    assert _Handler.hits["/flaky"] == 2


def test_mirror_fallback(server, tmp_path):
    res = fast_net().download([f"{server}/missing", f"{server}/file"], tmp_path / "f.bin")
    assert res.url.endswith("/file")


def test_all_mirrors_fail_gives_hint(server, tmp_path):
    with pytest.raises(NetworkError) as info:
        fast_net().download([f"{server}/missing"], tmp_path / "f.bin")
    assert "mirror" in info.value.hint


def test_bad_checksum_deleted(server, tmp_path):
    with pytest.raises(NetworkError, match="sha256 mismatch"):
        fast_net().download(f"{server}/file", tmp_path / "f.bin", sha256="0" * 64)
    assert not (tmp_path / "f.bin").exists()


def test_sri(tmp_path):
    f = tmp_path / "a"
    f.write_bytes(b"abc")
    good = "sha512-" + base64.b64encode(hashlib.sha512(b"abc").digest()).decode()
    verify_integrity(f, integrity=good)
    with pytest.raises(IntegrityError):
        verify_integrity(f, integrity="sha512-AAAA")


def test_run_parallel_collects_errors():
    def work(x):
        if x == 2:
            raise ValueError("boom")
        return x * 10

    results = run_parallel([1, 2, 3], work, workers=3)
    by_item = {i: (r, e) for i, r, e in results}
    assert by_item[1][0] == 10 and by_item[3][0] == 30
    assert isinstance(by_item[2][1], ValueError)


def test_content_length_via_range(server):
    net = fast_net()
    assert net.content_length(f"{server}/file") == len(PAYLOAD)
    assert net.content_length(f"{server}/missing") is None
