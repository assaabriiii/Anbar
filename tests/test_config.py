from pathlib import Path

import pytest

from anbar.config import load_config, parse_config
from anbar.errors import ConfigError


def test_defaults_when_no_file(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.path is None
    assert cfg.serve.pypi_port == 3141
    assert cfg.python.include_build_tools is True
    assert cfg.mirrors.pypi == []


def test_full_config(tmp_path):
    (tmp_path / "anbar.toml").write_text(
        """
ecosystems = ["python", "docker"]

[python]
extra = ["requests==2.32.3"]
platforms = ["manylinux2014_x86_64", "win_amd64"]
python_versions = ["3.11", "3.12"]

[[python.targets]]
platform = "macosx_11_0_arm64"
python_version = "3.12"

[docker]
extra = ["redis:7"]
platform = "linux/amd64"
build_args = { PY = 3.12 }

[[models.huggingface]]
repo = "bert-base-uncased"
allow_patterns = ["*.json"]

[[models.urls]]
url = "https://example.com/yolo.pt"
sha256 = "abc"

[[docs]]
name = "python"
url = "https://example.com/python-docs.zip"

[mirrors]
pypi = ["https://mirror.example/simple", "https://pypi.org/simple"]
docker = "docker.mirror.example"

[proxy]
socks = "socks5h://127.0.0.1:1080"

[serve]
host = "0.0.0.0"
npm_port = 5000
""",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.ecosystems == ["python", "docker"]
    assert cfg.python.extra == ["requests==2.32.3"]
    # 1 explicit target + 2x2 cross product
    assert len(cfg.python.targets) == 5
    assert cfg.python.targets[0].platform == "macosx_11_0_arm64"
    assert {t.label() for t in cfg.python.targets[1:]} == {
        "manylinux2014_x86_64-py3.11", "manylinux2014_x86_64-py3.12", "win_amd64-py3.11", "win_amd64-py3.12",
    }
    assert cfg.docker.build_args == {"PY": "3.12"}
    assert cfg.models.huggingface[0].allow_patterns == ["*.json"]
    assert cfg.models.urls[0].sha256 == "abc"
    assert cfg.docs[0].name == "python"
    assert cfg.mirrors.pypi[0] == "https://mirror.example/simple"
    assert cfg.mirrors.docker == ["docker.mirror.example"]
    assert cfg.proxy.socks.startswith("socks5h")
    assert cfg.serve.host == "0.0.0.0"
    assert cfg.serve.npm_port == 5000


def test_invalid_toml(tmp_path):
    (tmp_path / "anbar.toml").write_text("[python\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(tmp_path)


def test_tls_verification_cannot_be_disabled():
    with pytest.raises(ConfigError, match="TLS"):
        parse_config({"network": {"verify_tls": False}})


def test_unknown_keys_warn(capsys):
    parse_config({"pythn": {}})
    assert "unknown key 'pythn'" in capsys.readouterr().err


def test_bad_port():
    with pytest.raises(ConfigError, match="port"):
        parse_config({"serve": {"pypi_port": 99999}})


def test_missing_explicit_config(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path, Path(tmp_path / "nope.toml"))


def test_docs_require_name_and_url():
    with pytest.raises(ConfigError):
        parse_config({"docs": [{"url": "https://x"}]})
