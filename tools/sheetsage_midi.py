"""Transcribe a generated song to MIDI with SheetSage2.

Run with the separate SheetSage2 venv:
  .venv-sheetsage2\\Scripts\\python tools\\sheetsage_midi.py outputs/<folder>/<song>
"""
import sys
from pathlib import Path

import torch
from transformers import AutoModel

ROOT = Path(__file__).resolve().parent.parent
song = Path(sys.argv[1]).resolve()
audio = next(p for p in (song / "audio.flac", song / "audio.wav", song / "audio.mp3") if p.exists())
device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"Loading SheetSage2 on {device}", flush=True)
m = AutoModel.from_pretrained(str(ROOT / "models" / "SheetSage2"), trust_remote_code=True).eval().to(device)
print(f"Transcribing {audio}", flush=True)
r = m.transcribe(str(audio), output_dir=str(song / "sheetsage2"), dtype="bf16" if device == "cuda" else "fp32")
if r.get("abc_error"):
    print(f"abc_error: {r['abc_error']}", flush=True)
sheetsage_midis = {p.name for p in (song / "sheetsage2").glob("*.mid")}  # dropped once ours are written

sys.path.insert(0, str(ROOT / "tools"))
import abc_midi  # noqa: E402

try:
    print("ABC MIDI:", ", ".join(p.name for p in abc_midi.render(song)), flush=True)
except Exception as e:  # score.abc can be missing or malformed (see abc_error)
    print(f"ABC MIDI skipped: {e}", flush=True)

del m
torch.cuda.empty_cache()
import stem_midi  # noqa: E402

try:
    import stem_models
    print("Stem MIDI:", ", ".join(p.name for p in stem_midi.render(song, stem_models.separate, stem_models.transcribe)),
          flush=True)
except Exception as e:  # stems are optional; ABC MIDI above stands on its own
    print(f"Stem MIDI skipped: {e}", flush=True)

# Only abc_*.mid and the stem MIDI ship; SheetSage2's own MIDI files go once ours exist.
dropped = stem_midi.drop_sheetsage_midi(song / "sheetsage2", sheetsage_midis)
print("Removed SheetSage MIDI:", ", ".join(p.name for p in dropped), flush=True)
