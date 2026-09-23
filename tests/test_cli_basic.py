from typer.testing import CliRunner

from anbar import __version__
from anbar.cli import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_scan_empty_project(tmp_path):
    result = runner.invoke(app, ["scan", str(tmp_path)])
    assert result.exit_code == 0
    assert "Nothing found" in result.stdout


def test_scan_fixture_projects_offline(fixtures):
    result = runner.invoke(app, ["scan", str(fixtures / "docker-app"), "--offline"])
    assert result.exit_code == 0, result.output
    assert "python" in result.output and "postgres" in result.output
    assert "BASE_TAG" in result.output  # unresolved ARG warning


def test_unknown_ecosystem_is_an_error(tmp_path):
    from anbar.errors import AnbarError

    result = runner.invoke(app, ["scan", str(tmp_path), "--only", "cobol"])
    assert isinstance(result.exception, AnbarError)
    assert "choose from" in result.exception.hint


def test_bad_mirror_syntax(tmp_path):
    (tmp_path / "requirements.txt").write_text("six\n")
    result = runner.invoke(app, ["pack", str(tmp_path), "--out", str(tmp_path / "kit"), "--mirror", "nope"])
    assert "ECOSYSTEM=URL" in result.exception.hint
