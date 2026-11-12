"""Tes halaman Klip film: link atau file film di laptop, ditambah file .srt."""
import io
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

from tests.helpers import ffmpeg_available, make_test_video, probe


def _clock(seconds):
    ms = int(round(seconds * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _srt(seconds, start=0.0, step=4.0):
    lines = [
        "Kenapa kamu kembali ke sini?", "Ternyata semua ini rencana dia!",
        "Serius? Aku tidak percaya.", "Akhirnya kita berhasil keluar.",
        "Masalahnya mereka tidak bisa dihentikan.", "Hahaha beneran gila.",
    ]
    out, t, i = [], start, 0
    while t + 3 <= seconds:
        out.append(f"{i + 1}\n{_clock(t)} --> {_clock(t + 3.5)}\n{lines[i % len(lines)]} ayo pergi sekarang juga\n\n")
        t += step
        i += 1
    return "".join(out)


@unittest.skipIf(zoi_app is None, "app.py tidak bisa diimpor")
class FilmTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.films = tempfile.mkdtemp()
        cls.film = os.path.join(cls.films, "Film Uji (2026).mp4")
        if ffmpeg_available():
            make_test_video(cls.film, seconds=180)
        else:
            open(cls.film, "wb").close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.films, ignore_errors=True)

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.saved = (
            zoi_app.OUTPUT_DIR, exchange.DATA_DIR, zoi_app.fetch_video_metadata,
            zoi_app.transcribe_from_youtube, zoi_app.download_youtube_local,
        )
        zoi_app.OUTPUT_DIR = self.folder
        exchange.DATA_DIR = os.path.join(self.folder, "data")
        self.metadata = {"video_id": "abc/123:xyz", "title": "Film Online",
                         "url": "https://contoh.com/film/1", "duration": 180.0,
                         "width": 1280, "height": 720}
        self.metadata_error = None
        self.downloads = []
        test = self

        def fake_metadata(url):
            if test.metadata_error:
                raise RuntimeError(test.metadata_error)
            return dict(test.metadata)

        def no_subtitles(url):
            raise AssertionError("subtitle YouTube tidak boleh diambil untuk film")

        def fake_download(url, fmt="720", out_dir=None, progress_hook=None):
            test.downloads.append(url)
            path = os.path.join(out_dir, "source_online.mp4")
            shutil.copyfile(test.film, path)
            return path

        zoi_app.fetch_video_metadata = fake_metadata
        zoi_app.transcribe_from_youtube = no_subtitles
        zoi_app.download_youtube_local = fake_download
        self.client = zoi_app.app.test_client()

    def tearDown(self):
        (zoi_app.OUTPUT_DIR, exchange.DATA_DIR, zoi_app.fetch_video_metadata,
         zoi_app.transcribe_from_youtube, zoi_app.download_youtube_local) = self.saved
        shutil.rmtree(self.folder, ignore_errors=True)

    def post(self, subtitle=None, encoding="utf-8", **form):
        data = {"mode": "zoi", "film_url": "", "film_path": "", "shift": "0"}
        data.update(form)
        if subtitle is not None:
            data["subtitle_file"] = (io.BytesIO(subtitle.encode(encoding)), "film.srt")
        return self.client.post("/film/proses", data=data, content_type="multipart/form-data")

    def run_job(self, response):
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:500])
        job = response.get_data(as_text=True).split("/analyze/status/")[1].split('"')[0]
        deadline = time.time() + 60
        while time.time() < deadline:
            data = self.client.get(f"/analyze/status/{job}").get_json()
            if data["status"] in ("done", "error"):
                return data
            time.sleep(0.05)
        self.fail(data)

    def wait_render(self, html):
        job = html.split("/progress/")[1].split('"')[0]
        deadline = time.time() + 120
        while time.time() < deadline:
            data = self.client.get(f"/progress/{job}").get_json()
            if data["status"] in ("done", "error"):
                return data
            time.sleep(0.1)
        self.fail(data)

    # ---- halaman

    def test_home_links_to_film_page_and_page_loads(self):
        self.assertIn("/film", self.client.get("/").get_data(as_text=True))
        html = self.client.get("/film").get_data(as_text=True)
        for text in ("Link film", "lokasi file film di laptop", "Copy as path",
                     "Geser waktu subtitle", "hak cipta"):
            self.assertIn(text, html)

    # ---- film di laptop

    @unittest.skipUnless(ffmpeg_available(), "FFmpeg tidak terpasang")
    def test_local_film_is_analysed_and_rendered_without_copying(self):
        data = self.run_job(self.post(_srt(170), film_path=f'"{self.film}"'))
        self.assertEqual(data["status"], "done", data)
        self.assertTrue(data["video_id"].startswith("film_"))
        video_id = data["video_id"]

        transcript = exchange.load_transcript(video_id)
        self.assertEqual(transcript["title"], "Film Uji (2026)")
        self.assertEqual(transcript["url"], "")
        self.assertAlmostEqual(transcript["duration"], 180.0, delta=0.5)
        self.assertEqual(transcript["local_path"], os.path.realpath(self.film))

        page = self.client.get(f"/video/{video_id}").get_data(as_text=True)
        self.assertIn("Memakai file film di laptop", page)
        self.assertNotIn("Hapus video sumber", page)
        self.assertIn("diimpor dari file (film.srt)", page)
        self.assertIn("Render klip ini", page)
        self.assertNotIn("disabled", page.split("Render klip ini")[0][-200:])

        html = self.client.post("/select", data={
            "video_id": video_id, "title": "Adegan", "start_time": "10",
            "end_time": "45", "quality": "480",
        }).get_data(as_text=True)
        result = self.wait_render(html)
        self.assertEqual(result["status"], "done", result)
        video = probe(os.path.join(self.folder, result["filename"]))["video"]
        self.assertEqual((video["width"], video["height"]), (480, 854))
        self.assertAlmostEqual(video["duration"], 35.0, delta=0.5)

        # Film tidak diunduh, tidak disalin, dan tetap utuh.
        self.assertEqual(self.downloads, [])
        self.assertFalse([n for n in os.listdir(self.folder) if n.startswith("source_")])
        self.assertTrue(os.path.isfile(self.film))

    @unittest.skipUnless(ffmpeg_available(), "FFmpeg tidak terpasang")
    def test_subtitle_longer_than_the_film_is_refused(self):
        data = self.run_job(self.post(_srt(900), film_path=self.film))
        self.assertEqual(data["status"], "error")
        self.assertIn("versi film lain", data["error"])

    @unittest.skipUnless(ffmpeg_available(), "FFmpeg tidak terpasang")
    def test_unreadable_film_file_is_explained(self):
        broken = os.path.join(self.folder, "rusak.mp4")
        with open(broken, "wb") as file:
            file.write(b"bukan video")
        data = self.run_job(self.post(_srt(60), film_path=broken))
        self.assertEqual(data["status"], "error")
        self.assertIn("tidak bisa dibaca", data["error"])

    # ---- film dari link

    def test_link_from_another_site(self):
        data = self.run_job(self.post(_srt(170), film_url="https://contoh.com/film/1", mode="claude"))
        self.assertEqual(data["status"], "done", data)
        self.assertEqual(data["video_id"], "abc_123_xyz")
        self.assertEqual(data["next"], "ai")
        transcript = exchange.load_transcript("abc_123_xyz")
        self.assertEqual(transcript["title"], "Film Online")
        self.assertEqual(transcript["url"], "https://contoh.com/film/1")
        self.assertEqual(transcript["local_path"], "")

    def test_youtube_link_keeps_its_id(self):
        url = "https://www.youtube.com/watch?v=FILMYOUTUBE"
        self.metadata.update(video_id="FILMYOUTUBE", url=url)
        data = self.run_job(self.post(_srt(170), film_url=url))
        self.assertEqual(data["video_id"], "FILMYOUTUBE")

    def test_link_without_metadata_still_works(self):
        self.metadata_error = "Unsupported URL"
        data = self.run_job(self.post(_srt(120), film_url="https://contoh.com/x"))
        self.assertEqual(data["status"], "done", data)
        self.assertTrue(data["video_id"].startswith("film_"))

    # ---- subtitle

    def test_shift_moves_every_line_and_drops_negative_times(self):
        data = self.run_job(self.post(_srt(170), film_url="https://contoh.com/film/1", shift="-2.5"))
        transcript = exchange.load_transcript(data["video_id"])
        words = transcript["words"]
        # Hanya kata yang jatuh sebelum detik 0 yang terbuang.
        self.assertGreaterEqual(words[0]["start"], 0)
        self.assertNotEqual(words[0]["text"], "Kenapa")
        self.assertIn("Ternyata", [w["text"] for w in words[:12]])
        later = next(w for w in words if w["text"] == "Ternyata")
        self.assertAlmostEqual(later["start"], 1.5, places=2)
        self.assertGreaterEqual(transcript["segments"][0]["start"], 0)

        data = self.run_job(self.post(_srt(170), film_url="https://contoh.com/film/1", shift="1.5"))
        transcript = exchange.load_transcript(data["video_id"])
        self.assertEqual(transcript["segments"][0]["start"], 1.5)
        self.assertEqual(transcript["words"][0]["text"], "Kenapa")

    def test_windows_1252_subtitles_and_style_tags(self):
        subtitle = _srt(40).replace("Kenapa", "{\\an8}Café, kenapa", 1)
        data = self.run_job(self.post(subtitle, encoding="cp1252", film_url="https://contoh.com/film/1"))
        self.assertEqual(data["status"], "done", data)
        text = " ".join(s["text"] for s in exchange.load_transcript(data["video_id"])["segments"])
        self.assertIn("Café, kenapa", text)
        self.assertNotIn("an8", text)

    # ---- isian salah

    def test_bad_input_is_explained(self):
        cases = [
            ({}, "Isi salah satu"),
            ({"film_url": "https://contoh.com/x", "film_path": self.film}, "Isi salah satu"),
            ({"film_url": "contoh.com"}, "http://"),
            ({"film_path": os.path.join(self.films, "tidakada.mp4")}, "tidak ditemukan"),
            ({"film_path": __file__}, "bukan file video"),
            ({"film_url": "https://contoh.com/x", "shift": "abc"}, "angka"),
            ({"film_url": "https://contoh.com/x", "shift": "9999"}, "paling banyak"),
            ({"film_url": "https://contoh.com/x", "mode": "aneh"}, "Mode analisis"),
        ]
        for form, message in cases:
            response = self.post(_srt(60), **form)
            self.assertEqual(response.status_code, 400, form)
            self.assertIn(message, response.get_data(as_text=True), form)

        response = self.post(None, film_url="https://contoh.com/x")
        self.assertIn("Pilih file subtitle", response.get_data(as_text=True))
        response = self.post("teks tanpa waktu", film_url="https://contoh.com/x")
        self.assertIn("tidak berisi subtitle bertimestamp", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
