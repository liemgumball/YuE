# Loudness-based MIDI velocity (design)

## Intent
Exported MIDI (`tools/abc_midi.py`) writes one fixed velocity (Ins/Vocal 100, chords 70), so it
plays flat and hides the song's shape. Velocity should follow how loud the generated song actually
is, so the MIDI works both for layering new instruments and for studying the arrangement.

## Goals
- `Ins` track and `Chords` track get per-note velocities from the loudness of `audio.flac`.
- Dynamics show both the section arc (intro vs chorus) and phrase-level swells.
- Each song is scaled to its own range, so heavily mastered songs still show contrast.

## Non-goals
- Vocal dynamics. `Vocal` stays at fixed 100 for now (no stem separation).
- Changing note timing, pitches, or the on-grid layout.

## Anti-goals
- Jittery velocities driven by single drum hits (sounds random, not musical).

## Approaches
- **A. Mix RMS at each note onset, smoothed, normalized per song (chosen).** Robust on full
  mixes; captures section and phrase dynamics.
- B. Per-bar energy only: clean but flat inside each bar, so phrasing is lost.
- C. Pitch-band (CQT) energy per note: most specific, but noisy on dense mixes and fiddly to tune.

## Design
- **Time mapping:** score position (quarters) → audio seconds by interpolating the subbeat
  timestamps in `sheetsage2/events.json`.
- **Loudness:** RMS in dB of `audio.flac` (mono) over a short window after each note onset
  (~100 ms; builder may tune). Chords use the mean RMS over the chord's held span.
- **Smoothing:** light smoothing across neighbouring notes in the same track, so one transient
  can't spike a note.
- **Scaling:** map each track's loudness from its 5th–95th percentile to velocity 40–120, clamped
  to 1–127. Ins and chords keep their relative balance (chords offset lower, as today).
- **Fallback:** if the audio or events file is missing, keep today's fixed velocities and print a
  warning.

## Left to the builder
- Exact window length and smoothing strength (judge by ear/plot on the existing songs).
- Audio loading library (prefer what `.venv-sheetsage2` already has).
- Test: velocities vary, stay in 1–127, and louder audio regions give higher velocity (synthetic
  audio fixture).
