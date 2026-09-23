from anbar.plugins.node.parsers import parse_package_lock, parse_pnpm_lock, parse_yarn_lock, split_name_version


def pkgs(result):
    return sorted((p.name, p.version, p.tarball) for p in result.packages)


def test_split_name_version():
    assert split_name_version("@scope/name@1.2.3") == ("@scope/name", "1.2.3")
    assert split_name_version("name@^1") == ("name", "^1")
    assert split_name_version("@scope/name") == ("@scope/name", "")


def test_package_lock_v1(fixtures):
    result = parse_package_lock(fixtures / "node" / "package-lock-v1.json")
    names = [(n, v) for n, v, _ in pkgs(result)]
    assert names == [("@scope/real", "2.0.0"), ("inner", "0.1.0"), ("left-pad", "1.3.0"), ("outer", "1.0.0")]


def test_package_lock_v3(fixtures):
    result = parse_package_lock(fixtures / "node" / "package-lock-v3.json")
    assert [(n, v) for n, v, _ in pkgs(result)] == [
        ("@babel/code-frame", "7.24.2"), ("b", "2.0.0"), ("real-pkg", "1.0.0"),
    ]
    assert any("git dependency gitdep" in w for w in result.warnings)
    code_frame = next(p for p in result.packages if p.name == "@babel/code-frame")
    assert code_frame.integrity.startswith("sha512-")
    assert code_frame.filename == "code-frame-7.24.2.tgz"


def test_yarn_classic(fixtures):
    result = parse_yarn_lock(fixtures / "node" / "yarn-classic.lock")
    got = {p.name: p for p in result.packages}
    assert set(got) == {"@babel/code-frame", "lodash", "real-thing"}
    assert got["@babel/code-frame"].version == "7.12.13"
    assert got["@babel/code-frame"].tarball.endswith("code-frame-7.12.13.tgz")
    assert got["@babel/code-frame"].integrity.startswith("sha512-")
    # sha1 from the URL fragment becomes an SRI string
    assert got["lodash"].integrity.startswith("sha1-")


def test_yarn_berry(fixtures):
    result = parse_yarn_lock(fixtures / "node" / "yarn-berry.lock")
    assert pkgs(result) == [
        ("@types/node", "20.11.0", "https://registry.npmjs.org/@types/node/-/node-20.11.0.tgz"),
        ("lodash", "4.17.21", "https://registry.npmjs.org/lodash/-/lodash-4.17.21.tgz"),
    ]


def test_pnpm_all_versions(fixtures):
    expected = [("@babel/core", "7.24.0"), ("styled", "6.0.0")]
    for name in ("pnpm-v6.yaml", "pnpm-v9.yaml"):
        result = parse_pnpm_lock(fixtures / "node" / name)
        got = [(n, v) for n, v, _ in pkgs(result)]
        assert all(e in got for e in expected), (name, got)
        assert not any(n == "local-thing" for n, _ in got)
    v5 = parse_pnpm_lock(fixtures / "node" / "pnpm-v5.yaml")
    assert pkgs(v5) == [
        ("@babel/core", "7.24.0", "https://registry.npmjs.org/@babel/core/-/core-7.24.0.tgz"),
        ("custom", "1.0.0", "https://example.com/custom-1.0.0.tgz"),
        ("styled", "6.0.0", "https://registry.npmjs.org/styled/-/styled-6.0.0.tgz"),
    ]
