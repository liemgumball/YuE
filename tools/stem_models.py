"""Demucs separation and basic-pitch transcription, the real models behind stem_midi.render.

Install into the SheetSage2 venv:
  .venv-sheetsage2\\Scripts\\python -m pip install demucs basic-pitch soundfile
"""
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf
import torch

SEGMENT = 7.8  # seconds per Demucs chunk (htdemucs maximum), keeps VRAM low


def _demucs(model, wav, device):
    from demucs.apply import apply_model
    with torch.no_grad():
        return apply_model(model, wav[None], device=device, segment=SEGMENT, split=True, progress=False)[0]


def separate(audio: Path, out_dir: Path) -> None:
    from demucs.pretrained import get_model
    model = get_model("htdemucs").eval()
    x, _ = librosa.load(str(audio), sr=model.samplerate, mono=False)
    x = np.atleast_2d(x)
    if x.shape[0] == 1:
        x = np.repeat(x, 2, axis=0)
    wav = torch.from_numpy(x[:2]).float()
    ref = wav.mean(0)
    wav = (wav - ref.mean()) / (ref.std() + 1e-8)
    try:
        if not torch.cuda.is_available():
            raise torch.cuda.OutOfMemoryError
        out = _demucs(model, wav, "cuda")
    except torch.cuda.OutOfMemoryError:
        torch.cuda.empty_cache()
        print("Demucs on CPU (GPU busy or unavailable)", flush=True)
        out = _demucs(model, wav, "cpu")
    finally:
        torch.cuda.empty_cache()
    out = out * (ref.std() + 1e-8) + ref.mean()
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, stem in zip(model.sources, out.cpu().numpy()):
        sf.write(out_dir / f"{name}.wav", stem.T, model.samplerate)


def transcribe(wav: Path, bass: bool):
    from basic_pitch import ICASSP_2022_MODEL_PATH
    from basic_pitch.inference import predict
    kw = {"minimum_frequency": 30.0, "maximum_frequency": 400.0} if bass else {}
    _, _, events = predict(str(wav), ICASSP_2022_MODEL_PATH, **kw)
    return [(float(s), float(e), int(p), int(np.clip(round(a * 127), 1, 127))) for s, e, p, a, _ in events]
