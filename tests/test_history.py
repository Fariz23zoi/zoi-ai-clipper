"""Tes hapus riwayat: satu video atau semuanya."""
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

from tests.helpers import sample_transcript


@unittest.skipIf(zoi_app is None, "app.py tidak bisa diimpor")
class HistoryTest(unittest.TestCase):
    VIDEOS = ("VIDEOSATU01", "VIDEODUA002")

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.outside = tempfile.mkdtemp()
        self.saved = (zoi_app.OUTPUT_DIR, exchange.DATA_DIR)
        zoi_app.OUTPUT_DIR = self.folder
        exchange.DATA_DIR = os.path.join(self.folder, "data")

        self.film = os.path.join(self.outside, "film.mp4")
        self.touch(self.film)
        for video_id in self.VIDEOS:
            transcript = dict(sample_transcript(), video_id=video_id)
            exchange.save_transcript(transcript)
            exchange.save_analysis(video_id, {
                "video_id": video_id, "title": video_id, "duration": 100.0,
                "local_path": self.film, "updated_at": time.time(),
                "groups": [{"name": "CLAUDE (IMPOR)", "kind": "import", "clips": []}],
            })
            for name in (f"source_{video_id}.mp4", f"audio_{video_id}.webm",
                         f"youtube_transcript_{video_id}.id.vtt",
                         f"zoi_{video_id}_1-9_720p_klip_abcdef.mp4",
                         f"zoi_{video_id}_1-9_720p_klip_abcdef_thumb.jpg"):
                self.touch(os.path.join(self.folder, name))
        # Video lain yang ID-nya mirip, dan file yang bukan milik ZOI.
        self.touch(os.path.join(self.folder, "source_VIDEOSATU01X.mp4"))
        self.touch(os.path.join(self.folder, "catatan.txt"))
        self.client = zoi_app.app.test_client()

    def tearDown(self):
        zoi_app.OUTPUT_DIR, exchange.DATA_DIR = self.saved
        shutil.rmtree(self.folder, ignore_errors=True)
        shutil.rmtree(self.outside, ignore_errors=True)
        for jobs in (zoi_app.RENDER_JOBS, zoi_app.ANALYZE_JOBS):
            jobs.pop("sibuk", None)

    @staticmethod
    def touch(path):
        with open(path, "wb") as file:
            file.write(b"x")

    def files(self):
        return sorted(n for n in os.listdir(self.folder) if n != "data")

    def test_buttons_are_shown(self):
        home = self.client.get("/").get_data(as_text=True)
        self.assertIn("Hapus semua riwayat", home)
        page = self.client.get(f"/video/{self.VIDEOS[0]}").get_data(as_text=True)
        self.assertIn("Hapus video ini", page)

    def test_delete_one_video_keeps_renders_by_default(self):
        response = self.client.post(f"/video/{self.VIDEOS[0]}/delete")
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(exchange.load_analysis(self.VIDEOS[0]))
        self.assertIsNone(exchange.load_transcript(self.VIDEOS[0]))
        self.assertIsNotNone(exchange.load_analysis(self.VIDEOS[1]))
        files = self.files()
        self.assertNotIn(f"source_{self.VIDEOS[0]}.mp4", files)
        self.assertNotIn(f"audio_{self.VIDEOS[0]}.webm", files)
        self.assertNotIn(f"youtube_transcript_{self.VIDEOS[0]}.id.vtt", files)
        self.assertIn(f"zoi_{self.VIDEOS[0]}_1-9_720p_klip_abcdef.mp4", files)
        self.assertIn("source_VIDEOSATU01X.mp4", files)
        self.assertTrue(os.path.isfile(self.film))
        home = self.client.get(response.headers["Location"]).get_data(as_text=True)
        self.assertIn("sudah dihapus dari riwayat", home)

    def test_delete_one_video_with_renders(self):
        self.client.post(f"/video/{self.VIDEOS[0]}/delete", data={"renders": "1"})
        files = self.files()
        self.assertFalse([n for n in files if n.startswith(f"zoi_{self.VIDEOS[0]}_")])
        self.assertTrue([n for n in files if n.startswith(f"zoi_{self.VIDEOS[1]}_")])

    def test_clear_all_keeps_renders_by_default(self):
        response = self.client.post("/history/clear")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(exchange.list_videos(), [])
        files = self.files()
        self.assertFalse([n for n in files if n.startswith(("source_", "audio_", "youtube_transcript_"))])
        self.assertEqual(len([n for n in files if n.startswith("zoi_")]), 4)
        self.assertIn("catatan.txt", files)
        self.assertTrue(os.path.isfile(self.film))
        home = self.client.get(response.headers["Location"]).get_data(as_text=True)
        self.assertIn("Klip hasil render tetap ada", home)
        self.assertNotIn("Video yang sudah dianalisis", home)

    def test_clear_all_with_renders(self):
        self.client.post("/history/clear", data={"renders": "1"})
        self.assertEqual(self.files(), ["catatan.txt"])
        self.assertTrue(os.path.isfile(self.film))
        home = self.client.get("/?cleared=all_renders").get_data(as_text=True)
        self.assertIn("klip hasil render sudah dihapus", home)
        self.assertNotIn("Hapus semua riwayat</button>", home)

    def test_refused_while_something_is_running(self):
        zoi_app.ANALYZE_JOBS["sibuk"] = {"status": "running", "error": "", "created_at": time.time()}
        response = self.client.post("/history/clear", data={"renders": "1"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("Masih ada", response.get_data(as_text=True))
        self.assertEqual(len(exchange.list_videos()), 2)

    def test_one_video_refused_while_it_renders(self):
        zoi_app.RENDER_JOBS["sibuk"] = {"status": "running", "error": "", "created_at": time.time(),
                                        "video_id": self.VIDEOS[0]}
        response = self.client.post(f"/video/{self.VIDEOS[0]}/delete")
        self.assertEqual(response.status_code, 409)
        self.assertIsNotNone(exchange.load_analysis(self.VIDEOS[0]))

    def test_unknown_video(self):
        self.assertEqual(self.client.post("/video/tidakada/delete").status_code, 404)


if __name__ == "__main__":
    unittest.main()
