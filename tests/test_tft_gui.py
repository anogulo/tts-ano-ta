"""
test_tft_gui.py - Pengujian Unit Modul TFT GUI
"""

import unittest
from unittest.mock import MagicMock
import tft_gui


class TestTFTGui(unittest.TestCase):

    def test_gui_module_constants(self):
        """Memastikan modul TFT GUI terdefinisi dengan baik."""
        self.assertTrue(hasattr(tft_gui, "launch_gui"))
        self.assertTrue(hasattr(tft_gui, "TFTGuiPygame"))
        self.assertTrue(hasattr(tft_gui, "TFTGuiTkinter"))

    def test_pygame_gui_initialization(self):
        """Memastikan instance TFTGuiPygame dapat diinisialisasi tanpa crash."""
        mock_app = MagicMock()
        mock_app.busy = False
        mock_app.last_reading = ["Ini adalah teks uji."]
        mock_app.volume_steps = [40, 60, 80, 100]
        mock_app.current_vol_idx = 2

        gui = tft_gui.TFTGuiPygame(mock_app, width=320, height=240)
        self.assertEqual(gui.width, 320)
        self.assertEqual(gui.height, 240)
        self.assertIn("read", gui.buttons)
        self.assertIn("replay", gui.buttons)
        self.assertIn("stop", gui.buttons)
        self.assertIn("vol", gui.buttons)
        self.assertIn("off", gui.buttons)

    def test_button_actions_trigger_app_methods(self):
        """Memastikan aksi tombol sentuh memanggil method yang benar pada App."""
        mock_app = MagicMock()
        mock_app.busy = False
        mock_app._run_async = MagicMock()
        mock_app.speaker = MagicMock()

        gui = tft_gui.TFTGuiPygame(mock_app, width=320, height=240)

        # Klik BACA
        gui._action_read()
        mock_app._run_async.assert_called_with(mock_app.capture_and_read)

        # Klik REPLAY
        gui._action_replay()
        mock_app._run_async.assert_called_with(mock_app.replay)

        # Klik STOP
        gui._action_stop()
        mock_app.speaker.stop.assert_called_once()

        # Klik VOL
        gui._action_vol()
        mock_app._run_async.assert_called_with(mock_app.cycle_volume)


if __name__ == '__main__':
    unittest.main()
