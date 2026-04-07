import importlib.metadata


def test_version():
    version = importlib.metadata.version("ymmsl2svg")
    assert "unknown" not in version
    assert version != ""
