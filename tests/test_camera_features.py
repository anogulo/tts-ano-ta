"""
test_camera_features.py - Pengujian Fitur Pilihan Kamera, Terminal View, dan Pengambilan Gambar
"""

import os
import tempfile
import unittest
import numpy as np
import cv2

import terminal_view
from main import save_camera_device_to_config, CONFIG_PATH


class TestCameraFeatures(unittest.TestCase):

    def test_scan_cameras_returns_list(self):
        """Memastikan scan_cameras tidak error dan mengembalikan list."""
        cams = terminal_view.scan_cameras(max_devices=2)
        self.assertIsInstance(cams, list)

    def test_frame_to_ascii_clean(self):
        """Memastikan fungsi render ascii menghasilkan string berbingkai."""
        # Buat gambar sintetis dokumen putih dengan teks gelap
        test_img = np.full((100, 150), 240, dtype=np.uint8)
        test_img[40:60, 30:120] = 20  # Teks gelap

        ascii_out = terminal_view.frame_to_ascii_clean(test_img, width=40, show_guides=True)
        self.assertIsInstance(ascii_out, str)
        self.assertTrue(ascii_out.startswith("+---"))
        self.assertTrue(ascii_out.endswith("+"))
        # Karakter teks (@, #, %) harus muncul karena kontras stretching
        self.assertTrue(any(c in ascii_out for c in ['@', '#', '%', '*']))

    def test_print_camera_snapshot_execution(self):
        """Memastikan print_camera_snapshot berjalan tanpa error."""
        frame = np.full((120, 160, 3), 200, dtype=np.uint8)
        # Should not raise exception
        terminal_view.print_camera_snapshot(frame, label="TEST SNAPSHOT")

    def test_save_camera_device_to_config(self):
        """Memastikan save_camera_device_to_config dapat mengubah nilai device di config.toml."""
        import tomllib

        # Baca nilai awal
        with open(CONFIG_PATH, 'rb') as f:
            cfg_before = tomllib.load(f)
        orig_device = cfg_before['camera']['device']

        # Ubah ke nilai uji
        test_target = 1 if orig_device == 0 else 0
        save_camera_device_to_config(test_target)

        # Verifikasi nilai baru
        with open(CONFIG_PATH, 'rb') as f:
            cfg_after = tomllib.load(f)
        self.assertEqual(cfg_after['camera']['device'], test_target)

        # Kembalikan ke nilai awal
        save_camera_device_to_config(orig_device)
        with open(CONFIG_PATH, 'rb') as f:
            cfg_restored = tomllib.load(f)
        self.assertEqual(cfg_restored['camera']['device'], orig_device)


if __name__ == '__main__':
    unittest.main()
