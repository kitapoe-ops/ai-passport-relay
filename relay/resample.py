"""PCM16LE mono resampler, 24000 Hz -> 16000 Hz.

Linear interpolation. 24000/16000 = 3/2, so each output sample lands on an
input sample or exactly halfway between two input samples.
"""

from __future__ import annotations

import array
import sys

_IN_RATE = 24000
_OUT_RATE = 16000


def _round_avg(left: int, right: int) -> int:
    """Average two int16 samples, rounding halves away from zero."""
    total = left + right
    if total >= 0:
        return (total + 1) // 2
    return -(((-total) + 1) // 2)


def resample_24k_to_16k(pcm: bytes) -> bytes:
    """Return 16 kHz PCM16LE mono bytes for a 24 kHz PCM16LE mono buffer."""
    if not pcm:
        return b""
    if len(pcm) % 2 != 0:
        raise ValueError("PCM16LE length must be even")

    samples = array.array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()

    n_in = len(samples)
    n_out = (n_in * _OUT_RATE) // _IN_RATE
    if n_out < 1:
        n_out = 1

    out = array.array("h", [0]) * n_out
    last = n_in - 1
    for i in range(n_out):
        # Source position is i * 24000/16000 = i * 3/2.
        numer = i * 3
        idx = numer // 2
        halfway = (numer % 2) == 1
        if idx >= last or not halfway:
            out[i] = samples[last if idx > last else idx]
        else:
            out[i] = _round_avg(int(samples[idx]), int(samples[idx + 1]))

    if sys.byteorder != "little":
        out.byteswap()
    return out.tobytes()
