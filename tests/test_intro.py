"""Tes pembuka klip: kartu hook kuning, suara pembaca (Piper), thumbnail."""
import io
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import wave
from types import SimpleNamespace

from shorts_generator.local import clipper, voiceover

from tests.helpers import VIDEO_ID, ffmpeg_available, make_test_video, probe

try:
    import app as zoi_app
    from shorts_generator import exchange
except ImportError:
    zoi_app = None

CAN_RENDER = ffmpeg_available() and bool(clipper.find_card_font())


def _write_tone(path, seconds, rate=22050):
    """WAV mono berisi nada, pengganti suara pembaca."""
    import math
    import struct
    frames = int(seconds * rate)
    with wave.open(path, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(b"".join(
            struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate)))
            for i in range(frames)
        ))
    return path


def _duration(path):
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    ).stdout)


class SpeechTextTest(unittest.TestCase):
    def test_rupiah_and_markers(self):
        self.assertEqual(
            voiceover.speech_text("Kok bisa Rp800.000 [tertawa] 😂"),
            "Kok bisa 800.000 rupiah.",
        )

    def test_sentence_end_is_kept(self):
        self.assertEqual(voiceover.speech_text("Serius?"), "Serius?")
        self.assertEqual(voiceover.speech_text("   "), "")


class FakeVoice:
    def __init__(self):
        self.texts = []

    def synthesize_wav(self, text, wav_file, syn_config=None):
        self.texts.append((text, syn_config))
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22050)
        wav_file.writeframes(b"\x00\x00" * 22050 * 2)


class SynthesizeTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_synthesize_returns_duration_and_reads_clean_text(self):
        voice = FakeVoice()
        seconds = voiceover.synthesize(
            "Harganya Rp5.000 [musik]", os.path.join(self.folder, "a.wav"), voice=voice
        )
        self.assertAlmostEqual(seconds, 2.0)
        self.assertEqual(voice.texts[0][0], "Harganya 5.000 rupiah.")

    def test_empty_text_is_refused(self):
        with self.assertRaises(ValueError):
            voiceover.synthesize("[musik]", os.path.join(self.folder, "b.wav"), voice=FakeVoice())

    def test_voice_download_reports_progress_once(self):
        saved = (voiceover.VOICE_DIR, voiceover.urllib.request.urlopen)
        voiceover.VOICE_DIR = self.folder
        calls = []

        class Response(io.BytesIO):
            headers = {"Content-Length": str(3 << 20)}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_open(url, timeout=None):
            calls.append(url)
            return Response(b"x" * (3 << 20))

        voiceover.urllib.request.urlopen = fake_open
        reports = []
        try:
            model, config = voiceover._voice_files(lambda s, p=None: reports.append(p))
            again = voiceover._voice_files(lambda s, p=None: None)
        finally:
            voiceover.VOICE_DIR, voiceover.urllib.request.urlopen = saved

        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0].endswith(".onnx.json"))
        self.assertEqual(again, (model, config))
        self.assertTrue(os.path.isfile(model))
        numbers = [p for p in reports if isinstance(p, int)]
        self.assertTrue(numbers and all(0 < p < 100 for p in numbers))

    def test_download_failure_is_explained(self):
        saved = (voiceover.VOICE_DIR, voiceover.urllib.request.urlopen)
        voiceover.VOICE_DIR = self.folder

        def boom(url, timeout=None):
            raise OSError("koneksi terputus")

        voiceover.urllib.request.urlopen = boom
        try:
            with self.assertRaises(RuntimeError) as caught:
                voiceover._voice_files(lambda *a: None)
        finally:
            voiceover.VOICE_DIR, voiceover.urllib.request.urlopen = saved
        self.assertIn("gagal diunduh", str(caught.exception))
        self.assertFalse([n for n in os.listdir(self.folder) if n.endswith(".onnx")])


class CardLayoutTest(unittest.TestCase):
    def test_card_filters(self):
        if not clipper.find_card_font():
            self.skipTest("font tidak tersedia")
        folder = tempfile.mkdtemp()
        try:
            text = clipper._hook_filters(
                "pernah habis 120 juta", 720, 1280, "wide", 16 / 9, folder,
                style="card", position="middle", prefix="intro",
            )
            self.assertIn(clipper.CARD_COLOR, text)
            self.assertIn("fontcolor=black", text)
            names = sorted(n for n in os.listdir(folder) if n.endswith(".txt"))
            lines = [open(os.path.join(folder, n), encoding="utf-8").read() for n in names]
            self.assertEqual(" ".join(lines), "PERNAH HABIS 120 JUTA")
            self.assertTrue(os.path.isfile(os.path.join(folder, "introfont.ttf")))
        finally:
            shutil.rmtree(folder)

    def test_bad_style_or_position(self):
        for kwargs in ({"style": "bulat"}, {"position": "bawah"}):
            with self.assertRaises(ValueError):
                clipper._hook_filters("a", 720, 1280, "wide", 1.7, "/tmp", **kwargs)


@unittest.skipUnless(CAN_RENDER, "FFmpeg atau font tidak tersedia")
class IntroRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp()
        cls.source = make_test_video(os.path.join(cls.folder, "src.mp4"), seconds=20)
        cls.voice = _write_tone(os.path.join(cls.folder, "suara.wav"), 2.4)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, ignore_errors=True)

    def render(self, name, **kwargs):
        target = os.path.join(self.folder, name)
        clipper.crop_clip_local(self.source, 3, 11, "9:16", target, size=(360, 640), **kwargs)
        return target

    def frame(self, path, at):
        return subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", str(at), "-i", path, "-frames:v", "1",
             "-vf", "scale=36:64,format=gray", "-f", "rawvideo", "-"],
            capture_output=True, check=True,
        ).stdout

    def test_intro_with_voice_comes_first_then_the_clip(self):
        updates = []
        path = self.render(
            "pembuka.mp4", intro_text="Pernah habis 120 juta", intro_audio=self.voice,
            progress_callback=lambda p, s: updates.append(p),
        )
        # Pembuka = suara 2,4 dtk + jeda 0,5 dtk; klip 8 dtk.
        self.assertAlmostEqual(_duration(path), 8 + 2.9, delta=0.25)
        streams = probe(path)
        self.assertEqual((streams["video"]["width"], streams["video"]["height"]), (360, 640))
        self.assertIn("audio", streams)
        # Gambar pembuka diam, lalu konten bergerak.
        self.assertEqual(self.frame(path, 0.4), self.frame(path, 2.2))
        self.assertNotEqual(self.frame(path, 4.0), self.frame(path, 6.0))
        self.assertEqual(updates, sorted(updates))
        self.assertEqual(updates[-1], 100)

    def test_intro_without_voice_lasts_the_minimum(self):
        path = self.render("tanpa_suara.mp4", intro_text="Ternyata ini rahasianya")
        self.assertAlmostEqual(_duration(path), 8 + clipper.INTRO_MIN, delta=0.25)

    def test_intro_on_a_source_without_sound(self):
        silent = os.path.join(self.folder, "bisu.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", self.source, "-an", "-c", "copy", silent],
            check=True,
        )
        target = os.path.join(self.folder, "bisu_out.mp4")
        clipper.crop_clip_local(
            silent, 3, 9, "9:16", target, size=(240, 426),
            intro_text="Hook", intro_audio=self.voice,
        )
        self.assertIn("audio", probe(target))
        self.assertAlmostEqual(_duration(target), 6 + 2.9, delta=0.25)

    def test_card_in_the_middle_during_the_clip(self):
        plain = self.render("polos.mp4")
        card = self.render(
            "kartu.mp4", hook_text="Kartu di tengah", hook_style="card", hook_position="middle",
        )
        a, b = self.frame(plain, 3), self.frame(card, 3)
        middle = slice(36 * 28, 36 * 36)
        top = slice(0, 36 * 8)
        self.assertGreater(sum(1 for x, y in zip(a[middle], b[middle]) if abs(x - y) > 40), 40)
        self.assertLess(sum(1 for x, y in zip(a[top], b[top]) if abs(x - y) > 40), 5)

    def test_thumbnail(self):
        path = self.render("thumb_src.mp4", intro_text="Thumbnail", intro_audio=self.voice)
        image = clipper.save_thumbnail(path, os.path.join(self.folder, "thumb.jpg"))
        self.assertGreater(os.path.getsize(image), 1000)


@unittest.skipIf(zoi_app is None, "app.py tidak bisa diimpor")
class DashboardIntroTest(unittest.TestCase):
    def setUp(self):
        from tests.helpers import sample_transcript
        self.folder = tempfile.mkdtemp()
        self.saved = (zoi_app.OUTPUT_DIR, exchange.DATA_DIR, voiceover.is_available,
                      voiceover.synthesize, zoi_app.crop_clip_local)
        zoi_app.OUTPUT_DIR = self.folder
        exchange.DATA_DIR = os.path.join(self.folder, "data")
        transcript = sample_transcript()
        exchange.save_transcript(transcript)
        exchange.save_analysis(VIDEO_ID, {
            "video_id": VIDEO_ID, "title": "Uji", "url": "https://youtu.be/x",
            "duration": 1624.0, "mode": "zoi", "source_file": "", "source_res": 0,
            "groups": [{"name": "ZOI", "kind": "zoi", "clips": [{
                "title": "Klip", "start_time": 3.0, "end_time": 40.0, "duration": 37.0,
                "score": 50, "hook_sentence": "Kok bisa [tertawa] semahal ini?",
                "virality_reason": "", "warnings": []}]}],
        })
        self.source = os.path.join(self.folder, f"source_{VIDEO_ID}.mp4")
        if ffmpeg_available():
            make_test_video(self.source, seconds=20)
        else:
            open(self.source, "wb").close()
        self.spoken = []
        test = self

        def fake_synthesize(text, wav_path, report=None, voice=None):
            test.spoken.append(text)
            _write_tone(wav_path, 1.5)
            return 1.5

        voiceover.synthesize = fake_synthesize
        voiceover.is_available = lambda: True
        self.client = zoi_app.app.test_client()

    def tearDown(self):
        (zoi_app.OUTPUT_DIR, exchange.DATA_DIR, voiceover.is_available,
         voiceover.synthesize, zoi_app.crop_clip_local) = self.saved
        shutil.rmtree(self.folder, ignore_errors=True)

    def render(self, **form):
        data = {"video_id": VIDEO_ID, "title": "Klip", "start_time": "3",
                "end_time": "10", "quality": "240", "hook_text": "Kok bisa semahal ini?"}
        data.update(form)
        response = self.client.post("/select", data=data)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        html = response.get_data(as_text=True)
        job = html.split("/progress/")[1].split('"')[0]
        deadline = time.time() + 120
        while time.time() < deadline:
            status = self.client.get(f"/progress/{job}").get_json()
            if status["status"] in ("done", "error"):
                return status, html
            time.sleep(0.1)
        self.fail(status)

    def test_page_controls(self):
        html = self.client.get(f"/video/{VIDEO_ID}").get_data(as_text=True)
        for text in ('name="intro-choice" value="0" checked', 'name="style-choice" value="card"',
                     'name="pos-choice" value="middle"', 'class="intro-field"',
                     'class="style-field"', 'class="pos-field"', "Kok bisa semahal ini?"):
            self.assertIn(text, html)

    def test_bad_style_is_refused(self):
        for form in ({"hook_style": "bulat"}, {"hook_pos": "bawah"}):
            data = {"video_id": VIDEO_ID, "start_time": "3", "end_time": "10", **form}
            self.assertEqual(self.client.post("/select", data=data).status_code, 400)

    @unittest.skipUnless(CAN_RENDER, "FFmpeg atau font tidak tersedia")
    def test_intro_render_with_voice_and_thumbnail(self):
        status, html = self.render(intro_on="1", hook_on="1", hook_style="card", hook_pos="middle")
        self.assertIn("Pembuka + suara", html)
        self.assertIn("kartu kuning, tengah", html)
        self.assertEqual(status["status"], "done", status)
        self.assertEqual(self.spoken, ["Kok bisa semahal ini?"])
        self.assertTrue(status["thumbnail"].endswith("_thumb.jpg"))
        self.assertEqual(status["notice"], "")
        self.assertAlmostEqual(_duration(os.path.join(self.folder, status["filename"])), 7 + 2.0, delta=0.3)

        thumb = self.client.get(f"/thumb/{status['thumbnail']}")
        self.assertEqual(thumb.status_code, 200)
        self.assertEqual(thumb.mimetype, "image/jpeg")
        thumb.close()
        for bad in ("../app.py", f"source_{VIDEO_ID}.mp4", "zoi_x_thumb.jpg"):
            self.assertEqual(self.client.get(f"/thumb/{bad}").status_code, 404, bad)

        # Suara sementara dihapus; menghapus klip ikut menghapus thumbnail.
        self.assertFalse([n for n in os.listdir(self.folder) if n.endswith(".voice.wav")])
        self.client.post("/clip/delete", data={"filename": status["filename"], "video_id": VIDEO_ID})
        self.assertFalse(os.path.exists(os.path.join(self.folder, status["thumbnail"])))

    def test_without_voice_package_the_intro_is_silent_and_explained(self):
        voiceover.is_available = lambda: False
        captured = []
        zoi_app.crop_clip_local = lambda *a, **k: captured.append(k) or open(a[4], "wb").close()
        status, _ = self.render(intro_on="1", hook_on="0")
        self.assertEqual(status["status"], "done", status)
        self.assertIn("tanpa suara", status["notice"])
        self.assertEqual(self.spoken, [])
        self.assertEqual(captured[0]["intro_text"], "Kok bisa semahal ini?")
        self.assertIsNone(captured[0]["intro_audio"])
        self.assertIsNone(captured[0]["hook_text"])

    def test_intro_off_passes_nothing(self):
        captured = []
        zoi_app.crop_clip_local = lambda *a, **k: captured.append(k) or open(a[4], "wb").close()
        status, html = self.render(hook_on="1")
        self.assertNotIn("Pembuka + suara", html)
        self.assertIsNone(captured[0]["intro_text"])
        self.assertEqual(captured[0]["hook_text"], "Kok bisa semahal ini?")
        self.assertEqual(status["thumbnail"], "")


@unittest.skipUnless(
    voiceover.is_available() and os.getenv("ZOI_TEST_PIPER_VOICE"),
    "isi ZOI_TEST_PIPER_VOICE dengan file .onnx untuk uji suara asli",
)
class RealVoiceTest(unittest.TestCase):
    def test_real_voice(self):
        from piper import PiperVoice
        folder = tempfile.mkdtemp()
        try:
            voice = PiperVoice.load(os.environ["ZOI_TEST_PIPER_VOICE"])
            seconds = voiceover.synthesize(
                "Ebel pernah habis 120 juta buat nyawer biduan",
                os.path.join(folder, "v.wav"), voice=voice,
            )
            self.assertGreater(seconds, 1.0)
            self.assertLess(seconds, 8.0)
        finally:
            shutil.rmtree(folder)


if __name__ == "__main__":
    unittest.main()
