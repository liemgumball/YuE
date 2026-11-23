import sys
import tempfile
import unittest
from pathlib import Path

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

    def _notes(self, path):
        m = mido.MidiFile(path)
        out = []
        for tr in m.tracks:
            now, open_ = 0, {}
            for x in tr:
                now += x.time
                if x.type == "note_on" and x.velocity:
                    open_[x.note] = now
                elif x.type in ("note_on", "note_off"):
                    out.append((open_.pop(x.note), x.note, now))
        return m, sorted(out)

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


if __name__ == "__main__":
    unittest.main()
