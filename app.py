import os
import re

from shorts_generator.config import LOCAL_OUTPUT_DIR
from shorts_generator.local.transcriber_youtube import transcribe_from_youtube
from shorts_generator.zoi_engine import analyze_transcript
from shorts_generator.ai_analyzer import analyze_with_ai
from shorts_generator.local.clipper import crop_clip_local
from shorts_generator.local.downloader import download_youtube_local
import uuid
import threading

from flask import Flask, render_template_string, request, redirect, url_for, send_from_directory, jsonify

app = Flask(__name__)

OUTPUT_DIR = LOCAL_OUTPUT_DIR
os.makedirs(OUTPUT_DIR, exist_ok=True)

RENDER_JOBS = {}

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
.error{border-color:#ff6b6b;color:#ffb3b3;font-size:14px;line-height:1.6;overflow-wrap:anywhere}
footer{text-align:center;color:#777;margin:30px 0;font-size:12px}
</style>
</head>
<body>
<div class="container">
<div class="logo">ZOI AI CLIPPER</div>
<div class="subtitle">Your AI-powered video clipping studio</div>

{% if not results %}
{% if error %}
<div class="card error">⚠️ {{ error }}</div>
{% endif %}
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
<p class="note">Hasil analisis transkrip menggunakan ZOI Engine.</p>
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
<input type="hidden" name="start_time" value="{{ clip.start_time }}">
<input type="hidden" name="end_time" value="{{ clip.end_time }}">
<input type="hidden" name="source_path" value="{{ clip.source_path }}">
<button class="choose">PILIH & RENDER KLIP INI</button>
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
    video_url = request.form.get("youtube_url", "").strip()
    mode = request.form.get("mode", "zoi")

    try:
        source_path = download_youtube_local(video_url)
        transcript = transcribe_from_youtube(video_url)

        if mode == "zoi":
            analyses = [
                ("ZOI ENGINE V4.1", analyze_transcript(transcript, 5))
            ]
        elif mode == "claude":
            analyses = [
                ("CLAUDE AI", analyze_with_ai(transcript, "claude", 5))
            ]
        elif mode == "chatgpt":
            analyses = [
                ("CHATGPT AI", analyze_with_ai(transcript, "chatgpt", 5))
            ]
        elif mode == "hybrid":
            analyses = [
                ("CLAUDE AI", analyze_with_ai(transcript, "claude", 5)),
                ("CHATGPT AI", analyze_with_ai(transcript, "chatgpt", 5))
            ]
        else:
            raise ValueError("Mode analisis tidak dikenal.")

        def format_time(seconds):
            seconds = int(float(seconds))
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            secs = seconds % 60
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"

        results = []

        for group_name, analysis in analyses:
            clips = []

            for item in analysis.get("highlights", []):
                clips.append({
                    "title": item.get("title", "Kandidat AI"),
                    "preview_url": None,
                    "time": (
                        format_time(item["start_time"])
                        + " - "
                        + format_time(item["end_time"])
                    ),
                    "hook": item.get("hook_sentence", ""),
                    "reason": item.get("virality_reason", ""),
                    "score": item.get("score", 0),
                    "start_time": item["start_time"],
                    "end_time": item["end_time"],
                    "source_path": source_path
                })

            if clips:
                results.append({
                    "name": group_name,
                    "clips": clips
                })

        if not results:
            return render_template_string(
                HTML,
                results=None,
                error="Tidak ditemukan kandidat klip yang valid."
            )

        return render_template_string(
            HTML,
            results=results,
            mode=mode,
            video_url=transcript.get("title", video_url)
        )

    except Exception as e:
        return render_template_string(
            HTML,
            results=None,
            error=f"Gagal menganalisis video: {str(e)}"
        )


@app.route("/select", methods=["POST"])
def select():
    title = request.form.get("title", "")
    analyst = request.form.get("analyst", "")
    source_path = os.path.realpath(request.form.get("source_path", ""))

    try:
        start = float(request.form.get("start_time", "0"))
        end = float(request.form.get("end_time", "0"))
    except ValueError:
        return "Timestamp tidak valid.", 400

    output_root = os.path.realpath(OUTPUT_DIR)
    if not source_path.startswith(output_root + os.sep) or not os.path.isfile(source_path):
        return "File video sumber tidak ditemukan atau tidak valid.", 400

    if start < 0 or end <= start:
        return "Timestamp klip tidak valid.", 400

    safe_title = re.sub(r"[^a-zA-Z0-9_-]+", "_", title).strip("_")[:45]
    filename = f"zoi_{safe_title}_{uuid.uuid4().hex[:6]}.mp4"
    output_path = os.path.join(OUTPUT_DIR, filename)
    job_id = uuid.uuid4().hex

    RENDER_JOBS[job_id] = {
        "progress": 0,
        "stage": "Menyiapkan rendering",
        "status": "running",
        "filename": filename,
        "error": ""
    }

    def render_worker():
        try:
            def update_progress(percent, stage):
                RENDER_JOBS[job_id].update({
                    "progress": max(0, min(100, int(percent))),
                    "stage": stage
                })

            crop_clip_local(
                source_path, start, end, "9:16", output_path,
                progress_callback=update_progress
            )

            RENDER_JOBS[job_id].update({
                "progress": 100,
                "stage": "Rendering selesai",
                "status": "done"
            })

        except Exception as e:
            if os.path.exists(output_path):
                os.remove(output_path)
            RENDER_JOBS[job_id].update({
                "status": "error",
                "stage": "Rendering gagal",
                "error": str(e)
            })

    threading.Thread(target=render_worker, daemon=True).start()

    return render_template_string("""
    <body style="background:#101116;color:white;font-family:Arial;padding:22px">
      <h2>🎬 Rendering ZOI AI CLIPPER</h2>
      <p>Kandidat: {{ title }}</p>
      <p>Analisis: {{ analyst }}</p>
      <p>Durasi: {{ duration }} detik</p>
      <div style="background:#30313d;border-radius:20px;height:18px;overflow:hidden">
        <div id="bar" style="height:100%;width:0%;background:#9385ff;transition:width .3s"></div>
      </div>
      <h2 id="percent">0%</h2>
      <p id="stage">Menyiapkan rendering...</p>
      <div id="result"></div>
      <script>
        async function checkProgress() {
          try {
            const response = await fetch("/progress/{{ job_id }}");
            const data = await response.json();
            document.getElementById("bar").style.width = data.progress + "%";
            document.getElementById("percent").textContent = data.progress + "%";
            document.getElementById("stage").textContent = data.stage;

            if (data.status === "done") {
              document.getElementById("result").innerHTML =
                '<h3>✅ Rendering berhasil!</h3>' +
                '<video controls playsinline style="width:100%;max-width:420px;border-radius:12px">' +
                '<source src="/output/' + encodeURIComponent(data.filename) + '" type="video/mp4"></video>' +
                '<p><a style="color:#a99dff" download href="/output/' +
                encodeURIComponent(data.filename) + '">⬇ DOWNLOAD VIDEO MP4</a></p>' +
                '<a style="color:#aaa" href="/">Kembali ke dashboard</a>';
              return;
            }

            if (data.status === "error") {
              document.getElementById("result").innerHTML =
                '<h3>❌ Rendering gagal</h3><p></p>';
              document.querySelector("#result p").textContent = data.error;
              return;
            }

            setTimeout(checkProgress, 800);
          } catch (error) {
            document.getElementById("stage").textContent =
              "Menghubungkan kembali...";
            setTimeout(checkProgress, 1500);
          }
        }
        checkProgress();
      </script>
    </body>
    """, title=title, analyst=analyst, duration=round(end-start, 1), job_id=job_id)


@app.route("/progress/<job_id>")
def render_progress(job_id):
    job = RENDER_JOBS.get(job_id)
    if not job:
        return jsonify({"status": "not_found"}), 404
    return jsonify(job)

@app.route("/output/<path:filename>")
def serve_video(filename):
    return send_from_directory(OUTPUT_DIR, filename)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)

