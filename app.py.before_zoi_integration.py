import os
from flask import Flask, render_template_string, request, redirect, url_for, send_from_directory

app = Flask(__name__)

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)
from flask import Flask, render_template_string, request, redirect, url_for, send_from_directory

app = Flask(__name__)


OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

HTML = """
<!DOCTYPE html>
<html lang="id">
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ZOI AI CLIPPER</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#0d0e14;color:#f5f5f5;font-family:Arial;padding:18px}
.container{max-width:950px;margin:auto}
.logo{font-size:26px;font-weight:bold;color:#9385ff}
.subtitle{color:#999;font-size:13px;margin-top:6px}
.card{background:#191a24;padding:18px;border-radius:15px;margin-top:18px;border:1px solid #30313d}
h2{font-size:18px}
input[type=url]{width:100%;padding:14px;border-radius:9px;border:1px solid #414251;background:#101116;color:white;margin:12px 0}
.modes{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.mode{background:#242532;border:1px solid #414251;padding:15px 8px;border-radius:11px;text-align:center;cursor:pointer}
.mode.active{border:2px solid #9385ff;background:#302b50}
.mode strong{display:block;margin-bottom:6px}
.mode small{color:#aaa;font-size:11px}
button{width:100%;padding:15px;border:0;border-radius:9px;background:#7565ef;color:white;font-weight:bold;margin-top:15px}
.note{font-size:12px;color:#999;line-height:1.6}
.result-title{color:#a99dff}
.clip{background:#242530;padding:15px;border-radius:12px;margin-top:12px;border:1px solid #414251}
.clip h3{font-size:15px;margin:0 0 10px}
.score{color:#83d9a0;font-weight:bold}
.tag{font-size:11px;color:#b9adff}
.choose{background:#7565ef;margin-top:12px}
.selected{border:2px solid #83d9a0}
footer{text-align:center;color:#777;margin:30px 0;font-size:12px}
</style>
</head>
<body>
<div class="container">
<div class="logo">ZOI AI CLIPPER</div>
<div class="subtitle">Your AI-powered video clipping studio</div>

{% if not results %}
<div class="card">
<h2>🎬 Buat Video Shorts</h2>
<p class="note">Masukkan link YouTube dan pilih mode analisis.</p>
<form method="POST" action="/analyze">
<input type="url" name="youtube_url" placeholder="Tempel link YouTube di sini" required>
<h2>Pilih Mode AI</h2>
<div class="modes">
<label class="mode active"><input type="radio" name="mode" value="zoi" checked hidden><strong>⚡ ZOI</strong><small>Analisis bawaan ZOI</small></label>
<label class="mode"><input type="radio" name="mode" value="claude" hidden><strong>🟣 CLAUDE</strong><small>Analisis Claude</small></label>
<label class="mode"><input type="radio" name="mode" value="chatgpt" hidden><strong>🟢 CHATGPT</strong><small>Analisis ChatGPT</small></label>
<label class="mode"><input type="radio" name="mode" value="hybrid" hidden><strong>🔥 HYBRID</strong><small>Claude + ChatGPT</small></label>
</div>
<button type="submit">ANALYZE VIDEO</button>
</form>
</div>
{% else %}
<div class="card">
<h2>📊 Hasil Analisis</h2>
<p class="note">Mode: <b>{{ mode.upper() }}</b></p>
<p class="note">Video: {{ video_url }}</p>
<p class="note">Ini adalah data simulasi untuk menguji tampilan. Belum merupakan hasil AI sungguhan.</p>
</div>

{% for group in results %}
<div class="card">
<h2 class="result-title">{{ group.name }}</h2>
{% for clip in group.clips %}
<div class="clip">
<h3>{{ loop.index }}. {{ clip.title }}</h3>
<p class="note">⏱ {{ clip.time }}</p>

{% if clip.preview_url %}
<video controls playsinline preload="metadata"
style="width:100%;border-radius:10px;margin:10px 0;">
<source src="{{ clip.preview_url }}" type="video/mp4">
Browser tidak mendukung pemutar video.
</video>
{% else %}
<p class="note">🎬 Preview belum tersedia. Video belum dirender.</p>
{% endif %}

<p>🎯 Hook: {{ clip.hook }}</p>
<p class="note">Alasan: {{ clip.reason }}</p>
<p class="score">Viral Score: {{ clip.score }}/100</p>
<form method="POST" action="/select">
<input type="hidden" name="title" value="{{ clip.title }}">
<input type="hidden" name="time" value="{{ clip.time }}">
<input type="hidden" name="analyst" value="{{ group.name }}">
<button class="choose">PILIH KLIP INI</button>
</form>
</div>
{% endfor %}
</div>
{% endfor %}
<a href="/" style="color:#aaa">← Kembali</a>
{% endif %}
<footer>ZOI AI CLIPPER © 2026</footer>
</div>

<script>
document.querySelectorAll('.mode').forEach(item=>{
item.addEventListener('click',()=>{
document.querySelectorAll('.mode').forEach(el=>el.classList.remove('active'));
item.classList.add('active');
});
});
</script>
</body>
</html>
"""

def sample_clips(name):
    clips = []
    for i in range(1, 6):
        clips.append({
            "title": f"Kandidat Klip {i}",
            "preview_url": "/output/preview-test.mp4",
            "time": f"00:{(i-1)*2:02d}:00 - 00:{(i-1)*2+1:02d}:00",
            "hook": f"Contoh hook menarik nomor {i}",
            "reason": "Contoh alasan pemilihan adegan untuk simulasi.",
            "score": 95 - i * 3
        })
    return {"name": name, "clips": clips}

@app.route("/")
def home():
    return render_template_string(HTML, results=None)

@app.route("/analyze", methods=["POST"])
def analyze():
    video_url = request.form.get("youtube_url", "")
    mode = request.form.get("mode", "zoi")

    if mode == "zoi":
        results = [sample_clips("ZOI ENGINE")]
    elif mode == "claude":
        results = [sample_clips("CLAUDE")]
    elif mode == "chatgpt":
        results = [sample_clips("CHATGPT")]
    else:
        results = [sample_clips("CLAUDE"), sample_clips("CHATGPT")]

    return render_template_string(
        HTML, results=results, mode=mode, video_url=video_url
    )

@app.route("/select", methods=["POST"])
def select():
    title = request.form.get("title", "")
    time = request.form.get("time", "")
    analyst = request.form.get("analyst", "")
    return f"""
    <body style="background:#101116;color:white;font-family:Arial;padding:25px">
    <h2>✅ Klip berhasil dipilih</h2>
    <p>Analisis: {analyst}</p>
    <p>Kandidat: {title}</p>
    <p>Timestamp: {time}</p>
    <p>Rendering video akan diintegrasikan pada tahap berikutnya.</p>
    <a href="/" style="color:#a99dff">Kembali ke dashboard</a>
    </body>
    """

@app.route("/output/<path:filename>")
def serve_video(filename):
    return send_from_directory(OUTPUT_DIR, filename)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)

