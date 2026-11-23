"""Per-note MIDI velocities from the loudness of a song's audio mix."""
import subprocess
from pathlib import Path

import numpy as np

NOTE_WINDOW = 0.1  # seconds measured from each melody note's onset


def _points(events):
    pts = dict(sorted((e["subbeat"] / 4, e["values"]["timestamp"])
                      for e in events if "timestamp" in e.get("values", {})))
    if len(pts) < 2:
        raise ValueError("events.json has fewer than 2 distinct timestamps")
    return np.array(list(pts), float), np.array(list(pts.values()), float)


def _linear(xs, ys):
    """Piecewise-linear map xs -> ys, extended past the ends with the first/last segment slope."""
    def f(x):
        if x < xs[0]:
            return float(ys[0] + (x - xs[0]) * (ys[1] - ys[0]) / (xs[1] - xs[0]))
        if x > xs[-1]:
            return float(ys[-1] + (x - xs[-1]) * (ys[-1] - ys[-2]) / (xs[-1] - xs[-2]))
        return float(np.interp(x, xs, ys))
    return f


def quarter_clock(events):
    """SheetSage2 events.json events -> function mapping score quarters to audio seconds.

    subbeat is a 16th-note index from score quarter 0; timed events carry values["timestamp"].
    Outside the timed range, extend linearly with the first/last beat interval.
    """
    q, t = _points(events)
    return _linear(q, t)


def seconds_to_quarters(events):
    """Inverse of quarter_clock: audio seconds -> score quarters."""
    q, t = _points(events)
    return _linear(t, q)


def load_mono(path: Path, sr: int = 22050) -> np.ndarray:
    proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"],
                          capture_output=True)
    if proc.returncode:
        raise RuntimeError(f"ffmpeg failed on {path}: {proc.stderr.decode(errors='replace').strip()}")
    return np.frombuffer(proc.stdout, np.float32)


def span_db(samples, sr, t0, t1):
    seg = samples[max(int(t0 * sr), 0):max(int(t1 * sr), 0)]
    if not len(seg):
        return -180.0
    return float(20 * np.log10(np.sqrt(np.mean(np.square(seg, dtype=np.float64))) + 1e-9))


def to_velocities(levels, lo, hi, smooth=3):
    """Centred moving average, then map the 5th..95th percentile onto lo..hi.

    Outliers are clamped to lo//2..127 so silent spans stay audible instead of collapsing to 1.
    """
    if not levels:
        return []
    x = np.asarray(levels, float)
    half = smooth // 2
    x = np.array([x[max(i - half, 0):i + half + 1].mean() for i in range(len(x))])
    p5, p95 = np.percentile(x, [5, 95])
    if p95 - p5 < 1e-6:
        return [(lo + hi) // 2] * len(x)
    v = lo + (x - p5) * (hi - lo) / (p95 - p5)
    return [int(np.clip(round(float(a)), lo // 2, 127)) for a in v]
