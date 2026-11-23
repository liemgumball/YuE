# Loudness-based MIDI velocity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the `Ins` and `Chords` tracks exported by `tools/abc_midi.py` per-note velocities derived from the loudness of the song's audio.

**Architecture:** A new pure-function module `tools/midi_velocity.py` maps score quarters to audio seconds (via `sheetsage2/events.json`), measures RMS dB of the decoded mix per note span, smooths, and scales to velocities. `abc_midi.render` calls it when audio and events exist and falls back to today's fixed velocities otherwise.

**Tech Stack:** Python in `.venv-sheetsage2`, numpy, mido, `ffmpeg` CLI (on PATH) for decoding — no new pip dependency (the venv has no soundfile/FLAC backend).

**Spec:** `docs/superpowers/specs/2026-11-23-midi-velocity-design.md`

## Global Constraints

- `Vocal` track keeps fixed velocity 100.
- Ins velocities span 40–120 (5th→95th percentile of the track's loudness); chords span 30–90 (my call: keeps today's ~30 lower balance). All clamped to 1–127.
- Fallback when audio or `events.json` is missing or ffmpeg fails: today's fixed values (Ins 100, chords 70) plus one `warning:` line on stdout.
- Note timing, pitches and on-grid layout unchanged (existing `test_render` must still pass).
- Run tests with: `.venv-sheetsage2\Scripts\python -m unittest tests.test_abc_midi -v`

## Facts the implementer needs

- `events.json` → `events[i]["subbeat"]` is a 16th-note index from score quarter 0 (quarter `q` = subbeat `4*q`); events that carry `values["timestamp"]` (seconds) occur every 4 subbeats. Verified on `outputs/lofi/rainy_window` (Ins note at q=2.5 ↔ subbeat 10).
- Timestamps can end before the score does (rainy_window: last timestamp at q=173, score ends q=176) → extrapolate with the last beat interval, don't clamp.
- Audio file lookup, same as `tools/sheetsage_midi.py:14`: first existing of `audio.flac`, `audio.wav`, `audio.mp3` in the song dir.

## Review Focus

1. Silent/near-silent mix (all levels equal) → percentile range is zero; expect every note at the range midpoint, no divide-by-zero. Test in Task 1.
2. Note past the last timestamp → extrapolated time, still a valid velocity. Test in Task 1.
3. Note span shorter than the window or beyond audio end → measured on whatever samples exist; empty span gives the track's minimum level, not NaN. Test in Task 1.
4. Track with zero or one note (e.g. Ins rests `Z`) → no crash, empty list / midpoint. Test in Task 1.
5. Missing events.json or audio → fixed velocities + warning. Test in Task 2.

---

### Task 1: `tools/midi_velocity.py` pure functions

**Files:**
- Create: `tools/midi_velocity.py`
- Test: `tests/test_abc_midi.py` (new class `MidiVelocityTest`, needs numpy only)

**Interfaces:**
- Produces:
  - `quarter_clock(events: list[dict]) -> Callable[[float], float]` — quarter → seconds, linear interpolation over `(subbeat/4, timestamp)` pairs, linear extrapolation using first/last interval.
  - `load_mono(path: Path, sr: int = 22050) -> np.ndarray` — float32 mono via `ffmpeg -v error -i <path> -f f32le -ac 1 -ar <sr> -`; raises `RuntimeError` on non-zero exit.
  - `span_db(samples: np.ndarray, sr: int, t0: float, t1: float) -> float` — RMS in dB (`20*log10(rms + 1e-9)`) of `samples[int(t0*sr):int(t1*sr)]`; empty slice → `-180.0`.
  - `to_velocities(levels: list[float], lo: int, hi: int, smooth: int = 3) -> list[int]` — centred moving average of width `smooth` (edges use available neighbours), then map p5→`lo`, p95→`hi`, clamp 1–127; flat range → `(lo+hi)//2` for all; empty → `[]`.
  - `NOTE_WINDOW = 0.1` — seconds; Ins notes are measured over `[onset, onset + NOTE_WINDOW]` regardless of note length. Chords are measured over their full held span.

- [ ] **Step 1: Write failing tests**

```python
class MidiVelocityTest(unittest.TestCase):
    def setUp(self):
        import midi_velocity
        self.v = midi_velocity

    def test_quarter_clock_interpolates_and_extrapolates(self):
        ev = [{"subbeat": 0, "values": {"timestamp": 1.0}}, {"subbeat": 2, "values": {}},
              {"subbeat": 4, "values": {"timestamp": 1.5}}, {"subbeat": 8, "values": {"timestamp": 2.0}}]
        clock = self.v.quarter_clock(ev)
        self.assertAlmostEqual(clock(0.5), 1.25)
        self.assertAlmostEqual(clock(3.0), 2.5)   # past last timestamp
        self.assertAlmostEqual(clock(-1.0), 0.5)  # before first

    def test_span_db_louder_is_higher_and_empty_is_floor(self):
        import numpy as np
        x = np.concatenate([np.full(100, 0.1, np.float32), np.full(100, 0.5, np.float32)])
        self.assertLess(self.v.span_db(x, 100, 0, 1), self.v.span_db(x, 100, 1, 2))
        self.assertEqual(self.v.span_db(x, 100, 5, 6), -180.0)

    def test_to_velocities_scales_monotonic_and_clamps(self):
        out = self.v.to_velocities([-40, -30, -20, -10, -40, -30, -20, -10], 40, 120, smooth=1)
        self.assertTrue(all(1 <= o <= 127 for o in out))
        self.assertLess(out[0], out[3])
        self.assertEqual(min(out), 40)  # p5 of identical lows maps to lo
        self.assertEqual(max(out), 120)

    def test_to_velocities_flat_and_empty(self):
        self.assertEqual(self.v.to_velocities([-20.0] * 5, 40, 120), [80] * 5)
        self.assertEqual(self.v.to_velocities([-20.0], 40, 120), [80])
        self.assertEqual(self.v.to_velocities([], 40, 120), [])
```

- [ ] **Step 2: Run, expect FAIL** (`ModuleNotFoundError: midi_velocity`).
- [ ] **Step 3: Implement the interfaces above** with numpy (`np.interp` for the interior, manual extrapolation at ends; `np.percentile` for p5/p95). `load_mono` via `subprocess.run(..., capture_output=True)` + `np.frombuffer(..., np.float32)`.
- [ ] **Step 4: Run, expect PASS** (existing `AbcMidiTest` too).
- [ ] **Step 5: Commit** `git add tools/midi_velocity.py tests/test_abc_midi.py` — "Add loudness-to-velocity helpers for MIDI export".

### Task 2: Wire velocities into `abc_midi.render`

**Files:**
- Modify: `tools/abc_midi.py` (`_track`, `render`)
- Test: `tests/test_abc_midi.py`

**Interfaces:**
- Consumes: Task 1 functions.
- Produces: `_track(name, channel, notes, velocity: int | list[int])` — a list gives one velocity per note (same order as `notes`); an int keeps today's behaviour. `render(song_dir)` signature and outputs unchanged.

- [ ] **Step 1: Write failing tests** in `AbcMidiTest`:
  - `test_render_velocity_from_audio`: in the temp song dir write `events.json` with timestamps for subbeats 0..32 step 4 at 0.5 s per quarter, and an empty `audio.flac`; patch `midi_velocity.load_mono` (`unittest.mock.patch("midi_velocity.load_mono", return_value=x)`) with `x` = 0.05 amplitude for the first 2 s and 0.5 after, at sr 22050. Use a SCORE variant whose Ins bar has notes in both halves. Assert Ins note velocities are not all equal, the later (louder) note's velocity > earlier one's, all in 1–127; Vocal velocities all 100; chord velocities: second chord > first.
  - `test_render_without_events_uses_fixed`: existing SCORE, no events/audio → Ins/Vocal velocities all 100, chords all 70, and stdout contains `warning:` (use `contextlib.redirect_stdout`).
  - Update `_notes` helper to also return velocity (keep existing assertions via the `(start, note, end)` projection).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** In `render`: find audio (lookup above) and `sd/"events.json"`; if both exist, `try` `load_mono` + `quarter_clock`; on any missing file or `RuntimeError` print `warning: no audio/events, using fixed velocities` and use ints. Ins levels: `span_db(x, sr, clock(s), clock(s)+NOTE_WINDOW)` per note → `to_velocities(levels, 40, 120)`. Chord levels: one `span_db` over `clock(start)..clock(nxt)` per chord symbol, `to_velocities(levels, 30, 90)`, then each chord's velocity applied to all its pitches. Vocal stays `100`.
- [ ] **Step 4: Run tests, expect PASS.**
- [ ] **Step 5: Real-song check:** `.venv-sheetsage2\Scripts\python tools\abc_midi.py outputs/lofi/rainy_window` and the same for `outputs/citypop/midnight_drive`; print min/median/max Ins and chord velocities per song (one-liner with mido). Expect spread across roughly 40–120 / 30–90 and higher medians in chorus bars than intro bars. If neighbouring notes jitter by >25 regularly, raise `smooth` to 5 and note it in the commit.
- [ ] **Step 6: Commit** `git add tools/abc_midi.py tests/test_abc_midi.py` — "Derive MIDI note velocities from song loudness".
