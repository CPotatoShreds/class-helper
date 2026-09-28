import pytest

from class_helper.config import load_config


def test_load_example_config():
    cfg = load_config("config.example.toml")
    assert cfg.server.port == 8765
    assert cfg.detection.aliases == ["陈嘉毅", "嘉毅"]
    assert cfg.archive.keep_audio is True


def test_unknown_key_rejected(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text("[server]\npor = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="por"):
        load_config(p)


def test_explicit_missing_config_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.toml")


def test_archive_root_resolves_relative_to_cwd(tmp_path):
    import os

    old = os.getcwd()
    os.chdir(tmp_path)
    try:
        cfg = load_config(None)
        assert cfg.archive_root == (tmp_path / "archives").resolve()
    finally:
        os.chdir(old)
