"""Render SheetSage2's quantized score.abc to DAW-ready MIDI (on-grid, constant tempo, bar 1 = score bar 1).

Writes sheetsage2/abc_melody.mid (Vocal + Ins tracks) and sheetsage2/abc_chords.mid (block chords
from the chord symbols, held until the next symbol).

  .venv-sheetsage2\\Scripts\\python tools\\abc_midi.py outputs/<folder>/<song>
"""
import json
import re
import sys
from pathlib import Path

import mido

import midi_velocity

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "yue2-music" / "scripts"))
import abc_tools  # noqa: E402

PPQ = 960
SAMPLE_RATE = 22050
ROOTS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
QUALITIES = {  # longest suffix first when matching
    "maj7": (0, 4, 7, 11), "m7b5": (0, 3, 6, 10), "dim7": (0, 3, 6, 9), "7sus4": (0, 5, 7, 10),
    "min7": (0, 3, 7, 10), "m7": (0, 3, 7, 10), "sus4": (0, 5, 7), "sus2": (0, 2, 7), "dim": (0, 3, 6),
    "aug": (0, 4, 8), "min": (0, 3, 7), "m6": (0, 3, 7, 9), "6": (0, 4, 7, 9), "7": (0, 4, 7, 10),
    "m": (0, 3, 7), "": (0, 4, 7),
}


def chord_pitches(symbol):
    """'Bbmaj7' -> MIDI pitches: bass root in octave 2-3, chord tones from C4 up. None if unknown."""
    m = re.fullmatch(r"([A-G])([#b]?)(\S*?)(?:\s.*)?", symbol.strip())
    if not m or m.group(3) not in QUALITIES:
        return None
    root = (ROOTS[m.group(1)] + {"#": 1, "b": -1, "": 0}[m.group(2)]) % 12
    return [36 + root] + [60 + (root + i) % 12 for i in QUALITIES[m.group(3)]]


def _track(name, channel, notes, velocity):
    """notes: (start_quarter, pitch, length_quarter) with Fraction times; velocity: int or one per note."""
    events = [(0, 0, mido.MetaMessage("track_name", name=name))]
    velocities = velocity if isinstance(velocity, list) else [velocity] * len(notes)
    for (start, pitch, length), velocity in zip(notes, velocities):
        on, off = round(start * PPQ), round((start + length) * PPQ)
        events += [(on, 2, mido.Message("note_on", channel=channel, note=pitch, velocity=velocity)),
                   (off, 1, mido.Message("note_off", channel=channel, note=pitch, velocity=0))]
    events.sort(key=lambda e: (e[0], e[1]))
    track, now = mido.MidiTrack(), 0
    for t, _, msg in events:
        track.append(msg.copy(time=t - now))
        now = t
    return track


def _file(score, tracks):
    mid = mido.MidiFile(ticks_per_beat=PPQ)
    num, den = score.voices["Vocal"].bars[0][2]
    head = mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(score.bpm)),
                           mido.MetaMessage("time_signature", numerator=num, denominator=den)])
    mid.tracks.append(head)
    mid.tracks.extend(tracks)
    return mid


def _loudness(song_dir):
    """(samples, sample_rate, quarter->seconds clock) for the song's mix, or None with a warning."""
    song = Path(song_dir)
    audio = next((p for p in (song / "audio.flac", song / "audio.wav", song / "audio.mp3") if p.exists()), None)
    events = song / "sheetsage2" / "events.json"
    try:
        if audio is None or not events.exists():
            raise RuntimeError("no audio/events")
        clock = midi_velocity.quarter_clock(json.loads(events.read_text(encoding="utf-8"))["events"])
        return midi_velocity.load_mono(audio, SAMPLE_RATE), SAMPLE_RATE, clock
    except (RuntimeError, OSError, KeyError, IndexError, ValueError) as e:
        print(f"warning: {e}, using fixed velocities", flush=True)
        return None


def render(song_dir):
    sd = Path(song_dir) / "sheetsage2"
    score = abc_tools.parse((sd / "score.abc").read_text(encoding="utf-8"))
    vocal, ins = score.voices["Vocal"], score.voices["Ins"]

    end = vocal.bars[-1][0] + vocal.bars[-1][1]
    marks = vocal.chords + [(end, None)]
    spans, unknown = [], set()  # (start, end, pitches) per sounding chord
    for (start, symbol), (nxt, _) in zip(marks, marks[1:]):
        pitches = chord_pitches(symbol)
        if pitches is None:
            unknown.add(symbol)
        elif nxt > start:
            spans.append((start, nxt, pitches))

    ins_vel, span_vel = 100, [70] * len(spans)
    loud = _loudness(song_dir)
    if loud:
        x, sr, clock = loud
        ins_vel = midi_velocity.to_velocities(
            [midi_velocity.span_db(x, sr, clock(s), clock(s) + midi_velocity.NOTE_WINDOW) for s, _, _ in ins.notes],
            40, 120, smooth=5)
        span_vel = midi_velocity.to_velocities(
            [midi_velocity.span_db(x, sr, clock(s), clock(e)) for s, e, _ in spans], 30, 90, smooth=1)

    melody = _file(score, [_track("Vocal", 0, vocal.notes, 100), _track("Ins", 1, ins.notes, ins_vel)])
    chord_notes = [(s, p, e - s) for s, e, ps in spans for p in ps]
    chord_vel = [v for (_, _, ps), v in zip(spans, span_vel) for _ in ps]
    chords = _file(score, [_track("Chords", 2, chord_notes, chord_vel)])

    out = [sd / "abc_melody.mid", sd / "abc_chords.mid"]
    melody.save(out[0])
    chords.save(out[1])
    for symbol in sorted(unknown):
        print(f"warning: unknown chord symbol {symbol!r} skipped", flush=True)
    return out


if __name__ == "__main__":
    for p in render(sys.argv[1]):
        print("ABC MIDI:", p)
