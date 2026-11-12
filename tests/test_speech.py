"""Tes pengenal suara untuk video tanpa subtitle.

Sebagian besar memakai model Whisper tiruan, jadi tidak butuh internet
maupun faster-whisper. Uji dengan model asli ada di bagian bawah dan hanya
berjalan bila ZOI_TEST_WHISPER_MODEL diisi folder model.
"""
import os
import shutil
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

from shorts_generator.local import speech_to_text as speech

from tests.helpers import sample_transcript

try:
    import app as zoi_app
    from shorts_generator import exchange
    from shorts_generator.local.transcriber_youtube import SubtitlesUnavailable
except ImportError:
    zoi_app = None


def _word(start, end, text):
    return SimpleNamespace(start=start, end=end, word=" " + text, probability=0.9)


def _segment(start, end, words, no_speech=0.01, logprob=-0.2, text=None):
    return SimpleNamespace(
        start=start, end=end, words=words,
        text=text if text is not None else " ".join(w.word.strip() for w in words),
        no_speech_prob=no_speech, avg_logprob=logprob,
    )


class FakeModel:
    """Meniru WhisperModel: deteksi bahasa dan segmen yang dihasilkan bertahap."""

    supported_languages = ["en", "id", "ms", "jw", "su", "ja"]

    def __init__(self, segments, detected="id", duration=60.0, detect_error=False):
        self.segments = segments
        self.detected = detected
        self.duration = duration
        self.detect_error = detect_error
        self.calls = []

    def detect_language(self, audio, **kwargs):
        self.calls.append(("detect", kwargs))
        if self.detect_error:
            raise RuntimeError("deteksi gagal")
        return self.detected, 0.9, []

    def transcribe(self, audio, **kwargs):
        self.calls.append(("transcribe", kwargs))
        return iter(self.segments), SimpleNamespace(duration=self.duration)


def _speech_segments():
    """Empat segmen ucapan: 0-20, 20-40, 40-55 detik dan satu 'derau'."""
    return [
        _segment(0.0, 20.0, [_word(0.5 + i * 2, 1.5 + i * 2, f"kata{i}") for i in range(10)]),
        _segment(20.0, 40.0, [_word(20.5 + i * 2, 21.5 + i * 2, f"lagi{i}") for i in range(10)]),
        _segment(40.0, 50.0, [_word(41.0, 42.0, "musik")], no_speech=0.9, logprob=-1.5),
        _segment(50.0, 55.0, [], text="tanpa waktu per kata"),
    ]


class TranscribeFileTest(unittest.TestCase):
    def setUp(self):
        self.saved = (speech.LANGUAGE, speech.BEAM_SIZE)
        self.reports = []

    def tearDown(self):
        speech.LANGUAGE, speech.BEAM_SIZE = self.saved

    def run_with(self, model, hint=None):
        return speech.transcribe_file(
            "video.mp4", report=lambda stage, progress=None: self.reports.append((stage, progress)),
            language_hint=hint, model=model, audio=[0.0] * 10,
        )

    def test_result_has_the_same_shape_as_a_subtitle_transcript(self):
        result = self.run_with(FakeModel(_speech_segments()))
        self.assertEqual(result["language"], "id")
        self.assertEqual(result["duration"], 60.0)
        self.assertEqual(result["model"], speech.MODEL_NAME)

        words = [w["text"] for w in result["words"]]
        self.assertEqual(words[:3], ["kata0", "kata1", "kata2"])
        self.assertIn("lagi9", words)
        # Segmen yang kemungkinan bukan ucapan dibuang.
        self.assertNotIn("musik", words)
        # Segmen tanpa waktu per kata tetap terpakai, waktunya dibagi rata.
        self.assertEqual(words[-4:], ["tanpa", "waktu", "per", "kata"])
        tail = result["words"][-4:]
        self.assertEqual(tail[0]["start"], 50.0)
        self.assertTrue(all(w["start"] <= w["end"] for w in result["words"]))

        for segment in result["segments"]:
            self.assertLess(segment["start"], segment["end"])
            self.assertLessEqual(segment["end"] - segment["start"], 10.0)
            self.assertTrue(segment["text"])
        for before, after in zip(result["segments"], result["segments"][1:]):
            self.assertLessEqual(before["end"], after["start"])

    def test_reduplicated_words_are_joined(self):
        segment = _segment(0.0, 3.0, [
            _word(0.0, 0.5, "Selamat"), _word(0.5, 0.9, "teman"),
            SimpleNamespace(start=0.9, end=1.4, word=" -teman", probability=0.9),
            _word(1.5, 2.0, "semua"),
        ])
        result = self.run_with(FakeModel([segment], duration=3.0))
        self.assertEqual(
            [w["text"] for w in result["words"]], ["Selamat", "teman-teman", "semua"]
        )
        self.assertEqual(result["words"][1]["end"], 1.4)
        self.assertEqual(result["segments"][0]["text"], "Selamat teman-teman semua")

    def test_split_numbers_are_joined_but_sentences_are_not(self):
        segment = _segment(0.0, 4.0, [
            _word(0.0, 0.5, "harganya"), _word(0.5, 1.0, "Rp800"),
            SimpleNamespace(start=1.0, end=1.3, word=" .000", probability=0.9),
            _word(1.4, 2.0, "selesai."), _word(2.1, 2.5, "Oke"),
        ])
        result = self.run_with(FakeModel([segment], duration=4.0))
        self.assertEqual(
            [w["text"] for w in result["words"]],
            ["harganya", "Rp800.000", "selesai.", "Oke"],
        )

    def test_whisper_options(self):
        speech.BEAM_SIZE = 1
        model = FakeModel(_speech_segments())
        self.run_with(model)
        options = dict(model.calls)["transcribe"]
        self.assertEqual(options["language"], "id")
        self.assertTrue(options["word_timestamps"])
        self.assertTrue(options["vad_filter"])
        self.assertFalse(options["condition_on_previous_text"])
        self.assertEqual(options["beam_size"], 1)

    def test_progress_is_real_and_never_reaches_100(self):
        self.run_with(FakeModel(_speech_segments()))
        numbers = [p for _, p in self.reports if isinstance(p, int)]
        self.assertEqual(numbers, sorted(numbers))
        self.assertTrue(all(0 <= p < 100 for p in numbers))
        self.assertIn(("Mengenali ucapan: 00:20 dari 01:00", 33), self.reports)
        self.assertIn(("Mengenali ucapan: 00:55 dari 01:00", 91), self.reports)

    def test_malay_family_is_written_as_indonesian(self):
        for detected in ("ms", "jw", "su", "id"):
            model = FakeModel(_speech_segments(), detected=detected)
            self.assertEqual(self.run_with(model)["language"], "id", detected)

    def test_other_languages_are_kept(self):
        model = FakeModel(_speech_segments(), detected="en")
        self.assertEqual(self.run_with(model)["language"], "en")

    def test_youtube_language_is_used_when_detection_fails(self):
        model = FakeModel(_speech_segments(), detect_error=True)
        self.assertEqual(self.run_with(model, hint="en-US")["language"], "en")
        model = FakeModel(_speech_segments(), detect_error=True)
        self.assertEqual(self.run_with(model, hint="")["language"], "id")

    def test_language_setting_overrides_detection(self):
        speech.LANGUAGE = "ja"
        model = FakeModel(_speech_segments(), detected="id")
        self.assertEqual(self.run_with(model)["language"], "ja")
        self.assertNotIn("detect", [name for name, _ in model.calls])

    def test_no_speech_gives_an_empty_transcript(self):
        result = self.run_with(FakeModel([]))
        self.assertEqual(result["segments"], [])
        self.assertEqual(result["words"], [])

    def test_only_one_transcription_runs_at_a_time(self):
        speech._LOCK.acquire()
        releaser = threading.Timer(0.3, speech._LOCK.release)
        releaser.start()
        started = time.time()
        self.run_with(FakeModel(_speech_segments()))
        self.assertGreaterEqual(time.time() - started, 0.25)
        self.assertEqual(self.reports[0], ("Menunggu giliran pengenal suara", None))
        self.assertFalse(speech._LOCK.locked())

    def test_lock_is_released_after_an_error(self):
        class Broken(FakeModel):
            def transcribe(self, audio, **kwargs):
                raise RuntimeError("rusak")

        with self.assertRaises(RuntimeError):
            self.run_with(Broken([]))
        self.assertFalse(speech._LOCK.locked())


@unittest.skipUnless(speech.is_available(), "faster-whisper tidak terpasang")
class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.saved = speech.BATCH_SIZE

    def tearDown(self):
        speech.BATCH_SIZE = self.saved

    def test_real_model_is_batched(self):
        from faster_whisper import BatchedInferencePipeline

        WhisperModel = type("WhisperModel", (), {})
        runner, options = speech._runner(WhisperModel())
        self.assertIsInstance(runner, BatchedInferencePipeline)
        self.assertEqual(options, {"batch_size": speech.BATCH_SIZE})

    def test_batch_size_one_means_one_at_a_time(self):
        speech.BATCH_SIZE = 1
        model = type("WhisperModel", (), {})()
        runner, options = speech._runner(model)
        self.assertIs(runner, model)
        self.assertTrue(options["vad_filter"])


class HelpersTest(unittest.TestCase):
    def test_is_available_is_a_boolean(self):
        self.assertIsInstance(speech.is_available(), bool)

    def test_local_model_folder_is_used_as_is(self):
        folder = tempfile.mkdtemp()
        try:
            self.assertEqual(speech._model_files(folder, lambda *a: None), folder)
        finally:
            shutil.rmtree(folder)

    def test_clock(self):
        self.assertEqual(speech._clock(65), "01:05")
        self.assertEqual(speech._clock(3725), "1:02:05")

    def test_models_live_inside_the_project_folder(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertEqual(speech.MODEL_DIR, os.path.join(root, "models"))


def _has_numpy():
    try:
        import numpy  # noqa: F401
    except ImportError:
        return False
    return bool(shutil.which("ffmpeg"))


@unittest.skipUnless(_has_numpy(), "numpy atau FFmpeg tidak terpasang")
class ReadAudioTest(unittest.TestCase):
    """Audio dibaca lewat FFmpeg, bukan lewat pustaka av."""

    @classmethod
    def setUpClass(cls):
        from tests.helpers import make_test_video
        cls.folder = tempfile.mkdtemp()
        cls.video = make_test_video(os.path.join(cls.folder, "v.mp4"), seconds=3)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, ignore_errors=True)

    def test_video_file_gives_16khz_mono_samples(self):
        audio = speech.read_audio(self.video)
        self.assertEqual(str(audio.dtype), "float32")
        self.assertAlmostEqual(len(audio) / speech.SAMPLING_RATE, 3.0, delta=0.1)
        self.assertGreater(float(abs(audio).max()), 0.05)

    def test_missing_file_is_explained(self):
        with self.assertRaises(RuntimeError) as caught:
            speech.read_audio(os.path.join(self.folder, "tidak_ada.mp4"))
        self.assertIn("Audio gagal dibaca", str(caught.exception))

    def test_video_without_sound_is_explained(self):
        import subprocess
        silent = os.path.join(self.folder, "bisu.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", "testsrc=size=320x240:rate=10", "-t", "1", "-an", silent],
            check=True,
        )
        with self.assertRaises(RuntimeError):
            speech.read_audio(silent)


@unittest.skipUnless(speech.is_available(), "faster-whisper tidak terpasang")
class ModelDownloadTest(unittest.TestCase):
    """Unduhan model pertama kali: kemajuan dilaporkan dari ukuran folder."""

    def setUp(self):
        import faster_whisper.utils as utils

        self.utils = utils
        self.saved = (utils.download_model, speech.MODEL_DIR)
        self.folder = tempfile.mkdtemp()
        speech.MODEL_DIR = self.folder

    def tearDown(self):
        self.utils.download_model, speech.MODEL_DIR = self.saved
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_progress_while_downloading(self):
        target = os.path.join(self.folder, "models--Systran--faster-whisper-tiny")

        def fake_download(name, cache_dir=None, local_files_only=False, **_):
            if local_files_only:
                raise FileNotFoundError("belum ada")
            os.makedirs(target, exist_ok=True)
            for part in range(3):
                with open(os.path.join(target, f"bagian{part}"), "wb") as file:
                    file.write(b"x" * 20 * 1_048_576)
                time.sleep(1.1)
            return target

        self.utils.download_model = fake_download
        reports = []
        path = speech._model_files("tiny", lambda s, p=None: reports.append((s, p)))

        self.assertEqual(path, target)
        self.assertTrue(any("sekitar 75 MB" in stage for stage, _ in reports))
        numbers = [p for _, p in reports if isinstance(p, int)]
        self.assertTrue(numbers, reports)
        self.assertTrue(all(0 < p < 100 for p in numbers))

    def test_cached_model_is_not_downloaded_again(self):
        calls = []

        def fake_download(name, cache_dir=None, local_files_only=False, **_):
            calls.append(local_files_only)
            return "/model"

        self.utils.download_model = fake_download
        self.assertEqual(speech._model_files("small", lambda *a: None), "/model")
        self.assertEqual(calls, [True])

    def test_download_failure_is_explained(self):
        def fake_download(name, cache_dir=None, local_files_only=False, **_):
            raise OSError("koneksi terputus")

        self.utils.download_model = fake_download
        with self.assertRaises(RuntimeError) as caught:
            speech._model_files("small", lambda *a: None)
        self.assertIn("gagal diunduh", str(caught.exception))
        self.assertIn("koneksi terputus", str(caught.exception))


@unittest.skipIf(zoi_app is None, "app.py tidak bisa diimpor")
class SpeechFallbackTest(unittest.TestCase):
    """Dashboard: video tanpa subtitle dianalisis dari suaranya."""

    VIDEO = "SPEECHVID01"
    URL = "https://www.youtube.com/watch?v=SPEECHVID01"

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.saved = (
            zoi_app.OUTPUT_DIR, exchange.DATA_DIR, zoi_app.SPEECH_ENABLED,
            zoi_app.transcribe_from_youtube, zoi_app.download_audio_local,
            speech.is_available, speech.transcribe_file,
        )
        zoi_app.OUTPUT_DIR = self.folder
        exchange.DATA_DIR = os.path.join(self.folder, "data")
        zoi_app.SPEECH_ENABLED = True

        self.reason = "none"
        self.audio_error = None
        self.media = []
        self.audio_downloads = 0
        self.speech_result = None
        sample = sample_transcript()
        test = self

        def no_subtitles(url):
            raise SubtitlesUnavailable(
                "YouTube tidak menyediakan subtitle untuk video ini.",
                video={
                    "video_id": test.VIDEO, "title": "Tanpa subtitle",
                    "url": test.URL, "channel": "Kanal", "width": 1280,
                    "height": 720, "duration": 1624.0, "language": "",
                },
                reason=test.reason,
            )

        def fake_audio(url, out_dir=None, progress_hook=None):
            test.audio_downloads += 1
            if test.audio_error:
                raise RuntimeError(test.audio_error)
            if progress_hook:
                progress_hook({
                    "status": "downloading", "downloaded_bytes": 5,
                    "total_bytes": 10, "info_dict": {"vcodec": "none"},
                })
            path = os.path.join(out_dir, f"audio_{test.VIDEO}.webm")
            with open(path, "wb") as file:
                file.write(b"audio")
            return path

        def fake_speech(media, report=None, language_hint=None):
            test.media.append(media)
            test.assertTrue(os.path.isfile(media))
            report("Mengenali ucapan: 13:32 dari 27:04", 50)
            if test.speech_result is not None:
                return test.speech_result
            return {
                "segments": sample["segments"], "words": sample["words"],
                "language": "id", "duration": 1624.0, "model": "small",
            }

        zoi_app.transcribe_from_youtube = no_subtitles
        zoi_app.download_audio_local = fake_audio
        speech.is_available = lambda: True
        speech.transcribe_file = fake_speech
        self.client = zoi_app.app.test_client()

    def tearDown(self):
        (
            zoi_app.OUTPUT_DIR, exchange.DATA_DIR, zoi_app.SPEECH_ENABLED,
            zoi_app.transcribe_from_youtube, zoi_app.download_audio_local,
            speech.is_available, speech.transcribe_file,
        ) = self.saved
        shutil.rmtree(self.folder, ignore_errors=True)

    def analyze(self):
        html = self.client.post(
            "/analyze", data={"youtube_url": self.URL, "mode": "zoi"}
        ).get_data(as_text=True)
        job_id = html.split("/analyze/status/")[1].split('"')[0]
        seen = []
        deadline = time.time() + 60
        while time.time() < deadline:
            data = self.client.get(f"/analyze/status/{job_id}").get_json()
            seen.append(data)
            if data["status"] in ("done", "error"):
                return data, seen
            time.sleep(0.02)
        self.fail(seen[-1])

    def test_video_without_subtitles_is_transcribed_from_audio(self):
        # Catat setiap tahap yang dilaporkan, tidak bergantung pada polling.
        original = zoi_app._update_job
        updates = []

        def recording(jobs, job_id, **values):
            if jobs is zoi_app.ANALYZE_JOBS:
                updates.append(dict(values))
            return original(jobs, job_id, **values)

        zoi_app._update_job = recording
        try:
            data, _ = self.analyze()
        finally:
            zoi_app._update_job = original

        self.assertEqual(data["status"], "done", data)
        self.assertEqual(data["video_id"], self.VIDEO)
        self.assertEqual(self.audio_downloads, 1)

        stages = [item.get("stage") for item in updates]
        for expected in (
            "Mengambil subtitle YouTube",
            "Video ini tidak punya subtitle. Transkrip dibuat dari suara",
            "Mengunduh audio",
            "Mengenali ucapan: 13:32 dari 27:04",
            "Menganalisis transkrip dengan ZOI Engine",
        ):
            self.assertIn(expected, stages)
        self.assertIn({"stage": "Mengunduh audio", "progress": 50}, updates)

        # Audio dihapus setelah transkrip jadi; transkripnya disimpan.
        self.assertEqual(
            [name for name in os.listdir(self.folder) if name.startswith("audio_")], []
        )
        transcript = exchange.load_transcript(self.VIDEO)
        self.assertEqual(transcript["source"], "whisper")
        self.assertEqual(transcript["speech_reason"], "none")
        self.assertEqual(transcript["title"], "Tanpa subtitle")
        self.assertGreater(len(transcript["segments"]), 100)

        html = self.client.get(f"/video/{self.VIDEO}").get_data(as_text=True)
        self.assertIn("tidak menyediakan subtitle untuk video ini, jadi transkrip", html)
        self.assertIn("Whisper small", html)
        self.assertIn("Render klip ini", html)

        # Analisis ulang memakai transkrip tersimpan, tanpa mengenali ulang.
        data, _ = self.analyze()
        self.assertEqual(data["status"], "done")
        self.assertEqual(len(self.media), 1)

    def test_saved_source_video_is_used_instead_of_downloading_audio(self):
        source = os.path.join(self.folder, f"source_{self.VIDEO}.mp4")
        with open(source, "wb") as file:
            file.write(b"video")

        data, _ = self.analyze()
        self.assertEqual(data["status"], "done", data)
        self.assertEqual(self.audio_downloads, 0)
        self.assertEqual(self.media, [os.path.realpath(source)])
        self.assertTrue(os.path.isfile(source))

    def test_failed_subtitles_are_explained_in_the_notice(self):
        self.reason = "failed"
        data, _ = self.analyze()
        self.assertEqual(data["status"], "done", data)
        html = self.client.get(f"/video/{self.VIDEO}").get_data(as_text=True)
        self.assertIn("gagal mengirim subtitle", html)

    def test_missing_speech_package_explains_how_to_install(self):
        speech.is_available = lambda: False
        data, _ = self.analyze()
        self.assertEqual(data["status"], "error")
        self.assertIn("tidak menyediakan subtitle", data["error"])
        self.assertIn("mulai_windows.bat", data["error"])
        self.assertEqual(self.audio_downloads, 0)

    def test_speech_can_be_switched_off(self):
        zoi_app.SPEECH_ENABLED = False
        data, _ = self.analyze()
        self.assertEqual(data["status"], "error")
        self.assertNotIn("mulai_windows.bat", data["error"])
        self.assertEqual(self.audio_downloads, 0)

    def test_audio_download_failure_is_reported(self):
        self.audio_error = "HTTP Error 403: Forbidden"
        data, _ = self.analyze()
        self.assertEqual(data["status"], "error")
        self.assertIn("tidak menyediakan subtitle", data["error"])
        self.assertIn("Audio untuk pengenal suara juga gagal diunduh", data["error"])
        self.assertIn("403", data["error"])

    def test_no_speech_found(self):
        self.speech_result = {
            "segments": [], "words": [], "language": "id",
            "duration": 60.0, "model": "small",
        }
        data, _ = self.analyze()
        self.assertEqual(data["status"], "error")
        self.assertIn("tidak menemukan ucapan", data["error"])
        self.assertEqual(
            [name for name in os.listdir(self.folder) if name.startswith("audio_")], []
        )
        self.assertIsNone(exchange.load_transcript(self.VIDEO))

    def test_english_speech_gets_the_language_notice(self):
        sample = sample_transcript()
        self.speech_result = {
            "segments": sample["segments"], "words": sample["words"],
            "language": "en", "duration": 1624.0, "model": "small",
        }
        data, _ = self.analyze()
        self.assertEqual(data["status"], "done", data)
        html = self.client.get(f"/video/{self.VIDEO}").get_data(as_text=True)
        self.assertIn("Ucapan video ini berbahasa", html)


@unittest.skipUnless(
    speech.is_available() and os.getenv("ZOI_TEST_WHISPER_MODEL"),
    "isi ZOI_TEST_WHISPER_MODEL dengan folder model untuk uji model asli",
)
class RealModelTest(unittest.TestCase):
    """Uji dengan model Whisper asli dan suara video contoh."""

    def test_sample_audio(self):
        audio_path = os.getenv(
            "ZOI_TEST_WHISPER_AUDIO",
            os.path.join(os.path.dirname(__file__), "..", "output", "audio-test.mp3"),
        )
        if not os.path.isfile(audio_path):
            self.skipTest("file audio uji tidak ada")

        from faster_whisper import WhisperModel

        model = WhisperModel(
            os.environ["ZOI_TEST_WHISPER_MODEL"], device="cpu", compute_type="int8"
        )
        audio = speech.read_audio(audio_path)[: 16000 * 120]
        result = speech.transcribe_file(audio_path, model=model, audio=audio)

        self.assertEqual(result["language"], "id")
        self.assertGreater(len(result["words"]), 100)
        self.assertGreater(len(result["segments"]), 5)
        self.assertLessEqual(result["segments"][-1]["end"], 121)


if __name__ == "__main__":
    unittest.main()
