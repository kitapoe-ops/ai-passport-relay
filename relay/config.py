"""Environment configuration. Secrets stay in the process environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


DEFAULT_STT_URL = "https://asrhkchat.kitahim.uk/api/stt"
DEFAULT_LLM_BASE_URL = "https://chat.kitahim.uk"
DEFAULT_TTS_URL = "https://tts.kitahim.uk/api/tts"
DEFAULT_LLM_MODEL = "hkgai-v3"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8788
DEFAULT_TASK_STORE = "tasks.jsonl"

MAX_WAV_BYTES = 4 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024
MAX_TASK_INPUT_CHARS = 8000
MAX_SUMMARY_CHARS = 500
MAX_ARTIFACT_URL_CHARS = 2000
TTS_MAX_CHARS = 3000
LLM_MAX_TOKENS = 200

STT_TIMEOUT_S = 30
LLM_TIMEOUT_S = 20
TTS_TIMEOUT_S = 45
CURSOR_TIMEOUT_S = 20


class ConfigError(ValueError):
    """Invalid environment configuration."""


def _http_url(value: str, name: str) -> str:
    text = value.strip().rstrip("/")
    if not text:
        raise ConfigError(f"{name} must be an http(s) URL")
    if not (text.startswith("https://") or text.startswith("http://")):
        raise ConfigError(f"{name} must be an http(s) URL")
    return text


@dataclass(frozen=True)
class Config:
    stt_url: str
    llm_base_url: str
    tts_url: str
    llm_model: str
    cursor_automation_url: str
    cursor_automation_token: str = field(repr=False)
    relay_token: str = field(repr=False)
    callback_secret: str = field(repr=False)
    task_store_path: str
    host: str
    port: int

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Config:
        src = os.environ if env is None else env
        port_raw = (src.get("RELAY_PORT") or "").strip()
        if port_raw:
            try:
                port = int(port_raw)
            except ValueError as exc:
                raise ConfigError("RELAY_PORT must be an integer") from exc
        else:
            port = DEFAULT_PORT
        if not 0 <= port <= 65535:
            raise ConfigError("RELAY_PORT must be between 0 and 65535")

        cursor_url = (src.get("CURSOR_AUTOMATION_URL") or "").strip()
        if cursor_url:
            cursor_url = _http_url(cursor_url, "CURSOR_AUTOMATION_URL")

        model = (src.get("LLM_MODEL") or DEFAULT_LLM_MODEL).strip() or DEFAULT_LLM_MODEL
        store = (src.get("TASK_STORE_PATH") or DEFAULT_TASK_STORE).strip() or DEFAULT_TASK_STORE
        host = (src.get("RELAY_HOST") or DEFAULT_HOST).strip() or DEFAULT_HOST

        return cls(
            stt_url=_http_url(src.get("STT_URL") or DEFAULT_STT_URL, "STT_URL"),
            llm_base_url=_http_url(src.get("LLM_BASE_URL") or DEFAULT_LLM_BASE_URL, "LLM_BASE_URL"),
            tts_url=_http_url(src.get("TTS_URL") or DEFAULT_TTS_URL, "TTS_URL"),
            llm_model=model,
            cursor_automation_url=cursor_url,
            cursor_automation_token=src.get("CURSOR_AUTOMATION_TOKEN") or "",
            relay_token=src.get("RELAY_TOKEN") or "",
            callback_secret=src.get("CALLBACK_SECRET") or "",
            task_store_path=store,
            host=host,
            port=port,
        )

    def llm_completions_url(self) -> str:
        return f"{self.llm_base_url}/v1/chat/completions"
