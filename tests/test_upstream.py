"""Upstream request shape. urllib is mocked; nothing is dialed."""

import io
import json
import urllib.error
import urllib.request

import pytest

from relay.config import Config
from relay.prompt import SYSTEM_PROMPT
from relay.upstream import (
    USER_AGENT,
    HttpUpstream,
    UpstreamError,
    _NoRedirect,
    build_multipart_audio,
    parse_run_uuid,
    parse_transcript,
)


def _config() -> Config:
    return Config(
        stt_url="https://stt.test/api/stt",
        llm_base_url="https://llm.test",
        tts_url="https://tts.test/api/tts",
        llm_model="hkgai-v3",
        cursor_automation_url="https://cursor.test/hook",
        cursor_automation_token="test-cursor-token",
        relay_token="test-relay-token",
        callback_secret="test-callback-secret",
        task_store_path="tasks.jsonl",
        host="127.0.0.1",
        port=8788,
    )


class _Resp:
    def __init__(self, body: bytes, status: int = 200):
        self.status = status
        self._body = body
        self.closed = False

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0 or n >= len(self._body):
            data, self._body = self._body, b""
            return data
        data, self._body = self._body[:n], self._body[n:]
        return data

    def getcode(self) -> int:
        return self.status

    def close(self) -> None:
        self.closed = True


def test_transcript_and_run_parsers():
    assert parse_transcript({"transcript": " 早晨 "}) == "早晨"
    assert parse_transcript({"text": "hi"}) == "hi"
    assert parse_transcript({"data": {"text": "yo"}}) == "yo"
    with pytest.raises(UpstreamError):
        parse_transcript({"nope": 1})
    assert parse_run_uuid({"success": True, "runUuid": "abc"}) == "abc"
    with pytest.raises(UpstreamError):
        parse_run_uuid({"success": False, "runUuid": "abc"})
    with pytest.raises(UpstreamError):
        parse_run_uuid({"success": True})


def test_token_expired_classification():
    assert UpstreamError("stt", 503, "Token Expired").token_expired
    assert UpstreamError("llm", 503, '{"error":"token expired"}').token_expired
    assert not UpstreamError("tts", 503, "overloaded").token_expired
    assert not UpstreamError("tts", 500, "token expired").token_expired


def test_multipart_audio_field():
    wav = b"RIFF....WAVE" + b"\x00" * 16
    body, boundary = build_multipart_audio(wav)
    assert f'name="audio"'.encode() in body
    assert b'filename="audio.wav"' in body
    assert b"Content-Type: audio/wav" in body
    assert wav in body
    assert boundary.encode() in body
    assert boundary.encode() not in wav


def test_stt_llm_tts_and_cursor_requests(monkeypatch):
    captured = []

    def fake_open(req, timeout):
        captured.append((req, timeout))
        url = req.full_url
        if url.endswith("/api/stt"):
            return _Resp(b'{"transcript":"\xe6\x97\xa9\xe6\x99\xa8"}')
        if url.endswith("/v1/chat/completions"):
            sse = (
                b'data: {"choices":[{"delta":{"content":"\xe6\x97\xa9"}}]}\n\n'
                b'data: {"choices":[{"delta":{"content":"\xe4\xb8\x8a\xe5\xa5\xbd"}}]}\n\n'
                b"data: [DONE]\n\n"
            )
            return _Resp(sse)
        if url.endswith("/api/tts"):
            return _Resp(b"\x01\x00\x02\x00")
        if url.endswith("/hook"):
            return _Resp(b'{"success":true,"runUuid":"run-9"}')
        raise AssertionError(url)

    monkeypatch.setattr("relay.upstream._open", fake_open)
    upstream = HttpUpstream(_config())

    assert upstream.transcribe(b"RIFF-fake") == "早晨"
    assert list(upstream.stream_chat("早晨")) == ["早", "上好"]
    assert upstream.synthesize("你好") == b"\x01\x00\x02\x00"
    assert upstream.submit_cursor("do the thing") == "run-9"

    stt_req, stt_timeout = captured[0]
    assert stt_req.full_url == "https://stt.test/api/stt"
    assert stt_timeout == 30
    assert 'name="audio"' in stt_req.data.decode("latin1")
    assert b"RIFF-fake" in stt_req.data
    assert stt_req.get_header("User-agent") == USER_AGENT

    llm_req, llm_timeout = captured[1]
    assert llm_req.full_url == "https://llm.test/v1/chat/completions"
    assert llm_timeout == 20
    llm_body = json.loads(llm_req.data.decode("utf-8"))
    assert llm_body["model"] == "hkgai-v3"
    assert llm_body["stream"] is True
    assert "tools" not in llm_body
    assert llm_body["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert llm_body["messages"][1] == {"role": "user", "content": "早晨"}
    assert len(SYSTEM_PROMPT) < 400

    tts_req, tts_timeout = captured[2]
    assert tts_timeout == 45
    assert json.loads(tts_req.data.decode("utf-8")) == {"text": "你好"}

    cursor_req, cursor_timeout = captured[3]
    assert cursor_timeout == 20
    assert json.loads(cursor_req.data.decode("utf-8")) == {"input": "do the thing"}
    assert cursor_req.get_header("Authorization") == "Bearer test-cursor-token"
    assert "test-cursor-token" not in cursor_req.full_url


def test_http_503_token_expired_is_raised(monkeypatch):
    def fake_open(req, timeout):
        raise urllib.error.HTTPError(
            req.full_url,
            503,
            "unavailable",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"token expired"}'),
        )

    monkeypatch.setattr("relay.upstream._open", fake_open)
    upstream = HttpUpstream(_config())
    with pytest.raises(UpstreamError) as caught:
        upstream.transcribe(b"RIFF")
    assert caught.value.token_expired
    assert caught.value.service == "stt"
    assert "token" not in str(caught.value)


def test_redirects_are_refused():
    handler = _NoRedirect()
    req = urllib.request.Request("https://stt.test/api/stt", data=b"wav", method="POST")
    with pytest.raises(urllib.error.HTTPError) as caught:
        handler.redirect_request(
            req,
            io.BytesIO(b""),
            302,
            "Found",
            headers=None,
            newurl="https://evil.test/collect",
        )
    assert caught.value.code == 302


def test_cursor_unconfigured_does_not_call_network(monkeypatch):
    def fake_open(req, timeout):
        raise AssertionError("network")

    monkeypatch.setattr("relay.upstream._open", fake_open)
    cfg = _config()
    blank = Config(
        stt_url=cfg.stt_url,
        llm_base_url=cfg.llm_base_url,
        tts_url=cfg.tts_url,
        llm_model=cfg.llm_model,
        cursor_automation_url="",
        cursor_automation_token="",
        relay_token=cfg.relay_token,
        callback_secret=cfg.callback_secret,
        task_store_path=cfg.task_store_path,
        host=cfg.host,
        port=cfg.port,
    )
    with pytest.raises(UpstreamError) as caught:
        HttpUpstream(blank).submit_cursor("hi")
    assert caught.value.kind == "unconfigured"
