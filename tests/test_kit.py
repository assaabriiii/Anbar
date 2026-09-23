import json

import pytest

from anbar.errors import KitError
from anbar.kit import FORMAT_VERSION, Kit, sha256_file


def test_create_add_and_reload(tmp_path):
    kit = Kit.open(tmp_path / "kit", create=True)
    f = kit.dir("python", "packages") / "demo-1.0-py3-none-any.whl"
    f.write_bytes(b"hello")
    art = kit.add(f, "python", "https://example/demo.whl")
    kit.save()
    assert art.path == "python/packages/demo-1.0-py3-none-any.whl"
    assert art.sha256 == sha256_file(f)

    again = Kit.open(tmp_path / "kit")
    assert again.get(art.path).size == 5
    assert again.has_valid(f)
    data = json.loads((tmp_path / "kit" / "manifest.json").read_text())
    assert data["format_version"] == FORMAT_VERSION


def test_open_missing(tmp_path):
    with pytest.raises(KitError, match="not found"):
        Kit.open(tmp_path / "nope")
    (tmp_path / "empty").mkdir()
    with pytest.raises(KitError, match="not an Anbar kit"):
        Kit.open(tmp_path / "empty")


def test_future_format_rejected(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"format_version": FORMAT_VERSION + 1}))
    with pytest.raises(KitError, match="upgrade Anbar"):
        Kit.open(tmp_path)


def test_has_valid_detects_size_change(tmp_path):
    kit = Kit.open(tmp_path, create=True)
    f = kit.dir("docs") / "a.txt"
    f.write_text("abc")
    kit.add(f, "docs", "x")
    f.write_text("abcd")
    assert not kit.has_valid(f)
