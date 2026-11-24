"""Minimal YuE2 web UI: list generated songs and launch `yue2 generate`.

Stdlib only. Run:  .venv\\Scripts\\python tools\\webui.py  then open http://127.0.0.1:7860
"""
import json
import os
import re
import mimetypes
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
OUTPUTS = ROOT / "outputs"
REFERENCES = OUTPUTS / "_references"
AUDIO_EXTS = (".wav", ".flac", ".mp3", ".m4a", ".ogg", ".aac")
JOBS = {}  # id -> {"proc", "log", "cmd", "started", "output"}

# (flag, type, default, choices/help). type: text|number|int|choice|bool
OPTIONS = [
    ("--model", "text", "", "Repo Hugging Face hoặc thư mục model. Để trống = model mặc định."),
    ("--vae", "combo", "standard", ["standard", "legacy"], "Bộ giải mã âm thanh: standard (nghe tốt nhất), legacy (đánh giá theo paper), hoặc đường dẫn/repo."),
    ("--revision", "text", "", "Phiên bản (branch/commit) của model trên Hugging Face."),
    ("--vae-revision", "text", "", "Phiên bản (branch/commit) của VAE."),
    ("--device", "combo", "auto", ["auto", "cuda", "cuda:0", "cuda:1", "xpu", "mps", "cpu"], "Thiết bị chạy. auto = tự chọn GPU tốt nhất."),
    ("--budget", "combo", "24", ["8", "12", "16", "24", "32", "48", "80"], "Giới hạn VRAM (GB). Giảm nếu GPU ít bộ nhớ."),
    ("--backend", "choice", "torch", ["torch", "torch-eager", "vllm"], "torch: mặc định; torch-eager: không biên dịch, dễ debug; vllm: nhanh nhất (chỉ Linux)."),
    ("--quantization", "choice", "none", ["none", "fp8"], "fp8 giảm VRAM, có thể giảm nhẹ chất lượng."),
    ("--cot", "choice", "", ["", "full", "melody", "off"], "Chế độ lập kế hoạch: full = đầy đủ, melody = chỉ giai điệu, off = tắt."),
    ("--stage", "choice", "audio", ["audio", "plan"], "audio: tạo bài hát hoàn chỉnh; plan: chỉ tạo bản nhạc ABC để chỉnh sửa."),
    ("--seed", "int", "", "Số ngẫu nhiên. Cùng seed + cùng đầu vào → kết quả giống nhau. Trống = ngẫu nhiên."),
    ("--cfg-scale", "number", "", "Mức bám theo mô tả phong cách. Cao hơn = sát mô tả hơn, ít đa dạng hơn."),
    ("--config", "text", "", "Đường dẫn file cấu hình JSON (tùy chọn)."),
    ("--abc-file", "text", "", "Đường dẫn score.abc đã chỉnh sửa, dùng để tạo lại âm thanh từ bản nhạc."),
    ("--offload-ar", "bool", "", "Chuyển model AR sang CPU khi không dùng (tiết kiệm VRAM)."),
    ("--low-vram", "bool", "", "Chỉ giữ phần model đang chạy trên GPU. Chậm hơn nhưng ít VRAM."),
    ("--offline", "bool", "", "Không tải gì từ Internet, chỉ dùng file đã có."),
    ("--resume", "bool", "", "Tiếp tục từ kết quả dở dang trong thư mục đầu ra."),
]


def songs():
    out = []
    for audio in sorted(OUTPUTS.rglob("audio.*"), key=lambda p: p.stat().st_mtime, reverse=True):
        if audio.suffix not in (".flac", ".wav", ".mp3"):
            continue
        d = audio.parent
        req = {}
        try:
            req = json.loads((d / "request.json").read_text(encoding="utf-8"))
        except Exception:
            pass
        out.append({
            "name": str(d.relative_to(OUTPUTS)).replace("\\", "/"),
            "audio": str(audio.relative_to(OUTPUTS)).replace("\\", "/"),
            "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(audio.stat().st_mtime)),
            "style": req.get("style", ""), "lyrics": req.get("lyrics", ""), "seed": req.get("seed"),
            "tempo": tempo(d),
            "midis": sorted(str(f.relative_to(OUTPUTS)).replace("\\", "/") for f in (d / "sheetsage2").glob("*.mid")),
        })
    return out


def tempo(d):
    """Tempo from score.abc (Q: line) and, if available, measured from sheetsage2/beat.lab."""
    score, measured = None, None
    try:
        for line in (d / "score.abc").read_text(encoding="utf-8").splitlines():
            if line.startswith("Q:"):
                score = int(float(line.split("=")[-1].strip()))
                break
    except Exception:
        pass
    try:
        beats = [float(l.split()[0]) for l in (d / "sheetsage2" / "beat.lab").read_text().splitlines() if l.strip()]
        if len(beats) > 1:
            measured = round(60 * (len(beats) - 1) / (beats[-1] - beats[0]), 1)
    except Exception:
        pass
    return {"score": score, "measured": measured}


def folders():
    return sorted(p.name for p in OUTPUTS.iterdir() if p.is_dir() and not p.name.startswith("_")) if OUTPUTS.exists() else []


def start_job(form):
    if form.get("instrumental"):
        form["lyrics"] = ""
        st = form.get("style", "")
        if "instrumental" not in st.lower():
            form["style"] = "Instrumental, " + st + ", no vocals"
    for k in list(form):
        if form[k] == "__new__":
            form[k] = form.get(k + "__custom", "")
    jid = uuid.uuid4().hex[:8]
    sid = (form.get("id") or f"song_{jid}").strip()
    outdir = OUTPUTS / (form.get("folder") or "webui").strip()
    outdir.mkdir(parents=True, exist_ok=True)
    exe = Path(sys.executable).with_name("yue2.exe" if sys.platform == "win32" else "yue2")
    exe = str(exe) if exe.exists() else "yue2"
    inst = bool(form.get("instrumental"))
    ref = form.get("reference")
    if ref:
        ref = (OUTPUTS / ref).resolve()
        if REFERENCES.resolve() not in ref.parents or not ref.is_file():
            return {"error": "Không tìm thấy file bài hát tham khảo. Hãy tải lên lại."}
        if inst:
            return {"error": "Cover không lời chưa hỗ trợ trong giao diện. Bỏ chọn “Nhạc không lời” hoặc bỏ bài tham khảo."}
        if any(j["proc"].poll() is None for j in JOBS.values()):
            return {"error": "Đang có tác vụ khác chạy trên GPU. Vui lòng chờ xong rồi thử lại."}
        sheetsage = ROOT / ".venv-sheetsage2" / "Scripts" / "python.exe"
        if not sheetsage.exists():
            return {"error": "Chưa cài .venv-sheetsage2 (cần để chép nhạc từ bài tham khảo)."}
    opts = []
    for flag, kind, *_ in OPTIONS:
        if inst and flag in ("--stage", "--abc-file", "--resume"):
            continue
        if ref and flag in ("--cot", "--abc-file", "--stage", "--resume"):
            continue
        v = form.get(flag)
        if kind == "bool":
            if v:
                opts.append(flag)
        elif v not in (None, ""):
            opts += [flag, str(v)]
    if ref:
        mode = form.get("cover_mode") if form.get("cover_mode") in ("melody", "full", "transcribe") else "melody"
        cmd = [sys.executable, "-u", str(ROOT / "tools/webui_cover.py"), str(sheetsage), exe, str(outdir), sid,
               str(ref), mode, form.get("style", ""), form.get("lyrics", ""), *opts]
    elif inst:
        cmd = [sys.executable, "-u", str(ROOT / "tools/webui_instrumental.py"), exe, str(outdir), sid,
               form.get("style", ""), *opts]
    else:
        cmd = [exe, "generate", "--output", str(outdir), "--id", sid,
               "--style", form.get("style", ""), "--lyrics", form.get("lyrics", ""), *opts]
    log = outdir / f"{sid}.webui.log"
    fh = open(log, "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT)
    JOBS[jid] = {"proc": proc, "log": log, "cmd": cmd, "started": time.time(), "id": f"Cover: {sid}" if ref else sid}
    threading.Thread(target=lambda: (proc.wait(), fh.close()), daemon=True).start()
    return {"job": jid}


def save_reference(name, data):
    """Store an uploaded reference recording under outputs/_references/ (prefixed, so never named audio.*)."""
    REFERENCES.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w.-]+", "_", Path(name).name).strip("._") or "reference"
    if Path(stem).suffix.lower() not in AUDIO_EXTS:
        return {"error": "Chỉ nhận file âm thanh: " + ", ".join(AUDIO_EXTS)}
    f = REFERENCES / f"{uuid.uuid4().hex[:8]}_{stem}"
    f.write_bytes(data)
    return {"path": str(f.relative_to(OUTPUTS)).replace("\\", "/"), "name": Path(name).name,
            "lyrics": embedded_lyrics(f)}


def sung_lyrics(rel):
    """Lyrics heard in the singing (tools/lyrics_asr.py: Demucs + Whisper). Blocks ~30 s; cached per file."""
    ref = (OUTPUTS / rel).resolve()
    if REFERENCES.resolve() not in ref.parents or not ref.is_file():
        return {"error": "Không tìm thấy file bài hát tham khảo."}
    cache = ref.with_name(ref.name + ".lyrics.json")
    if not cache.exists():
        if any(j["proc"].poll() is None for j in JOBS.values()):
            return {"error": "GPU đang bận với tác vụ khác, chưa nghe lời được. Hãy nhập lời bằng tay hoặc tải lên lại sau."}
        py = ROOT / ".venv-sheetsage2" / "Scripts" / "python.exe"
        if not py.exists():
            return {"error": "Chưa cài .venv-sheetsage2 (cần để nghe lời bài hát)."}
        log = ref.with_name(ref.name + ".lyrics.log")
        with open(log, "w", encoding="utf-8") as fh:
            env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            proc = subprocess.Popen([str(py), str(ROOT / "tools" / "lyrics_asr.py"), str(ref), str(cache)], cwd=ROOT,
                                    stdout=fh, stderr=subprocess.STDOUT, env=env)
            JOBS[uuid.uuid4().hex[:8]] = {"proc": proc, "log": log, "started": time.time(),
                                          "id": f"Nghe lời: {ref.name.split('_', 1)[-1]}"}
            try:
                proc.wait(timeout=900)
            except subprocess.TimeoutExpired:
                proc.kill()
        if proc.returncode or not cache.exists():
            return {"error": "Không nghe được lời bài hát (xem log trong mục “Đang chạy”). Hãy nhập lời bằng tay."}
    try:
        return json.loads(cache.read_text(encoding="utf-8"))
    except Exception:
        return {"error": "Kết quả nghe lời bị hỏng. Hãy nhập lời bằng tay."}


LRC_TIME = re.compile(r"\[\d+:\d+(?:[.:]\d+)?\]")
LRC_META = re.compile(r"^\[(ar|ti|al|au|by|re|ve|length|offset|id|la):.*\]$", re.I)


def embedded_lyrics(f):
    """Lyrics stored in the file's tags (ID3 USLT, Vorbis LYRICS, MP4 ©lyr), shaped for YuE2; "" if none.

    Never raises: a missing ffprobe, an unreadable file or untagged audio all mean "no lyrics".
    """
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags:stream_tags", "-of", "json", str(f)],
                             capture_output=True, timeout=30, encoding="utf-8", errors="replace").stdout
        info = json.loads(out or "{}")
        tags = [info.get("format", {}).get("tags", {})] + [s.get("tags", {}) for s in info.get("streams", [])]
        text = next((v for t in tags for k, v in t.items()
                     if k.lower().startswith(("lyrics", "unsyncedlyrics")) and str(v).strip()), "")
    except Exception:
        return ""
    lines = []
    for line in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.strip()
        if LRC_META.match(line):
            continue
        timed = bool(LRC_TIME.match(line))
        line = LRC_TIME.sub("", line).strip()
        if timed and not line:  # an empty timed line is an instrumental gap
            continue
        lines.append(line)
    paragraphs = [p.strip("\n") for p in re.split(r"\n\s*\n", "\n".join(lines)) if p.strip()]
    if not paragraphs:
        return ""
    if not any(re.match(r"^\[[^\]]+\]$", line) for p in paragraphs for line in p.split("\n")):
        paragraphs = ["[Verse]\n" + p for p in paragraphs]  # YuE2 needs section tags; user relabels choruses
    return "\n\n".join(paragraphs) + "\n"


def start_midi(name):
    if any(j["proc"].poll() is None for j in JOBS.values()):
        return {"error": "Đang có tác vụ khác chạy trên GPU. Vui lòng chờ xong rồi thử lại."}
    song = (OUTPUTS / name).resolve()
    if OUTPUTS.resolve() not in song.parents or not song.is_dir():
        return {"error": "Không tìm thấy bài hát."}
    py = ROOT / ".venv-sheetsage2" / "Scripts" / "python.exe"
    if not py.exists():
        return {"error": "Chưa cài .venv-sheetsage2."}
    jid = uuid.uuid4().hex[:8]
    log = song / "sheetsage2.webui.log"
    fh = open(log, "w", encoding="utf-8")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([str(py), str(ROOT / "tools" / "sheetsage_midi.py"), str(song)], cwd=ROOT,
                            stdout=fh, stderr=subprocess.STDOUT, env=env)
    JOBS[jid] = {"proc": proc, "log": log, "started": time.time(), "id": f"MIDI: {name}", "song": name}
    threading.Thread(target=lambda: (proc.wait(), fh.close()), daemon=True).start()
    return {"job": jid}


def jobs():
    res = []
    for jid, j in sorted(JOBS.items(), key=lambda kv: -kv[1]["started"]):
        code = j["proc"].poll()
        try:
            tail = j["log"].read_text(encoding="utf-8", errors="replace").splitlines()[-6:]
        except Exception:
            tail = []
        res.append({"job": jid, "id": j["id"], "song": j.get("song"), "status": "running" if code is None else ("xong" if code == 0 else f"lỗi ({code})"),
                    "tail": "\n".join(tail)})
    return res


PAGE = r"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>YuE2 Studio</title><link rel="icon" href="/favicon.ico"><style>
:root{--bg:#f6f6f4;--fg:#1d1d1b;--card:#fff;--mute:#6b6b66;--acc:#3b5bdb;--bd:#ddd}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#eee;--card:#222220;--mute:#9a9a94;--acc:#748ffc;--bd:#3a3a37}}
body{background:var(--bg);color:var(--fg);font:14px system-ui,sans-serif;margin:0}
main{max-width:1200px;margin:auto;padding:16px;display:grid;grid-template-columns:420px 1fr;gap:20px}
@media(max-width:900px){main{grid-template-columns:1fr}}
section{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:16px}
h1{font-size:20px;margin:16px auto 0;max-width:1200px;padding:0 16px}h2{font-size:16px;margin:0 0 12px}
label{display:block;font-size:12px;color:var(--mute);margin-top:8px}
input,select,textarea{width:100%;box-sizing:border-box;padding:6px;border:1px solid var(--bd);border-radius:6px;background:var(--bg);color:var(--fg);font:inherit}
textarea{min-height:90px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:0 10px}
.chk{display:flex;gap:6px;align-items:center;margin-top:8px}.chk input{width:auto}
button{margin-top:12px;padding:8px 16px;background:var(--acc);color:#fff;border:0;border-radius:6px;cursor:pointer}
.song{border-top:1px solid var(--bd);padding:10px 0}.song:first-child{border:0}
.midi{margin-top:6px;font-size:13px}.midi a{color:var(--acc)}button.sm{margin:0;padding:4px 10px;font-size:12px}
.song b{font-size:15px}.meta{color:var(--mute);font-size:12px}audio{width:100%;margin-top:6px}
.req{color:#e03131;font-weight:600}label.big{font-size:14px;color:var(--fg);font-weight:600;margin-top:14px}
.help{font-size:12px;color:var(--mute);margin:3px 0 4px;line-height:1.4}code{font-size:12px}
summary{margin-top:14px;cursor:pointer;font-weight:600}
.note{margin:6px 0;padding:8px 10px;border-radius:6px;border-left:4px solid;font-size:13px;line-height:1.4;color:var(--fg)}
.note.ok{background:#ebfbee;border-color:#2f9e44}.note.warn{background:#fff3bf;border-color:#f08c00}.note.err{background:#ffe3e3;border-color:#e03131}
@media (prefers-color-scheme:dark){.note.ok{background:#1f3326}.note.warn{background:#3d3214}.note.err{background:#3d1f1f}}
details pre,pre{white-space:pre-wrap;font-size:12px;color:var(--mute);margin:4px 0}
</style></head><body><h1><img src="/favicon.ico" alt="" style="width:28px;height:28px;vertical-align:-6px;margin-right:8px">YuE2 Studio</h1><main>
<section><h2>Tạo bài hát</h2>
<p class="meta">Các trường có dấu <span class="req">*</span> là <b>bắt buộc</b>. Các tùy chọn khác có thể để mặc định.</p>
<form id="f">
<label class="big">Phong cách <span class="req">* bắt buộc</span></label>
<div class="help">Mô tả bằng tiếng Anh, ngăn cách bằng dấu phẩy: ngôn ngữ, thể loại, giọng hát, nhạc cụ, cảm xúc, tốc độ (BPM).<br>Ví dụ: <i>English, lofi hip hop, soft female vocal, warm Rhodes piano, chill, 75 BPM</i></div>
<textarea name="style" required placeholder="English, pop ballad, emotional male vocal, acoustic piano, 80 BPM"></textarea>
<label class="chk big"><input type="checkbox" name="instrumental" id="inst">🎹 Nhạc không lời (instrumental)</label>
<div class="help">Bỏ trống lời và tự thêm “Instrumental, no vocals” vào phong cách. Lưu ý: model vẫn có thể lẫn chút giọng hát — hãy nghe thử. Mô tả rõ nhạc cụ chính (vd. <i>solo piano</i>, <i>acoustic guitar</i>) để kết quả tốt hơn.</div>
<details id="coverBox"><summary>🎤 Bài hát tham khảo (cover) — không bắt buộc</summary>
<div class="help">Tải lên một bản thu. SheetSage2 chép <b>giai điệu</b> (và tùy chọn hợp âm) thành bản nhạc ABC, rồi YuE2 hát lại theo phong cách và lời bạn nhập. Giọng ca, âm sắc và cách phối của bài gốc <b>không</b> được giữ — hãy mô tả chúng trong “Phong cách”.</div>
<input type="file" id="refFile" accept="audio/*,.wav,.flac,.mp3,.m4a,.ogg,.aac"><input type="hidden" name="reference" id="refPath">
<div id="refStatus" class="help"></div>
<label>Chế độ cover</label><select name="cover_mode" id="coverMode">
<option value="melody">Chỉ giữ giai điệu (khuyên dùng — phần đệm tự phối theo phong cách mới)</option>
<option value="full">Giữ giai điệu + hợp âm gốc (sát bài gốc hơn)</option>
<option value="transcribe">Chỉ chép nhạc ABC (xem/sửa trước, chưa tạo bài)</option></select>
<div class="help">Lời nên khớp số âm tiết và thứ tự đoạn với giai điệu gốc. Bản nhạc chép được lưu ở <code>&lt;thư mục&gt;/&lt;tên bài&gt;_reference/score.abc</code>; có thể sửa rồi dùng lại qua tùy chọn <code>--abc-file</code>.</div>
<button type="button" class="sm" id="refClear" hidden>✕ Bỏ bài tham khảo</button></details>
<div id="lyricsBox"><label class="big">Lời bài hát <span class="req">* bắt buộc</span></label>
<div class="help">Chia đoạn bằng thẻ trong ngoặc vuông: <code>[Verse]</code>, <code>[Chorus]</code>, <code>[Bridge]</code>, <code>[Outro]</code>. Mỗi câu một dòng, các đoạn cách nhau một dòng trống. Lời càng dài thì bài càng dài và tạo càng lâu.</div>
<textarea name="lyrics" required style="min-height:180px" placeholder="[Verse]&#10;Câu hát thứ nhất&#10;Câu hát thứ hai&#10;&#10;[Chorus]&#10;Điệp khúc..."></textarea></div>
<div class="grid"><div><label>Tên bài (id)</label><input name="id" placeholder="tự động"><div class="help">Tên thư mục lưu bài. Chỉ dùng chữ không dấu, số, gạch dưới.</div></div>
<div><label>Thư mục lưu (trong outputs/)</label><select name="folder" id="folder" data-combo></select><input name="folder__custom" placeholder="tên thư mục mới" hidden><div class="help">Chọn thư mục có sẵn hoặc “+ Tạo mới…”.</div></div></div>
<details><summary>Tùy chọn nâng cao (không bắt buộc)</summary><div class="grid" id="opts"></div></details>
<button>Tạo bài hát</button><div class="help">Mỗi bài mất vài phút tùy GPU. Tiến trình hiện ở mục “Đang chạy” bên dưới.</div></form>
<h2 style="margin-top:20px">Đang chạy</h2><div id="jobs" class="meta">không có</div></section>
<section><h2>Bài hát đã tạo</h2><div id="songs"></div></section></main>
<script>
const OPTS=__OPTS__;const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const opt=(o,d)=>`<option ${o==d?'selected':''} value="${esc(o)}">${esc(o)||'(mặc định)'}</option>`;
document.getElementById('opts').innerHTML=OPTS.map(([f,k,d,h,t])=>{
 if(k=='combo')return `<div><label>${f}</label><select name="${f}" data-combo>${h.map(o=>opt(o,d)).join('')}<option value="__new__">✎ Nhập giá trị khác…</option></select><input name="${f}__custom" placeholder="nhập giá trị" hidden><div class="help">${esc(t)}</div></div>`;
 if(k=='bool')return `<div><label class="chk"><input type="checkbox" name="${f}">${f}</label><div class="help">${esc(h)}</div></div>`;
 if(k=='choice')return `<div><label>${f}</label><select name="${f}">${h.map(o=>`<option ${o==d?'selected':''} value="${o}">${o||'(mặc định)'}</option>`).join('')}</select><div class="help">${esc(t)}</div></div>`;
 return `<div><label>${f}</label><input name="${f}" value="${esc(d)}" ${k!='text'?'type=number step=any':''}><div class="help">${esc(h)}</div></div>`}).join('');
document.getElementById('f').onsubmit=async e=>{e.preventDefault();const fd=new FormData(e.target),o={};
 for(const[k,v]of fd)o[k]=v==='on'?true:v;
 if(refUploading)return alert('Đang tải lên / nghe lời bài tham khảo, vui lòng chờ xong.');
 const r=await(await fetch('/api/generate',{method:'POST',body:JSON.stringify(o)})).json();if(r.error)alert(r.error);loadJobs()};
let refUploading=false;const refPath=document.getElementById('refPath'),refStatus=document.getElementById('refStatus'),refClear=document.getElementById('refClear');
function syncLyrics(){const t=document.querySelector('[name=lyrics]'),inst=document.getElementById('inst').checked,
 onlyAbc=!!refPath.value&&document.getElementById('coverMode').value=='transcribe';
 document.getElementById('lyricsBox').hidden=inst||onlyAbc;t.required=!(inst||onlyAbc)}
refClear.onclick=()=>{refPath.value='';document.getElementById('refFile').value='';setRef('');refClear.hidden=true;syncLyrics()};
document.getElementById('refFile').onchange=async e=>{const f=e.target.files[0];refPath.value='';refClear.hidden=true;if(!f)return syncLyrics();
 refUploading=true;setRef('⏳ Đang tải lên '+f.name+'…');
 try{const r=await(await fetch('/api/upload',{method:'POST',headers:{'X-Filename':encodeURIComponent(f.name)},body:f})).json();
  if(r.error){setRef('❌ '+r.error,'err');e.target.value=''}else{refPath.value=r.path;refClear.hidden=false;
   const t=document.querySelector('[name=lyrics]'),up='Đã tải lên: '+r.name+'. ';
   if(!r.lyrics&&t.value.trim())setRef('✅ '+up+'Giữ lời bạn đang nhập.','ok');
   else if(!r.lyrics){setRef('⏳ '+up+'Đang nghe lời từ giọng hát (tách giọng + Whisper, khoảng 30 giây – 2 phút)…','warn');
    let s;try{s=await(await fetch('/api/lyrics',{method:'POST',body:JSON.stringify({path:r.path})})).json()}catch(err){s={error:'Mất kết nối: '+err}}
    if(refPath.value!=r.path)return;  // another file was picked meanwhile
    if(s.error)setRef('⚠️ '+up+s.error,'warn');
    else if(!s.lyrics)setRef('⚠️ '+up+'Không nghe thấy lời hát (có thể là nhạc không lời) — hãy tự nhập lời vào ô “Lời bài hát”.','warn');
    else if(t.value.trim()&&!confirm('Đã nghe được lời bài hát. Thay lời bạn vừa nhập bằng lời nghe được?'))setRef('✅ '+up+'Đã nghe được lời nhưng bạn giữ lời đang nhập.','ok');
    else{t.value=s.lyrics;setRef('🎧 '+up+'Đã nghe và điền '+s.lines+' câu hát vào ô “Lời bài hát”.'
     +(s.missed?' Có '+s.missed+' đoạn hát không nghe được — lời của đoạn đó bị thiếu, hãy tự bổ sung.':'')
     +' Máy nghe có thể sai chữ, sai dấu và đoán sai nhãn đoạn — hãy đọc soát lại trước khi tạo bài.','warn')}}
   else if(!t.value.trim()||confirm('File có sẵn lời bài hát. Thay lời đang nhập bằng lời trong file?')){t.value=r.lyrics;setRef('✅ '+up+'Đã chép lời từ file vào ô “Lời bài hát”. Hãy kiểm tra lại nhãn đoạn ([Verse], [Chorus]…).','ok')}
   else setRef('✅ '+up+'File có lời nhưng bạn giữ lời đang nhập.','ok')}}
 catch(err){setRef('❌ Tải lên thất bại: '+err,'err')}finally{refUploading=false;syncLyrics()}};
function setRef(msg,kind){refStatus.textContent=msg;refStatus.className=kind?'note '+kind:'help'}
document.addEventListener('change',e=>{if(e.target.id=='inst'||e.target.id=='coverMode')syncLyrics()
 if(e.target.matches('select[data-combo]')){const i=e.target.nextElementSibling;i.hidden=e.target.value!='__new__';i.required=!i.hidden;if(!i.hidden)i.focus()}});
async function loadFolders(){const fs=await(await fetch('/api/folders')).json(),sel=document.getElementById('folder'),cur=sel.value&&sel.value!='__new__'?sel.value:'webui';
 if(!fs.includes('webui'))fs.unshift('webui');sel.innerHTML=fs.map(o=>opt(o,cur)).join('')+'<option value="__new__">+ Tạo mới…</option>';sel.nextElementSibling.hidden=true}
async function loadSongs(){loadFolders();const s=await(await fetch('/api/songs')).json();
 document.getElementById('songs').innerHTML=s.length?s.map(x=>`<div class="song"><b>${esc(x.name)}</b> <button type="button" class="sm" onclick="openDir('${esc(x.name)}')">📂 Mở thư mục bài hát</button>
 <div class="meta">${x.time}${x.seed!=null?' · seed '+x.seed:''}${x.tempo.score?' · ♩ '+x.tempo.score+' BPM':''}${x.tempo.measured?' (đo: '+x.tempo.measured+')':''}</div><div class="meta">${esc(x.style)}</div>
 <audio controls preload="none" src="/files/${encodeURI(x.audio)}"></audio>
 <span class="sm" draggable="true" style="cursor:grab" title="Kéo file audio ra Explorer/desktop" ondragstart="dragAudio(event,'${esc(x.audio)}')">⠿ Kéo audio</span>
 <div class="midi">${x.midis.length?`🎼 Đã có ${x.midis.length} file MIDI <button type="button" class="sm" onclick="openDir('${esc(x.name)}/sheetsage2')">📂 Mở thư mục MIDI</button>`
  :midiRunning.has(x.name)?'<span class="meta">⏳ Đang tạo MIDI… (~1 phút)</span>'
  :`<button type="button" class="sm" onclick="makeMidi('${esc(x.name)}')">🎼 Tạo MIDI</button> <span class="help">Chuyển audio thành MIDI (giai điệu, hợp âm, bass, nhạc cụ khác) và tách stem audio (trống, bass, khác, vocal).</span>`}</div>
 <details><summary class="meta">lời bài hát</summary><pre>${esc(x.lyrics)}</pre></details></div>`).join(''):'<p class="meta">Chưa có bài hát nào.</p>'}
let midiRunning=new Set();
function dragAudio(e,a){const u=location.origin+'/files/'+encodeURI(a),n=a.split('/').pop();
 e.dataTransfer.setData('DownloadURL',`audio/flac:${n}:${u}`);e.dataTransfer.setData('text/uri-list',u)}
async function openDir(p){const r=await(await fetch('/api/open',{method:'POST',body:JSON.stringify({path:p})})).json();if(r.error)alert(r.error)}
async function makeMidi(n){const r=await(await fetch('/api/midi',{method:'POST',body:JSON.stringify({song:n})})).json();
 if(r.error)return alert(r.error);await loadJobs();loadSongs()}
let running=0;async function loadJobs(){const j=await(await fetch('/api/jobs')).json();
 const mr=new Set(j.filter(x=>x.song&&x.status=='running').map(x=>x.song));const changed=[...midiRunning].some(n=>!mr.has(n));midiRunning=mr;if(changed)loadSongs();
 document.getElementById('jobs').innerHTML=j.length?j.map(x=>`<div><b>${esc(x.id)}</b> — ${x.status=='running'?'đang chạy…':x.status}<pre>${esc(x.tail)}</pre></div>`).join(''):'không có';
 const r=j.filter(x=>x.status=='running').length;if(r<running)loadSongs();running=r}
loadSongs();loadJobs();setInterval(loadJobs,3000);
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def send(self, body, ctype="application/json", code=200):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            return self.send(PAGE.replace("__OPTS__", json.dumps(OPTIONS)), "text/html; charset=utf-8")
        if path == "/favicon.ico":
            return self.send((ROOT / "tools" / "assets" / "yue2-studio.ico").read_bytes(), "image/x-icon")
        if path == "/api/folders":
            return self.send(folders())
        if path == "/api/songs":
            return self.send(songs())
        if path == "/api/jobs":
            return self.send(jobs())
        if path.startswith("/files/"):
            f = (OUTPUTS / unquote(path[7:])).resolve()
            if OUTPUTS.resolve() in f.parents and f.is_file():
                data, ctype = f.read_bytes(), mimetypes.guess_type(f.name)[0] or "audio/flac"
                m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
                if not m or not (m[1] or m[2]):
                    return self.send(data, ctype)
                n = len(data)
                start, end = (int(m[1]), int(m[2] or n - 1)) if m[1] else (max(0, n - int(m[2])), n - 1)
                end = min(end, n - 1)
                if start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{n}")
                    return self.end_headers()
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{n}")
                self.send_header("Content-Length", str(end - start + 1))
                self.end_headers()
                return self.wfile.write(data[start:end + 1])
        self.send({"error": "not found"}, code=404)

    def do_POST(self):
        if self.path == "/api/open":
            form = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            d = (OUTPUTS / form.get("path", "")).resolve()
            if (d == OUTPUTS.resolve() or OUTPUTS.resolve() in d.parents) and d.is_dir():
                os.startfile(d) if sys.platform == "win32" else subprocess.Popen(["xdg-open", str(d)])
                return self.send({"ok": True})
            return self.send({"error": "Không tìm thấy thư mục."})
        if self.path == "/api/midi":
            form = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            return self.send(start_midi(form.get("song", "")))
        if self.path == "/api/generate":
            form = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            return self.send(start_job(form))
        if self.path == "/api/lyrics":
            form = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            return self.send(sung_lyrics(form.get("path", "")))
        if self.path == "/api/upload":
            n = int(self.headers["Content-Length"])
            if n > 200 * 2**20:
                return self.send({"error": "File quá lớn (tối đa 200 MB)."})
            return self.send(save_reference(unquote(self.headers.get("X-Filename", "")), self.rfile.read(n)))
        self.send({"error": "not found"}, code=404)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7860
    print(f"YuE2 Studio on http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
