"""Tes tombol Render semua: beberapa klip masuk antrean sekaligus."""
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

from tests.helpers import VIDEO_ID, ffmpeg_available, make_test_video, probe, sample_transcript


def _clip(start, end, title):
    return {"title": title, "start_time": start, "end_time": end, "duration": end - start,
            "score": 50, "hook_sentence": f"Hook {title}", "virality_reason": "", "warnings": []}


@unittest.skipIf(zoi_app is None, "app.py tidak bisa diimpor")
class RenderAllTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.saved = (zoi_app.OUTPUT_DIR, exchange.DATA_DIR)
        zoi_app.OUTPUT_DIR = self.folder
        exchange.DATA_DIR = os.path.join(self.folder, "data")
        exchange.save_transcript(sample_transcript())
        exchange.save_analysis(VIDEO_ID, {
            "video_id": VIDEO_ID, "title": "Uji", "url": "https://youtu.be/x",
            "duration": 1624.0, "mode": "zoi", "source_file": "", "source_res": 0,
            "groups": [
                {"name": "ZOI", "kind": "zoi",
                 "clips": [_clip(1, 7, "Satu"), _clip(8, 14, "Dua"), _clip(15, 21, "Tiga")]},
                {"name": "Sendiri", "kind": "zoi", "clips": [_clip(2, 9, "Hanya")]},
            ],
        })
        self.source = os.path.join(self.folder, f"source_{VIDEO_ID}.mp4")
        if ffmpeg_available():
            make_test_video(self.source, seconds=25)
        else:
            open(self.source, "wb").close()
        self.client = zoi_app.app.test_client()

    def tearDown(self):
        zoi_app.OUTPUT_DIR, exchange.DATA_DIR = self.saved
        shutil.rmtree(self.folder, ignore_errors=True)

    def clips(self, *ranges, **extra):
        return [dict({"video_id": VIDEO_ID, "title": f"Klip {i}", "start_time": str(a),
                      "end_time": str(b), "quality": "240", "frame": "wide",
                      "hook_on": "0", "intro_on": "0"}, **extra)
                for i, (a, b) in enumerate(ranges, 1)]

    def test_button_only_for_groups_with_several_clips(self):
        html = self.client.get(f"/video/{VIDEO_ID}").get_data(as_text=True)
        self.assertEqual(html.count('class="btn secondary render-all"'), 1)
        self.assertIn("Render semua (3 klip)", html)

    @unittest.skipUnless(ffmpeg_available(), "FFmpeg tidak terpasang")
    def test_all_clips_are_rendered_one_after_another(self):
        response = self.client.post("/render/batch", json=self.clips((1, 7), (8, 14), (15, 21)))
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(len(data["jobs"]), 3)
        self.assertEqual(data["errors"], [])

        ids = [job["job_id"] for job in data["jobs"]]
        page = self.client.get(
            f"/render/batch/view?jobs={','.join(ids)}&video={VIDEO_ID}&t=Klip+1&t=Klip+2&t=Klip+3"
        ).get_data(as_text=True)
        self.assertEqual(page.count('class="card batch-job"'), 3)
        self.assertIn("Klip 2", page)

        deadline = time.time() + 180
        results = {}
        running_at_once = 0
        while time.time() < deadline and len(results) < 3:
            states = [self.client.get(f"/progress/{i}").get_json() for i in ids]
            running_at_once = max(running_at_once, sum(s["status"] == "running" for s in states))
            for job_id, state in zip(ids, states):
                if state["status"] in ("done", "error"):
                    results[job_id] = state
            time.sleep(0.1)

        self.assertEqual([results[i]["status"] for i in ids], ["done"] * 3)
        self.assertLessEqual(running_at_once, 1)
        names = {results[i]["filename"] for i in ids}
        self.assertEqual(len(names), 3)
        for name in names:
            video = probe(os.path.join(self.folder, name))["video"]
            self.assertEqual((video["width"], video["height"]), (240, 426))

    def test_bad_clips_are_reported_and_good_ones_still_start(self):
        clips = self.clips((1, 7)) + [{"video_id": VIDEO_ID, "start_time": "9", "end_time": "5"}, "x"]
        data = self.client.post("/render/batch", json=clips).get_json()
        self.assertEqual(len(data["jobs"]), 1)
        self.assertEqual([e["index"] for e in data["errors"]], [2, 3])
        self.assertIn("tidak valid", data["errors"][0]["error"])

    def test_nothing_valid_or_too_many(self):
        self.assertEqual(self.client.post("/render/batch", json=[]).status_code, 400)
        self.assertEqual(self.client.post("/render/batch", data="bukan json").status_code, 400)
        bad = [{"video_id": "x", "start_time": "1", "end_time": "9"}]
        self.assertEqual(self.client.post("/render/batch", json=bad).status_code, 400)
        many = self.clips(*[(1, 7)] * (zoi_app.MAX_BATCH + 1))
        response = self.client.post("/render/batch", json=many)
        self.assertEqual(response.status_code, 400)
        self.assertIn("Paling banyak", response.get_json()["error"])

    def test_view_ignores_bad_ids(self):
        page = self.client.get("/render/batch/view?jobs=../x,abc,%3Cb%3E&video=../y").get_data(as_text=True)
        self.assertEqual(page.count('class="card batch-job"'), 0)
        self.assertNotIn("Kembali ke daftar kandidat", page)


if __name__ == "__main__":
    unittest.main()
