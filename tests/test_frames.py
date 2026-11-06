"""Byte-exact muse frame codec."""

import struct

import pytest

from relay.frames import (
    AUDIO_CHUNK_BYTES,
    FRAME_AUDIO,
    FRAME_END,
    FRAME_TEXT,
    chunk_audio,
    decode_frames,
    encode_audio,
    encode_end,
    encode_frame,
    encode_text,
)


def test_end_text_and_audio_are_byte_exact():
    assert encode_end() == b"\x00\x00\x00\x00\x00"
    assert encode_frame(FRAME_END, b"") == b"\x00\x00\x00\x00\x00"
    assert encode_text("A") == b"\x01\x01\x00\x00\x00A"
    assert encode_text("你") == bytes([FRAME_TEXT, 3, 0, 0, 0]) + "你".encode("utf-8")

    pcm = b"\x00\x01\xff\xff"
    assert encode_audio(pcm) == bytes([FRAME_AUDIO, 4, 0, 0, 0]) + pcm


def test_length_is_uint32_little_endian():
    payload = b"x" * 300
    frame = encode_audio(payload)
    assert frame[0] == FRAME_AUDIO
    assert frame[1:5] == struct.pack("<I", 300)
    assert frame[1:5] == b"\x2c\x01\x00\x00"
    assert frame[5:] == payload

    bigger = b"y" * 65536
    encoded = encode_frame(FRAME_TEXT, bigger)
    assert encoded[1:5] == struct.pack("<I", 65536)
    assert encoded[1:5] == b"\x00\x00\x01\x00"


def test_roundtrip_preserves_order_and_bytes():
    blob = encode_text("早晨") + encode_text("，") + encode_audio(b"\x34\x12" * 3) + encode_end()
    frames = decode_frames(blob)
    assert frames == [
        (FRAME_TEXT, "早晨".encode("utf-8")),
        (FRAME_TEXT, "，".encode("utf-8")),
        (FRAME_AUDIO, b"\x34\x12" * 3),
        (FRAME_END, b""),
    ]
    assert b"".join(encode_frame(kind, payload) for kind, payload in frames) == blob


def test_truncated_stream_raises():
    with pytest.raises(ValueError):
        decode_frames(b"\x01\x05\x00\x00\x00ab")
    with pytest.raises(ValueError):
        decode_frames(b"\x01\x01\x00")


def test_audio_chunks_are_about_16kib():
    pcm = b"\x00" * (AUDIO_CHUNK_BYTES + 10)
    parts = list(chunk_audio(pcm))
    assert len(parts) == 2
    assert len(parts[0]) == AUDIO_CHUNK_BYTES == 16384
    assert parts[0] + parts[1] == pcm
