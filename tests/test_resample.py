"""24 kHz -> 16 kHz PCM16LE linear resampler."""

import math
import struct

import pytest

from relay.resample import resample_24k_to_16k


def _pcm(samples: list[int]) -> bytes:
    return b"".join(struct.pack("<h", int(sample)) for sample in samples)


def _samples(pcm: bytes) -> list[int]:
    count = len(pcm) // 2
    return list(struct.unpack("<" + "h" * count, pcm))


def _rms(samples: list[int]) -> float:
    if not samples:
        return 0.0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


def _zero_crossings(samples: list[int]) -> int:
    count = 0
    for prev, cur in zip(samples, samples[1:]):
        if (prev <= 0 < cur) or (cur <= 0 < prev):
            count += 1
    return count


def test_length_ratio_is_two_thirds():
    tone = [
        int(10000 * math.sin(2 * math.pi * 440 * i / 24000))
        for i in range(24000)
    ]
    out = _samples(resample_24k_to_16k(_pcm(tone)))
    assert len(out) == 16000
    assert len(out) / len(tone) == pytest.approx(16000 / 24000)

    short = _samples(resample_24k_to_16k(_pcm([1000] * 100)))
    assert len(short) == 66
    assert len(short) / 100 == pytest.approx(2 / 3, abs=0.01)


def test_sine_sweep_stays_sane():
    samples = []
    phase = 0.0
    n = 24000
    for i in range(n):
        # Sweep 200 Hz -> 2000 Hz across one second. Both sit under 8 kHz Nyquist.
        freq = 200 + (2000 - 200) * (i / (n - 1))
        phase += 2 * math.pi * freq / 24000
        samples.append(int(12000 * math.sin(phase)))
    out = _samples(resample_24k_to_16k(_pcm(samples)))
    assert len(out) == 16000
    assert max(abs(sample) for sample in out) <= 12000
    ratio = _rms(out) / _rms(samples)
    assert 0.75 <= ratio <= 1.05


def test_pure_sine_zero_crossings_track_the_tone():
    samples = [
        int(10000 * math.sin(2 * math.pi * 1000 * i / 24000))
        for i in range(24000)
    ]
    out = _samples(resample_24k_to_16k(_pcm(samples)))
    # 1000 Hz for 1 second crosses zero about 2000 times at either rate.
    assert abs(_zero_crossings(out) - _zero_crossings(samples)) < 30
    assert abs(_rms(out) - _rms(samples)) / _rms(samples) < 0.05


def test_linear_points_and_dc():
    ramp = _samples(resample_24k_to_16k(_pcm([0, 10, 20, 30, 40, 50])))
    # i * 3/2 -> 0, 1.5, 3, 4.5
    assert ramp == [0, 15, 30, 45]

    dc = _samples(resample_24k_to_16k(_pcm([1234] * 300)))
    assert dc == [1234] * 200

    negative = _samples(resample_24k_to_16k(_pcm([0, -3, -2])))
    # Halfway between -3 and -2 is -2.5, rounded away from zero.
    assert negative == [0, -3]


def test_edges():
    assert resample_24k_to_16k(b"") == b""
    assert resample_24k_to_16k(struct.pack("<h", 7)) == struct.pack("<h", 7)
    with pytest.raises(ValueError):
        resample_24k_to_16k(b"\x00")
