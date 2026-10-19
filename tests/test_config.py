import json

import pytest

from plexbot.config import Config, parse_env_value, setup_default_config


def fresh_config(monkeypatch):
    """A Config with every real setting registered, so env names match the bot's."""
    from plexbot import config as config_module

    fresh = Config()
    monkeypatch.setattr(config_module, "config", fresh)
    setup_default_config()
    return fresh


@pytest.mark.parametrize("raw, default, expected", [
    ("yes", False, True),
    ("0", True, False),
    ("3600", 0, 3600),
    ("#E5A00D", 0, 0xE5A00D),
    ("0xE5A00D", 0, 0xE5A00D),
    ("2.5", 1.0, 2.5),
    ("123, 456,", [], ["123", "456"]),
    ("host:8181", "", "host:8181"),
])
def test_parse_env_value(raw, default, expected):
    assert parse_env_value(raw, default) == expected


@pytest.mark.parametrize("raw, default", [("maybe", False), ("abc", 0)])
def test_parse_env_value_rejects_bad_values(raw, default):
    with pytest.raises(ValueError):
        parse_env_value(raw, default)


def test_environment_alone_is_enough(monkeypatch, tmp_path):
    cfg = fresh_config(monkeypatch)
    for name, value in {"PLEXBOT_CORE_TOKEN": "t", "PLEXBOT_TAUTULLI_IP": "tautulli:8181",
                        "PLEXBOT_TAUTULLI_APIKEY": "k", "PLEXBOT_TAUTULLI_USE_HTTPS": "false",
                        "PLEXBOT_DISCORD_ADMIN_IDS": "1,2"}.items():
        monkeypatch.setenv(name, value)
    assert cfg.initialize(str(tmp_path / "missing.json"))
    assert cfg.get("tautulli", "ip") == "tautulli:8181"
    assert cfg.get("tautulli", "use_https") is False
    assert cfg.get("discord", "admin_ids") == ["1", "2"]


def test_environment_overrides_file_and_empty_means_unset(monkeypatch, tmp_path):
    cfg = fresh_config(monkeypatch)
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"core": {"token": "file"}, "tautulli": {"ip": "file:8181", "apikey": "k"}}))
    monkeypatch.setenv("PLEXBOT_TAUTULLI_IP", "env:8181")
    monkeypatch.setenv("PLEXBOT_CORE_TOKEN", "")
    assert cfg.initialize(str(path))
    assert cfg.get("tautulli", "ip") == "env:8181"
    assert cfg.get("core", "token") == "file"


def test_missing_required_names_the_variable(monkeypatch, tmp_path, caplog):
    cfg = fresh_config(monkeypatch)
    monkeypatch.setenv("PLEXBOT_TAUTULI_IP", "typo")
    assert not cfg.initialize(str(tmp_path / "missing.json"))
    assert "PLEXBOT_CORE_TOKEN" in caplog.text
    assert "Unknown setting PLEXBOT_TAUTULI_IP" in caplog.text
