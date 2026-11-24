"""Read the sung lyrics of a recording: Demucs isolates the vocals, Whisper transcribes them.

Run with the SheetSage2 venv (it has Demucs; faster-whisper is installed there too):
  .venv-sheetsage2\\Scripts\\python tools\\lyrics_asr.py SONG.wav OUT.json
OUT.json: {"lyrics": "[Verse]\\n...", "language": "vi", "lines": 12, "missed": 0}. "lyrics" is "" when
nothing is sung; "missed" counts sung passages Whisper could not hear (their lines are absent).
Lines follow Whisper's phrases; a long pause starts a new section, and a section whose words
recur elsewhere is labelled [Chorus]. Both are guesses for the user to proofread.
"""
import difflib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import torch  # first: its lib folder carries the cuBLAS/cuDNN DLLs CTranslate2 loads on Windows

if sys.platform == "win32":
    os.add_dll_directory(str(Path(torch.__file__).parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import stem_models  # noqa: E402

WHISPER = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
SECTION_GAP = 4.0  # seconds of silence that start a new section
CHORUS_SIMILARITY = 0.6


def whisper():
    from faster_whisper import WhisperModel
    if torch.cuda.is_available():
        try:
            return WhisperModel(WHISPER, device="cuda", compute_type="float16")
        except Exception as e:  # missing CUDA DLLs or no VRAM: CPU is slow but works
            print(f"Whisper on CPU ({e})", flush=True)
    return WhisperModel(WHISPER, device="cpu", compute_type="int8")


# Captions Whisper learned from YouTube subtitles and "hears" in instrumental passages.
HALLUCINATION = re.compile(
    r"subscribe|đăng ký kênh|ghiền mì gõ|video hấp dẫn|cảm ơn các bạn đã|hẹn gặp lại các bạn|"
    r"thanks? (you )?for watching|like and share|amara\.org|subtitles? by|"
    r"^(thank you|thanks|you|bye|okay|cảm ơn|xin chào|music|nhạc)[.!…]*$|^[♪♫\s.]*$|"
    r"^((m+|h?m+h?|u+h+|a+h*|o+h*|oo+h*|la|na|da|yeah|ya)[\s,.!…-]*)+$", re.I)  # humming and vocalise


SR = 16000
HOP = 0.05  # seconds per loudness frame
SILENT_DB = -30  # relative to the loudest frame of the original mix, so instrument bleed in the stem stays silent
JOIN_GAP = 1.0  # shorter pauses stay inside one passage
MAX_PASSAGE = 28.0  # Whisper hears 30 s at a time


def passages(audio, mix):
    """(start, end) seconds of sung passages. The vocal stem is near-silent between phrases."""
    import librosa
    import numpy as np

    def rms(x):
        return librosa.feature.rms(y=x, frame_length=2048, hop_length=int(SR * HOP))[0]
    db = librosa.amplitude_to_db(rms(audio), ref=float(rms(mix).max()))
    spans, start = [], None
    for i, loud in enumerate(np.append(db > SILENT_DB, False)):
        if loud and start is None:
            start = i
        elif not loud and start is not None:
            if spans and (start - spans[-1][1]) * HOP < JOIN_GAP:
                spans[-1][1] = i
            else:
                spans.append([start, i])
            start = None
    out = []
    for a, b in spans:
        while (b - a) * HOP > MAX_PASSAGE:  # cut long passages at their quietest frame
            lo, hi = a + int(MAX_PASSAGE / 2 / HOP), a + int(MAX_PASSAGE / HOP)
            cut = lo + int(np.argmin(db[lo:hi]))
            out.append((a * HOP, cut * HOP))
            a = cut
        if (b - a) * HOP >= 0.5:
            out.append((a * HOP, b * HOP))
    return out


def heard(model, clip, language, temperature):
    """Whisper's real lines in one clip ([] when all it 'hears' is captions or loops), and its language."""
    segments, info = model.transcribe(clip, language=language, beam_size=5, temperature=temperature,
                                      vad_filter=False, word_timestamps=True, condition_on_previous_text=False)
    lines = []
    for s in segments:
        text = re.sub(r"\s+", " ", s.text).strip()
        if (not text or HALLUCINATION.search(text) or s.compression_ratio > 2.4
                or (s.no_speech_prob > 0.6 and s.avg_logprob < -1)):
            continue  # captions, repetition loops and "speech" heard in silence
        words = s.words or []
        lines.append((words[0].start if words else s.start, words[-1].end if words else s.end, text))
    return lines, info.language


def phrases(vocals, mix):
    """Transcribe each sung passage alone, so a hallucination costs one passage, never the rest of the song.

    On Vietnamese songs, large-v3-turbo answers whole passages with a YouTube caption, and which passages
    depends on the input: the vocal stem and the original mix fail on different ones, and sampling
    rescues some more. So each passage tries those in turn and keeps the first real lines.
    Whole-song decoding skipped half of a 3-minute song on some runs; VAD on the stem made it worse.
    """
    import librosa
    stem, _ = librosa.load(str(vocals), sr=SR, mono=True)
    full, _ = librosa.load(str(mix), sr=SR, mono=True)
    model, kept, missed = whisper(), [], 0
    spans = passages(stem, full)
    if not spans:
        return [], None, 0
    # The longest passage decides the language for all: a short intro once made an English song "zh".
    a, b = max(spans, key=lambda p: p[1] - p[0])
    language = model.detect_language(stem[int(a * SR):int(b * SR)])[0]
    for start, end in spans:
        a, b = int(start * SR), int(end * SR)
        for clip, temperature in ((stem, 0.0), (full, 0.0), (stem, 0.4), (full, 0.4), (full, 0.8)):
            lines, _ = heard(model, clip[a:b], language, temperature)
            if lines:
                kept += [(start + s, start + e, text) for s, e, text in lines]
                break
        else:
            missed += 1
            print(f"Không nghe được đoạn {start:.0f}-{end:.0f}s", flush=True)
    return kept, language, missed


def shape(kept):
    sections, last_end = [], None
    for start, end, text in kept:
        if last_end is None or start - last_end > SECTION_GAP:
            sections.append([])
        sections[-1].append(text)
        last_end = end
    def recurs(line, i):  # the line is sung again, roughly, in another section
        return any(difflib.SequenceMatcher(None, line.lower(), other.lower()).ratio() > CHORUS_SIMILARITY
                   for j, s in enumerate(sections) if j != i for other in s)

    out = []
    for i, lines in enumerate(sections):
        chorus = sum(recurs(line, i) for line in lines) > len(lines) / 2
        out.append(("[Chorus]" if chorus else "[Verse]") + "\n" + "\n".join(lines))
    return "\n\n".join(out) + "\n" if out else ""


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    audio, out = Path(sys.argv[1]), Path(sys.argv[2])
    with tempfile.TemporaryDirectory() as tmp:
        print("[1/2] Demucs: tách giọng hát", flush=True)
        stem_models.separate(audio, Path(tmp))
        torch.cuda.empty_cache()
        print("[2/2] Whisper: nghe lời bài hát", flush=True)
        kept, language, missed = phrases(Path(tmp) / "vocals.wav", audio)
    lyrics = shape(kept)
    out.write_text(json.dumps({"lyrics": lyrics, "language": language, "lines": len(kept), "missed": missed},
                              ensure_ascii=False), encoding="utf-8")
    print(f"{len(kept)} lines, language {language}", flush=True)


if __name__ == "__main__":
    main()
