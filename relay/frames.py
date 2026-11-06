"""Muse relay frames: [1 type byte][4 length LE][payload]."""

from __future__ import annotations

import struct
from typing import Iterator

FRAME_END = 0x00
FRAME_TEXT = 0x01
FRAME_AUDIO = 0x02

# Device audio frames are about 16 KiB of PCM16LE.
AUDIO_CHUNK_BYTES = 16 * 1024

_HEADER = struct.Struct("<BI")


def encode_frame(frame_type: int, payload: bytes = b"") -> bytes:
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError("payload must be bytes")
    if not 0 <= int(frame_type) <= 255:
        raise ValueError("frame type must fit in one byte")
    if len(payload) > 0xFFFFFFFF:
        raise ValueError("payload too large")
    return _HEADER.pack(int(frame_type), len(payload)) + bytes(payload)


def encode_end() -> bytes:
    return encode_frame(FRAME_END, b"")


def encode_text(text: str) -> bytes:
    return encode_frame(FRAME_TEXT, text.encode("utf-8"))


def encode_audio(pcm: bytes) -> bytes:
    return encode_frame(FRAME_AUDIO, pcm)


def decode_frames(buf: bytes) -> list[tuple[int, bytes]]:
    """Decode a concatenated frame stream. Raises ValueError if truncated."""
    frames: list[tuple[int, bytes]] = []
    view = memoryview(buf)
    offset = 0
    total = len(view)
    while offset < total:
        if total - offset < 5:
            raise ValueError("truncated frame header")
        frame_type, length = _HEADER.unpack_from(view, offset)
        offset += 5
        if length > total - offset:
            raise ValueError("truncated frame payload")
        payload = bytes(view[offset : offset + length])
        offset += length
        frames.append((frame_type, payload))
    return frames


def chunk_audio(pcm: bytes, size: int = AUDIO_CHUNK_BYTES) -> Iterator[bytes]:
    if size <= 0:
        raise ValueError("chunk size must be positive")
    for start in range(0, len(pcm), size):
        yield bytes(pcm[start : start + size])
