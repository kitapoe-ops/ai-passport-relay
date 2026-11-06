"""WAV -> STT -> streaming LLM captions -> resampled TTS -> muse frames."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Iterator

from relay.config import TTS_MAX_CHARS
from relay.frames import chunk_audio, encode_audio, encode_end, encode_text
from relay.prompt import BUSY_TEXT, EMPTY_TEXT, FAIL_TEXT
from relay.resample import resample_24k_to_16k
from relay.upstream import UpstreamError

log = logging.getLogger("relay")


class TurnLog:
    """Last finished /ask turn. Safe to read from other request threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: dict | None = None

    def record(self, info: dict) -> None:
        # Copy so a caller mutating the dict afterwards cannot change the log.
        snapshot = json.loads(json.dumps(info, ensure_ascii=False))
        with self._lock:
            self._last = snapshot

    def snapshot(self) -> dict:
        with self._lock:
            if self._last is None:
                return {"heard": None, "reply": None, "timings": {}, "error": None}
            return json.loads(json.dumps(self._last, ensure_ascii=False))


def _ms(since: float) -> int:
    return int((time.perf_counter() - since) * 1000)


def iter_ask_frames(wav: bytes, upstream, turn_log: TurnLog) -> Iterator[bytes]:
    """Yield muse frames for one device turn.

    TEXT frames are emitted as LLM deltas arrive. AUDIO frames follow, each
    about 16 KiB of 16 kHz PCM16LE. The stream always ends with END, including
    the upstream-503 path.
    """
    started = time.perf_counter()
    timings = {
        "stt_ms": 0,
        "llm_ttfb_ms": 0,
        "llm_ms": 0,
        "tts_ms": 0,
        "total_ms": 0,
    }
    heard: str | None = None
    reply_parts: list[str] = []
    error: str | None = None
    try:
        stamp = time.perf_counter()
        transcript = upstream.transcribe(wav)
        timings["stt_ms"] = _ms(stamp)
        if not isinstance(transcript, str):
            raise UpstreamError("stt", 200, "bad transcript", kind="bad_response")
        heard = transcript
        if not transcript.strip():
            reply_parts.append(EMPTY_TEXT)
            yield encode_text(EMPTY_TEXT)
            yield encode_end()
            return

        stamp = time.perf_counter()
        saw_delta = False
        chat = iter(upstream.stream_chat(transcript))
        try:
            for delta in chat:
                if not saw_delta:
                    timings["llm_ttfb_ms"] = _ms(stamp)
                    saw_delta = True
                if isinstance(delta, str) and delta:
                    reply_parts.append(delta)
                    yield encode_text(delta)
        finally:
            # Drop the LLM socket if the device disconnects mid-caption.
            closer = getattr(chat, "close", None)
            if closer is not None:
                closer()
        if not saw_delta:
            timings["llm_ttfb_ms"] = _ms(stamp)
        timings["llm_ms"] = _ms(stamp)

        spoken = "".join(reply_parts).strip()
        if len(spoken) > TTS_MAX_CHARS:
            spoken = spoken[:TTS_MAX_CHARS]
        if spoken:
            tts_stamp = time.perf_counter()
            pcm24 = upstream.synthesize(spoken)
            timings["tts_ms"] = _ms(tts_stamp)
            if not isinstance(pcm24, (bytes, bytearray)):
                raise UpstreamError("tts", 200, "bad audio", kind="bad_response")
            pcm24 = bytes(pcm24)
            if len(pcm24) % 2:
                pcm24 = pcm24[:-1]
            if pcm24:
                pcm16 = resample_24k_to_16k(pcm24)
                for piece in chunk_audio(pcm16):
                    yield encode_audio(piece)
        yield encode_end()
    except UpstreamError as exc:
        error = "token_expired" if exc.token_expired else f"upstream_{exc.service}"
        message = BUSY_TEXT if exc.token_expired else FAIL_TEXT
        reply_parts.append(message)
        log.warning(
            "ask upstream service=%s status=%s kind=%s",
            exc.service,
            exc.status,
            exc.kind,
        )
        yield encode_text(message)
        yield encode_end()
    except Exception:
        error = "internal"
        reply_parts.append(FAIL_TEXT)
        log.error("ask pipeline failed")
        yield encode_text(FAIL_TEXT)
        yield encode_end()
    finally:
        timings["total_ms"] = _ms(started)
        turn_log.record(
            {
                "heard": heard,
                "reply": "".join(reply_parts),
                "timings": timings,
                "error": error,
            }
        )
        log.info(
            "ask done heard_chars=%s reply_chars=%s error=%s stt_ms=%s llm_ttfb_ms=%s llm_ms=%s tts_ms=%s total_ms=%s",
            0 if heard is None else len(heard),
            len("".join(reply_parts)),
            error,
            timings["stt_ms"],
            timings["llm_ttfb_ms"],
            timings["llm_ms"],
            timings["tts_ms"],
            timings["total_ms"],
        )
