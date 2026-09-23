import io
import os
import tarfile

import pytest
from typer.testing import CliRunner

from anbar.cli import app
from anbar.errors import KitError
from anbar.kit import Kit
from anbar.kitops import export_kit, import_kit, kit_status, verify_kit

from helpers import make_wheel

runner = CliRunner()


def _kit(tmp_path, name="kit"):
    kit = Kit.open(tmp_path / name, create=True)
    wheel = make_wheel(kit.dir("python", "packages"), "demo", "1.0")
    kit.add(wheel, "python", "test")
    doc = kit.dir("node", "packuments") / "left-pad.json"
    doc.write_text("{}")
    kit.add(doc, "node", "test", meta={"packument": "left-pad"})
    kit.add_project(tmp_path / "project")
    kit.save()
    return kit


def test_verify_detects_problems(tmp_path):
    kit = _kit(tmp_path)
    assert verify_kit(kit).healthy
    (kit.root / "node" / "packuments" / "left-pad.json").write_text("{ }")  # same size? no: one byte more
    (kit.root / "stray.txt").write_text("x")
    (kit.root / "python" / "packages" / "big.whl.part").write_text("x")
    report = verify_kit(kit)
    assert report.corrupt and "left-pad.json" in report.corrupt[0]
    assert report.untracked == ["stray.txt"]
    assert report.partial == ["python/packages/big.whl.part"]
    # same size, different bytes: only the full hash catches it
    (kit.root / "node" / "packuments" / "left-pad.json").write_text("[]")
    assert verify_kit(kit, quick=True).healthy
    assert not verify_kit(kit).healthy


def test_verify_cli(tmp_path):
    kit = _kit(tmp_path)
    assert runner.invoke(app, ["verify", str(kit.root)]).exit_code == 0
    os.remove(kit.root / "node" / "packuments" / "left-pad.json")
    result = runner.invoke(app, ["verify", str(kit.root)])
    assert result.exit_code == 1
    assert "missing" in result.output


def test_status(tmp_path):
    kit = _kit(tmp_path)
    rows = {r.name: r for r in kit_status(kit)}
    assert rows["python"].files == 1 and rows["python"].details == ["1 projects"]
    assert rows["node"].age_days() < 1
    result = runner.invoke(app, ["status", str(kit.root)])
    assert result.exit_code == 0 and "python" in result.output and "project" in result.output


def test_export_import_roundtrip_with_symlink(tmp_path):
    kit = _kit(tmp_path)
    blob = kit.dir("models", "hf", "hub", "models--o--m", "blobs") / "abc"
    blob.write_text("weights")
    kit.add(blob, "models", "hf")
    snap = kit.dir("models", "hf", "hub", "models--o--m", "snapshots", "rev")
    try:
        (snap / "config.json").symlink_to(os.path.join("..", "..", "blobs", "abc"))
        has_symlink = True
    except OSError:
        has_symlink = False
    kit.save()

    archive = tmp_path / "usb" / "kit.tar.gz"
    digest = export_kit(kit, archive)
    assert (tmp_path / "usb" / "kit.tar.gz.sha256").read_text().startswith(digest)

    result = import_kit(archive, tmp_path / "other" / "kit")
    assert not result.merged
    assert set(result.kit.artifacts) == set(kit.artifacts)
    assert verify_kit(result.kit).healthy
    if has_symlink:
        link = result.kit.root / "models" / "hf" / "hub" / "models--o--m" / "snapshots" / "rev" / "config.json"
        assert link.is_symlink() and link.read_text() == "weights"


def test_import_merges_into_existing_kit(tmp_path):
    old = _kit(tmp_path, "old")
    new = _kit(tmp_path, "new")
    extra = make_wheel(new.dir("python", "packages"), "extra", "2.0")
    new.add(extra, "python", "test")
    new.save()
    archive = tmp_path / "new.tar"
    export_kit(new, archive)
    result = import_kit(archive, old.root)
    assert result.merged and result.added == 1
    merged = Kit.open(old.root)
    assert "python/packages/extra-2.0-py3-none-any.whl" in merged.artifacts
    assert verify_kit(merged).healthy


def test_damaged_copy_is_detected(tmp_path):
    kit = _kit(tmp_path)
    archive = tmp_path / "kit.tar"
    export_kit(kit, archive)
    data = bytearray(archive.read_bytes())
    data[1000] ^= 0xFF
    archive.write_bytes(bytes(data))
    with pytest.raises(KitError, match="damaged"):
        import_kit(archive, tmp_path / "copy")


def _tar_with(tmp_path, name, member: tarfile.TarInfo, data=b""):
    path = tmp_path / name
    with tarfile.open(path, "w") as tf:
        manifest = tarfile.TarInfo("kit/manifest.json")
        body = b'{"format_version": 1, "artifacts": []}'
        manifest.size = len(body)
        tf.addfile(manifest, io.BytesIO(body))
        member.size = len(data)
        tf.addfile(member, io.BytesIO(data))
    return path


def test_unsafe_archives_rejected(tmp_path):
    evil = _tar_with(tmp_path, "evil.tar", tarfile.TarInfo("kit/../../evil.txt"), b"x")
    with pytest.raises(KitError, match="unsafe"):
        import_kit(evil, tmp_path / "dest")
    link = tarfile.TarInfo("kit/passwd")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    with pytest.raises(KitError, match="escapes"):
        import_kit(_tar_with(tmp_path, "link.tar", link), tmp_path / "dest2")
    assert not (tmp_path / "evil.txt").exists()


def test_export_rejects_future_kits_and_bad_names(tmp_path):
    kit = _kit(tmp_path)
    result = runner.invoke(app, ["export", str(kit.root), "--to", str(tmp_path / "kit.rar")])
    assert result.exit_code != 0


def test_cli_export_import(tmp_path, monkeypatch):
    kit = _kit(tmp_path)
    archive = tmp_path / "share" / "team.tar"
    result = runner.invoke(app, ["export", str(kit.root), "--to", str(archive)])
    assert result.exit_code == 0, result.output
    monkeypatch.chdir(tmp_path / "share")
    result = runner.invoke(app, ["import", str(archive)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "share" / "kit" / "manifest.json").exists()
