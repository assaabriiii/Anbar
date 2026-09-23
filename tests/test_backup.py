import pytest

from anbar.backup import Changes, load_state, restore_files
from anbar.errors import AnbarError


def test_backup_and_restore_exactly(tmp_path, anbar_home):
    existing = tmp_path / "pip.conf"
    existing.write_text("[global]\ntimeout = 10\n")
    new = tmp_path / "sub" / ".npmrc"

    changes = Changes(kit=str(tmp_path / "kit"))
    changes.write(existing, "[global]\nindex-url = http://127.0.0.1:3141/simple/\n")
    changes.write(existing, "second write keeps the first backup\n")
    changes.write(new, "registry=http://127.0.0.1:4873/\n")
    changes.set_env("HF_HUB_OFFLINE", "1")
    changes.write_env_files()
    changes.save()

    state = load_state()
    assert len(state.files) == 2
    assert (anbar_home / "env" / "anbar.sh").read_text() == "export HF_HUB_OFFLINE='1'\n"

    report = restore_files()
    assert existing.read_text() == "[global]\ntimeout = 10\n"
    assert not new.exists()
    assert not (anbar_home / "env" / "anbar.sh").exists()
    assert load_state() is None
    assert report.modified_since == []


def test_restore_reports_user_edits(tmp_path):
    target = tmp_path / "file"
    changes = Changes(kit=None)
    changes.write(target, "anbar")
    changes.save()
    target.write_text("user edited")
    report = restore_files()
    assert report.modified_since == [str(target.absolute())]


def test_restore_without_state():
    with pytest.raises(AnbarError, match="nothing to restore"):
        restore_files()
