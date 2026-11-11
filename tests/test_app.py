"""Tes rute dashboard Flask. Unduhan dan subtitle YouTube diganti data
contoh, sehingga tes ini tidak memakai internet."""
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
            zoi_app.DOWNLOAD_AT_ANALYSIS,
        )
        zoi_app.DOWNLOAD_AT_ANALYSIS = False
        zoi_app.OUTPUT_DIR = cls.folder
        exchange.DATA_DIR = os.path.join(cls.folder, "data")
        zoi_app.CLAUDE_API_KEY = ""
        zoi_app.OPENAI_API_KEY = ""

        cls.source = os.path.join(cls.folder, f"source_{VIDEO_ID}.mp4")
        cls.pristine = os.path.join(cls.spare, "asli.mp4")
        cls.has_ffmpeg = ffmpeg_available()
        if cls.has_ffmpeg:
            make_test_video(cls.pristine, seconds=40)
        else:
            open(cls.pristine, "wb").close()
        shutil.copyfile(cls.pristine, cls.source)
        cls.downloads = []
        cls.download_error = None

        transcript = sample_transcript()
        # Samakan durasi dengan video uji agar klip bisa dirender.
        cls.transcript = transcript

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
        zoi_app.transcribe_from_youtube = lambda url: transcript
        cls.client = zoi_app.app.test_client()

    @classmethod
    def tearDownClass(cls):
        (
            zoi_app.OUTPUT_DIR, exchange.DATA_DIR,
            zoi_app.download_youtube_local, zoi_app.transcribe_from_youtube,
            zoi_app.CLAUDE_API_KEY, zoi_app.OPENAI_API_KEY,
            zoi_app.DOWNLOAD_AT_ANALYSIS,
        ) = cls.saved
        shutil.rmtree(cls.folder, ignore_errors=True)
        shutil.rmtree(cls.spare, ignore_errors=True)

    # ---- alat bantu

    def wait(self, url, timeout=120):
        deadline = time.time() + timeout
        seen = []
        while time.time() < deadline:
            data = self.client.get(url).get_json()
            seen.append(data)
            if data["status"] in ("done", "error"):
                return data, seen
            time.sleep(0.1)
        self.fail(f"job tidak selesai: {seen[-1]}")

    def analyze(self, mode="zoi", quality=None):
        form = {
            "youtube_url": f"https://www.youtube.com/watch?v={VIDEO_ID}",
            "mode": mode,
        }
        if quality:
            form["quality"] = quality
        page = self.client.post("/analyze", data=form)
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        job_id = html.split("/analyze/status/")[1].split('"')[0]
        data, _ = self.wait(f"/analyze/status/{job_id}")
        return data

    def page(self):
        return self.client.get(f"/video/{VIDEO_ID}").get_data(as_text=True)

    def valid_import(self, **top):
        segments = exchange.build_export(self.transcript)["transcript"]["segments"]
        data = {
            "schema": "zoi.import.v1", "video_id": VIDEO_ID,
            "transcript_sha256": exchange.transcript_hash(self.transcript),
            "highlights": [
                {
                    "title": "Klip dari Claude", "start_segment": 10,
                    "end_segment": 16, "start_time": segments[10]["start"],
                    "end_time": segments[16]["end"],
                    "hook_sentence": "", "virality_reason": "Menarik", "score": 91,
                },
                {"title": "Ngawur", "start_time": 5000, "end_time": 5050, "score": 99},
            ],
        }
        data.update(top)
        return data

    # ---- halaman utama dan input

    def test_01_home_page(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        for mode in ("zoi", "claude", "chatgpt", "hybrid"):
            self.assertIn(f'value="{mode}"', html)
        self.assertIn('name="youtube_url"', html)
        self.assertIn('name="quality"', html)
        self.assertIn('value="480" selected', html)

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
        self.assertEqual(self.client.get("/video/tidakada").status_code, 404)
        self.assertEqual(self.client.get("/progress/tidakada").status_code, 404)
        self.assertEqual(self.client.get("/analyze/status/tidakada").status_code, 404)

    # ---- analisis

    def test_10_analysis_job_finishes_and_shows_candidates(self):
        data = self.analyze("zoi")
        self.assertEqual(data["status"], "done", data)
        self.assertEqual(data["video_id"], VIDEO_ID)
        self.assertEqual(data["progress"], 100)

        html = self.page()
        self.assertIn("ZOI ENGINE V4.1", html)
        self.assertIn("ZOI ENGINE V4.2", html)
        self.assertEqual(html.count("Render klip ini"), 10)
        self.assertIn("Skor", html)
        self.assertIn("Hook", html)

        analysis = exchange.load_analysis(VIDEO_ID)
        self.assertEqual(analysis["download_height"], "480")
        self.assertTrue(exchange.load_transcript(VIDEO_ID)["segments"])
        # Analisis hanya memakai subtitle: tidak ada unduhan video.
        self.assertEqual(self.downloads, [])

    def test_13_download_at_analysis_is_optional(self):
        zoi_app.DOWNLOAD_AT_ANALYSIS = True
        try:
            data = self.analyze("zoi", quality="720")
        finally:
            zoi_app.DOWNLOAD_AT_ANALYSIS = False

        self.assertEqual(data["status"], "done", data)
        self.assertEqual(len(self.downloads), 1)
        self.assertEqual(self.downloads[0]["fmt"], "720")
        analysis = exchange.load_analysis(VIDEO_ID)
        self.assertEqual(analysis["download_height"], "720")
        self.assertEqual(analysis["source_file"], f"source_{VIDEO_ID}.mp4")
        del self.downloads[:]

    def test_14_unknown_quality_falls_back_to_default(self):
        self.analyze("zoi", quality="9999")
        self.assertEqual(
            exchange.load_analysis(VIDEO_ID)["download_height"], "480"
        )

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

    def test_12_claude_mode_without_key_points_to_export(self):
        data = self.analyze("claude")
        self.assertEqual(data["status"], "done", data)
        html = self.page()
        self.assertIn("tanpa API key", html)
        self.assertIn("Render klip ini", html)

    # ---- ekspor

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

    # ---- impor

    def test_30_import_rejects_garbage(self):
        response = self.client.post(
            f"/video/{VIDEO_ID}/import", data={"json_text": "bukan json"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("bukan JSON yang valid", response.get_data(as_text=True))

    def test_31_import_rejects_other_video(self):
        response = self.client.post(f"/video/{VIDEO_ID}/import", data={
            "json_text": json.dumps(self.valid_import(video_id="VIDEOLAIN01"))
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("video lain", response.get_data(as_text=True))

    def test_32_import_with_no_valid_candidate(self):
        data = self.valid_import()
        data["highlights"] = data["highlights"][1:]
        response = self.client.post(
            f"/video/{VIDEO_ID}/import", data={"json_text": json.dumps(data)}
        )
        self.assertEqual(response.status_code, 400)
        html = response.get_data(as_text=True)
        self.assertIn("Tidak ada kandidat yang lolos", html)
        self.assertIn("melewati durasi", html)

    def test_33_import_from_pasted_text(self):
        text = "Ini hasilnya:\n```json\n" + json.dumps(self.valid_import()) + "\n```"
        response = self.client.post(
            f"/video/{VIDEO_ID}/import",
            data={"json_text": text, "analyst": "claude"},
        )
        self.assertEqual(response.status_code, 302)

        html = self.page()
        self.assertIn("CLAUDE (IMPOR)", html)
        self.assertIn("Klip dari Claude", html)
        self.assertIn("1 kandidat ditolak validasi", html)
        self.assertNotIn("Ngawur</h3>", html)

    def test_34_import_from_uploaded_file(self):
        payload = json.dumps(self.valid_import()).encode("utf-8")
        response = self.client.post(
            f"/video/{VIDEO_ID}/import",
            data={
                "analyst": "chatgpt",
                "json_file": (io.BytesIO(payload), "hasil.json"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("CHATGPT (IMPOR)", self.page())

    def test_35_reanalysis_keeps_imported_candidates(self):
        self.analyze("zoi")
        self.assertIn("CLAUDE (IMPOR)", self.page())

    def test_36_delete_import_group(self):
        response = self.client.post(
            f"/video/{VIDEO_ID}/group/delete", data={"name": "CHATGPT (IMPOR)"}
        )
        self.assertEqual(response.status_code, 302)
        html = self.page()
        self.assertNotIn("CHATGPT (IMPOR)", html)
        self.assertIn("ZOI ENGINE V4.1", html)

    # ---- pemilihan dan rendering

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
        ]
        for data in cases:
            response = self.client.post("/select", data=data)
            self.assertEqual(response.status_code, 400, data)

    @unittest.skipUnless(ffmpeg_available(), "FFmpeg tidak terpasang")
    def test_41_render_progress_and_output(self):
        response = self.client.post("/select", data={
            "video_id": VIDEO_ID, "title": "Uji: klip/../aneh <b>",
            "analyst": "ZOI", "start_time": "2", "end_time": "34",
        })
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        job_id = html.split("/progress/")[1].split('"')[0]

        data, seen = self.wait(f"/progress/{job_id}")
        self.assertEqual(data["status"], "done", data)
        self.assertEqual(data["progress"], 100)
        self.assertEqual(data["stage"], "Rendering selesai")

        # Sebelum selesai, progres tidak pernah menunjukkan 100.
        running = [item["progress"] for item in seen if item["status"] != "done"]
        self.assertTrue(all(value < 100 for value in running))
        self.assertEqual(running, sorted(running))

        filename = data["filename"]
        self.assertRegex(filename, r"^zoi_[A-Za-z0-9_-]+\.mp4$")
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
        # Video sumber sudah ada, jadi tidak ada unduhan dan tidak ada langkah.
        self.assertEqual(self.downloads, [])
        self.assertTrue(all(item["step"] == "" for item in seen))

    @unittest.skipUnless(ffmpeg_available(), "FFmpeg tidak terpasang")
    def test_42_two_renders_do_not_collide(self):
        jobs = []
        for _ in range(2):
            html = self.client.post("/select", data={
                "video_id": VIDEO_ID, "title": "Sama",
                "start_time": "3", "end_time": "13",
            }).get_data(as_text=True)
            jobs.append(html.split("/progress/")[1].split('"')[0])

        results = [self.wait(f"/progress/{job}")[0] for job in jobs]
        self.assertEqual([r["status"] for r in results], ["done", "done"])
        self.assertNotEqual(results[0]["filename"], results[1]["filename"])
        for result in results:
            self.assertTrue(
                os.path.isfile(os.path.join(self.folder, result["filename"]))
            )

    @unittest.skipUnless(ffmpeg_available(), "FFmpeg tidak terpasang")
    def test_43_render_error_is_reported_and_cleaned(self):
        original = zoi_app.crop_clip_local

        def boom(*args, **kwargs):
            raise RuntimeError("FFmpeg meledak")

        zoi_app.crop_clip_local = boom
        try:
            html = self.client.post("/select", data={
                "video_id": VIDEO_ID, "title": "Gagal",
                "start_time": "3", "end_time": "13",
            }).get_data(as_text=True)
            job_id = html.split("/progress/")[1].split('"')[0]
            data, _ = self.wait(f"/progress/{job_id}")
        finally:
            zoi_app.crop_clip_local = original

        self.assertEqual(data["status"], "error")
        self.assertIn("FFmpeg meledak", data["error"])
        self.assertLess(data["progress"], 100)
        self.assertFalse(os.path.exists(os.path.join(self.folder, data["filename"])))

    def test_44_without_source_and_url_render_is_refused(self):
        analysis = exchange.load_analysis(VIDEO_ID)
        saved_url = analysis["url"]
        analysis["url"] = ""
        exchange.save_analysis(VIDEO_ID, analysis)
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
            analysis["url"] = saved_url
            exchange.save_analysis(VIDEO_ID, analysis)

    @unittest.skipUnless(ffmpeg_available(), "FFmpeg tidak terpasang")
    def test_45_video_is_downloaded_when_first_rendered(self):
        os.remove(self.source)
        del self.downloads[:]

        html = self.page()
        self.assertIn("Video belum diunduh", html)
        self.assertNotIn("disabled", html.split("Render klip ini")[0][-200:])

        jobs = []
        for _ in range(2):
            page = self.client.post("/select", data={
                "video_id": VIDEO_ID, "title": "Unduh dulu",
                "start_time": "3", "end_time": "13",
            }).get_data(as_text=True)
            jobs.append(page.split("/progress/")[1].split('"')[0])

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
        self.assertEqual(self.downloads[0]["fmt"], "480")
        self.assertEqual(self.downloads[0]["out_dir"], self.folder)

        self.assertTrue(os.path.isfile(self.source))
        self.assertEqual(
            exchange.load_analysis(VIDEO_ID)["source_file"],
            f"source_{VIDEO_ID}.mp4",
        )
        self.assertIn("Video sumber tersimpan", self.page())
        del self.downloads[:]

    def test_46_download_failure_is_reported(self):
        os.rename(self.source, self.source + ".simpan")
        type(self).download_error = "Sign in to confirm you're not a bot"
        try:
            page = self.client.post("/select", data={
                "video_id": VIDEO_ID, "title": "Gagal unduh",
                "start_time": "3", "end_time": "13",
            }).get_data(as_text=True)
            job_id = page.split("/progress/")[1].split('"')[0]
            data, _ = self.wait(f"/progress/{job_id}")
        finally:
            type(self).download_error = None
            os.rename(self.source + ".simpan", self.source)
            del self.downloads[:]

        self.assertEqual(data["status"], "error")
        self.assertIn("Video gagal diunduh", data["error"])
        self.assertIn("not a bot", data["error"])
        self.assertFalse(os.path.exists(os.path.join(self.folder, data["filename"])))

    # ---- penyimpanan

    def test_47_source_cannot_be_deleted_while_rendering(self):
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

    def test_48_delete_source_frees_space_and_keeps_candidates(self):
        partial = os.path.join(self.folder, f"source_{VIDEO_ID}.f135.mp4.part")
        other = os.path.join(self.folder, f"source_{VIDEO_ID}LAIN.mp4")
        for path in (partial, other):
            with open(path, "wb") as file:
                file.write(b"x")

        before = self.client.get("/").get_data(as_text=True)
        self.assertIn("Penyimpanan terpakai", before)

        try:
            response = self.client.post(f"/video/{VIDEO_ID}/source/delete")
            self.assertEqual(response.status_code, 302)
            self.assertFalse(os.path.exists(self.source))
            self.assertFalse(os.path.exists(partial))
            # Video lain yang ID-nya mirip tidak ikut terhapus.
            self.assertTrue(os.path.exists(other))

            analysis = exchange.load_analysis(VIDEO_ID)
            self.assertEqual(analysis["source_file"], "")
            self.assertTrue(analysis["groups"])

            html = self.client.get(
                f"/video/{VIDEO_ID}?deleted=sumber"
            ).get_data(as_text=True)
            self.assertIn("Video sumber dihapus", html)
            self.assertIn("Video belum diunduh", html)
            self.assertIn("Render klip ini", html)
        finally:
            os.remove(other)
            if not os.path.exists(self.source):
                shutil.copyfile(self.pristine, self.source)

    def test_49_delete_rendered_clip(self):
        name = f"zoi_{VIDEO_ID}_1-2_uji_abcdef.mp4"
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

    # ---- keamanan file

    def test_50_only_rendered_clips_are_served(self):
        for name in (
            f"source_{VIDEO_ID}.mp4",
            f"data/{VIDEO_ID}/transcript.json",
            "../app.py",
            "zoi_../../app.py",
        ):
            response = self.client.get(f"/output/{name}")
            self.assertEqual(response.status_code, 404, name)

    def test_51_page_text_is_escaped(self):
        analysis = exchange.load_analysis(VIDEO_ID)
        saved = analysis["title"]
        analysis["title"] = "<script>alert(1)</script>"
        exchange.save_analysis(VIDEO_ID, analysis)
        try:
            html = self.page()
            self.assertNotIn("<script>alert(1)</script>", html)
            self.assertIn("&lt;script&gt;", html)
        finally:
            analysis["title"] = saved
            exchange.save_analysis(VIDEO_ID, analysis)


if __name__ == "__main__":
    unittest.main()
