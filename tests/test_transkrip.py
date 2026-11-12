"""Tes pilihan tambahan: transkrip lewat Google Colab lalu diimpor ke ZOI.

Unduhan YouTube dan data video diganti tiruan, jadi tes tidak memakai internet.
"""
import io
import json
import os
import shutil
import tempfile
import time
import unittest

try:
    import app as zoi_app
    from shorts_generator import exchange
except ImportError:
    zoi_app = None

from shorts_generator.local.transcriber_youtube import transcript_from_vtt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOTEBOOK = os.path.join(ROOT, "colab", "zoi_transkrip_colab.ipynb")
VIDEO = "COLABVID001"
URL = f"https://www.youtube.com/watch?v={VIDEO}"


def _srt(cues):
    def clock(seconds):
        ms = int(round(seconds * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    return "".join(
        f"{i}\n{clock(a)} --> {clock(b)}\n{text}\n\n"
        for i, (a, b, text) in enumerate(cues, 1)
    )


def _long_srt(minutes=12):
    """Transkrip palsu yang cukup panjang untuk menghasilkan kandidat."""
    cues, t = [], 0.0
    lines = [
        "Kenapa harganya bisa semahal ini?", "Ternyata rahasianya ada di bahan.",
        "Serius kamu beli seharga 500 ribu?", "Hahaha akhirnya berhasil juga.",
        "Masalahnya tidak bisa dipakai lagi.", "Beneran ini hasilnya bagus banget.",
    ]
    while t < minutes * 60:
        cues.append((t, t + 4.5, lines[len(cues) % len(lines)] + " ayo kita lihat sama sama"))
        t += 5.0
    return _srt(cues)


class SrtParsingTest(unittest.TestCase):
    def test_srt_with_comma_milliseconds_and_numbers(self):
        parsed = transcript_from_vtt(_srt([
            (0.5, 3.0, "Selamat siang teman-teman"),
            (3.2, 6.0, "harganya Rp800.000"),
            (3700.0, 3703.5, "lewat satu jam"),
        ]))
        text = " ".join(s["text"] for s in parsed["segments"])
        self.assertIn("Selamat siang teman-teman", text)
        self.assertIn("Rp800.000", text)
        self.assertNotIn(" 2 ", f" {text} ")
        self.assertEqual(parsed["segments"][0]["start"], 0.5)
        self.assertEqual(parsed["segments"][-1]["start"], 3700.0)

    def test_byte_order_mark_and_windows_newlines(self):
        content = "﻿" + _srt([(1.0, 2.0, "halo dunia")]).replace("\n", "\r\n")
        parsed = transcript_from_vtt(content.lstrip("﻿"))
        self.assertEqual(parsed["segments"][0]["text"], "halo dunia")


class NotebookTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(NOTEBOOK, encoding="utf-8") as file:
            cls.notebook = json.load(file)
        cls.code = ["".join(c["source"]) for c in cls.notebook["cells"] if c["cell_type"] == "code"]

    def test_notebook_is_up_to_date_with_its_builder(self):
        import subprocess, sys
        before = open(NOTEBOOK, encoding="utf-8").read()
        subprocess.run([sys.executable, os.path.join(ROOT, "colab", "build_notebook.py")],
                       check=True, capture_output=True)
        self.assertEqual(open(NOTEBOOK, encoding="utf-8").read(), before)

    def test_steps(self):
        self.assertEqual(self.notebook["metadata"]["accelerator"], "GPU")
        self.assertEqual(len(self.code), 4)
        self.assertIn("pip install -q faster-whisper", self.code[0])
        self.assertIn("files.upload()", self.code[1])
        self.assertIn("files.download(SRT)", self.code[3])
        for source in self.code:
            self.assertNotIn("api_key", source.lower())

    def test_core_cell_compiles_and_its_helpers_work(self):
        core = self.code[2]
        compile(core, "notebook", "exec")
        start = core.index("def jam(")
        end = core.index("def transkrip(")
        helpers = {}
        exec(core[start:end], helpers)
        self.assertEqual(helpers["jam"](3725.5), "01:02:05,500")

        words = [{"start": i * 0.5, "end": i * 0.5 + 0.4, "text": f"kata{i}"} for i in range(30)]
        words[5]["text"] += "."
        lines = helpers["kelompokkan"](words)
        self.assertTrue(all(b - a <= 6.5 for a, b, _ in lines))
        self.assertTrue(lines[0][2].endswith("kata5."))

        # Hasil notebook terbaca oleh ZOI.
        rendered = "".join(
            f"{i}\n{helpers['jam'](a)} --> {helpers['jam'](b)}\n{t}\n\n"
            for i, (a, b, t) in enumerate(lines, 1)
        )
        parsed = transcript_from_vtt(rendered)
        self.assertEqual(len(parsed["words"]), 30)


@unittest.skipIf(zoi_app is None, "app.py tidak bisa diimpor")
class TranskripPageTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.saved = (
            zoi_app.OUTPUT_DIR, exchange.DATA_DIR, zoi_app.download_audio_local,
            zoi_app.fetch_video_metadata, zoi_app.transcribe_from_youtube,
        )
        zoi_app.OUTPUT_DIR = self.folder
        exchange.DATA_DIR = os.path.join(self.folder, "data")
        self.metadata_error = None
        self.audio_error = None
        test = self

        def fake_audio(url, out_dir=None, progress_hook=None):
            if test.audio_error:
                raise RuntimeError(test.audio_error)
            if progress_hook:
                progress_hook({"status": "downloading", "downloaded_bytes": 3,
                               "total_bytes": 10, "info_dict": {"vcodec": "none"}})
            path = os.path.join(out_dir, f"audio_{VIDEO}.webm")
            with open(path, "wb") as file:
                file.write(b"x" * 2048)
            return path

        def fake_metadata(url):
            if test.metadata_error:
                raise RuntimeError(test.metadata_error)
            return {"video_id": VIDEO, "title": "Video Colab", "url": url, "channel": "Kanal",
                    "width": 1280, "height": 720, "duration": 900.0, "language": "id"}

        def must_not_fetch_subtitles(url):
            raise AssertionError("subtitle YouTube tidak boleh diambil saat impor")

        zoi_app.download_audio_local = fake_audio
        zoi_app.fetch_video_metadata = fake_metadata
        zoi_app.transcribe_from_youtube = must_not_fetch_subtitles
        self.client = zoi_app.app.test_client()

    def tearDown(self):
        (zoi_app.OUTPUT_DIR, exchange.DATA_DIR, zoi_app.download_audio_local,
         zoi_app.fetch_video_metadata, zoi_app.transcribe_from_youtube) = self.saved
        shutil.rmtree(self.folder, ignore_errors=True)

    def wait(self, url):
        deadline = time.time() + 60
        while time.time() < deadline:
            data = self.client.get(url).get_json()
            if data["status"] in ("done", "error"):
                return data
            time.sleep(0.05)
        self.fail(data)

    def post_import(self, content, mode="zoi", url=URL, name="hasil.srt"):
        form = {"youtube_url": url, "mode": mode}
        if content is not None:
            form["transcript_file"] = (io.BytesIO(content.encode("utf-8")), name)
        return self.client.post("/transkrip/impor", data=form, content_type="multipart/form-data")

    # ---- halaman

    def test_home_links_to_the_new_page_and_keeps_the_old_form(self):
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn("/transkrip", html)
        self.assertIn('action="/analyze"', html)

    def test_page_shows_the_three_steps(self):
        html = self.client.get("/transkrip").get_data(as_text=True)
        for text in ("1. Unduh audionya", "2. Buat transkrip di Colab", "3. Impor transkripnya ke ZOI",
                     "colab.research.google.com", "T4 GPU"):
            self.assertIn(text, html)

    def test_notebook_download(self):
        response = self.client.get("/transkrip/notebook")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertEqual(len(json.loads(response.get_data(as_text=True))["cells"]), 5)
        response.close()

    # ---- unduh audio

    def test_audio_download_job_and_file(self):
        html = self.client.post("/transkrip/audio", data={"youtube_url": URL}).get_data(as_text=True)
        job = html.split("/transkrip/audio/status/")[1].split('"')[0]
        data = self.wait(f"/transkrip/audio/status/{job}")
        self.assertEqual(data["status"], "done", data)
        self.assertEqual(data["filename"], f"audio_{VIDEO}.webm")

        response = self.client.get(f"/transkrip/audio/file/{data['filename']}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        response.close()

    def test_audio_download_error(self):
        self.audio_error = "HTTP Error 403"
        html = self.client.post("/transkrip/audio", data={"youtube_url": URL}).get_data(as_text=True)
        job = html.split("/transkrip/audio/status/")[1].split('"')[0]
        data = self.wait(f"/transkrip/audio/status/{job}")
        self.assertEqual(data["status"], "error")
        self.assertIn("HTTP Error 403", data["error"])

    def test_only_audio_files_can_be_downloaded(self):
        for name in ("source_x.mp4", "../app.py", "audio_x.exe", "audio_tidakada.webm", "data"):
            self.assertEqual(self.client.get(f"/transkrip/audio/file/{name}").status_code, 404, name)

    def test_bad_links_are_refused(self):
        for url in ("bukan link", "https://example.com/video"):
            response = self.client.post("/transkrip/audio", data={"youtube_url": url})
            self.assertEqual(response.status_code, 400, url)

    # ---- impor

    def test_import_runs_the_normal_analysis(self):
        html = self.post_import(_long_srt()).get_data(as_text=True)
        job = html.split("/analyze/status/")[1].split('"')[0]
        data = self.wait(f"/analyze/status/{job}")
        self.assertEqual(data["status"], "done", data)
        self.assertEqual(data["video_id"], VIDEO)
        self.assertEqual(data["next"], "video")

        transcript = exchange.load_transcript(VIDEO)
        self.assertEqual(transcript["source"], "import")
        self.assertEqual(transcript["title"], "Video Colab")
        self.assertEqual(transcript["duration"], 900.0)

        page = self.client.get(f"/video/{VIDEO}").get_data(as_text=True)
        self.assertIn("diimpor dari file (hasil.srt)", page)
        self.assertIn("Render klip ini", page)

    def test_ai_mode_goes_to_the_copy_paste_page(self):
        html = self.post_import(_long_srt(), mode="hybrid").get_data(as_text=True)
        job = html.split("/analyze/status/")[1].split('"')[0]
        data = self.wait(f"/analyze/status/{job}")
        self.assertEqual(data["next"], "ai")

    def test_import_works_without_youtube_metadata(self):
        self.metadata_error = "Sign in to confirm you're not a bot"
        html = self.post_import(_long_srt(5)).get_data(as_text=True)
        job = html.split("/analyze/status/")[1].split('"')[0]
        data = self.wait(f"/analyze/status/{job}")
        self.assertEqual(data["status"], "done", data)
        transcript = exchange.load_transcript(VIDEO)
        self.assertEqual(transcript["title"], VIDEO)
        self.assertAlmostEqual(transcript["duration"], 299.5, delta=1)

    def test_transcript_longer_than_the_video_is_refused(self):
        html = self.post_import(_long_srt(30)).get_data(as_text=True)
        job = html.split("/analyze/status/")[1].split('"')[0]
        data = self.wait(f"/analyze/status/{job}")
        self.assertEqual(data["status"], "error")
        self.assertIn("video lain", data["error"])
        self.assertIsNone(exchange.load_transcript(VIDEO))

    def test_bad_imports_are_explained(self):
        self.assertIn("Pilih file transkrip", self.post_import(None).get_data(as_text=True))
        response = self.post_import("hanya teks biasa tanpa waktu")
        self.assertEqual(response.status_code, 400)
        self.assertIn("tidak berisi transkrip bertimestamp", response.get_data(as_text=True))
        response = self.post_import(_long_srt(), url="https://example.com/x")
        self.assertEqual(response.status_code, 400)
        response = self.post_import(_long_srt(), mode="aneh")
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
