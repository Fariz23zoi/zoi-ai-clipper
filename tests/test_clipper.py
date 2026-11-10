"""Tes rendering FFmpeg: potong, 9:16, audio, progres, pembersihan."""
import os
import shutil
import tempfile
import unittest

from shorts_generator.local import clipper

from tests.helpers import ffmpeg_available, make_test_video, probe


@unittest.skipUnless(ffmpeg_available(), "FFmpeg/ffprobe tidak terpasang")
class RenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp()
        cls.source = make_test_video(os.path.join(cls.folder, "source_uji.mp4"))
        cls.output = os.path.join(cls.folder, "hasil.mp4")
        cls.updates = []
        clipper.crop_clip_local(
            cls.source, 5, 37, "9:16", cls.output,
            progress_callback=lambda percent, stage: cls.updates.append(
                (percent, stage)
            ),
        )
        cls.streams = probe(cls.output)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, ignore_errors=True)

    def test_output_is_vertical_720x1280_h264(self):
        video = self.streams["video"]
        self.assertEqual((video["width"], video["height"]), (720, 1280))
        self.assertEqual(video["codec"], "h264")

    def test_audio_is_kept(self):
        self.assertEqual(self.streams["audio"]["codec"], "aac")

    def test_duration_matches_requested_range(self):
        self.assertAlmostEqual(self.streams["video"]["duration"], 32.0, delta=0.5)
        self.assertAlmostEqual(self.streams["audio"]["duration"], 32.0, delta=0.5)

    def test_progress_is_real_monotonic_and_reaches_100(self):
        percents = [percent for percent, _ in self.updates]
        self.assertEqual(percents, sorted(percents))
        self.assertEqual(percents[-1], 100)
        # Banyak nilai antara berarti angka berasal dari FFmpeg.
        self.assertGreater(len(set(percents)), 8)
        self.assertTrue(all(0 <= percent <= 100 for percent in percents))

    def test_source_is_untouched_and_temp_files_removed(self):
        self.assertTrue(os.path.isfile(self.source))
        self.assertEqual(
            sorted(os.listdir(self.folder)), ["hasil.mp4", "source_uji.mp4"]
        )

    def test_failed_render_raises_and_leaves_nothing(self):
        target = os.path.join(self.folder, "gagal.mp4")
        with self.assertRaises(RuntimeError):
            clipper.crop_clip_local(
                os.path.join(self.folder, "tidak_ada.mp4"), 0, 40, "9:16", target
            )
        self.assertFalse(
            [name for name in os.listdir(self.folder) if name.startswith("gagal")]
        )

    def test_vertical_source_also_renders(self):
        source = make_test_video(
            os.path.join(self.folder, "tegak.mp4"), seconds=8, size="540x960"
        )
        target = os.path.join(self.folder, "tegak_hasil.mp4")
        try:
            clipper.crop_clip_local(source, 1, 7, "9:16", target)
            video = probe(target)["video"]
            self.assertEqual((video["width"], video["height"]), (720, 1280))
        finally:
            for path in (source, target):
                if os.path.exists(path):
                    os.remove(path)


if __name__ == "__main__":
    unittest.main()
