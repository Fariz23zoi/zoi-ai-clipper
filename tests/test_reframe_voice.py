"""Tes bingkai otomatis (ikuti pembicara), suara VoiceStudio, dan pengurang
bass suara pria."""
import http.server
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import wave

from shorts_generator.local import clipper, reframe, voiceover

from tests.helpers import VIDEO_ID, ffmpeg_available

try:
    import numpy
except ImportError:
    numpy = None

try:
    import app as zoi_app
    from shorts_generator import exchange
except ImportError:
    zoi_app = None


def _tone(path, seconds=1.0, rate=24000):
    import math
    import struct
    with wave.open(path, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(b"".join(
            struct.pack("<h", int(9000 * math.sin(2 * math.pi * 300 * i / rate)))
            for i in range(int(seconds * rate))
        ))
    return path


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ------------------------------------------------------------ rencana bingkai

class SampleViewTest(unittest.TestCase):
    """Keputusan untuk satu gambar 320x180."""

    def test_no_face(self):
        self.assertEqual(reframe._sample_view([], 320, 180), ("none", None))

    def test_one_face_on_the_right(self):
        kind, x = reframe._sample_view([(258, 75, 47, 47)], 320, 180)
        self.assertEqual(kind, "square")
        self.assertEqual(x, 1.0)

    def test_one_face_in_the_middle(self):
        kind, x = reframe._sample_view([(136, 60, 48, 48)], 320, 180)
        self.assertEqual(kind, "square")
        self.assertAlmostEqual(x, 0.5, places=2)

    def test_two_people_far_apart_is_wide(self):
        self.assertEqual(
            reframe._sample_view([(40, 70, 40, 40), (240, 70, 40, 40)], 320, 180),
            ("wide", None),
        )

    def test_two_people_close_together_stay_square(self):
        kind, x = reframe._sample_view([(110, 70, 40, 40), (170, 70, 40, 40)], 320, 180)
        self.assertEqual(kind, "square")
        self.assertAlmostEqual(x, 0.5, places=1)

    def test_duplicate_box_inside_a_face_counts_once(self):
        kind, _ = reframe._sample_view([(200, 50, 90, 90), (230, 80, 30, 30)], 320, 180)
        self.assertEqual(kind, "square")

    def test_tiny_background_faces_are_ignored(self):
        kind, x = reframe._sample_view([(20, 70, 50, 50), (290, 20, 10, 10)], 320, 180)
        self.assertEqual(kind, "square")
        self.assertEqual(x, 0.0)


@unittest.skipUnless(numpy, "numpy tidak terpasang")
class PlanTest(unittest.TestCase):
    """Rencana per shot dari gambar buatan dan pendeteksi palsu."""

    def frames(self, shots):
        """shots: [(kecerahan, jumlah gambar, wajah)] -> (gambar, pendeteksi)."""
        images, faces = [], {}
        for level, count, found in shots:
            for _ in range(count):
                image = numpy.full((180, 320), level, dtype=numpy.uint8)
                faces[id(image)] = found
                images.append(image)
        return images, lambda gray: faces[id(gray)]

    def test_right_then_wide_then_left(self):
        right, both, left = [(258, 75, 47, 47)], [(20, 70, 45, 45), (250, 70, 45, 45)], [(10, 75, 47, 47)]
        images, detect = self.frames([(40, 8, right), (120, 8, both), (200, 8, left)])
        plan = reframe.plan_from_frames(images, detect, duration=12)
        self.assertEqual(
            [(p["start"], p["end"], p["frame"], p["x"]) for p in plan],
            [(0.0, 4.0, "square", 1.0), (4.0, 8.0, "wide", 0.5), (8.0, 12, "square", 0.0)],
        )

    def test_short_shot_follows_the_previous_one(self):
        right = [(258, 75, 47, 47)]
        images, detect = self.frames([(40, 6, right), (120, 1, []), (200, 6, right)])
        plan = reframe.plan_from_frames(images, detect, duration=6.5)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["frame"], "square")

    def test_short_shot_with_its_own_speaker_keeps_its_position(self):
        left, right = [(10, 75, 47, 47)], [(258, 75, 47, 47)]
        images, detect = self.frames([(40, 8, left), (200, 2, right)])
        plan = reframe.plan_from_frames(images, detect, duration=5)
        self.assertEqual([(p["frame"], p["x"]) for p in plan], [("square", 0.0), ("square", 1.0)])

    def test_shot_without_faces_is_wide(self):
        images, detect = self.frames([(40, 6, [])])
        plan = reframe.plan_from_frames(images, detect, duration=3)
        self.assertEqual(plan, [{"start": 0.0, "end": 3, "frame": "wide", "x": 0.5}])

    def test_occasional_misses_inside_a_shot_are_tolerated(self):
        right = [(258, 75, 47, 47)]
        images, detect = self.frames([(40, 5, right), (40, 2, []), (40, 3, right)])
        plan = reframe.plan_from_frames(images, detect, duration=5)
        self.assertEqual([(p["frame"], p["x"]) for p in plan], [("square", 1.0)])

    def test_describe(self):
        self.assertIn("16:9 penuh", reframe.describe([{"start": 0, "end": 5, "frame": "wide", "x": 0.5}]))
        self.assertIn("seluruh klip dipotong 1:1", reframe.describe([{"start": 0, "end": 5, "frame": "square", "x": 1}]))
        self.assertIn("3 bagian", reframe.describe([
            {"start": 0, "end": 4, "frame": "square", "x": 1},
            {"start": 4, "end": 8, "frame": "wide", "x": 0.5},
            {"start": 8, "end": 12, "frame": "square", "x": 0},
        ]))


class AutoFilterTest(unittest.TestCase):
    PLAN = [
        {"start": 0.0, "end": 4.0, "frame": "square", "x": 1.0},
        {"start": 4.0, "end": 8.0, "frame": "wide", "x": 0.5},
        {"start": 8.0, "end": 12.0, "frame": "square", "x": 0.0},
    ]

    def test_position_expression(self):
        self.assertEqual(
            clipper._segments_expression(self.PLAN, lambda p: p["x"]),
            "if(lt(t,4.000),1.000,if(lt(t,8.000),0.500,0.000))",
        )

    def test_layers_are_switched_by_time(self):
        graph = clipper._auto_foreground(360, 640, self.PLAN)
        self.assertIn("enable='gte(t,4.000)*lt(t,8.000)'", graph)
        self.assertIn("enable='not(gte(t,4.000)*lt(t,8.000))'", graph)
        self.assertIn("scale=360:360", graph)

    def test_single_part_becomes_a_plain_frame(self):
        self.assertEqual(clipper._simplify_plan([self.PLAN[0]], 0.5), ("square", 1.0, None))
        self.assertEqual(clipper._simplify_plan([], 0.3), ("wide", 0.3, None))
        self.assertEqual(clipper._simplify_plan(self.PLAN, 0.5)[0], "auto")

    def test_auto_is_a_frame(self):
        self.assertIn("auto", clipper.RENDER_FRAMES)


def _two_colour_video(path, seconds=12):
    """1280x720: separuh kiri merah, separuh kanan biru."""
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c=red:s=1280x720:r=25:d={seconds}",
        "-f", "lavfi", "-i", f"sine=f=440:d={seconds}",
        "-vf", "drawbox=x=640:y=0:w=640:h=720:color=blue:t=fill",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path,
    ], check=True)
    return path


def _pixel(path, second, x, y):
    out = subprocess.run([
        "ffmpeg", "-v", "error", "-ss", f"{second}", "-i", path, "-frames:v", "1",
        "-vf", f"format=rgb24,crop=1:1:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ], capture_output=True, check=True).stdout
    return tuple(out[:3])


@unittest.skipUnless(ffmpeg_available(), "FFmpeg tidak terpasang")
class AutoRenderTest(unittest.TestCase):
    """Render nyata dengan rencana yang ditentukan (tanpa OpenCV)."""

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp()
        cls.source = _two_colour_video(os.path.join(cls.folder, "warna.mp4"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, ignore_errors=True)

    def test_square_right_then_wide(self):
        plan = [
            {"start": 0.0, "end": 4.0, "frame": "square", "x": 1.0},
            {"start": 4.0, "end": 8.0, "frame": "wide", "x": 0.5},
        ]
        target = os.path.join(self.folder, "auto.mp4")
        clipper._render_vertical_single_pass(
            self.source, 0, 8, target, size=(360, 640), frame="auto", plan=plan
        )
        # Titik 1/4 lebar kanvas, di tengah tinggi.
        square = _pixel(target, 1.0, 90, 320)
        wide = _pixel(target, 6.0, 90, 320)
        self.assertGreater(square[2], 150, square)   # 1:1 kanan: biru
        self.assertLess(square[0], 100, square)
        self.assertGreater(wide[0], 150, wide)       # 16:9 penuh: kiri merah
        self.assertLess(wide[2], 100, wide)
        # Di 16:9, bagian atas kanvas (y=200) adalah latar blur, bukan video;
        # di 1:1 (360 tinggi, mulai y=140) y=200 masih video biru tajam.
        self.assertGreater(_pixel(target, 1.0, 270, 200)[2], 150)

    def test_auto_without_opencv_falls_back_to_wide_with_a_note(self):
        saved = reframe.available
        reframe.available = lambda: False
        try:
            info = {}
            target = os.path.join(self.folder, "tanpa_cv.mp4")
            clipper.crop_clip_local(
                self.source, 0, 3, "9:16", target, size=(240, 426), frame="auto", info=info
            )
        finally:
            reframe.available = saved
        self.assertIn("OpenCV", info["frame_note"])
        self.assertIsNone(info["frame_plan"])
        self.assertGreater(_pixel(target, 1.0, 60, 213)[0], 150)

    def test_auto_with_a_plan_from_the_detector(self):
        saved = reframe.plan_auto_frame
        calls = []

        def fake_plan(source, start, end, aspect, report=None):
            calls.append((start, end, round(aspect, 2)))
            return [{"start": 0.0, "end": end - start, "frame": "square", "x": 0.0}]

        reframe.plan_auto_frame = fake_plan
        try:
            info = {}
            target = os.path.join(self.folder, "rencana.mp4")
            clipper.crop_clip_local(
                self.source, 2, 5, "9:16", target, size=(240, 426), frame="auto",
                hook_text="Tes hook", info=info
            )
        finally:
            reframe.plan_auto_frame = saved
        self.assertEqual(calls, [(2, 5, 1.78)])
        self.assertIn("1:1", info["frame_note"])
        self.assertGreater(_pixel(target, 1.0, 180, 213)[0], 150)  # 1:1 kiri: merah


@unittest.skipUnless(ffmpeg_available() and numpy, "FFmpeg atau numpy tidak ada")
class RefineCutTest(unittest.TestCase):
    """Batas antarbagian dipindah ke gambar ganti kamera yang sebenarnya."""

    def test_cut_between_samples(self):
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "potong.mp4")
            # 30 fps: merah sampai 2,3 detik, lalu biru.
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=red:s=320x180:r=30:d=2.3",
                "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=30:d=2.7",
                "-filter_complex", "[0][1]concat=n=2:v=1", "-c:v", "libx264",
                "-pix_fmt", "yuv420p", source,
            ], check=True)
            for start in (0.0, 0.4, 1.1):
                cut = 2.3 - start
                rough = round((cut + 0.25) * 2) / 2  # seperti sampel 2 per detik
                plan = [
                    {"start": 0.0, "end": rough, "frame": "square", "x": 0.0},
                    {"start": rough, "end": 4.0 - start, "frame": "square", "x": 1.0},
                ]
                reframe.refine_cuts(plan, source, start, 16 / 9)
                boundary = plan[1]["start"]
                # Gambar terakhir merah < batas <= gambar pertama biru.
                self.assertLess(cut - 1 / 30, boundary, (start, plan))
                self.assertLessEqual(boundary, cut + 0.001, (start, plan))
                self.assertEqual(plan[0]["end"], boundary)

    def test_no_change_keeps_the_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "polos.mp4")
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                "-i", "color=c=gray:s=320x180:r=30:d=4", "-c:v", "libx264",
                "-pix_fmt", "yuv420p", source,
            ], check=True)
            plan = [{"start": 0.0, "end": 2.0, "frame": "square", "x": 0.0},
                    {"start": 2.0, "end": 4.0, "frame": "wide", "x": 0.5}]
            reframe.refine_cuts(plan, source, 0, 16 / 9)
            self.assertEqual(plan[1]["start"], 2.0)


@unittest.skipUnless(
    reframe.available() and ffmpeg_available() and os.getenv("ZOI_TEST_FACE_VIDEO"),
    "Isi ZOI_TEST_FACE_VIDEO dengan video uji berisi wajah",
)
class RealFaceTest(unittest.TestCase):
    """Uji dengan OpenCV sungguhan. Video uji: 12 detik, 1280x720,
    satu wajah di kanan (0-4 s), dua wajah berjauhan (4-8 s), satu wajah
    di kiri (8-12 s)."""

    def test_follows_the_speaker(self):
        plan = reframe.plan_auto_frame(os.environ["ZOI_TEST_FACE_VIDEO"], 0, 12, 16 / 9)
        self.assertEqual([p["frame"] for p in plan], ["square", "wide", "square"])
        self.assertGreater(plan[0]["x"], 0.8)
        self.assertLess(plan[2]["x"], 0.2)
        # Ganti kamera tepat di detik 4 dan 8 (video 30 fps).
        self.assertAlmostEqual(plan[1]["start"], 4.0, delta=1 / 30)
        self.assertAlmostEqual(plan[2]["start"], 8.0, delta=1 / 30)


# ------------------------------------------------------------- VoiceStudio

class _FakeStudio(http.server.BaseHTTPRequestHandler):
    status = 200
    requests = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append((self.path, body, dict(self.headers)))
        if type(self).status != 200:
            self.send_response(type(self).status)
            self.end_headers()
            self.wfile.write(b'{"error":"voice not found"}')
            return
        with tempfile.TemporaryDirectory() as folder:
            data = open(_tone(os.path.join(folder, "a.wav"), 1.4), "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@unittest.skipUnless(ffmpeg_available(), "FFmpeg tidak terpasang")
class VoiceStudioTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.saved = (voiceover.VOICESTUDIO_URL, voiceover.ENGINE,
                      voiceover.edge_available, voiceover._edge_synthesize)
        _FakeStudio.status = 200
        _FakeStudio.requests = []
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeStudio)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        voiceover.VOICESTUDIO_URL = f"http://127.0.0.1:{self.server.server_address[1]}"
        voiceover.ENGINE = "auto"
        self.edge_calls = []

        def fake_edge(spoken, wav_path, gender):
            self.edge_calls.append(gender)
            _tone(wav_path, 0.8)

        voiceover._edge_synthesize = fake_edge
        voiceover.edge_available = lambda: True

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        (voiceover.VOICESTUDIO_URL, voiceover.ENGINE,
         voiceover.edge_available, voiceover._edge_synthesize) = self.saved
        shutil.rmtree(self.folder, ignore_errors=True)

    def say(self, gender="pria"):
        return voiceover.synthesize_voice(
            "Kok bisa Rp800.000?", os.path.join(self.folder, "v.wav"), gender=gender
        )

    def test_running_studio_is_used_first(self):
        self.assertTrue(voiceover.voicestudio_running())
        result = self.say("pria")
        self.assertEqual(result["engine"], "voicestudio")
        self.assertIn("VoiceStudio", result["note"])
        self.assertAlmostEqual(result["seconds"], 1.4, delta=0.05)
        self.assertEqual(self.edge_calls, [])
        path, body, headers = _FakeStudio.requests[0]
        self.assertEqual(path, "/v1/audio/speech")
        self.assertEqual(body, {
            "model": "omnivoice", "input": "Kok bisa 800.000 rupiah?",
            "voice": "pria", "response_format": "wav", "language": "id",
        })
        self.assertNotIn("Authorization", headers)

    def test_female_profile(self):
        self.say("wanita")
        self.assertEqual(_FakeStudio.requests[0][1]["voice"], "wanita")

    def test_failure_falls_back_to_edge_with_a_note(self):
        _FakeStudio.status = 422
        result = self.say("pria")
        self.assertEqual(result["engine"], "edge")
        self.assertIn("VoiceStudio gagal", result["note"])
        self.assertIn("voice not found", result["note"])
        self.assertEqual(self.edge_calls, ["pria"])

    def test_closed_studio_is_skipped(self):
        voiceover.VOICESTUDIO_URL = f"http://127.0.0.1:{_free_port()}"
        self.assertFalse(voiceover.voicestudio_running())
        result = self.say("pria")
        self.assertEqual(result["engine"], "edge")
        self.assertEqual(result["note"], "")

    def test_studio_only_mode_reports_the_error(self):
        voiceover.ENGINE = "voicestudio"
        voiceover.VOICESTUDIO_URL = f"http://127.0.0.1:{_free_port()}"
        self.assertFalse(voiceover.is_available())
        with self.assertRaises(RuntimeError) as caught:
            self.say()
        self.assertIn("VoiceStudio", str(caught.exception))
        self.assertEqual(self.edge_calls, [])


# ------------------------------------------------------- bass suara pria

class MaleVoiceTest(unittest.TestCase):
    def test_settings(self):
        self.assertEqual(voiceover.EDGE_PITCH["pria"], "+10Hz")
        self.assertEqual(voiceover.EDGE_PITCH["wanita"], "+0Hz")
        self.assertEqual(voiceover.PIPER_PITCH["pria"], 0.72)
        self.assertIn("highpass", voiceover._voice_filter("pria"))
        self.assertEqual(voiceover._voice_filter("wanita"), "")

    @unittest.skipUnless(ffmpeg_available() and numpy, "FFmpeg atau numpy tidak ada")
    def test_filter_reduces_low_frequencies(self):
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "campur.wav")
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "sine=f=80:d=2", "-f", "lavfi", "-i", "sine=f=1000:d=2",
                "-filter_complex", "amix=inputs=2", source,
            ], check=True)
            target = os.path.join(folder, "pria.wav")
            voiceover._to_wav(source, target, voiceover._voice_filter("pria"))

            def low_share(path):
                raw = subprocess.run(
                    ["ffmpeg", "-v", "error", "-i", path, "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
                    capture_output=True, check=True).stdout
                x = numpy.frombuffer(raw, numpy.float32)
                power = numpy.abs(numpy.fft.rfft(x)) ** 2
                freq = numpy.fft.rfftfreq(len(x), 1 / 16000)
                return power[freq < 200].sum() / power.sum()

            self.assertLess(low_share(target), low_share(source) / 3)


# ------------------------------------------------------------- dashboard

@unittest.skipUnless(zoi_app is not None, "app.py tidak bisa diimpor")
class DashboardTest(unittest.TestCase):
    def setUp(self):
        from tests.helpers import sample_transcript
        self.folder = tempfile.mkdtemp()
        self.saved = (zoi_app.OUTPUT_DIR, exchange.DATA_DIR, reframe.plan_auto_frame)
        zoi_app.OUTPUT_DIR = self.folder
        exchange.DATA_DIR = os.path.join(self.folder, "data")
        exchange.save_transcript(sample_transcript())
        exchange.save_analysis(VIDEO_ID, {
            "video_id": VIDEO_ID, "title": "Uji", "url": "", "duration": 1624.0,
            "mode": "zoi", "source_file": "", "source_res": 720,
            "groups": [{"name": "ZOI", "kind": "zoi", "clips": [{
                "title": "Klip", "start_time": 1.0, "end_time": 9.0, "duration": 8.0,
                "score": 50, "hook_sentence": "Kok bisa?", "virality_reason": "",
                "warnings": []}]}],
        })
        if ffmpeg_available():
            _two_colour_video(os.path.join(self.folder, f"source_{VIDEO_ID}.mp4"))
        self.client = zoi_app.app.test_client()

    def tearDown(self):
        (zoi_app.OUTPUT_DIR, exchange.DATA_DIR, reframe.plan_auto_frame) = self.saved
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_page_offers_the_auto_frame_and_hook_off_by_default(self):
        html = self.client.get(f"/video/{VIDEO_ID}").get_data(as_text=True)
        self.assertIn('name="frame-choice" value="auto"', html)
        self.assertIn("Otomatis", html)
        self.assertIn('name="hook-choice" value="0" checked', html)
        self.assertIn("hilang begitu suara pembaca selesai", html)
        self.assertIn('"zoi-blok-hook-2"', html)

    def test_unknown_frame_is_still_refused(self):
        response = self.client.post("/select", data={
            "video_id": VIDEO_ID, "start_time": "1", "end_time": "9", "frame": "bulat"})
        self.assertEqual(response.status_code, 400)

    def test_auto_needs_a_source_like_square(self):
        self.assertEqual(
            zoi_app._needed_source_res_for(720, 16 / 9, "auto"),
            zoi_app._needed_source_res_for(720, 16 / 9, "square"),
        )

    @unittest.skipUnless(ffmpeg_available(), "FFmpeg tidak terpasang")
    def test_auto_render_reports_the_plan(self):
        reframe.plan_auto_frame = lambda source, start, end, aspect, report=None: [
            {"start": 0.0, "end": 4.0, "frame": "square", "x": 1.0},
            {"start": 4.0, "end": end - start, "frame": "wide", "x": 0.5},
        ]
        response = self.client.post("/select", data={
            "video_id": VIDEO_ID, "title": "Klip", "start_time": "1", "end_time": "9",
            "quality": "240", "frame": "auto", "hook_on": "0", "intro_on": "0"})
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Bingkai otomatis", html)
        job = html.split("/progress/")[1].split('"')[0]
        deadline = time.time() + 120
        while time.time() < deadline:
            status = self.client.get(f"/progress/{job}").get_json()
            if status["status"] in ("done", "error"):
                break
            time.sleep(0.1)
        self.assertEqual(status["status"], "done", status)
        self.assertIn("_auto_", status["filename"])
        self.assertIn("2 bagian", status["notice"])
        target = os.path.join(self.folder, status["filename"])
        self.assertGreater(_pixel(target, 1.0, 60, 213)[2], 150)   # 1:1 kanan: biru
        self.assertGreater(_pixel(target, 6.0, 60, 213)[0], 150)   # 16:9: kiri merah

    def test_batch_page_has_a_preview_player(self):
        html = self.client.get("/render/batch/view?jobs=abc&t=Klip").get_data(as_text=True)
        self.assertIn('document.createElement("video")', html)

    @unittest.skipUnless(ffmpeg_available(), "FFmpeg tidak terpasang")
    def test_render_lists_have_a_preview_player(self):
        name = f"zoi_{VIDEO_ID}_1-9_240p_Klip_abcdef.mp4"
        shutil.copyfile(os.path.join(self.folder, f"source_{VIDEO_ID}.mp4"),
                        os.path.join(self.folder, name))
        for page in ("/", f"/video/{VIDEO_ID}"):
            html = self.client.get(page).get_data(as_text=True)
            self.assertIn("Putar pratinjau", html, page)
            self.assertIn(f'<video controls playsinline preload="none" src="/output/{name}"', html, page)



class LauncherTest(unittest.TestCase):
    def test_opencv_is_installed_but_not_required(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        self.assertIn("opencv-python-headless", (root / "requirements-reframe.txt").read_text(encoding="utf-8"))
        for name in ("mulai_windows.bat", "mulai.sh"):
            text = (root / name).read_text(encoding="utf-8")
            self.assertIn("requirements-reframe.txt", text, name)
            self.assertIn("fitur lain tetap jalan", text, name)
        # Termux: tetap hanya kebutuhan inti.
        self.assertNotIn("opencv", (root / "requirements-termux.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
