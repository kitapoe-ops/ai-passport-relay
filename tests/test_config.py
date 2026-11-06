"""Environment defaults. Tokens are never given a built-in value."""

import pytest

from relay.config import (
    DEFAULT_LLM_BASE_URL,
    DEFAULT_PORT,
    DEFAULT_STT_URL,
    DEFAULT_TTS_URL,
    Config,
    ConfigError,
)


def test_defaults_and_overrides():
    cfg = Config.from_env({})
    assert cfg.stt_url == DEFAULT_STT_URL
    assert cfg.llm_base_url == DEFAULT_LLM_BASE_URL
    assert cfg.tts_url == DEFAULT_TTS_URL
    assert cfg.llm_completions_url() == DEFAULT_LLM_BASE_URL + "/v1/chat/completions"
    assert cfg.llm_model == "hkgai-v3"
    assert cfg.host == "0.0.0.0"
    assert cfg.port == DEFAULT_PORT == 8788
    assert cfg.task_store_path == "tasks.jsonl"
    assert cfg.relay_token == ""
    assert cfg.callback_secret == ""
    assert cfg.cursor_automation_token == ""
    assert cfg.cursor_automation_url == ""

    custom = Config.from_env(
        {
            "STT_URL": "https://stt.example/api/stt/",
            "LLM_BASE_URL": "https://llm.example/",
            "TTS_URL": "http://tts.example/api/tts",
            "LLM_MODEL": "other-model",
            "RELAY_HOST": "127.0.0.1",
            "RELAY_PORT": "9001",
            "TASK_STORE_PATH": "/var/lib/ai-passport-relay/tasks.jsonl",
            "CURSOR_AUTOMATION_URL": "https://cursor.example/hook",
            "RELAY_TOKEN": "placeholder-relay",
            "CALLBACK_SECRET": "placeholder-secret",
            "CURSOR_AUTOMATION_TOKEN": "placeholder-cursor",
        }
    )
    assert custom.stt_url == "https://stt.example/api/stt"
    assert custom.llm_completions_url() == "https://llm.example/v1/chat/completions"
    assert custom.tts_url == "http://tts.example/api/tts"
    assert custom.llm_model == "other-model"
    assert custom.host == "127.0.0.1"
    assert custom.port == 9001
    assert custom.relay_token == "placeholder-relay"
    text = repr(custom)
    assert "placeholder-relay" not in text
    assert "placeholder-secret" not in text
    assert "placeholder-cursor" not in text


def test_bad_env_is_rejected():
    with pytest.raises(ConfigError):
        Config.from_env({"RELAY_PORT": "nope"})
    with pytest.raises(ConfigError):
        Config.from_env({"STT_URL": "ftp://files.example/stt"})
    with pytest.raises(ConfigError):
        Config.from_env({"RELAY_PORT": "70000"})
