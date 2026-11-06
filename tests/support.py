"""Offline test helpers. Nothing here opens a socket except the local relay."""

from __future__ import annotations

import http.client
import io
import math
import socket
import struct
import wave
from urllib.parse import urlsplit

from relay.upstream import UpstreamError


def make_wav(
    n_frames: int = 160,
    rate: int = 16000,
    channels: int = 1,
    freq: float = 440.0,
) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = bytearray()
        for i in range(n_frames):
            sample = int(8000 * math.sin(2 * math.pi * freq * i / max(rate, 1)))
            frames += struct.pack("<h", sample)
        handle.writeframes(bytes(frames))
    return buf.getvalue()


class FakeUpstream:
    def __init__(
        self,
        transcript: str = "hello",
        deltas: list[str] | None = None,
        pcm24: bytes = b"\x00\x00" * 48,
        run_uuid: str = "run-1",
        stt_error: Exception | None = None,
        llm_error: Exception | None = None,
        tts_error: Exception | None = None,
        cursor_error: Exception | None = None,
        llm_error_after: int | None = None,
    ):
        self.transcript = transcript
        self.deltas = ["hello"] if deltas is None else list(deltas)
        self.pcm24 = pcm24
        self.run_uuid = run_uuid
        self.stt_error = stt_error
        self.llm_error = llm_error
        self.tts_error = tts_error
        self.cursor_error = cursor_error
        self.llm_error_after = llm_error_after
        self.stt_wavs: list[bytes] = []
        self.chat_inputs: list[str] = []
        self.tts_texts: list[str] = []
        self.cursor_inputs: list[str] = []

    def transcribe(self, wav: bytes) -> str:
        self.stt_wavs.append(wav)
        if self.stt_error:
            raise self.stt_error
        return self.transcript

    def stream_chat(self, user_text: str):
        self.chat_inputs.append(user_text)
        if self.llm_error is not None and self.llm_error_after is None:
            raise self.llm_error
        for index, delta in enumerate(self.deltas):
            if (
                self.llm_error is not None
                and self.llm_error_after is not None
                and index >= self.llm_error_after
            ):
                raise self.llm_error
            yield delta

    def synthesize(self, text: str) -> bytes:
        self.tts_texts.append(text)
        if self.tts_error:
            raise self.tts_error
        return self.pcm24

    def submit_cursor(self, text: str) -> str:
        self.cursor_inputs.append(text)
        if self.cursor_error:
            raise self.cursor_error
        return self.run_uuid


def expired(service: str) -> UpstreamError:
    return UpstreamError(service, 503, '{"error":"token expired"}', kind="http")


def http_request(
    method: str,
    url: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 5,
) -> tuple[int, bytes, dict[str, str]]:
    parts = urlsplit(url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout)
    try:
        conn.request(method, parts.path or "/", body=body, headers=headers or {})
        resp = conn.getresponse()
        payload = resp.read()
        header_map = {key.lower(): value for key, value in resp.getheaders()}
        return resp.status, payload, header_map
    finally:
        conn.close()


def raw_http(host: str, port: int, payload: bytes, timeout: float = 5) -> bytes:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(payload)
        sock.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        while True:
            piece = sock.recv(4096)
            if not piece:
                break
            chunks.append(piece)
        return b"".join(chunks)
