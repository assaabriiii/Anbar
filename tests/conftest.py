from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def anbar_home(tmp_path, monkeypatch):
    """Keep every test away from the real ~/.anbar and user config files."""
    home = tmp_path / "anbar-home"
    monkeypatch.setenv("ANBAR_HOME", str(home))
    fake_user_home = tmp_path / "user-home"
    fake_user_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_user_home))
    monkeypatch.setenv("USERPROFILE", str(fake_user_home))
    monkeypatch.setenv("APPDATA", str(fake_user_home / "AppData" / "Roaming"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_user_home / ".config"))
    for var in ("PIP_INDEX_URL", "PIP_CONFIG_FILE", "NPM_CONFIG_REGISTRY", "HF_HOME", "HF_HUB_OFFLINE"):
        monkeypatch.delenv(var, raising=False)
    return home


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES
