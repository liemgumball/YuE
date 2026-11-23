"""Split a song into Demucs stems and transcribe bass/other to MIDI on the ABC MIDI's grid.

Writes sheetsage2/stems/{drums,bass,other,vocals}.wav (cut to start at score beat 0) and
sheetsage2/bass.mid + sheetsage2/other.mid (same tempo and bar 1 as abc_*.mid, not quantized).
Delete sheetsage2/stems/ to force a new separation.
"""
import json
import shutil
from pathlib import Path

import mido
import numpy as np
import soundfile as sf

import midi_velocity
from abc_midi import PPQ, _track, abc_tools

STEMS = ("drums", "bass", "other", "vocals")
DEFAULT_BPM = 120


def to_grid(notes, to_q):
    """(start_s, end_s, pitch, velocity) -> (start_q, pitch, length_q, velocity); drops notes starting before beat 0."""
    out = []
    for start, end, pitch, velocity in notes:
        q0, q1 = to_q(start), to_q(end)
        if q0 >= 0 and q1 > q0:
            out.append((q0, pitch, q1 - q0, velocity))
    return out


OURS = {"abc_melody.mid", "abc_chords.mid", "bass.mid", "other.mid"}


def drop_sheetsage_midi(sd, names):
    """Delete the .mid files SheetSage2 just wrote (names), never our own outputs."""
    dropped = [sd / n for n in sorted(names) if n not in OURS and (sd / n).exists()]
    for p in dropped:
        p.unlink()
    return dropped


def _bpm(sd):
    try:
        return abc_tools.parse((sd / "score.abc").read_text(encoding="utf-8")).bpm
    except Exception as e:  # missing or malformed score.abc
        print(f"warning: no usable score.abc ({e}), stem MIDI uses {DEFAULT_BPM} BPM", flush=True)
        return DEFAULT_BPM


def _grid(sd, bpm):
    """(beat-0 time in seconds, trimmed-seconds -> quarters) from events.json, or a fixed-tempo fallback."""
    try:
        events = json.loads((sd / "events.json").read_text(encoding="utf-8"))["events"]
        beat0 = midi_velocity.quarter_clock(events)(0)
        inv = midi_velocity.seconds_to_quarters(events)
        return beat0, lambda s: inv(s + beat0)
    except (OSError, KeyError, ValueError) as e:
        print(f"warning: no events.json ({e}), stem MIDI uses fixed tempo", flush=True)
        return 0.0, lambda s: s * bpm / 60


def _trim(src, dst, beat0):
    x, sr = sf.read(src, dtype="float32", always_2d=True)
    n = round(abs(beat0) * sr)
    if beat0 >= 0:
        x = x[n:]
    else:
        x = np.concatenate([np.zeros((n, x.shape[1]), x.dtype), x])
    sf.write(dst, x, sr)


def _audio(song):
    return next(p for p in (song / "audio.flac", song / "audio.wav", song / "audio.mp3") if p.exists())


def render(song_dir, separate, transcribe):
    """separate(audio, out_dir) writes the raw stem WAVs; transcribe(wav, bass) -> [(start_s, end_s, pitch, velocity)]."""
    song = Path(song_dir)
    sd = song / "sheetsage2"
    stems = sd / "stems"
    bpm = _bpm(sd)
    beat0, to_q = _grid(sd, bpm)

    cut = stems / "beat0.json"  # beat-0 time the stems were cut at; a new events.json can move it
    fresh = all((stems / f"{n}.wav").exists() for n in STEMS) and cut.exists() \
        and abs(json.loads(cut.read_text())["beat0"] - beat0) < 1e-6
    if not fresh:
        raw = stems / "raw"
        separate(_audio(song), raw)
        for n in STEMS:
            _trim(raw / f"{n}.wav", stems / f"{n}.wav", beat0)
        shutil.rmtree(raw)
        cut.write_text(json.dumps({"beat0": beat0}))

    out = []
    for name, channel in (("bass", 3), ("other", 4)):
        grid = to_grid(transcribe(stems / f"{name}.wav", name == "bass"), to_q)
        mid = mido.MidiFile(ticks_per_beat=PPQ)
        mid.tracks.append(mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm)),
                                          mido.MetaMessage("time_signature", numerator=4, denominator=4)]))
        mid.tracks.append(_track(name.capitalize(), channel, [(s, p, l) for s, p, l, _ in grid],
                                 [v for *_, v in grid]))
        path = sd / f"{name}.mid"
        mid.save(path)
        out.append(path)
    return out


if __name__ == "__main__":
    import sys
    import stem_models
    print("Stem MIDI:", ", ".join(p.name for p in render(sys.argv[1], stem_models.separate, stem_models.transcribe)))
