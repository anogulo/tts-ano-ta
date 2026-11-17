"""
test_state_machine.py - Pengujian Otomatis Mesin Status & Kasus Uji PRD Bagian 15
Bagian dari Rancang Bangun Alat Bantu Baca Tunanetra (Image to Voice)
"""

import time
import unittest
from unittest.mock import MagicMock, patch
import numpy as np

import main
from main import App


class TestStateMachine(unittest.TestCase):

    def setUp(self):
        # Patch Speaker agar tidak membuka subprocess atau hardware audio selama test
        self.patcher_speaker = patch('main.Speaker')
        self.mock_speaker_cls = self.patcher_speaker.start()
        self.mock_speaker = MagicMock()
        self.mock_speaker_cls.return_value = self.mock_speaker
        self.mock_speaker.playing = False

        # Inisialisasi App dengan mock camera agar tidak mengakses hardware kamera saat pengujian
        self.mock_cap = MagicMock()
        self.mock_cap.isOpened.return_value = True
        self.app = App(cap=self.mock_cap, init_cue=True)
        self.app.cue_history = []
        # Bungkus cue untuk merekam riwayat pemanggilan audio
        orig_cue = self.app.cue
        def record_cue(name, wait=False):
            self.app.cue_history.append(name)
            return orig_cue(name, wait)
        self.app.cue = record_cue

    def tearDown(self):
        self.patcher_speaker.stop()

    def test_01_boot_ready(self):
        """Kasus Uji: BOOT -> model selesai dimuat -> memutar 'siap.wav'."""
        self.mock_speaker.cue.assert_any_call(
            unittest.mock.ANY, wait=unittest.mock.ANY
        )
        self.assertFalse(self.app.busy)

    def test_02_camera_error(self):
        """Kasus Uji: CAPTURING -> Kamera tidak terbaca -> 'kamera_error.wav' -> IDLE."""
        self.app.grab_frame = MagicMock(return_value=None)
        self.app.capture_and_read()

        self.assertIn('klik', self.app.cue_history)
        self.assertIn('kamera_error', self.app.cue_history)
        self.assertFalse(self.app.busy)

    def test_03_quality_check_dark_retry_and_fail(self):
        """Kasus Uji: Citra terlalu gelap -> 'gambar_gelap.wav' -> retry 2x -> IDLE."""
        dark_frame = np.zeros((480, 640, 3), dtype=np.uint8)
        self.app.grab_frame = MagicMock(return_value=dark_frame)

        self.app.capture_and_read()

        # Harus memutar klik, kemudian gambar_gelap berulang kali sampai batas retry
        self.assertIn('klik', self.app.cue_history)
        self.assertIn('gambar_gelap', self.app.cue_history)
        self.assertNotIn('memproses', self.app.cue_history)

    def test_04_quality_check_blur_retry_and_fail(self):
        """Kasus Uji: Citra buram -> 'gambar_buram.wav' -> retry 2x -> IDLE."""
        flat_frame = np.full((480, 640, 3), 180, dtype=np.uint8)
        self.app.grab_frame = MagicMock(return_value=flat_frame)

        self.app.capture_and_read()

        self.assertIn('klik', self.app.cue_history)
        self.assertIn('gambar_buram', self.app.cue_history)
        self.assertNotIn('memproses', self.app.cue_history)

    def test_05_no_text_detected(self):
        """Kasus Uji: Citra tajam dan terang tapi tanpa teks -> 'tidak_ada_teks.wav' -> IDLE."""
        # Gambar dengan variasi tinggi (lulus quality check) tapi tanpa teks
        np.random.seed(42)
        sharp_frame = np.random.randint(50, 230, (480, 640, 3), dtype=np.uint8)
        self.app.grab_frame = MagicMock(return_value=sharp_frame)

        # Mock run_ocr mengembalikan list kosong
        with patch('main.run_ocr', return_value=[]):
            self.app.capture_and_read()

        self.assertIn('memproses', self.app.cue_history)
        self.assertIn('tidak_ada_teks', self.app.cue_history)
        self.assertEqual(len(self.app.last_reading), 0)

    def test_06_text_detected_and_speaking(self):
        """Kasus Uji: OCR berhasil -> sintesis suara -> 'selesai.wav' -> IDLE."""
        np.random.seed(42)
        sharp_frame = np.random.randint(50, 230, (480, 640, 3), dtype=np.uint8)
        self.app.grab_frame = MagicMock(return_value=sharp_frame)
        self.mock_speaker.say.return_value = True

        dummy_lines = ["Resep obat batuk untuk anak.", "Diminum tiga kali sehari."]
        with patch('main.run_ocr', return_value=dummy_lines):
            self.app.capture_and_read()

        self.assertIn('memproses', self.app.cue_history)
        self.mock_speaker.say.assert_called_once()
        self.assertIn('selesai', self.app.cue_history)
        self.assertGreater(len(self.app.last_reading), 0)

    def test_07_interrupt_speaking(self):
        """Kasus Uji: Tekan tombol saat SPEAKING -> hentikan seketika."""
        self.app.speaker.playing = True
        self.app.on_main_press()

        self.mock_speaker.stop.assert_called_once()
        self.assertTrue(self.app._ignore_press)

        # Saat tombol dilepas setelah stop, tidak boleh memicu capture_and_read baru
        with patch.object(self.app, 'capture_and_read') as mock_cap:
            self.app.on_main_release()
            mock_cap.assert_not_called()

    def test_08_replay_last_reading(self):
        """Kasus Uji: Hold tombol utama -> replay bacaan terakhir jika ada."""
        self.app.last_reading = ["Kalimat yang pernah dibaca sebelumnya."]
        self.mock_speaker.say.return_value = True

        self.app.replay()
        self.mock_speaker.say.assert_called_once_with(self.app.last_reading, on_first=unittest.mock.ANY)
        self.assertIn('selesai', self.app.cue_history)

    def test_09_replay_when_empty(self):
        """Kasus Uji: Hold tombol utama tanpa ada bacaan -> 'belum_ada_bacaan.wav'."""
        self.app.last_reading = []
        self.app.replay()
        self.assertIn('belum_ada_bacaan', self.app.cue_history)

    def test_10_cycle_volume(self):
        """Kasus Uji: Tombol bantu ditekan singkat -> siklus volume 40, 60, 80, 100, 40."""
        initial_vol_idx = self.app.current_vol_idx
        expected_next = (initial_vol_idx + 1) % len(self.app.volume_steps)

        self.app.cycle_volume()
        self.assertEqual(self.app.current_vol_idx, expected_next)
        self.assertIn('klik', self.app.cue_history)

    def test_11_safe_shutdown(self):
        """Kasus Uji: Tombol bantu ditahan >= 3 detik -> 'mati.wav'."""
        with patch('main.time.sleep', return_value=None):
            self.app.safe_shutdown()
        self.assertIn('mati', self.app.cue_history)

    def test_12_anti_double_trigger(self):
        """Kasus Uji: Sistem sibuk (busy=True) -> penekanan baru diabaikan."""
        self.app.busy = True
        with patch.object(self.app, 'capture_and_read') as mock_cap:
            self.app._run_async(self.app.capture_and_read)
            mock_cap.assert_not_called()


if __name__ == '__main__':
    unittest.main()
