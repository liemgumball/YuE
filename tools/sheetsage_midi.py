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
print("MIDI:", ", ".join(sorted(r.get("midis", {}))), flush=True)
