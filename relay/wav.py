"""Validate the device upload: 16 kHz mono PCM16 WAV, at most 4 MiB."""

from __future__ import annotations

import io
import wave

from relay.config import MAX_WAV_BYTES


def validate_wav(data: bytes) -> None:
    if len(data) > MAX_WAV_BYTES:
        raise ValueError("WAV exceeds 4MB")
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("expected a WAV body")
    try:
        with wave.open(io.BytesIO(data), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frames = handle.getnframes()
    except (wave.Error, EOFError) as exc:
        raise ValueError("invalid WAV") from exc
    if channels != 1 or width != 2 or rate != 16000:
        raise ValueError("WAV must be 16 kHz mono PCM16")
    if frames <= 0:
        raise ValueError("WAV has no audio")
