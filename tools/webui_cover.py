"""Cover for the web UI: transcribe a reference recording with SheetSage2, then render it with YuE2.

Usage: webui_cover.py SHEETSAGE_PY YUE2_EXE OUTDIR ID REFERENCE MODE STYLE LYRICS [extra yue2 generate options...]
MODE: melody (chords dropped, accompaniment re-arranged), full (source chords kept),
      transcribe (stop after the score, for review before generating).
The score lands in OUTDIR/ID_reference/score.abc; the song in OUTDIR/ID.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # the log is UTF-8; file names may be Vietnamese
    sheetsage, exe, outdir, sid, reference, mode, style, lyrics, *extra = sys.argv[1:]
    score_dir = Path(outdir) / f"{sid}_reference"
    print(f"[1/2] SheetSage2: transcribing {Path(reference).name}", flush=True)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONNOUSERSITE="1")
    env.pop("PYTHONPATH", None)
    cmd = [sheetsage, str(ROOT / "models/SheetSage2/infer.py"), reference, "--output", str(score_dir)]
    if mode != "full":
        cmd.append("--melody-only")
    subprocess.run(cmd, check=True, env=env)
    score = score_dir / "score.abc"
    if not score.is_file():
        sys.exit(f"SheetSage2 produced no score.abc in {score_dir}")
    print(f"Score: {score}", flush=True)
    if mode == "transcribe":
        print("Transcription only: review the score, then pass it as --abc-file.", flush=True)
        return
    print(f"[2/2] YuE2: generating cover (cot={mode})", flush=True)
    subprocess.run([exe, "generate", "--output", outdir, "--id", sid, "--style", style, "--lyrics", lyrics,
                    "--abc-file", str(score), "--cot", mode, *extra], check=True)


if __name__ == "__main__":
    main()
