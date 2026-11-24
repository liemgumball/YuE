"""Instrumental song for the web UI: plan with YuE2, move the vocal melody to Ins, render.

Usage: webui_instrumental.py YUE2_EXE OUTDIR ID STYLE [extra yue2 generate options...]
Empty lyrics alone are not enough: YuE2 still plans a Vocal voice and sings it.
"""
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/yue2-music/instrumental/scripts"
sys.path.insert(0, str(SCRIPTS))
from instrumentalize import convert_score  # noqa: E402

PLAN_LYRICS = "[Intro]\n\n[Verse]\n\n[Chorus]\n\n[Outro]\n"


def lyric_tags(abc):
    labels = [line[2:] for line in abc.splitlines() if line.startswith("% ")]
    return "\n\n".join("[" + x.title() + "]" for x in labels) + ("\n" if labels else "")


def main():
    exe, outdir, sid, style, *extra = sys.argv[1:]
    base = [exe, "generate", "--output", outdir, "--style", style, *extra]
    plan_id = sid + "_plan"
    subprocess.run([*base, "--id", plan_id, "--stage", "plan", "--lyrics", PLAN_LYRICS], check=True)
    converted, transfer = convert_score((Path(outdir) / plan_id / "score.abc").read_text(encoding="utf-8"))
    print("voice transfer:", transfer, flush=True)
    abc = Path(outdir) / plan_id / "score.instrumental.abc"
    abc.write_text(converted, encoding="utf-8")
    subprocess.run([*base, "--id", sid, "--abc-file", str(abc), "--lyrics", lyric_tags(converted)], check=True)


if __name__ == "__main__":
    main()
