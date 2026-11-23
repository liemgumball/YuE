import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

try:
    import midi_velocity
except ImportError:  # numpy lives in .venv-sheetsage2
    midi_velocity = None

EVENTS = [{"subbeat": 0, "values": {"timestamp": 1.0}},
          {"subbeat": 4, "values": {"timestamp": 1.5}},
          {"subbeat": 8, "values": {"timestamp": 2.2}}]


@unittest.skipIf(midi_velocity is None, "numpy not installed")
class SecondsToQuartersTest(unittest.TestCase):
    def test_seconds_to_quarters_inverts_clock(self):
        clock = midi_velocity.quarter_clock(EVENTS)
        inv = midi_velocity.seconds_to_quarters(EVENTS)
        for q in [-2, 0, 0.5, 1.7, 5]:
            self.assertAlmostEqual(inv(clock(q)), q, places=6)
        self.assertAlmostEqual(inv(1.5), 1.0, places=9)


if __name__ == "__main__":
    unittest.main()
