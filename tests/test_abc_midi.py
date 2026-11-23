import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

try:
    import mido
except ImportError:  # mido lives in .venv-sheetsage2
    mido = None

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

SCORE = """X:1
T:
M:4/4
L:1/16
Q:1/4=117
V: Vocal clef=treble name="Vocal Melody" snm="Vocal"
V: Ins clef=treble name="Ins Melody" snm="Inst."
K:Dm
% intro
V: Vocal
"Dm7"z12de2f-|"Bbmaj7"f4z12|
V: Ins
Z2|
"""


@unittest.skipIf(mido is None, "mido not installed")
class AbcMidiTest(unittest.TestCase):
    def setUp(self):
        import abc_midi
        self.a = abc_midi

    def test_chord_pitches(self):
        self.assertEqual(self.a.chord_pitches("Dm7"), [38, 62, 65, 69, 60])
        self.assertEqual(self.a.chord_pitches("Bbmaj7"), [46, 70, 62, 65, 69])
        self.assertEqual(self.a.chord_pitches("A7sus4"), [45, 69, 62, 64, 67])
        self.assertIsNone(self.a.chord_pitches("Xyz"))
        # slash chords: the note after "/" replaces the bass root
        self.assertEqual(self.a.chord_pitches("Dm/F"), [41, 62, 65, 69])
        self.assertEqual(self.a.chord_pitches("Dm7/F"), [41, 62, 65, 69, 60])
        self.assertEqual(self.a.chord_pitches("F/A"), [45, 65, 69, 60])
        self.assertEqual(self.a.chord_pitches("C/Bb"), [46, 60, 64, 67])
        self.assertIsNone(self.a.chord_pitches("C/H"))

    def _notes(self, path, with_velocity=False, track=None):
        m = mido.MidiFile(path)
        out = []
        for tr in m.tracks:
            if track and not any(x.type == "track_name" and x.name == track for x in tr):
                continue
            now, open_ = 0, {}
            for x in tr:
                now += x.time
                if x.type == "note_on" and x.velocity:
                    open_[x.note] = (now, x.velocity)
                elif x.type in ("note_on", "note_off"):
                    start, vel = open_.pop(x.note)
                    out.append((start, x.note, now, vel) if with_velocity else (start, x.note, now))
        return m, sorted(out)

    def _song(self, tmp, score, events=True):
        sd = Path(tmp) / "sheetsage2"
        sd.mkdir()
        (sd / "score.abc").write_text(score, encoding="utf-8")
        if events:
            ev = [{"subbeat": s, "values": {"timestamp": s / 4 * 0.5}} for s in range(0, 33, 4)]
            (sd / "events.json").write_text(json.dumps({"events": ev}), encoding="utf-8")
            (Path(tmp) / "audio.flac").write_bytes(b"")

    def test_render_velocity_from_audio(self):
        import numpy as np
        sr = 22050
        x = np.concatenate([np.full(2 * sr, 0.05, np.float32), np.full(2 * sr, 0.5, np.float32)])
        score = SCORE.replace("Z2|", "c4c4c4c4|c4c4c4c4|")
        with tempfile.TemporaryDirectory() as tmp:
            self._song(tmp, score)
            with patch("midi_velocity.load_mono", return_value=x):
                mel, ch = self.a.render(tmp)
            _, ins = self._notes(mel, True, "Ins")
            _, voc = self._notes(mel, True, "Vocal")
            _, chords = self._notes(ch, True)
        vel = [n[3] for n in ins]
        self.assertEqual(len(vel), 8)
        self.assertTrue(all(1 <= v <= 127 for v in vel))
        self.assertLess(vel[0], vel[-1])
        self.assertEqual({n[3] for n in voc}, {100})
        first = {n[3] for n in chords if n[0] == 0}
        second = {n[3] for n in chords if n[0] == 3840}
        self.assertEqual(len(first), 1)
        self.assertLess(max(first), min(second))

    def test_render_without_events_uses_fixed(self):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            self._song(tmp, SCORE, events=False)
            with redirect_stdout(out):
                mel, ch = self.a.render(tmp)
            _, notes = self._notes(mel, True)
            _, chords = self._notes(ch, True)
        self.assertEqual({n[3] for n in notes}, {100})
        self.assertEqual({n[3] for n in chords}, {70})
        self.assertIn("warning:", out.getvalue())

    def test_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            sd = Path(tmp) / "sheetsage2"
            sd.mkdir()
            (sd / "score.abc").write_text(SCORE, encoding="utf-8")
            mel, ch = self.a.render(tmp)
            m, notes = self._notes(mel)
            self.assertEqual(round(mido.tempo2bpm(m.tracks[0][0].tempo)), 117)
            # d at beat 3, e (1/8) at 3.25, tied f from 3.75 through 4.75
            self.assertEqual(notes, [(2880, 74, 3120), (3120, 76, 3600), (3600, 77, 4800)])
            _, cnotes = self._notes(ch)
            self.assertEqual({(s, e) for s, _, e in cnotes}, {(0, 3840), (3840, 7680)})


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
        self.assertEqual(min(out), 40)
        self.assertEqual(max(out), 120)

    def test_quarter_clock_rejects_too_few_timestamps(self):
        with self.assertRaises(ValueError):
            self.v.quarter_clock([{"subbeat": 0, "values": {"timestamp": 1.0}}])
        with self.assertRaises(ValueError):
            self.v.quarter_clock([{"subbeat": 0, "values": {}}])

    def test_quarter_clock_dedupes_and_sorts(self):
        ev = [{"subbeat": 4, "values": {"timestamp": 1.5}}, {"subbeat": 0, "values": {"timestamp": 1.0}},
              {"subbeat": 4, "values": {"timestamp": 1.5}}]
        clock = self.v.quarter_clock(ev)
        self.assertAlmostEqual(clock(2.0), 2.0)
        self.assertAlmostEqual(clock(-1.0), 0.5)

    def test_to_velocities_silent_outlier_floors_at_half_lo(self):
        out = self.v.to_velocities([-20.0] * 30 + [-10.0] * 30 + [-180.0], 40, 120, smooth=1)
        self.assertEqual(out[-1], 20)

    def test_to_velocities_flat_and_empty(self):
        self.assertEqual(self.v.to_velocities([-20.0] * 5, 40, 120), [80] * 5)
        self.assertEqual(self.v.to_velocities([-20.0], 40, 120), [80])
        self.assertEqual(self.v.to_velocities([], 40, 120), [])


if __name__ == "__main__":
    unittest.main()
