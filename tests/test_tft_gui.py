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
        """Memastikan instance TFTGuiPygame memiliki tombol terpisah (BACA, ULANG, STOP, VOL-, VOL+, OFF)."""
        mock_app = MagicMock()
        mock_app.busy = False
        mock_app.last_reading = ["Ini adalah teks uji."]
        mock_app.volume_steps = [40, 60, 80, 100]
        mock_app.current_vol_idx = 2

        gui = tft_gui.TFTGuiPygame(mock_app, width=320, height=240)
        self.assertEqual(gui.width, 320)
        self.assertEqual(gui.height, 240)

        # Verifikasi tombol utama dan border independen
        expected_buttons = ["read", "replay", "stop", "vol_dn", "vol_up", "off"]
        for btn_name in expected_buttons:
            self.assertIn(btn_name, gui.buttons)
            self.assertIn("border_color", gui.buttons[btn_name])

        # Verifikasi batas rect antar tombol tidak bertabrakan/saling tumpang tindih
        btn_list = [gui.buttons[name] for name in expected_buttons]
        for i in range(len(btn_list) - 1):
            r1 = btn_list[i]["rect"]
            r2 = btn_list[i + 1]["rect"]
            # Tombol berikutnya harus berada di sebelah kanan tombol sebelumnya dengan jarak > 0
            self.assertGreater(r2.x, r1.x + r1.width - 1, f"Border tombol {i} dan {i+1} tumpang tindih!")

    def test_button_actions_trigger_app_methods(self):
        """Memastikan aksi tombol sentuh memanggil method yang benar pada App."""
        mock_app = MagicMock()
        mock_app.busy = False
        mock_app._run_async = MagicMock()
        mock_app.speaker = MagicMock()
        mock_app.speaker.playing = False
        mock_app.volume_steps = [40, 60, 80, 100]
        mock_app.current_vol_idx = 2

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

        # Klik VOL - (Turun)
        gui._action_vol_down()
        self.assertEqual(mock_app.current_vol_idx, 1)

        # Klik VOL + (Naik)
        gui._action_vol_up()
        self.assertEqual(mock_app.current_vol_idx, 2)


if __name__ == '__main__':
    unittest.main()
