"""Upstream clients: STT, streaming LLM, TTS, Cursor automation webhook.

Network I/O lives here so tests can substitute a fake. Redirects are refused
so a bearer token cannot be replayed to another host.
"""

from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.request
from typing import Iterator

from relay.config import (
    CURSOR_TIMEOUT_S,
    LLM_MAX_TOKENS,
    LLM_TIMEOUT_S,
    STT_TIMEOUT_S,
    TTS_MAX_CHARS,
    TTS_TIMEOUT_S,
    Config,
)
from relay.prompt import SYSTEM_PROMPT
from relay.sse import iter_sse_reader

USER_AGENT = "ai-passport-relay/0.1"


class UpstreamError(Exception):
    """A classified upstream failure. The message never includes response bodies."""

    def __init__(self, service: str, status: int, detail: str = "", kind: str = "http"):
        self.service = service
        self.status = int(status)
        self.detail = detail
        self.kind = kind
        super().__init__(f"{service} upstream failed ({self.status})")

    @property
    def token_expired(self) -> bool:
        return self.status == 503 and "token expired" in (self.detail or "").lower()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect)


def _open(req: urllib.request.Request, timeout: float):
    return _OPENER.open(req, timeout=timeout)


def _http_failure(service: str, status: int, body: bytes) -> UpstreamError:
    detail = body[:4000].decode("utf-8", errors="replace")
    return UpstreamError(service, status, detail, kind="http")


def _load_json(service: str, body: bytes) -> object:
    try:
        return json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpstreamError(service, 200, "invalid json", kind="bad_response") from exc


def build_multipart_audio(wav: bytes) -> tuple[bytes, str]:
    """multipart/form-data with a single file field named ``audio``."""
    boundary = "----AiPassportRelay" + secrets.token_hex(12)
    while boundary.encode("ascii") in wav:
        boundary = "----AiPassportRelay" + secrets.token_hex(12)
    crlf = b"\r\n"
    body = b"".join(
        (
            f"--{boundary}".encode("ascii"),
            crlf,
            b'Content-Disposition: form-data; name="audio"; filename="audio.wav"',
            crlf,
            b"Content-Type: audio/wav",
            crlf,
            crlf,
            wav,
            crlf,
            f"--{boundary}--".encode("ascii"),
            crlf,
        )
    )
    return body, boundary


def parse_transcript(obj: object) -> str:
    if isinstance(obj, str):
        return obj.strip()
    if not isinstance(obj, dict):
        raise UpstreamError("stt", 200, "missing transcript", kind="bad_response")
    for key in ("transcript", "text", "result"):
        value = obj.get(key)
        if isinstance(value, str):
            return value.strip()
    data = obj.get("data")
    if isinstance(data, dict):
        for key in ("transcript", "text", "result"):
            value = data.get(key)
            if isinstance(value, str):
                return value.strip()
    raise UpstreamError("stt", 200, "missing transcript", kind="bad_response")


def parse_run_uuid(obj: object) -> str:
    if not isinstance(obj, dict) or obj.get("success") is not True:
        raise UpstreamError("cursor", 200, "not successful", kind="bad_response")
    run = obj.get("runUuid")
    if not isinstance(run, str) or not run.strip():
        raise UpstreamError("cursor", 200, "missing runUuid", kind="bad_response")
    return run


def _call(service: str, req: urllib.request.Request, timeout: float) -> bytes:
    try:
        resp = _open(req, timeout)
    except urllib.error.HTTPError as exc:
        raise _http_failure(service, exc.code, exc.read()) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpstreamError(service, 0, "unreachable", kind="unreachable") from exc
    try:
        status = getattr(resp, "status", None)
        if status is None:
            status = resp.getcode()
        body = resp.read()
        if status >= 400:
            raise _http_failure(service, int(status), body)
        return body
    finally:
        resp.close()


class HttpUpstream:
    def __init__(self, config: Config):
        self.config = config

    def transcribe(self, wav: bytes) -> str:
        body, boundary = build_multipart_audio(wav)
        req = urllib.request.Request(
            self.config.stt_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        payload = _call("stt", req, STT_TIMEOUT_S)
        return parse_transcript(_load_json("stt", payload))

    def stream_chat(self, user_text: str) -> Iterator[str]:
        payload = {
            "model": self.config.llm_model,
            "stream": True,
            "max_tokens": LLM_MAX_TOKENS,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.config.llm_completions_url(),
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            resp = _open(req, LLM_TIMEOUT_S)
        except urllib.error.HTTPError as exc:
            raise _http_failure("llm", exc.code, exc.read()) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise UpstreamError("llm", 0, "unreachable", kind="unreachable") from exc
        try:
            status = getattr(resp, "status", None)
            if status is None:
                status = resp.getcode()
            if status >= 400:
                raise _http_failure("llm", int(status), resp.read())
            yield from iter_sse_reader(resp)
        finally:
            resp.close()

    def synthesize(self, text: str) -> bytes:
        spoken = text[:TTS_MAX_CHARS]
        if not spoken:
            raise UpstreamError("tts", 0, "empty text", kind="bad_response")
        data = json.dumps({"text": spoken}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.config.tts_url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/octet-stream",
                "User-Agent": USER_AGENT,
            },
        )
        return _call("tts", req, TTS_TIMEOUT_S)

    def submit_cursor(self, text: str) -> str:
        if not self.config.cursor_automation_url or not self.config.cursor_automation_token:
            raise UpstreamError("cursor", 0, "not configured", kind="unconfigured")
        data = json.dumps({"input": text}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.config.cursor_automation_url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self.config.cursor_automation_token}",
                "User-Agent": USER_AGENT,
            },
        )
        payload = _call("cursor", req, CURSOR_TIMEOUT_S)
        return parse_run_uuid(_load_json("cursor", payload))
