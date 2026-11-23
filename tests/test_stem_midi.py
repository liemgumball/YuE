import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

try:
    import mido
    import numpy as np
    import soundfile as sf
except ImportError:  # these live in .venv-sheetsage2
    mido = None

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

SR = 22050
STEMS = ("drums", "bass", "other", "vocals")
SCORE = """X:1
M:4/4
L:1/16
Q:1/4=120
V: Vocal clef=treble name="Vocal Melody" snm="Vocal"
V: Ins clef=treble name="Ins Melody" snm="Inst."
K:C
V: Vocal
"C"c16|
V: Ins
Z1|
"""


def fake_separate(audio, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    x = (0.1 * np.sin(np.arange(SR) * 0.05)).astype(np.float32)
    for name in STEMS:
        sf.write(out_dir / f"{name}.wav", x, SR)


def fake_transcribe(wav, bass):
    return [(0.5, 1.0, 40 if bass else 64, 90)]


def note_ticks(path):
    m = mido.MidiFile(path)
    now, out = 0, []
    for msg in m.tracks[1]:
        now += msg.time
        if msg.type == "note_on" and msg.velocity:
            out.append((now, msg.note, msg.velocity))
    return m, out


@unittest.skipIf(mido is None, "mido/numpy/soundfile not installed")
class StemMidiTest(unittest.TestCase):
    def setUp(self):
        import stem_midi
        self.s = stem_midi

    def _song(self, tmp, score=True, beat0=0.5):
        song = Path(tmp)
        (song / "audio.flac").write_bytes(b"")
        sd = song / "sheetsage2"
        sd.mkdir()
        if score:
            (sd / "score.abc").write_text(SCORE, encoding="utf-8")
        if beat0 is not None:  # 120 BPM: one quarter every 0.5 s, quarter 0 at beat0
            ev = [{"subbeat": s, "values": {"timestamp": beat0 + s / 4 * 0.5}} for s in range(0, 33, 4)]
            (sd / "events.json").write_text(json.dumps({"events": ev}), encoding="utf-8")
        return song

    def _render(self, song, separate=fake_separate):
        buf = io.StringIO()
        with redirect_stdout(buf):
            out = self.s.render(song, separate, fake_transcribe)
        return out, buf.getvalue()

    def test_writes_stems_and_midis(self):
        with tempfile.TemporaryDirectory() as tmp:
            song = self._song(tmp)
            out, _ = self._render(song)
            sd = song / "sheetsage2"
            self.assertEqual(out, [sd / "bass.mid", sd / "other.mid"])
            for name in STEMS:
                self.assertTrue((sd / "stems" / f"{name}.wav").exists(), name)
            self.assertFalse((sd / "stems" / "raw").exists())
            self.assertEqual(mido.MidiFile(out[0]).tracks[1].name, "Bass")
            self.assertEqual(mido.MidiFile(out[1]).tracks[1].name, "Other")

    def test_notes_on_abc_grid(self):
        with tempfile.TemporaryDirectory() as tmp:
            song = self._song(tmp)
            out, _ = self._render(song)
            m, notes = note_ticks(out[0])
            # trimmed-time 0.5 s = audio 1.0 s = quarter 1
            self.assertEqual(notes, [(self.s.PPQ, 40, 90)])
            self.assertEqual(m.tracks[0][0].tempo, mido.bpm2tempo(120))

    def test_trimmed_stem_starts_at_beat0(self):
        with tempfile.TemporaryDirectory() as tmp:
            song = self._song(tmp, beat0=0.25)
            self._render(song)
            x, sr = sf.read(song / "sheetsage2" / "stems" / "drums.wav")
            self.assertEqual(len(x), SR - SR // 4)

    def test_note_before_beat0_dropped(self):
        notes = [(0.5, 1.5, 40, 80), (1.2, 2.0, 41, 81)]
        grid = self.s.to_grid(notes, lambda s: s - 1)
        self.assertEqual(len(grid), 1)
        start, pitch, length, vel = grid[0]
        self.assertAlmostEqual(start, 0.2)
        self.assertAlmostEqual(length, 0.8)
        self.assertEqual((pitch, vel), (41, 81))

    def test_reuses_existing_stems(self):
        def boom(audio, out_dir):
            raise AssertionError("separate should not run")
        with tempfile.TemporaryDirectory() as tmp:
            song = self._song(tmp)
            fake_separate(None, song / "sheetsage2" / "stems")
            out, _ = self._render(song, boom)
            self.assertTrue(out[0].exists())

    def test_no_events_or_score_uses_120bpm(self):
        with tempfile.TemporaryDirectory() as tmp:
            song = self._song(tmp, score=False, beat0=None)
            out, log = self._render(song)
            m, notes = note_ticks(out[0])
            self.assertEqual(m.tracks[0][0].tempo, mido.bpm2tempo(120))
            self.assertEqual(notes, [(self.s.PPQ, 40, 90)])  # 0.5 s at 120 BPM = quarter 1
            self.assertIn("warning", log)

    def test_negative_clock0_pads(self):
        with tempfile.TemporaryDirectory() as tmp:
            song = self._song(tmp, beat0=-0.25)
            self._render(song)
            x, _ = sf.read(song / "sheetsage2" / "stems" / "drums.wav")
            self.assertEqual(len(x), SR + SR // 4)
            self.assertTrue(np.all(x[: SR // 4] == 0))


if __name__ == "__main__":
    unittest.main()
