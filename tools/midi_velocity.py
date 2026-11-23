"""Per-note MIDI velocities from the loudness of a song's audio mix."""
import subprocess
from pathlib import Path

import numpy as np

NOTE_WINDOW = 0.1  # seconds measured from each melody note's onset


def quarter_clock(events):
    """SheetSage2 events.json events -> function mapping score quarters to audio seconds.

    subbeat is a 16th-note index from score quarter 0; timed events carry values["timestamp"].
    Outside the timed range, extend linearly with the first/last beat interval.
    """
    pts = dict(sorted((e["subbeat"] / 4, e["values"]["timestamp"])
                      for e in events if "timestamp" in e.get("values", {})))
    if len(pts) < 2:
        raise ValueError("events.json has fewer than 2 distinct timestamps")
    q, t = np.array(list(pts), float), np.array(list(pts.values()), float)

    def clock(quarter):
        if quarter < q[0]:
            return t[0] + (quarter - q[0]) * (t[1] - t[0]) / (q[1] - q[0])
        if quarter > q[-1]:
            return t[-1] + (quarter - q[-1]) * (t[-1] - t[-2]) / (q[-1] - q[-2])
        return float(np.interp(quarter, q, t))
    return clock


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
