import json

import pytest

from companion.config import ConfigError, load_settings

MINIMAL_VALID = {
    "onebot": {
        "ws_url": "ws://127.0.0.1:3001/",
        "access_token": "some-token",
        "self_id": 3666167977,
    },
    "bot": {"allowed_private_senders": [844281600]},
}


def write_config(tmp_path, payload) -> str:
    path = tmp_path / "local.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_missing_file_mentions_example(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        load_settings(tmp_path / "absent.json")
    assert "bot.example.json" in str(excinfo.value)


def test_invalid_json_reported(tmp_path):
    path = tmp_path / "local.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_settings(path)
    assert "JSON" in str(excinfo.value)


def test_field_error_lists_location(tmp_path):
    bad = json.loads(json.dumps(MINIMAL_VALID))
    bad["onebot"]["self_id"] = 0  # gt=10000 不满足
    with pytest.raises(ConfigError) as excinfo:
        load_settings(write_config(tmp_path, bad))
    assert "onebot.self_id" in str(excinfo.value)


def test_minimal_valid_config_loads(tmp_path):
    settings = load_settings(write_config(tmp_path, MINIMAL_VALID))
    assert settings.onebot.self_id == 3666167977
    assert settings.bot.reply_mode == "fixed"
    assert settings.bot.fixed_reply_text == "收到，QQ 链路已连通。"
    assert settings.deepseek.model == "deepseek-flash"


def test_bad_ws_url_rejected(tmp_path):
    bad = json.loads(json.dumps(MINIMAL_VALID))
    bad["onebot"]["ws_url"] = "http://127.0.0.1:3001/"
    with pytest.raises(ConfigError):
        load_settings(write_config(tmp_path, bad))


def test_deepseek_mode_requires_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    payload = json.loads(json.dumps(MINIMAL_VALID))
    payload["bot"]["reply_mode"] = "deepseek"
    with pytest.raises(ConfigError) as excinfo:
        load_settings(write_config(tmp_path, payload))
    assert "DEEPSEEK_API_KEY" in str(excinfo.value)

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    settings = load_settings(write_config(tmp_path, payload))
    assert settings.bot.reply_mode == "deepseek"
