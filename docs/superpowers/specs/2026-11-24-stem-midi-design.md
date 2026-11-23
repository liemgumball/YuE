# Stem MIDI for FL Studio — Design

## Intent
The user takes YuE-generated songs into FL Studio to build new sounds on top of them.
Right now they get a lead sheet (melody + chords) as MIDI. A song with many instruments
also needs its bass line and the rest of its parts, and its drum groove for reference.

## Goals
- Keep the clean on-grid ABC MIDI: `abc_melody.mid` (Vocal + Ins) and `abc_chords.mid`.
- Stop shipping SheetSage2's own MIDI files: delete whatever `m.transcribe` writes as `*.mid` (its `midis` dict) once it has run.
- Split the song into stems and add:
  - `sheetsage2/bass.mid`: bass stem transcribed to MIDI
  - `sheetsage2/other.mid`: "other" stem (guitar/keys/pads mixed) transcribed to MIDI
  - `sheetsage2/stems/{drums,bass,other,vocals}.wav`: the separated audio stems
- Still one button ("🎼 Tạo MIDI") and one job per song in the web UI.

## Non-goals
- No drum MIDI. Drums ship as audio only (user's choice: drum transcription is the weakest link).
- No vocal MIDI from the stem. The ABC melody already covers it.
- No splitting "other" into individual instruments.
- No quantizing of stem MIDI. FL users quantize themselves; we keep the real timing.

## Anti-goals
- Breaking or degrading the ABC MIDI path. Stem failures must not stop ABC output, and the other way around.
- A second environment or second job the user has to manage.

## Constraints
- Windows, RTX 3060 12 GB. Runs in `.venv-sheetsage2` (torch 2.8 cu126, torchaudio, mido).
- Separation and transcription models are not installed yet. Install them into `.venv-sheetsage2`.
- Input audio is the song's `audio.flac|wav|mp3`, the same lookup as `tools/sheetsage_midi.py`.

## Approaches considered
- A. Melody + chords only. Smallest change, but the user writes everything else by hand.
- B. Add a bass track derived from chord roots. Clean and cheap, but not the song's real bass.
- **C. Stem separation + per-stem transcription (chosen).** Closest to the original parts.
  Costs: new models, extra minutes per song, and messy off-grid notes the user cleans up in FL.
  The user picked C. For drums they picked audio over MIDI.

## Design
- Separation: Demucs `htdemucs` (4 stems: drums, bass, other, vocals) on GPU. *(My call: standard and good quality; the 6-stem model's guitar/piano split is too unreliable to be worth it.)*
- Transcription: Spotify `basic-pitch` on `bass.wav` and `other.wav`. *(My call: polyphonic, handles both, lightweight.)* For bass, force monophonic output or clamp the pitch range low if the library supports it.
- Alignment with the ABC files *(my call)*: the ABC MIDI starts at score beat 0, which is audio time `clock(0)` (the quarter clock built from `sheetsage2/events.json`, `midi_velocity.quarter_clock`). Stem note times in seconds go back to quarters through the inverse of that clock, then to ticks at the score BPM. This way bass and other sit on the same grid as the ABC files, even when the tempo drifts slightly. Notes before beat 0 get dropped. If there's no `events.json`, use plain seconds at the score BPM (or 120) and print a warning.
- Stem WAVs are cut to start at `clock(0)`, so dropping them at bar 1 in FL lines them up with the MIDI. They're audio, so they can't follow tempo drift.
- Reruns *(my call)*: when `stems/*.wav` already exist, skip separation and only redo transcription. Delete the `stems/` folder to force a new separation.
- GPU *(my call)*: the web UI can start a MIDI job while YuE is generating. Run Demucs with its chunked `segment` mode and free GPU memory between steps. If it hits CUDA out-of-memory, retry Demucs on CPU (slower, but it finishes).
- Velocities: whatever basic-pitch outputs.
- Track names: "Bass" and "Other".
- New module `tools/stem_midi.py` with `render(song_dir) -> list[Path]`. `sheetsage_midi.py` calls it after the ABC step, in its own try/except, and prints `Stem MIDI: ...` or `Stem MIDI skipped: <err>`.
- Web UI (`tools/webui.py`): the file count already globs `*.mid`. Update the help text to say melody, chords, bass, other + audio stems. The "📂" folder button already opens `sheetsage2/`.

## Left to the builder
- Exact install commands and pinned versions (check that basic-pitch runs on Windows with this Python; its ONNX backend is fine).
- Whether stems get written as WAV via torchaudio or soundfile.
- Test design: unit-test the MIDI writing and SheetSage-MIDI cleanup with fakes. The models are only run in one manual end-to-end check on a real song.
