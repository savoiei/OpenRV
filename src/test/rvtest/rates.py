"""Frame-rate to exact rational mapping."""
from __future__ import annotations

from fractions import Fraction

_NTSC = {
    23.976: "24000/1001", 29.97: "30000/1001", 47.952: "48000/1001",
    59.94: "60000/1001", 71.928: "72000/1001", 95.904: "96000/1001",
    119.88: "120000/1001",
}
_TOL = 0.01


def expected_rate(fps: float) -> str:
    for k, v in _NTSC.items():
        if abs(fps - k) < _TOL:
            return v
    n = round(fps)
    if abs(fps - n) < _TOL:
        return f"{n}/1"
    return str(Fraction(fps).limit_denominator(1001))


def rate_value(rate: str) -> float:
    if "/" in rate:
        num, den = rate.split("/", 1)
        return float(num) / float(den)
    return float(rate)
