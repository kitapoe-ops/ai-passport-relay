"""Server-sent events from an OpenAI-compatible worker.

Accepts chat completion chunks (``choices[].delta.content``) and the
shorthand worker shape (``{"delta": "..."}`` or ``{"delta": {"content": ...}}``).
``data: [DONE]`` ends the stream. Chunks may split a line anywhere.
"""

from __future__ import annotations

import json
from typing import BinaryIO, Iterable, Iterator


class _Done:
    pass


_DONE = _Done()


def _content_to_str(content: object) -> str | None:
    if isinstance(content, str):
        return content or None
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
        joined = "".join(parts)
        return joined or None
    return None


def _from_choice(choice: object) -> str | None:
    if not isinstance(choice, dict):
        return None
    delta = choice.get("delta")
    if isinstance(delta, dict):
        text = _content_to_str(delta.get("content"))
        if text:
            return text
    elif isinstance(delta, str) and delta:
        return delta
    text = _content_to_str(choice.get("text"))
    if text:
        return text
    message = choice.get("message")
    if isinstance(message, dict):
        return _content_to_str(message.get("content"))
    return None


def extract_delta_text(obj: object) -> str | None:
    """Pull one caption delta out of a worker JSON object."""
    if not isinstance(obj, dict):
        return None
    choices = obj.get("choices")
    if isinstance(choices, list) and choices:
        text = _from_choice(choices[0])
        if text:
            return text
    delta = obj.get("delta")
    if isinstance(delta, str) and delta:
        return delta
    if isinstance(delta, dict):
        text = _content_to_str(delta.get("content"))
        if text:
            return text
        if isinstance(delta.get("text"), str) and delta["text"]:
            return delta["text"]
    for key in ("content", "text", "response"):
        text = _content_to_str(obj.get(key))
        if text:
            return text
    return None


def _delta_from_line(raw: bytes) -> str | _Done | None:
    if not raw:
        return None
    try:
        line = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if line.startswith((":", "event:", "id:", "retry:")):
        return None
    if not line.startswith("data:"):
        return None
    data = line[5:]
    if data.startswith(" "):
        data = data[1:]
    if not data:
        return None
    if data.strip() == "[DONE]":
        return _DONE
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return None
    return extract_delta_text(obj)


def parse_sse_events(chunks: Iterable[bytes]) -> Iterator[str]:
    """Yield caption deltas from an SSE byte stream."""
    buf = bytearray()
    done = False

    def consume_line(raw: bytes) -> str | None:
        nonlocal done
        item = _delta_from_line(raw)
        if item is _DONE:
            done = True
            return None
        if isinstance(item, str) and item:
            return item
        return None

    for chunk in chunks:
        if done:
            break
        if not chunk:
            continue
        buf.extend(chunk)
        while not done:
            nl = buf.find(b"\n")
            if nl < 0:
                break
            raw = bytes(buf[:nl])
            del buf[: nl + 1]
            if raw.endswith(b"\r"):
                raw = raw[:-1]
            text = consume_line(raw)
            if text:
                yield text
    if not done and buf:
        raw = bytes(buf)
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        text = consume_line(raw)
        if text:
            yield text


def iter_sse_reader(reader: BinaryIO, read_size: int = 4096) -> Iterator[str]:
    def chunks() -> Iterator[bytes]:
        while True:
            piece = reader.read(read_size)
            if not piece:
                break
            yield piece

    yield from parse_sse_events(chunks())
