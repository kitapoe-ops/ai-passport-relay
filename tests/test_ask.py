"""POST /ask with mocked upstreams. No network beyond localhost."""

import json
import struct

from relay.frames import (
    FRAME_AUDIO,
    FRAME_END,
    FRAME_TEXT,
    decode_frames,
)
from relay.prompt import BUSY_TEXT, EMPTY_TEXT, FAIL_TEXT
from relay.resample import resample_24k_to_16k
from relay.upstream import UpstreamError
from tests.support import FakeUpstream, expired, http_request, make_wav, raw_http


def _auth(token: str = "test-relay-token") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "audio/wav",
    }


def test_ask_frame_order_text_then_audio_then_end(make_server):
    pcm24 = struct.pack("<h", 1000) * 13000
    fake = FakeUpstream(
        transcript="早晨",
        deltas=["早晨", "，", "食咗未？"],
        pcm24=pcm24,
    )
    srv = make_server(fake)
    wav = make_wav()
    status, body, headers = http_request(
        "POST",
        srv.base + "/ask",
        body=wav,
        headers=_auth(),
    )
    assert status == 200
    assert headers["content-type"].startswith("application/octet-stream")
    assert headers["transfer-encoding"] == "chunked"

    frames = decode_frames(body)
    kinds = [kind for kind, _payload in frames]
    assert kinds[-1] == FRAME_END
    first_audio = kinds.index(FRAME_AUDIO)
    assert first_audio > 0
    assert kinds[:first_audio] == [FRAME_TEXT, FRAME_TEXT, FRAME_TEXT]
    assert all(kind == FRAME_AUDIO for kind in kinds[first_audio:-1])
    assert kinds.count(FRAME_END) == 1

    texts = [payload.decode("utf-8") for kind, payload in frames if kind == FRAME_TEXT]
    assert texts == ["早晨", "，", "食咗未？"]
    audio_parts = [payload for kind, payload in frames if kind == FRAME_AUDIO]
    assert len(audio_parts) >= 2
    assert all(len(part) == 16384 for part in audio_parts[:-1])
    assert 0 < len(audio_parts[-1]) <= 16384
    assert b"".join(audio_parts) == resample_24k_to_16k(pcm24)
    assert frames[-1] == (FRAME_END, b"")

    assert fake.stt_wavs == [wav]
    assert fake.chat_inputs == ["早晨"]
    assert fake.tts_texts == ["早晨，食咗未？"]

    status, last_body, _headers = http_request("GET", srv.base + "/last")
    last = json.loads(last_body)
    assert status == 200
    assert last["heard"] == "早晨"
    assert last["reply"] == "早晨，食咗未？"
    assert last["error"] is None
    for key in ("stt_ms", "llm_ttfb_ms", "llm_ms", "tts_ms", "total_ms"):
        assert last["timings"][key] >= 0


def test_token_expired_returns_busy_text_then_end(make_server):
    fake = FakeUpstream(stt_error=expired("stt"))
    srv = make_server(fake)
    status, body, _headers = http_request(
        "POST",
        srv.base + "/ask",
        body=make_wav(),
        headers=_auth(),
    )
    assert status == 200
    frames = decode_frames(body)
    assert frames == [
        (FRAME_TEXT, BUSY_TEXT.encode("utf-8")),
        (FRAME_END, b""),
    ]
    assert fake.chat_inputs == []
    assert fake.tts_texts == []
    last = json.loads(http_request("GET", srv.base + "/last")[1])
    assert last["error"] == "token_expired"
    assert last["heard"] is None


def test_token_expired_mid_llm_does_not_call_tts(make_server):
    fake = FakeUpstream(
        transcript="hi",
        deltas=["keep", "me"],
        llm_error=expired("llm"),
        llm_error_after=1,
    )
    srv = make_server(fake)
    status, body, _headers = http_request(
        "POST",
        srv.base + "/ask",
        body=make_wav(),
        headers=_auth(),
    )
    assert status == 200
    frames = decode_frames(body)
    kinds = [kind for kind, _payload in frames]
    assert FRAME_AUDIO not in kinds
    texts = [payload.decode("utf-8") for kind, payload in frames if kind == FRAME_TEXT]
    assert texts == ["keep", BUSY_TEXT]
    assert kinds[-1] == FRAME_END
    assert fake.tts_texts == []


def test_other_upstream_failure_uses_generic_caption(make_server):
    fake = FakeUpstream(
        tts_error=UpstreamError("tts", 503, "overloaded", kind="http"),
        transcript="hi",
        deltas=["answer"],
    )
    srv = make_server(fake)
    _status, body, _headers = http_request(
        "POST",
        srv.base + "/ask",
        body=make_wav(),
        headers=_auth(),
    )
    frames = decode_frames(body)
    texts = [payload.decode("utf-8") for kind, payload in frames if kind == FRAME_TEXT]
    assert texts == ["answer", FAIL_TEXT]
    assert all(kind != FRAME_AUDIO for kind, _payload in frames)


def test_empty_transcript_skips_llm_and_tts(make_server):
    fake = FakeUpstream(transcript="   ")
    srv = make_server(fake)
    _status, body, _headers = http_request(
        "POST",
        srv.base + "/ask",
        body=make_wav(),
        headers=_auth(),
    )
    frames = decode_frames(body)
    assert frames == [
        (FRAME_TEXT, EMPTY_TEXT.encode("utf-8")),
        (FRAME_END, b""),
    ]
    assert fake.chat_inputs == []
    assert fake.tts_texts == []


def test_internal_error_does_not_leak_exception_text(make_server):
    fake = FakeUpstream(stt_error=RuntimeError("boom-secret-detail"))
    srv = make_server(fake)
    status, body, _headers = http_request(
        "POST",
        srv.base + "/ask",
        body=make_wav(),
        headers=_auth(),
    )
    assert status == 200
    assert b"boom-secret-detail" not in body
    frames = decode_frames(body)
    assert frames[0] == (FRAME_TEXT, FAIL_TEXT.encode("utf-8"))
    assert frames[-1][0] == FRAME_END


def test_ask_auth_and_wav_guards(make_server):
    fake = FakeUpstream()
    srv = make_server(fake)
    wav = make_wav()

    status, _body, _headers = http_request("POST", srv.base + "/ask", body=wav)
    assert status == 401
    status, _body, _headers = http_request(
        "POST",
        srv.base + "/ask",
        body=wav,
        headers=_auth("wrong-token"),
    )
    assert status == 401
    assert fake.stt_wavs == []

    status, body, _headers = http_request(
        "POST",
        srv.base + "/ask",
        body=b"not-a-wav",
        headers=_auth(),
    )
    assert status == 400
    assert b"WAV" in body

    stereo = make_wav(channels=2)
    status, _body, _headers = http_request(
        "POST",
        srv.base + "/ask",
        body=stereo,
        headers=_auth(),
    )
    assert status == 400

    slow = make_wav(rate=8000)
    status, _body, _headers = http_request(
        "POST",
        srv.base + "/ask",
        body=slow,
        headers=_auth(),
    )
    assert status == 400
    assert fake.stt_wavs == []

    missing_length = (
        f"POST /ask HTTP/1.1\r\nHost: {srv.host}\r\n"
        "Authorization: Bearer test-relay-token\r\n"
        "Connection: close\r\n\r\n"
    ).encode()
    raw = raw_http(srv.host, srv.port, missing_length)
    assert b"411" in raw.split(b"\r\n", 1)[0]

    too_big = (
        f"POST /ask HTTP/1.1\r\nHost: {srv.host}\r\n"
        "Authorization: Bearer test-relay-token\r\n"
        "Content-Length: 4194305\r\n"
        "Connection: close\r\n\r\n"
    ).encode()
    raw = raw_http(srv.host, srv.port, too_big)
    assert b"413" in raw.split(b"\r\n", 1)[0]


def test_last_and_health_before_any_turn(make_server):
    srv = make_server()
    status, body, _headers = http_request("GET", srv.base + "/health")
    assert status == 200
    assert json.loads(body) == {"ok": True, "tasks": 0}
    status, body, _headers = http_request("GET", srv.base + "/last")
    assert json.loads(body)["heard"] is None


def test_tts_text_is_truncated_to_3000_chars():
    from relay.pipeline import TurnLog, iter_ask_frames

    long = "啊" * 4000
    fake = FakeUpstream(transcript="hi", deltas=[long], pcm24=struct.pack("<h", 1) * 24)
    frames = list(iter_ask_frames(make_wav(), fake, TurnLog()))
    assert fake.tts_texts == ["啊" * 3000]
    texts = [
        payload.decode("utf-8")
        for kind, payload in decode_frames(b"".join(frames))
        if kind == FRAME_TEXT
    ]
    assert texts == [long]
