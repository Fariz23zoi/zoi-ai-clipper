"""Tes rute dashboard Flask. Unduhan dan subtitle YouTube diganti data
contoh, sehingga tes ini tidak memakai internet.

Alur yang diuji:
  halaman 1 (link + mode) -> halaman 2 (ekspor/impor, hanya mode AI)
  -> halaman 3 (pilih klip + kualitas render) -> render -> hasil.
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
except ImportError as error:  # Flask atau yt-dlp belum terpasang
    zoi_app = None
    IMPORT_ERROR = str(error)

from shorts_generator import exchange

from tests.helpers import (
    VIDEO_ID, ffmpeg_available, make_test_video, probe, sample_transcript,
)

NEEDS_FFMPEG = unittest.skipUnless(ffmpeg_available(), "FFmpeg tidak terpasang")


@unittest.skipIf(zoi_app is None, "app.py tidak bisa diimpor")
class SourceQualityRuleTest(unittest.TestCase):
    """Kualitas sumber paling ringan yang masih mengisi kanvas."""

    def needed(self, quality, aspect):
        width = zoi_app.QUALITIES[quality][0]
        return zoi_app._needed_source_res(width, aspect)

    def test_landscape_16_9_needs_one_tier_below_the_canvas(self):
        expected = {"240": 144, "360": 240, "480": 360, "720": 480, "1080": 720}
        for quality, tier in expected.items():
            self.assertEqual(self.needed(quality, 16 / 9), tier, quality)

    def test_4_3_video_needs_more_because_it_is_narrower(self):
        self.assertEqual(self.needed("720", 4 / 3), 720)
        self.assertEqual(self.needed("480", 4 / 3), 360)

    def test_portrait_video_fills_the_canvas(self):
        self.assertEqual(self.needed("720", 9 / 16), 720)
        self.assertEqual(self.needed("1080", 9 / 16), 1080)
        self.assertEqual(self.needed("480", 3 / 4), 480)

    def test_ultra_wide_video_needs_less(self):
        self.assertEqual(self.needed("720", 21 / 9), 360)

    def test_never_asks_for_more_than_1080(self):
        self.assertEqual(zoi_app._needed_source_res(4000, 1.0), 1080)

    def test_unknown_or_absurd_aspect_falls_back_to_16_9(self):
        for value in (None, 0, "x", 99, -1):
            self.assertAlmostEqual(
                zoi_app._aspect({"aspect": value}), 16 / 9, places=3
            )
        self.assertEqual(zoi_app._aspect({"aspect": 0.5625}), 0.5625)


@unittest.skipIf(zoi_app is None, "app.py tidak bisa diimpor")
class DashboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp()
        cls.spare = tempfile.mkdtemp()
        cls.saved = (
            zoi_app.OUTPUT_DIR, exchange.DATA_DIR,
            zoi_app.download_youtube_local, zoi_app.transcribe_from_youtube,
            zoi_app.CLAUDE_API_KEY, zoi_app.OPENAI_API_KEY,
            zoi_app.DOWNLOAD_AT_ANALYSIS, zoi_app.analyze_with_ai,
        )
        zoi_app.DOWNLOAD_AT_ANALYSIS = False
        zoi_app.OUTPUT_DIR = cls.folder
        exchange.DATA_DIR = os.path.join(cls.folder, "data")
        zoi_app.CLAUDE_API_KEY = ""
        zoi_app.OPENAI_API_KEY = ""

        # Video uji 1280x720, 40 detik.
        cls.source = os.path.join(cls.folder, f"source_{VIDEO_ID}.mp4")
        cls.pristine = os.path.join(cls.spare, "asli.mp4")
        if ffmpeg_available():
            make_test_video(cls.pristine, seconds=40)
        else:
            open(cls.pristine, "wb").close()
        shutil.copyfile(cls.pristine, cls.source)
        cls.downloads = []
        cls.download_error = None

        cls.transcript = sample_transcript()

        def fake_download(url, fmt="720", out_dir=None, progress_hook=None):
            """Meniru yt-dlp: melapor progres lalu menaruh file di output."""
            cls.downloads.append({"url": url, "fmt": fmt, "out_dir": out_dir})

            if cls.download_error:
                raise RuntimeError(cls.download_error)

            for done in (10, 40, 70, 100):
                if progress_hook:
                    progress_hook({
                        "status": "downloading",
                        "downloaded_bytes": done, "total_bytes": 100,
                        "info_dict": {"vcodec": "avc1.4d401f"},
                    })
                time.sleep(0.12)

            if progress_hook:
                progress_hook({"status": "finished"})

            if not os.path.exists(cls.source):
                shutil.copyfile(cls.pristine, cls.source)
            return cls.source

        zoi_app.download_youtube_local = fake_download
        zoi_app.transcribe_from_youtube = lambda url: cls.transcript
        cls.client = zoi_app.app.test_client()

    @classmethod
    def tearDownClass(cls):
        (
            zoi_app.OUTPUT_DIR, exchange.DATA_DIR,
            zoi_app.download_youtube_local, zoi_app.transcribe_from_youtube,
            zoi_app.CLAUDE_API_KEY, zoi_app.OPENAI_API_KEY,
            zoi_app.DOWNLOAD_AT_ANALYSIS, zoi_app.analyze_with_ai,
        ) = cls.saved
        shutil.rmtree(cls.folder, ignore_errors=True)
        shutil.rmtree(cls.spare, ignore_errors=True)

    # ---- alat bantu

    def wait(self, url, timeout=180):
        deadline = time.time() + timeout
        seen = []
        while time.time() < deadline:
            data = self.client.get(url).get_json()
            seen.append(data)
            if data["status"] in ("done", "error"):
                return data, seen
            time.sleep(0.1)
        self.fail(f"job tidak selesai: {seen[-1]}")

    def analyze(self, mode="zoi"):
        page = self.client.post("/analyze", data={
            "youtube_url": f"https://www.youtube.com/watch?v={VIDEO_ID}",
            "mode": mode,
        })
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        job_id = html.split("/analyze/status/")[1].split('"')[0]
        data, _ = self.wait(f"/analyze/status/{job_id}")
        return data

    def page(self):
        return self.client.get(f"/video/{VIDEO_ID}").get_data(as_text=True)

    def ai_page(self):
        return self.client.get(f"/video/{VIDEO_ID}/ai").get_data(as_text=True)

    def render(self, **form):
        data = {
            "video_id": VIDEO_ID, "title": "Uji",
            "start_time": "3", "end_time": "13",
        }
        data.update(form)
        response = self.client.post("/select", data=data)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        html = response.get_data(as_text=True)
        return html.split("/progress/")[1].split('"')[0], html

    def set_analysis(self, **values):
        analysis = exchange.load_analysis(VIDEO_ID)
        analysis.update(values)
        exchange.save_analysis(VIDEO_ID, analysis)

    def segments(self):
        return exchange.build_export(self.transcript)["transcript"]["segments"]

    def valid_import(self, first=10, last=16, title="Klip dari Claude", **top):
        segments = self.segments()
        data = {
            "schema": "zoi.import.v1", "video_id": VIDEO_ID,
            "transcript_sha256": exchange.transcript_hash(self.transcript),
            "highlights": [
                {
                    "title": title, "start_segment": first,
                    "end_segment": last, "start_time": segments[first]["start"],
                    "end_time": segments[last]["end"],
                    "hook_sentence": "", "virality_reason": "Menarik", "score": 91,
                },
                {"title": "Ngawur", "start_time": 5000, "end_time": 5050, "score": 99},
            ],
        }
        data.update(top)
        return data

    # ================================================= halaman 1: input

    def test_01_home_page(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        for mode in ("zoi", "claude", "chatgpt", "hybrid"):
            self.assertIn(f'value="{mode}"', html)
        self.assertIn('name="youtube_url"', html)
        self.assertIn("Langkah 1", html)
        # Kualitas dipilih di langkah render, bukan di sini.
        self.assertNotIn('name="quality"', html)

    def test_02_bad_input_shows_error(self):
        response = self.client.post(
            "/analyze", data={"youtube_url": "bukan link", "mode": "zoi"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Masukkan link YouTube", response.get_data(as_text=True))

        response = self.client.post(
            "/analyze", data={"youtube_url": "https://youtu.be/x", "mode": "aneh"}
        )
        self.assertEqual(response.status_code, 400)

    def test_03_unknown_pages_return_404(self):
        for url in (
            "/video/tidakada", "/video/tidakada/ai", "/progress/tidakada",
            "/analyze/status/tidakada",
        ):
            self.assertEqual(self.client.get(url).status_code, 404, url)

    # ================================================= analisis

    def test_10_zoi_mode_goes_straight_to_candidates(self):
        data = self.analyze("zoi")
        self.assertEqual(data["status"], "done", data)
        self.assertEqual(data["video_id"], VIDEO_ID)
        self.assertEqual(data["progress"], 100)
        self.assertEqual(data["next"], "video")

        html = self.page()
        self.assertIn("Langkah 2 dari 2", html)
        self.assertIn("ZOI ENGINE V4.1", html)
        self.assertIn("ZOI ENGINE V4.2", html)
        self.assertEqual(html.count("Render klip ini"), 10)
        self.assertIn("Skor", html)
        self.assertIn("Hook", html)

        self.assertTrue(exchange.load_transcript(VIDEO_ID)["segments"])
        # Analisis hanya memakai subtitle: tidak ada unduhan video.
        self.assertEqual(self.downloads, [])

    def test_11_analysis_error_is_reported(self):
        original = zoi_app.transcribe_from_youtube
        original_load = exchange.load_transcript

        def boom(url):
            raise RuntimeError("Video ini tidak memiliki subtitle")

        zoi_app.transcribe_from_youtube = boom
        exchange.load_transcript = lambda video_id: None
        try:
            data = self.analyze("zoi")
        finally:
            zoi_app.transcribe_from_youtube = original
            exchange.load_transcript = original_load

        self.assertEqual(data["status"], "error")
        self.assertIn("tidak memiliki subtitle", data["error"])

    def test_11b_non_indonesian_subtitles_are_flagged(self):
        self.assertNotIn("bukan Indonesia", self.page())

        english = dict(self.transcript, language="en")
        original = zoi_app.transcribe_from_youtube
        original_load = exchange.load_transcript
        zoi_app.transcribe_from_youtube = lambda url: english
        exchange.load_transcript = lambda video_id: None
        try:
            data = self.analyze("zoi")
        finally:
            zoi_app.transcribe_from_youtube = original
            exchange.load_transcript = original_load

        self.assertEqual(data["status"], "done", data)
        html = self.page()
        self.assertIn("bukan Indonesia", html)
        self.assertIn("Claude atau ChatGPT tetap bisa dipakai", html)

        # Kembalikan ke transkrip Indonesia untuk tes berikutnya.
        exchange.save_transcript(self.transcript)
        self.analyze("zoi")
        self.assertNotIn("bukan Indonesia", self.page())

    def test_12_candidates_page_offers_five_render_qualities(self):
        html = self.page()
        for quality, size in (
            ("240", "240x426"), ("360", "360x640"), ("480", "480x854"),
            ("720", "720x1280"), ("1080", "1080x1920"),
        ):
            self.assertIn(f'value="{quality}"', html)
            self.assertIn(f'data-size="{size}"', html)
        # 720p terpilih bawaan dan ikut terkirim di setiap tombol render.
        self.assertIn('data-size="720x1280" data-source="480"', html)
        self.assertEqual(
            html.count('name="quality" value="720" class="quality-field"'), 10
        )

    def test_13_claude_mode_continues_to_the_import_page(self):
        data = self.analyze("claude")
        self.assertEqual(data["status"], "done", data)
        self.assertEqual(data["next"], "ai")

        html = self.ai_page()
        self.assertIn("Langkah 2 dari 3", html)
        self.assertIn("Salin prompt", html)
        self.assertIn("Buka Claude", html)
        self.assertIn('name="json_claude"', html)
        # Mode Claude hanya menanyakan jawaban Claude.
        self.assertNotIn('name="json_chatgpt"', html)
        self.assertIn("Lewati, pakai hasil ZOI Engine", html)

        html = self.page()
        self.assertIn("Langkah 3 dari 3", html)
        self.assertIn("Belum ada hasil analisis Claude", html)
        self.assertIn(f"/video/{VIDEO_ID}/ai", html)
        self.assertIn("Render klip ini", html)

    def test_14_hybrid_mode_asks_for_both_answers(self):
        data = self.analyze("hybrid")
        self.assertEqual(data["next"], "ai")
        html = self.ai_page()
        self.assertIn('name="json_claude"', html)
        self.assertIn('name="json_chatgpt"', html)
        self.assertIn("Buka Claude", html)
        self.assertIn("Buka ChatGPT", html)

    def test_15_chatgpt_mode_asks_for_chatgpt_only(self):
        data = self.analyze("chatgpt")
        self.assertEqual(data["next"], "ai")
        html = self.ai_page()
        self.assertIn('name="json_chatgpt"', html)
        self.assertNotIn('name="json_claude"', html)

    def test_16_api_key_skips_the_import_page(self):
        segments = self.segments()
        calls = []

        def fake_ai(transcript, provider, num_clips):
            calls.append(provider)
            return {"highlights": [{
                "title": "Dari API", "start_time": segments[40]["start"],
                "end_time": segments[46]["end"], "score": 77,
                "hook_sentence": "", "virality_reason": "",
            }]}

        zoi_app.CLAUDE_API_KEY = "kunci-uji"
        zoi_app.analyze_with_ai = fake_ai
        try:
            data = self.analyze("claude")
        finally:
            zoi_app.CLAUDE_API_KEY = ""
            zoi_app.analyze_with_ai = self.saved[7]

        self.assertEqual(data["status"], "done", data)
        self.assertEqual(data["next"], "video")
        self.assertEqual(calls, ["claude"])
        html = self.page()
        self.assertIn("CLAUDE AI", html)
        self.assertLess(html.index("CLAUDE AI"), html.index("ZOI ENGINE V4.1"))
        self.assertNotIn("Belum ada hasil analisis", html)

    def test_17_download_at_analysis_is_optional(self):
        os.rename(self.source, self.source + ".simpan")
        zoi_app.DOWNLOAD_AT_ANALYSIS = True
        try:
            data = self.analyze("zoi")
            self.assertEqual(data["status"], "done", data)
            # Kualitas bawaan 720p hanya butuh sumber 480p.
            self.assertEqual([item["fmt"] for item in self.downloads], ["480"])
            analysis = exchange.load_analysis(VIDEO_ID)
            self.assertEqual(analysis["source_res"], 480)
            self.assertEqual(analysis["source_file"], f"source_{VIDEO_ID}.mp4")
        finally:
            zoi_app.DOWNLOAD_AT_ANALYSIS = False
            if os.path.exists(self.source):
                os.remove(self.source)
            os.rename(self.source + ".simpan", self.source)
            self.set_analysis(source_file="", source_res=0)
            del self.downloads[:]

    # ================================================= ekspor

    def test_20_export_json(self):
        response = self.client.get(f"/video/{VIDEO_ID}/export.json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        data = json.loads(response.get_data(as_text=True))
        self.assertEqual(data["schema"], "zoi.export.v1")
        self.assertEqual(data["video"]["video_id"], VIDEO_ID)
        self.assertEqual(
            data["transcript"]["segment_count"], len(data["transcript"]["segments"])
        )

    def test_21_export_prompt(self):
        response = self.client.get(f"/video/{VIDEO_ID}/prompt.txt")
        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertIn("zoi.import.v1", text)
        self.assertIn("DATA TRANSKRIP", text)

    # ================================================= halaman 2: impor

    def post_import(self, **form):
        return self.client.post(
            f"/video/{VIDEO_ID}/import", data=form,
            content_type="multipart/form-data",
        )

    def test_30_import_needs_an_answer(self):
        response = self.post_import(json_claude="   ")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Tempel jawaban JSON", response.get_data(as_text=True))

    def test_31_import_rejects_garbage_and_keeps_the_text(self):
        response = self.post_import(json_claude="bukan json sama sekali")
        self.assertEqual(response.status_code, 400)
        html = response.get_data(as_text=True)
        self.assertIn("bukan JSON yang valid", html)
        self.assertIn("bukan json sama sekali", html)
        self.assertIn("Salin prompt", html)

    def test_32_import_rejects_other_video(self):
        response = self.post_import(
            json_claude=json.dumps(self.valid_import(video_id="VIDEOLAIN01"))
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("video lain", response.get_data(as_text=True))

    def test_33_import_with_no_valid_candidate(self):
        data = self.valid_import()
        data["highlights"] = data["highlights"][1:]
        response = self.post_import(json_claude=json.dumps(data))
        self.assertEqual(response.status_code, 400)
        html = response.get_data(as_text=True)
        self.assertIn("Tidak ada kandidat yang lolos", html)
        self.assertIn("melewati durasi", html)

    def test_34_valid_answer_goes_to_the_candidates_page(self):
        self.analyze("claude")
        text = "Ini hasilnya:\n```json\n" + json.dumps(self.valid_import()) + "\n```"
        response = self.post_import(json_claude=text)
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/video/{VIDEO_ID}", response.headers["Location"])
        self.assertNotIn("/ai", response.headers["Location"])

        html = self.client.get(response.headers["Location"]).get_data(as_text=True)
        self.assertIn("berhasil diimpor", html)
        self.assertIn("CLAUDE (IMPOR)", html)
        self.assertIn("Klip dari Claude", html)
        self.assertIn("1 kandidat ditolak validasi", html)
        self.assertNotIn("Ngawur</h3>", html)
        # Hasil AI tampil lebih dulu daripada hasil ZOI.
        self.assertLess(
            html.index("CLAUDE (IMPOR)"), html.index("ZOI ENGINE V4.1")
        )
        self.assertNotIn("Belum ada hasil analisis", html)
        self.assertIn("Impor ulang analisis AI", html)

        # Halaman 2 kini memberi tahu bahwa sudah ada hasil.
        html = self.ai_page()
        self.assertIn("Sudah ada 1 kandidat dari Claude", html)
        self.assertIn("Lihat hasil analisis", html)

    def test_35_import_from_uploaded_file(self):
        payload = json.dumps(
            self.valid_import(first=60, last=66, title="Klip dari ChatGPT")
        ).encode("utf-8")
        response = self.post_import(
            file_chatgpt=(io.BytesIO(payload), "hasil.json")
        )
        self.assertEqual(response.status_code, 302)
        html = self.page()
        self.assertIn("CHATGPT (IMPOR)", html)
        self.assertIn("Klip dari ChatGPT", html)

    def test_36_hybrid_keeps_the_good_answer_and_reports_the_bad_one(self):
        self.analyze("hybrid")
        response = self.post_import(
            json_claude=json.dumps(
                self.valid_import(first=80, last=86, title="Claude benar")
            ),
            json_chatgpt="{rusak",
        )
        self.assertEqual(response.status_code, 400)
        html = response.get_data(as_text=True)
        self.assertIn("Kandidat dari CLAUDE (IMPOR) berhasil diimpor", html)
        self.assertIn("bukan JSON yang valid", html)
        self.assertIn("{rusak", html)
        self.assertIn("Lihat hasil analisis", html)
        self.assertIn("Claude benar", self.page())

    def test_37_hybrid_imports_both_answers_at_once(self):
        response = self.post_import(
            json_claude=json.dumps(
                self.valid_import(first=100, last=106, title="Pilihan Claude")
            ),
            json_chatgpt=json.dumps(
                self.valid_import(first=120, last=126, title="Pilihan ChatGPT")
            ),
        )
        self.assertEqual(response.status_code, 302)
        html = self.client.get(response.headers["Location"]).get_data(as_text=True)
        self.assertIn("CLAUDE (IMPOR), CHATGPT (IMPOR)", html)
        self.assertIn("Pilihan Claude", html)
        self.assertIn("Pilihan ChatGPT", html)
        # Mengimpor ulang mengganti hasil lama dari analis yang sama.
        self.assertNotIn("Claude benar", html)

    def test_38_reanalysis_keeps_imported_candidates(self):
        self.analyze("zoi")
        html = self.page()
        self.assertIn("Pilihan Claude", html)
        self.assertIn("Pilihan ChatGPT", html)

    def test_39_delete_import_group(self):
        response = self.client.post(
            f"/video/{VIDEO_ID}/group/delete", data={"name": "CHATGPT (IMPOR)"}
        )
        self.assertEqual(response.status_code, 302)
        html = self.page()
        self.assertNotIn("CHATGPT (IMPOR)", html)
        self.assertIn("CLAUDE (IMPOR)", html)
        self.assertIn("ZOI ENGINE V4.1", html)

    # ================================================= halaman 3: render

    def test_40_select_rejects_bad_requests(self):
        cases = [
            {"video_id": "../x", "start_time": "1", "end_time": "40"},
            {"video_id": "belumada", "start_time": "1", "end_time": "40"},
            {"video_id": VIDEO_ID, "start_time": "abc", "end_time": "40"},
            {"video_id": VIDEO_ID, "start_time": "40", "end_time": "10"},
            {"video_id": VIDEO_ID, "start_time": "-3", "end_time": "30"},
            {"video_id": VIDEO_ID, "start_time": "0", "end_time": "2"},
            {"video_id": VIDEO_ID, "start_time": "0", "end_time": "999"},
            {"video_id": VIDEO_ID, "start_time": "9000", "end_time": "9040"},
            {"video_id": VIDEO_ID, "start_time": "3", "end_time": "13",
             "quality": "4320"},
            {"video_id": VIDEO_ID, "start_time": "3", "end_time": "13",
             "quality": "abc"},
        ]
        for data in cases:
            response = self.client.post("/select", data=data)
            self.assertEqual(response.status_code, 400, data)

    @NEEDS_FFMPEG
    def test_41_render_progress_and_output(self):
        job_id, html = self.render(
            title="Uji: klip/../aneh <b>", analyst="ZOI",
            start_time="2", end_time="34",
        )
        self.assertIn("720p (720x1280)", html)

        data, seen = self.wait(f"/progress/{job_id}")
        self.assertEqual(data["status"], "done", data)
        self.assertEqual(data["progress"], 100)
        self.assertEqual(data["stage"], "Rendering selesai")

        # Sebelum selesai, progres tidak pernah menunjukkan 100.
        running = [item["progress"] for item in seen if item["status"] != "done"]
        self.assertTrue(all(value < 100 for value in running))
        self.assertEqual(running, sorted(running))

        filename = data["filename"]
        self.assertRegex(filename, r"^zoi_[A-Za-z0-9_-]+_720p_[A-Za-z0-9_]+\.mp4$")
        path = os.path.join(self.folder, filename)
        streams = probe(path)
        self.assertEqual(
            (streams["video"]["width"], streams["video"]["height"]), (720, 1280)
        )
        self.assertIn("audio", streams)

        served = self.client.get(f"/output/{filename}")
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.mimetype, "video/mp4")
        served.close()

        download = self.client.get(f"/output/{filename}?dl=1")
        self.assertIn("attachment", download.headers["Content-Disposition"])
        download.close()

        self.assertTrue(os.path.isfile(self.source))
        leftovers = [
            name for name in os.listdir(self.folder)
            if ".part." in name or ".cut." in name
        ]
        self.assertEqual(leftovers, [])
        self.assertIn(filename, self.page())
        self.assertIn(filename, self.client.get("/").get_data(as_text=True))
        # Video sumber 1280x720 sudah ada dan cukup: tidak ada unduhan.
        self.assertEqual(self.downloads, [])
        self.assertTrue(all(item["step"] == "" for item in seen))

    @NEEDS_FFMPEG
    def test_42_each_quality_gives_its_own_size(self):
        for quality, size in (
            ("240", (240, 426)), ("360", (360, 640)), ("480", (480, 854)),
            ("1080", (1080, 1920)),
        ):
            job_id, _ = self.render(quality=quality, end_time="8")
            data, _ = self.wait(f"/progress/{job_id}")
            self.assertEqual(data["status"], "done", data)
            self.assertIn(f"_{quality}p_", data["filename"])
            video = probe(os.path.join(self.folder, data["filename"]))["video"]
            self.assertEqual((video["width"], video["height"]), size, quality)
        # Sumber 720p cukup sampai 1080p, jadi tetap tanpa unduhan.
        self.assertEqual(self.downloads, [])

    @NEEDS_FFMPEG
    def test_43_two_renders_do_not_collide(self):
        jobs = [self.render(title="Sama")[0] for _ in range(2)]
        results = [self.wait(f"/progress/{job}")[0] for job in jobs]
        self.assertEqual([r["status"] for r in results], ["done", "done"])
        self.assertNotEqual(results[0]["filename"], results[1]["filename"])
        for result in results:
            self.assertTrue(
                os.path.isfile(os.path.join(self.folder, result["filename"]))
            )

    @NEEDS_FFMPEG
    def test_44_render_error_is_reported_and_cleaned(self):
        original = zoi_app.crop_clip_local

        def boom(*args, **kwargs):
            raise RuntimeError("FFmpeg meledak")

        zoi_app.crop_clip_local = boom
        try:
            job_id, _ = self.render(title="Gagal")
            data, _ = self.wait(f"/progress/{job_id}")
        finally:
            zoi_app.crop_clip_local = original

        self.assertEqual(data["status"], "error")
        self.assertIn("FFmpeg meledak", data["error"])
        self.assertLess(data["progress"], 100)
        self.assertFalse(os.path.exists(os.path.join(self.folder, data["filename"])))

    def test_45_without_source_and_url_render_is_refused(self):
        saved_url = exchange.load_analysis(VIDEO_ID)["url"]
        self.set_analysis(url="")
        os.rename(self.source, self.source + ".simpan")
        try:
            response = self.client.post("/select", data={
                "video_id": VIDEO_ID, "start_time": "3", "end_time": "13",
            })
            self.assertEqual(response.status_code, 400)
            self.assertIn("Analisis ulang", response.get_data(as_text=True))
            self.assertIn("disabled", self.page().split("Render klip ini")[0][-200:])
        finally:
            os.rename(self.source + ".simpan", self.source)
            self.set_analysis(url=saved_url)

    def test_46_frame_choice_on_page_and_validation(self):
        html = self.page()
        self.assertIn('name="frame-choice" value="wide" checked', html)
        self.assertIn('name="frame-choice" value="square"', html)
        self.assertEqual(html.count('name="frame" value="wide" class="frame-field"'),
                         html.count("Render klip ini"))
        # Bingkai 1:1 pada 720p butuh sumber 720p (sisi terpendek selebar kanvas).
        self.assertIn('data-source-square="720"', html)
        response = self.client.post("/select", data={
            "video_id": VIDEO_ID, "start_time": "3", "end_time": "13", "frame": "bulat",
        })
        self.assertEqual(response.status_code, 400)

    @NEEDS_FFMPEG
    def test_47_square_frame_render(self):
        self.set_analysis(source_res=720)
        job_id, html = self.render(quality="480", frame="square", end_time="8")
        self.assertIn("Bingkai 1:1", html)
        data, _ = self.wait(f"/progress/{job_id}")
        self.assertEqual(data["status"], "done", data)
        self.assertIn("_480p_1x1_", data["filename"])
        video = probe(os.path.join(self.folder, data["filename"]))["video"]
        self.assertEqual((video["width"], video["height"]), (480, 854))
        self.assertEqual(self.downloads, [])

    def test_47b_hook_editor_and_crop_controls_on_page(self):
        html = self.page()
        self.assertIn('name="hook-choice" value="1" checked', html)
        self.assertEqual(html.count('name="hook_text"'), html.count("Render klip ini"))
        self.assertEqual(html.count('class="hook-field"'), html.count("Render klip ini"))
        self.assertEqual(html.count('class="crop-field"'), html.count("Render klip ini"))
        self.assertIn('id="crop-x"', html)
        # Teks awal = hook hasil analisis tanpa tanda seperti [tertawa].
        analysis = exchange.load_analysis(VIDEO_ID)
        clip = next(c for g in analysis["groups"] for c in g["clips"] if c["hook_sentence"])
        self.assertIn(zoi_app.clean_hook_text(clip["hook_sentence"])[:30], html)

    def test_47c_preview_image(self):
        response = self.client.get(f"/video/{VIDEO_ID}/preview.jpg?t=2")
        if not ffmpeg_available():
            return
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/jpeg")
        self.assertGreater(len(response.data), 1000)
        self.assertEqual(self.client.get("/video/tidakada/preview.jpg").status_code, 404)

    def test_47d_bad_crop_position_is_refused(self):
        for value in ("abc", "nan"):
            response = self.client.post("/select", data={
                "video_id": VIDEO_ID, "start_time": "3", "end_time": "13", "crop_x": value,
            })
            self.assertEqual(response.status_code, 400, value)

    @NEEDS_FFMPEG
    def test_47e_render_with_hook_and_crop(self):
        if not zoi_app.crop_clip_local.__module__:
            return
        from shorts_generator.local.clipper import find_hook_font
        if not find_hook_font():
            self.skipTest("font tidak tersedia")
        job_id, html = self.render(
            quality="240", frame="square", crop_x="20", hook_on="1",
            hook_text="Ternyata [tertawa] ini rahasianya 😂", end_time="9",
        )
        self.assertIn("Blok hook", html)
        self.assertIn("kiri 20%", html)
        data, _ = self.wait(f"/progress/{job_id}")
        self.assertEqual(data["status"], "done", data)
        video = probe(os.path.join(self.folder, data["filename"]))["video"]
        self.assertEqual((video["width"], video["height"]), (240, 426))

    def test_47f_hook_off_or_empty_means_no_hook(self):
        captured = []
        original = zoi_app.crop_clip_local
        zoi_app.crop_clip_local = lambda *a, **k: captured.append(k) or open(a[4], "wb").close()
        try:
            for form in ({"hook_on": "0", "hook_text": "teks"}, {"hook_on": "1", "hook_text": " [musik] "}):
                job_id, html = self.render(**form)
                self.wait(f"/progress/{job_id}")
                self.assertNotIn("Blok hook</span>", html)
        finally:
            zoi_app.crop_clip_local = original
        self.assertEqual([k["hook_text"] for k in captured], [None, None])
        self.assertEqual([k["crop_x"] for k in captured], [0.5, 0.5])

    def test_48_square_frame_rule(self):
        for quality, tier in (("240", 240), ("480", 480), ("720", 720), ("1080", 1080)):
            width = zoi_app.QUALITIES[quality][0]
            self.assertEqual(zoi_app._needed_source_res_for(width, 16 / 9, "square"), tier)
            self.assertEqual(
                zoi_app._needed_source_res_for(width, 16 / 9, "wide"),
                zoi_app._needed_source_res(width, 16 / 9),
            )

    # ================================================= unduhan saat render

    @NEEDS_FFMPEG
    def test_50_video_is_downloaded_when_first_rendered(self):
        os.remove(self.source)
        self.set_analysis(source_file="", source_res=0)
        del self.downloads[:]

        html = self.page()
        self.assertIn("Video sumber 480p diunduh saat render pertama", html)
        self.assertNotIn("disabled", html.split("Render klip ini")[0][-200:])

        jobs = [self.render(title="Unduh dulu", quality="480")[0] for _ in range(2)]

        data, seen = self.wait(f"/progress/{jobs[0]}")
        self.assertEqual(data["status"], "done", data)
        self.assertEqual(data["progress"], 100)

        download = [item for item in seen if item["step"] == "Langkah 1 dari 2"]
        render = [item for item in seen if item["step"] == "Langkah 2 dari 2"]
        self.assertTrue(download, seen)
        self.assertTrue(render, seen)
        self.assertTrue(any("Mengunduh" in item["stage"] for item in download))
        # Angka unduhan berasal dari yt-dlp dan tidak pernah menyentuh 100.
        numbers = [
            item["progress"] for item in download
            if isinstance(item["progress"], int)
        ]
        self.assertTrue(numbers)
        self.assertEqual(numbers, sorted(numbers))
        self.assertTrue(all(value < 100 for value in numbers))

        # Render kedua yang antre memakai file yang sama, tanpa unduh ulang.
        second, _ = self.wait(f"/progress/{jobs[1]}")
        self.assertEqual(second["status"], "done", second)
        self.assertEqual(len(self.downloads), 1)
        # Render 480p hanya butuh sumber 360p.
        self.assertEqual(self.downloads[0]["fmt"], "360")
        self.assertEqual(self.downloads[0]["out_dir"], self.folder)

        analysis = exchange.load_analysis(VIDEO_ID)
        self.assertEqual(analysis["source_file"], f"source_{VIDEO_ID}.mp4")
        self.assertEqual(analysis["source_res"], 360)

        html = self.page()
        self.assertIn("Video sumber tersimpan", html)
        # Sumber 360p cukup untuk 240p-480p, belum cukup untuk 720p ke atas.
        self.assertIn('data-size="480x854" data-source="360" data-ready="1"', html)
        self.assertIn('data-size="720x1280" data-source="480" data-ready=""', html)
        del self.downloads[:]

    @NEEDS_FFMPEG
    def test_51_lower_quality_reuses_the_saved_source(self):
        self.set_analysis(source_res=360)
        job_id, _ = self.render(quality="240", end_time="8")
        data, seen = self.wait(f"/progress/{job_id}")
        self.assertEqual(data["status"], "done", data)
        self.assertEqual(self.downloads, [])
        self.assertTrue(all(item["step"] == "" for item in seen))

    @NEEDS_FFMPEG
    def test_52_higher_quality_downloads_a_better_source(self):
        self.set_analysis(source_res=360)
        del self.downloads[:]

        job_id, _ = self.render(quality="1080", end_time="8")
        data, seen = self.wait(f"/progress/{job_id}")
        self.assertEqual(data["status"], "done", data)

        # Render 1080p butuh sumber 720p; yang tersimpan baru 360p.
        self.assertEqual([item["fmt"] for item in self.downloads], ["720"])
        self.assertTrue(any(item["step"] == "Langkah 1 dari 2" for item in seen))
        self.assertEqual(exchange.load_analysis(VIDEO_ID)["source_res"], 720)
        self.assertTrue(os.path.isfile(self.source))
        # File lama yang disisihkan sudah dibuang.
        self.assertEqual(
            [name for name in os.listdir(self.folder) if name.endswith(".lama")], []
        )
        video = probe(os.path.join(self.folder, data["filename"]))["video"]
        self.assertEqual((video["width"], video["height"]), (1080, 1920))
        del self.downloads[:]

    def test_53_failed_upgrade_keeps_the_old_source(self):
        self.set_analysis(source_res=240)
        type(self).download_error = "Sign in to confirm you're not a bot"
        try:
            job_id, _ = self.render(quality="1080", title="Gagal naik")
            data, _ = self.wait(f"/progress/{job_id}")
        finally:
            type(self).download_error = None
            del self.downloads[:]

        self.assertEqual(data["status"], "error")
        self.assertIn("Video gagal diunduh", data["error"])
        self.assertIn("not a bot", data["error"])
        # Video lama dikembalikan dan catatannya tidak berubah.
        self.assertTrue(os.path.isfile(self.source))
        self.assertEqual(
            [name for name in os.listdir(self.folder) if name.endswith(".lama")], []
        )
        self.assertEqual(exchange.load_analysis(VIDEO_ID)["source_res"], 240)
        self.assertFalse(os.path.exists(os.path.join(self.folder, data["filename"])))
        self.set_analysis(source_res=720)

    def test_54_download_failure_without_any_source_is_reported(self):
        os.rename(self.source, self.source + ".simpan")
        type(self).download_error = "Jaringan terputus"
        try:
            job_id, _ = self.render(title="Gagal unduh")
            data, _ = self.wait(f"/progress/{job_id}")
        finally:
            type(self).download_error = None
            os.rename(self.source + ".simpan", self.source)
            del self.downloads[:]

        self.assertEqual(data["status"], "error")
        self.assertIn("Video gagal diunduh", data["error"])
        self.assertIn("Jaringan terputus", data["error"])

    @NEEDS_FFMPEG
    def test_55_source_from_an_older_version_is_measured_not_redownloaded(self):
        # Tanpa catatan kualitas, ukuran file dibaca langsung (1280x720).
        self.set_analysis(source_file="", source_res=0)
        html = self.page()
        self.assertIn('data-size="1080x1920" data-source="720" data-ready="1"', html)

        job_id, _ = self.render(quality="1080", end_time="8")
        data, _ = self.wait(f"/progress/{job_id}")
        self.assertEqual(data["status"], "done", data)
        self.assertEqual(self.downloads, [])

    # ================================================= penyimpanan

    def test_60_source_cannot_be_deleted_while_rendering(self):
        zoi_app.RENDER_JOBS["sibuk"] = {
            "status": "running", "error": "", "created_at": time.time(),
            "video_id": VIDEO_ID, "progress": 10, "stage": "", "step": "",
            "filename": "x.mp4",
        }
        try:
            response = self.client.post(f"/video/{VIDEO_ID}/source/delete")
        finally:
            del zoi_app.RENDER_JOBS["sibuk"]

        self.assertEqual(response.status_code, 409)
        self.assertIn("sedang dipakai", response.get_data(as_text=True))
        self.assertTrue(os.path.isfile(self.source))

    def test_61_delete_source_frees_space_and_keeps_candidates(self):
        partial = os.path.join(self.folder, f"source_{VIDEO_ID}.f135.mp4.part")
        other = os.path.join(self.folder, f"source_{VIDEO_ID}LAIN.mp4")
        for path in (partial, other):
            with open(path, "wb") as file:
                file.write(b"x")

        self.assertIn(
            "Penyimpanan terpakai", self.client.get("/").get_data(as_text=True)
        )

        try:
            response = self.client.post(f"/video/{VIDEO_ID}/source/delete")
            self.assertEqual(response.status_code, 302)
            self.assertFalse(os.path.exists(self.source))
            self.assertFalse(os.path.exists(partial))
            # Video lain yang ID-nya mirip tidak ikut terhapus.
            self.assertTrue(os.path.exists(other))

            analysis = exchange.load_analysis(VIDEO_ID)
            self.assertEqual(analysis["source_file"], "")
            self.assertEqual(analysis["source_res"], 0)
            self.assertTrue(analysis["groups"])

            html = self.client.get(
                f"/video/{VIDEO_ID}?deleted=sumber"
            ).get_data(as_text=True)
            self.assertIn("Video sumber dihapus", html)
            self.assertIn("diunduh saat render pertama", html)
            self.assertIn("Render klip ini", html)
        finally:
            os.remove(other)
            if not os.path.exists(self.source):
                shutil.copyfile(self.pristine, self.source)

    def test_62_delete_rendered_clip(self):
        name = f"zoi_{VIDEO_ID}_1-2_720p_uji_abcdef.mp4"
        path = os.path.join(self.folder, name)
        with open(path, "wb") as file:
            file.write(b"x")

        self.assertIn(name, self.page())
        response = self.client.post(
            "/clip/delete", data={"filename": name, "video_id": VIDEO_ID}
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(os.path.exists(path))
        self.assertNotIn(name, self.page())

        # Hanya hasil render yang boleh dihapus lewat rute ini.
        for bad in (
            f"source_{VIDEO_ID}.mp4", "../app.py", name,
            f"data/{VIDEO_ID}/analysis.json", "",
        ):
            response = self.client.post("/clip/delete", data={"filename": bad})
            self.assertEqual(response.status_code, 404, bad)
        self.assertTrue(os.path.isfile(self.source))

    # ================================================= keamanan file

    def test_70_only_rendered_clips_are_served(self):
        for name in (
            f"source_{VIDEO_ID}.mp4",
            f"data/{VIDEO_ID}/transcript.json",
            "../app.py",
            "zoi_../../app.py",
        ):
            response = self.client.get(f"/output/{name}")
            self.assertEqual(response.status_code, 404, name)

    def test_71_page_text_is_escaped(self):
        saved = exchange.load_analysis(VIDEO_ID)["title"]
        self.set_analysis(title="<script>alert(1)</script>")
        try:
            for html in (self.page(), self.ai_page()):
                self.assertNotIn("<script>alert(1)</script>", html)
                self.assertIn("&lt;script&gt;", html)
        finally:
            self.set_analysis(title=saved)


if __name__ == "__main__":
    unittest.main()
