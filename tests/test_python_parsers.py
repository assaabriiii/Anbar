from anbar.plugins.python.parsers import (
    Requirement,
    parse_pipfile_lock,
    parse_poetry_lock,
    parse_pyproject,
    parse_requirements_txt,
    parse_uv_lock,
    poetry_version_to_pep440,
)


def specs(result):
    return [r.spec for r in result.requirements]


def test_requirements_txt(fixtures):
    result = parse_requirements_txt(fixtures / "python" / "requirements.txt")
    assert specs(result) == [
        "six",
        "Django>=4.2,<5",
        "requests[socks]==2.32.3",
        'pywin32==306 ; sys_platform == "win32"',
        "mypkg @ https://example.com/mypkg-1.0.tar.gz",
    ]
    assert result.constraints and result.constraints[0].name == "constraints.txt"
    joined = "\n".join(result.warnings)
    assert "VCS requirement" in joined
    assert "--index-url" in joined


def test_requirement_helpers():
    assert Requirement("requests[socks]==2.32.3", "x").pinned_version == "2.32.3"
    assert Requirement("Django>=4.2", "x").pinned_version is None
    assert Requirement("Foo.Bar_baz==1 ; python_version<'3.12'", "x").name == "foo-bar-baz"


def test_pyproject(fixtures):
    result = parse_pyproject(fixtures / "python" / "pyproject.toml")
    got = specs(result)
    assert "httpx>=0.27" in got
    assert "pytest>=8" in got
    assert "ruff" in got
    assert "numpy>=1.26,<2.0.0" in got
    assert "torch[cuda]>=2.2,<2.3.0" in got
    assert "setuptools>=61" in got and "wheel" in got
    assert not any("local" in s or "mylib" in s or s.startswith("python") for s in got)


def test_poetry_constraints():
    assert poetry_version_to_pep440("^0.2.3") == ">=0.2.3,<0.3.0"
    assert poetry_version_to_pep440("^0.0.3") == ">=0.0.3,<0.0.4"
    assert poetry_version_to_pep440("~1") == ">=1,<2.0.0"
    assert poetry_version_to_pep440("*") == ""
    assert poetry_version_to_pep440(">=1.0,<2") == ">=1.0,<2"
    assert poetry_version_to_pep440("1.2.3") == "==1.2.3"


def test_poetry_lock(fixtures):
    result = parse_poetry_lock(fixtures / "python" / "poetry.lock")
    assert specs(result) == ["certifi==2024.2.2", "Flask==3.0.2"]
    assert all(r.locked for r in result.requirements)


def test_uv_lock(fixtures):
    result = parse_uv_lock(fixtures / "python" / "uv.lock")
    assert specs(result) == ["anyio==4.3.0"]
    assert "gitdep" in result.warnings[0]


def test_pipfile_lock(fixtures):
    result = parse_pipfile_lock(fixtures / "python" / "Pipfile.lock")
    assert specs(result) == ["idna==3.6", "pytest==8.0.0"]


def test_broken_files_warn(tmp_path):
    (tmp_path / "poetry.lock").write_text("[[package]\n")
    assert parse_poetry_lock(tmp_path / "poetry.lock").warnings
    (tmp_path / "Pipfile.lock").write_text("{")
    assert parse_pipfile_lock(tmp_path / "Pipfile.lock").warnings
