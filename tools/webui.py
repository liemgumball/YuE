"""Minimal YuE2 web UI: list generated songs and launch `yue2 generate`.

Stdlib only. Run:  .venv\\Scripts\\python tools\\webui.py  then open http://127.0.0.1:7860
"""
import json
import os
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
    return sorted(p.name for p in OUTPUTS.iterdir() if p.is_dir()) if OUTPUTS.exists() else []


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
    cmd = [str(exe) if exe.exists() else "yue2", "generate", "--output", str(outdir), "--id", sid,
           "--style", form.get("style", ""), "--lyrics", form.get("lyrics", "")]
    for flag, kind, *_ in OPTIONS:
        v = form.get(flag)
        if kind == "bool":
            if v:
                cmd.append(flag)
        elif v not in (None, ""):
            cmd += [flag, str(v)]
    log = outdir / f"{sid}.webui.log"
    fh = open(log, "w", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT)
    JOBS[jid] = {"proc": proc, "log": log, "cmd": cmd, "started": time.time(), "id": sid}
    threading.Thread(target=lambda: (proc.wait(), fh.close()), daemon=True).start()
    return jid


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
 await fetch('/api/generate',{method:'POST',body:JSON.stringify(o)});loadJobs()};
document.addEventListener('change',e=>{if(e.target.id=='inst'){const on=e.target.checked,t=document.querySelector('[name=lyrics]');document.getElementById('lyricsBox').hidden=on;t.required=!on}
 if(e.target.matches('select[data-combo]')){const i=e.target.nextElementSibling;i.hidden=e.target.value!='__new__';i.required=!i.hidden;if(!i.hidden)i.focus()}});
async function loadFolders(){const fs=await(await fetch('/api/folders')).json(),sel=document.getElementById('folder'),cur=sel.value&&sel.value!='__new__'?sel.value:'webui';
 if(!fs.includes('webui'))fs.unshift('webui');sel.innerHTML=fs.map(o=>opt(o,cur)).join('')+'<option value="__new__">+ Tạo mới…</option>';sel.nextElementSibling.hidden=true}
async function loadSongs(){loadFolders();const s=await(await fetch('/api/songs')).json();
 document.getElementById('songs').innerHTML=s.length?s.map(x=>`<div class="song"><b>${esc(x.name)}</b>
 <div class="meta">${x.time}${x.seed!=null?' · seed '+x.seed:''}${x.tempo.score?' · ♩ '+x.tempo.score+' BPM':''}${x.tempo.measured?' (đo: '+x.tempo.measured+')':''}</div><div class="meta">${esc(x.style)}</div>
 <audio controls preload="none" src="/files/${encodeURI(x.audio)}"></audio>
 <div class="midi">${x.midis.length?`🎼 Đã có ${x.midis.length} file MIDI <button type="button" class="sm" onclick="openDir('${esc(x.name)}/sheetsage2')">📂 Mở thư mục MIDI</button>`
  :midiRunning.has(x.name)?'<span class="meta">⏳ Đang tạo MIDI… (~1 phút)</span>'
  :`<button type="button" class="sm" onclick="makeMidi('${esc(x.name)}')">🎼 Tạo MIDI</button> <span class="help">Chuyển audio thành file MIDI (giai điệu, hợp âm, toàn bộ) bằng SheetSage2.</span>`}</div>
 <details><summary class="meta">lời bài hát</summary><pre>${esc(x.lyrics)}</pre></details></div>`).join(''):'<p class="meta">Chưa có bài hát nào.</p>'}
let midiRunning=new Set();
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
                return self.send(f.read_bytes(), mimetypes.guess_type(f.name)[0] or "audio/flac")
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
            return self.send({"job": start_job(form)})
        self.send({"error": "not found"}, code=404)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7860
    print(f"YuE2 Studio on http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
