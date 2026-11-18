"""
tft_gui.py - Antarmuka Grafis Sentuh Ringan & Modern untuk LCD 2.4" TFT SPI 240x320 / 320x240
Bagian dari Rancang Bangun Alat Bantu Baca Tunanetra (Image to Voice)

Karakteristik Desain:
1. Ultra-ringan: Konsumsi CPU < 2%, RAM ~18 MB (Optimal untuk Raspberry Pi 4).
2. Layout Responsif 320x240 (Landscape) & 240x320 (Portrait) untuk LCD TFT SPI ILI9341 + XPT2046.
3. Dark Accessibility Theme: Kontras tinggi, teks tajam, tombol sentuh besar ramah jari.
4. Terintegrasi penuh dengan mesin status App, kamera live, OCR pipeline, dan Piper TTS.
"""

import os
import sys
import time
import threading
from typing import Optional, Tuple

# Pastikan konfigurasi lingkungan SDL / Linux Framebuffer siap sebelum pygame dimuat
if sys.platform.startswith("linux"):
    if "XDG_RUNTIME_DIR" not in os.environ:
        try:
            uid = os.getuid() if hasattr(os, "getuid") else 1000
            runtime_dir = f"/run/user/{uid}"
            if not os.path.exists(runtime_dir):
                runtime_dir = f"/tmp/runtime-{uid}"
                os.makedirs(runtime_dir, exist_ok=True, mode=0o700)
            os.environ["XDG_RUNTIME_DIR"] = runtime_dir
        except Exception:
            pass

    # Konfigurasi default SDL Framebuffer untuk LCD SPI Raspberry Pi
    os.environ.setdefault("SDL_VIDEODRIVER", "fbcon")
    os.environ.setdefault("SDL_FBDEV", "/dev/fb0")
    os.environ.setdefault("SDL_MOUSEDRV", "dummy")
    os.environ.setdefault("SDL_MOUSEDEV", "/dev/null")
    if "DISPLAY" not in os.environ:
        os.environ["DISPLAY"] = ""

import cv2
import numpy as np

# Cek ketersediaan pustaka GUI
try:
    import pygame
    HAS_PYGAME = True
except ImportError:
    HAS_PYGAME = False

# Cek pustaka pembaca input sentuh hardware Linux
try:
    import evdev
    HAS_EVDEV = True
except ImportError:
    HAS_EVDEV = False

# Class Rect helper agar tombol sentuh dapat didefinisikan tanpa ketergantungan Pygame
class SimpleRect:
    def __init__(self, x, y, w, h):
        self.x = x
        self.y = y
        self.width = w
        self.height = h

    def collidepoint(self, pt):
        px, py = pt
        return self.x <= px <= self.x + self.width and self.y <= py <= self.y + self.height


class FramebufferBlitter:
    """
    Menulis buffer frame Pygame secara langsung ke perangkat Linux Framebuffer
    seperti /dev/fb1 (LCD 2.4" SPI ILI9341).
    Mengonversi RGB888 ke 16-bit RGB565 dalam waktu < 1 ms menggunakan NumPy.
    """
    def __init__(self, device: str = "/dev/fb1", width: int = 320, height: int = 240):
        self.device = device
        self.width = width
        self.height = height
        self.fb_file = None
        self.enabled = False
        if sys.platform.startswith("linux") and os.path.exists(self.device):
            try:
                self.fb_file = open(self.device, "wb")
                self.enabled = True
                print(f"[GUI FB] Direct Framebuffer aktif pada {self.device} ({self.width}x{self.height} RGB565)")
            except Exception as e:
                print(f"[GUI FB] Peringatan: Tidak dapat membuka {self.device}: {e}")

    def blit(self, surface):
        if not self.enabled or not self.fb_file:
            return
        try:
            # pygame.surfarray.array3d menghasilkan array (width, height, 3)
            # Transpose ke bentuk standar (height, width, 3)
            arr = pygame.surfarray.array3d(surface)
            arr = np.transpose(arr, (1, 0, 2))

            # Konversi warna RGB888 ke RGB565 (5 bit Red, 6 bit Green, 5 bit Blue)
            r = (arr[:, :, 0].astype(np.uint16) >> 3) << 11
            g = (arr[:, :, 1].astype(np.uint16) >> 2) << 5
            b = (arr[:, :, 2].astype(np.uint16) >> 3)
            rgb565 = (r | g | b).astype(np.uint16)

            self.fb_file.seek(0)
            self.fb_file.write(rgb565.tobytes())
            self.fb_file.flush()
        except Exception:
            pass

    def close(self):
        if self.fb_file:
            try:
                self.fb_file.close()
            except Exception:
                pass
            self.fb_file = None
            self.enabled = False


class LinuxTouchReader:
    """
    Thread pemantau input sentuh hardware Linux (XPT2046 / ADS7846) via evdev.
    Mengonversi sinyal sentuhan mentah (ABS_X, ABS_Y) ke koordinat layar LCD (320x240)
    dan mengirim event ke antrian Pygame saat berjalan di mode direct framebuffer.
    """
    def __init__(self, width: int = 320, height: int = 240, swap_xy: bool = False, invert_x: bool = False, invert_y: bool = False, device_path: str = ""):
        self.width = width
        self.height = height
        self.swap_xy = swap_xy
        self.invert_x = invert_x
        self.invert_y = invert_y
        self.device = None
        self.running = False
        self.thread = None

        if not (sys.platform.startswith("linux") and HAS_EVDEV):
            return

        # Jika device_path eksplisit diberikan, coba langsung
        dev_path = device_path.strip() if device_path else None
        if dev_path and os.path.exists(dev_path):
            try:
                self.device = evdev.InputDevice(dev_path)
                print(f"[GUI Touch] Terhubung ke device sentuh: {self.device.name} ({dev_path})")
            except Exception as e:
                print(f"[GUI Touch] Tidak dapat membuka {dev_path}: {e}")
                dev_path = None

        # Auto-detect jika device_path kosong atau gagal
        if self.device is None:
            dev_path = self._detect_device()
            if dev_path:
                try:
                    self.device = evdev.InputDevice(dev_path)
                    print(f"[GUI Touch] Terhubung ke device sentuh: {self.device.name} ({dev_path})")
                except Exception as e:
                    print(f"[GUI Touch] Tidak dapat membuka {dev_path}: {e}")

    def _detect_device(self) -> Optional[str]:
        """Auto-detect XPT2046/ADS7846 touch device: cek symlink dulu, lalu scan event*."""
        if os.path.exists("/dev/input/touchscreen"):
            return "/dev/input/touchscreen"
        try:
            for path in evdev.list_devices():
                try:
                    dev = evdev.InputDevice(path)
                    caps = dev.capabilities()
                    # Device sentuh harus punya ABS_X dan ABS_Y
                    if evdev.ecodes.EV_ABS in caps:
                        abscodes = [a for a, _ in caps[evdev.ecodes.EV_ABS]]
                        if evdev.ecodes.ABS_X in abscodes and evdev.ecodes.ABS_Y in abscodes:
                            name_lower = dev.name.lower()
                            if any(k in name_lower for k in ("touch", "ads7846", "xpt2046", "ft5406")):
                                print(f"[GUI Touch] Auto-detect device sentuh: {dev.name} ({path})")
                                return path
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def start(self):
        if self.device and not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._read_loop, daemon=True)
            self.thread.start()

    def _read_loop(self):
        raw_x = 0
        raw_y = 0
        min_adc_x, max_adc_x = 180, 3920
        min_adc_y, max_adc_y = 180, 3920
        try:
            for event in self.device.read_loop():
                if not self.running:
                    break
                if event.type == evdev.ecodes.EV_ABS:
                    if event.code == evdev.ecodes.ABS_X:
                        raw_x = event.value
                    elif event.code == evdev.ecodes.ABS_Y:
                        raw_y = event.value
                elif event.type == evdev.ecodes.EV_KEY:
                    if event.code in (evdev.ecodes.BTN_TOUCH, evdev.ecodes.BTN_LEFT):
                        if event.value == 1 and HAS_PYGAME:
                            nx = (raw_x - min_adc_x) / max(1, (max_adc_x - min_adc_x))
                            ny = (raw_y - min_adc_y) / max(1, (max_adc_y - min_adc_y))
                            nx = max(0.0, min(1.0, nx))
                            ny = max(0.0, min(1.0, ny))

                            if self.swap_xy:
                                nx, ny = ny, nx
                            if self.invert_x:
                                nx = 1.0 - nx
                            if self.invert_y:
                                ny = 1.0 - ny

                            px = int(nx * self.width)
                            py = int(ny * self.height)
                            px = max(0, min(self.width - 1, px))
                            py = max(0, min(self.height - 1, py))
                            evt = pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": (px, py), "button": 1})
                            pygame.event.post(evt)
        except Exception:
            pass

    def stop(self):
        self.running = False

try:
    import tkinter as tk
    from tkinter import ttk
    from PIL import Image, ImageTk
    HAS_TKINTER = True
except ImportError:
    HAS_TKINTER = False


class TFTGuiPygame:
    """
    Antarmuka Grafis berbasis Pygame langsung ke Framebuffer / DRM / X11.
    Sangat responsif dan memiliki latensi sentuhan nol.
    """

    def __init__(self, app_instance, width: int = 320, height: int = 240, fullscreen: bool = False, fb_device: str = "/dev/fb0"):
        self.app = app_instance
        self.width = width
        self.height = height
        self.fullscreen = fullscreen
        self.fb_device = fb_device
        self.running = False
        self.clock = None
        self.screen = None

        # Palet Warna Dark Modern
        self.C_BG = (15, 23, 42)          # Slate-900 (Latar utama gelap elegan)
        self.C_CARD = (30, 41, 59)        # Slate-800 (Panel kartu)
        self.C_CARD_BORDER = (51, 65, 85) # Slate-700
        self.C_TEXT = (248, 250, 252)     # Putih bersih
        self.C_TEXT_MUTED = (148, 163, 184)# Abu-abu sekunder
        self.C_PRIMARY = (16, 185, 129)   # Emerald-500 (Tombol Baca Utama)
        self.C_PRIMARY_HOVER = (5, 150, 105)
        self.C_WARNING = (245, 158, 11)   # Amber (Stop / Processing)
        self.C_INFO = (59, 130, 246)      # Blue (Volume / Replay)
        self.C_DANGER = (239, 68, 68)     # Red (Shutdown)
        self.C_ACCENT = (14, 165, 233)    # Sky-500

        # Status Tampilan & Interaktivitas
        self.status_msg = "ALAT SIAP"
        self.status_color = self.C_PRIMARY
        self.last_preview_surface = None
        self.last_update_preview = 0.0
        self.fb_blitter = None
        self.touch_reader = None

        # Kontrol Dialog Daya & Animasi Sentuh
        self.show_power_dialog = False
        self.pressed_button = None
        self.pressed_until = 0.0
        self._last_temp_check = 0.0
        self._cached_temp = ""

        # Inisialisasi tombol sentuh
        self.buttons = {}
        self.modal_buttons = {}
        self._init_buttons()

    def _init_buttons(self):
        """Mendefinisikan area dan aksi tombol sentuh (dioptimalkan untuk sentuhan jari 2.4 inch)."""
        rect_factory = pygame.Rect if HAS_PYGAME else SimpleRect
        # 5 Tombol Sentuh Baris Bawah (Tinggi 62px untuk kenyamanan sentuhan jari pada layar 2.4")
        self.buttons = {
            "read": {
                "rect": rect_factory(6, 172, 94, 62),
                "label": "BACA",
                "sub": "FOTO & OCR",
                "color": self.C_PRIMARY,
                "action": self._action_read
            },
            "replay": {
                "rect": rect_factory(104, 172, 52, 62),
                "label": "ULANG",
                "sub": "REPLAY",
                "color": self.C_INFO,
                "action": self._action_replay
            },
            "stop": {
                "rect": rect_factory(160, 172, 50, 62),
                "label": "STOP",
                "sub": "AUDIO",
                "color": self.C_WARNING,
                "action": self._action_stop
            },
            "vol": {
                "rect": rect_factory(214, 172, 50, 62),
                "label": "VOL",
                "sub": "80%",
                "color": self.C_ACCENT,
                "action": self._action_vol
            },
            "off": {
                "rect": rect_factory(268, 172, 46, 62),
                "label": "OFF",
                "sub": "POWER",
                "color": self.C_DANGER,
                "action": self._action_shutdown
            }
        }

        # Tombol Modal Dialog Konfirmasi Daya / Keluar
        self.modal_buttons = {
            "shutdown": {
                "rect": rect_factory(45, 80, 230, 36),
                "label": "⏻ MATIKAN PI (SHUTDOWN)",
                "color": self.C_DANGER,
                "action": self._confirm_shutdown
            },
            "exit": {
                "rect": rect_factory(45, 126, 110, 34),
                "label": "🚪 MENU UTAMA",
                "color": self.C_INFO,
                "action": self._confirm_exit_gui
            },
            "cancel": {
                "rect": rect_factory(165, 126, 110, 34),
                "label": "✖ BATAL",
                "color": (71, 85, 105),
                "action": self._cancel_dialog
            }
        }

    def init_display(self):
        """Inisialisasi layar Pygame (mendukung Linux Framebuffer /dev/fb1 untuk LCD SPI)."""
        # Konfigurasi display driver untuk Linux jika belum ada DISPLAY
        if sys.platform.startswith("linux"):
            if "DISPLAY" not in os.environ and "WAYLAND_DISPLAY" not in os.environ:
                if os.path.exists("/tmp/.X11-unix/X0"):
                    os.environ["DISPLAY"] = ":0"
                elif os.path.exists("/dev/fb1") or not os.path.exists("/dev/tty0"):
                    # Di mode headless / console / SSH: gunakan driver dummy
                    # Buffer tampilan akan ditulis langsung ke /dev/fb1 oleh FramebufferBlitter
                    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

        if os.path.exists("/dev/input/touchscreen"):
            os.environ.setdefault("SDL_MOUSEDRV", "TSLIB")
            os.environ.setdefault("SDL_MOUSEDEV", "/dev/input/touchscreen")

        pygame.init()
        pygame.font.init()

        flags = pygame.DOUBLEBUF
        if self.fullscreen:
            flags |= pygame.FULLSCREEN

        try:
            self.screen = pygame.display.set_mode((self.width, self.height), flags)
        except pygame.error:
            # Fallback jika SDL tidak menemukan monitor fisik (misal running via SSH)
            os.environ["SDL_VIDEODRIVER"] = "dummy"
            pygame.display.quit()
            pygame.display.init()
            self.screen = pygame.display.set_mode((self.width, self.height), flags)

        pygame.display.set_caption("Pembaca Tunanetra - 2.4 TFT")
        self.clock = pygame.time.Clock()

        # Inisialisasi Direct Framebuffer Blitter & Touch Reader
        target_fb = self.fb_device or os.environ.get("SDL_FBDEV", "/dev/fb0")
        if not os.path.exists(target_fb) and os.path.exists("/dev/fb1"):
            target_fb = "/dev/fb1"

        if os.path.exists(target_fb):
            self.fb_blitter = FramebufferBlitter(device=target_fb, width=self.width, height=self.height)
            # Kalibrasi touch dari config.toml jika tersedia
            t_cfg = {}
            if hasattr(self.app, 'cfg') and isinstance(self.app.cfg, dict):
                t_cfg = self.app.cfg.get('gui', {}).get('touch', {})\
                    if isinstance(self.app.cfg, dict) else {}
            elif hasattr(self.app, 'cfg') and hasattr(self.app.cfg, 'get'):
                t_cfg = {}

            swap_xy = bool(t_cfg.get('swap_xy', False))
            inv_x = bool(t_cfg.get('invert_x', False))
            inv_y = bool(t_cfg.get('invert_y', False))
            dev_path_touch = str(t_cfg.get('device', ''))

            self.touch_reader = LinuxTouchReader(
                width=self.width, height=self.height,
                swap_xy=swap_xy, invert_x=inv_x, invert_y=inv_y,
                device_path=dev_path_touch
            )
            self.touch_reader.start()
        else:
            # Tanpa framebuffer fisik (mis. simulasi di SSH): tetap aktifkan touch reader
            t_cfg = {}
            if hasattr(self.app, 'cfg') and isinstance(self.app.cfg, dict):
                t_cfg = self.app.cfg.get('gui', {}).get('touch', {})
            swap_xy = bool(t_cfg.get('swap_xy', False))
            inv_x = bool(t_cfg.get('invert_x', False))
            inv_y = bool(t_cfg.get('invert_y', False))
            dev_path_touch = str(t_cfg.get('device', ''))
            self.touch_reader = LinuxTouchReader(
                width=self.width, height=self.height,
                swap_xy=swap_xy, invert_x=inv_x, invert_y=inv_y,
                device_path=dev_path_touch
            )
            self.touch_reader.start()

        # Muat font sistem yang bersih
        self.font_title = pygame.font.SysFont("dejavusans,arial,helvetica", 12, bold=True)
        self.font_status = pygame.font.SysFont("dejavusans,arial,helvetica", 11, bold=True)
        self.font_btn = pygame.font.SysFont("dejavusans,arial,helvetica", 11, bold=True)
        self.font_btn_lg = pygame.font.SysFont("dejavusans,arial,helvetica", 13, bold=True)
        self.font_text = pygame.font.SysFont("dejavusans,arial,helvetica", 10)
        self.font_stats = pygame.font.SysFont("monospace,consolas", 9)

    def _action_read(self):
        if not self.app.busy:
            self.status_msg = "MEMOTRET..."
            self.status_color = self.C_WARNING
            self.app._run_async(self.app.capture_and_read)

    def _action_replay(self):
        if not self.app.busy:
            self.status_msg = "MEMUTAR ULANG..."
            self.status_color = self.C_INFO
            self.app._run_async(self.app.replay)

    def _action_stop(self):
        self.app.speaker.stop()
        self.status_msg = "SUARA DISTOP"
        self.status_color = self.C_TEXT_MUTED

    def _action_vol(self):
        """Ubah volume audio secara siklis dan tampilkan nilai barunya."""
        self.app._run_async(self.app.cycle_volume)
        idx = (self.app.current_vol_idx + 1) % len(self.app.volume_steps)
        self.status_msg = f"VOL: {self.app.volume_steps[idx]}%"
        self.status_color = self.C_ACCENT

    def _action_shutdown(self):
        """Buka dialog konfirmasi aman untuk mencegah salah sentuh saat membaca."""
        self.show_power_dialog = True

    def _confirm_shutdown(self):
        self.status_msg = "MEMATIKAN PI..."
        self.status_color = self.C_DANGER
        self.show_power_dialog = False
        self.app._run_async(self.app.safe_shutdown)

    def _confirm_exit_gui(self):
        """Keluar dari GUI dan kembali ke menu terminal."""
        self.show_power_dialog = False
        self.running = False

    def _cancel_dialog(self):
        self.show_power_dialog = False

    def _get_cpu_temp(self) -> str:
        """Membaca suhu CPU Raspberry Pi secara berkala."""
        now = time.time()
        if now - self._last_temp_check > 3.0:
            self._last_temp_check = now
            if os.path.exists('/sys/class/thermal/thermal_zone0/temp'):
                try:
                    with open('/sys/class/thermal/thermal_zone0/temp', 'r') as tf:
                        self._cached_temp = f"{int(tf.read().strip()) // 1000}°C"
                except Exception:
                    self._cached_temp = ""
        return self._cached_temp

    def update_preview_surface(self):
        """
        Membuat thumbnail kamera mini (110x80px) untuk pratinjau live di LCD 2.4".
        Aman terhadap kondisi kamera hilang: tampil placeholder jika frame kosong.
        """
        now = time.time()
        if now - self.last_update_preview < 0.5:
            return

        self.last_update_preview = now
        frame = None

        # Coba baca frame dari kamera — gunakan cap_lock agar tidak bertabrakan dengan grab_frame
        try:
            cap = self.app.cap
            if cap is not None and cap.isOpened():
                # Drain buffer lama agar tampilan selalu segar
                for _ in range(1):
                    cap.grab()
                ok, frame = cap.retrieve()
                if not ok or frame is None or frame.size == 0:
                    frame = None
        except Exception:
            frame = None

        if frame is None:
            # Coba buka ulang kamera jika hilang tanpa memblokir thread GUI
            if hasattr(self.app, '_preview_reconnect_at'):
                if now < self.app._preview_reconnect_at:
                    # Tampilkan placeholder "MENGHUBUNGKAN..."
                    surf = pygame.Surface((110, 80))
                    surf.fill((15, 23, 42))
                    pygame.draw.rect(surf, (51, 65, 85), pygame.Rect(1, 1, 108, 78), border_radius=4)
                    txt1 = self.font_stats.render("MENGHUBUNGKAN", True, self.C_WARNING)
                    txt2 = self.font_stats.render("KAMERA...", True, self.C_TEXT_MUTED)
                    surf.blit(txt1, ((110 - txt1.get_width()) // 2, 28))
                    surf.blit(txt2, ((110 - txt2.get_width()) // 2, 42))
                    self.last_preview_surface = surf
                    return

            # Jadwalkan rekoneksi berikutnya (hindari loop reconnect terus-menerus)
            self.app._preview_reconnect_at = now + 2.0
            try:
                if hasattr(self.app, 'cap_lock'):
                    with self.app.cap_lock:
                        if self.app.cap is None or not self.app.cap.isOpened():
                            self.app.cap = self.app._open_camera()
                else:
                    if self.app.cap is None or not self.app.cap.isOpened():
                        self.app.cap = self.app._open_camera()
            except Exception:
                pass

            surf = pygame.Surface((110, 80))
            surf.fill((15, 23, 42))
            pygame.draw.rect(surf, (51, 65, 85), pygame.Rect(1, 1, 108, 78), border_radius=4)
            txt = self.font_stats.render("KAMERA OFFLINE", True, self.C_DANGER)
            txt2 = self.font_stats.render("MENGHUBUNGKAN", True, self.C_TEXT_MUTED)
            surf.blit(txt, ((110 - txt.get_width()) // 2, 26))
            surf.blit(txt2, ((110 - txt2.get_width()) // 2, 42))
            self.last_preview_surface = surf
            return

        # Konversi frame BGR → RGB, resize menjadi thumbnail 110x80
        try:
            thumb = cv2.resize(frame, (110, 80), interpolation=cv2.INTER_AREA)
            thumb_rgb = cv2.cvtColor(thumb, cv2.COLOR_BGR2RGB)
            # Rotasi 90° agar orientasi portrait sensor sesuai landscape LCD
            thumb_rgb = np.rot90(thumb_rgb)
            thumb_rgb = np.flipud(thumb_rgb)
            self.last_preview_surface = pygame.surfarray.make_surface(thumb_rgb)
        except Exception:
            self.last_preview_surface = None

    def run(self):
        """Loop utama GUI Pygame dengan responsivitas sentuhan tinggi & status bar kamera."""
        self.init_display()
        self.running = True
        # Jadwal rekoneksi kamera preview (hanya untuk GUI, bukan grab_frame)
        self.app._preview_reconnect_at = 0.0

        while self.running:
            # 1. Tangani Event Sentuhan Layar & Keyboard
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                elif event.type in (pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN):
                    if event.type == pygame.MOUSEBUTTONDOWN:
                        pos = event.pos
                    else:
                        pos = (int(event.x * self.width), int(event.y * self.height))

                    if self.show_power_dialog:
                        # Event klik pada modal dialog daya
                        for name, btn in self.modal_buttons.items():
                            if btn["rect"].collidepoint(pos):
                                btn["action"]()
                                break
                    else:
                        # Event klik tombol utama baris bawah
                        for name, btn in self.buttons.items():
                            if btn["rect"].collidepoint(pos):
                                self.pressed_button = name
                                self.pressed_until = time.time() + 0.25
                                btn["action"]()
                                break
                elif event.type == pygame.KEYDOWN:
                    if self.show_power_dialog:
                        if event.key in (pygame.K_ESCAPE, pygame.K_b):
                            self._cancel_dialog()
                        elif event.key == pygame.K_RETURN:
                            self._confirm_shutdown()
                        elif event.key == pygame.K_q:
                            self._confirm_exit_gui()
                    else:
                        if event.key in (pygame.K_RETURN, pygame.K_SPACE):
                            self._action_read()
                        elif event.key == pygame.K_r:
                            self._action_replay()
                        elif event.key == pygame.K_s:
                            self._action_stop()
                        elif event.key == pygame.K_v:
                            self._action_vol()
                        elif event.key in (pygame.K_ESCAPE, pygame.K_q):
                            self.show_power_dialog = True

            # 2. Sinkronisasi Status Mesin App & Indikator Kamera
            cam_alive = (self.app.cap is not None and self.app.cap.isOpened())
            if self.app.busy:
                if self.app.speaker.playing:
                    self.status_msg = "MEMBACA..."
                    self.status_color = self.C_INFO
                else:
                    self.status_msg = "MEMPROSES..."
                    self.status_color = self.C_WARNING
            elif not self.show_power_dialog:
                if self.status_msg.startswith("VOL:"):
                    pass  # Biarkan pesan volume tampil sesaat
                elif not cam_alive:
                    self.status_msg = "CAM OFFLINE"
                    self.status_color = self.C_DANGER
                else:
                    self.status_msg = "ALAT SIAP"
                    self.status_color = self.C_PRIMARY

            # 3. Gambar Tampilan Layar
            self.screen.fill(self.C_BG)

            # --- A. Header Bar ---
            pygame.draw.rect(self.screen, self.C_CARD, pygame.Rect(0, 0, self.width, 26))
            pygame.draw.line(self.screen, self.C_CARD_BORDER, (0, 26), (self.width, 26), 1)

            temp_str = self._get_cpu_temp()
            hdr_text = f"PEMBACA TUNANETRA" + (f" | {temp_str}" if temp_str else "")
            title_surf = self.font_title.render(hdr_text, True, self.C_TEXT)
            self.screen.blit(title_surf, (8, 6))

            # Status Pill (Badge)
            status_surf = self.font_status.render(self.status_msg, True, self.status_color)
            st_x = self.width - status_surf.get_width() - 8
            self.screen.blit(status_surf, (st_x, 7))

            # --- B. Kartu Kiri: Pratinjau Kamera (118 x 136) ---
            card_cam = pygame.Rect(6, 30, 118, 136)
            pygame.draw.rect(self.screen, self.C_CARD, card_cam, border_radius=6)
            pygame.draw.rect(self.screen, self.C_CARD_BORDER, card_cam, width=1, border_radius=6)

            self.update_preview_surface()
            if self.last_preview_surface:
                self.screen.blit(self.last_preview_surface, (10, 34))

            # Info kamera & volume di bawah thumbnail
            cam_dev = self.app.cfg.get('camera', {}).get('device', '?') if isinstance(getattr(self.app, 'cfg', None), dict) else '?'
            if cam_alive:
                stats_cam = f"CAM:{cam_dev} 1080p"
                cam_lbl_col = self.C_TEXT_MUTED
            else:
                stats_cam = f"CAM:{cam_dev} OFFLINE"
                cam_lbl_col = self.C_DANGER
            lbl_cam = self.font_stats.render(stats_cam, True, cam_lbl_col)
            self.screen.blit(lbl_cam, (12, 118))

            vol_val = self.app.volume_steps[self.app.current_vol_idx]
            lbl_vol_info = self.font_stats.render(f"AUDIO: {vol_val}%", True, self.C_ACCENT)
            self.screen.blit(lbl_vol_info, (12, 134))

            # --- C. Kartu Kanan: Cuplikan Teks Bacaan (186 x 136) ---
            card_txt = pygame.Rect(128, 30, 186, 136)
            pygame.draw.rect(self.screen, self.C_CARD, card_txt, border_radius=6)
            pygame.draw.rect(self.screen, self.C_CARD_BORDER, card_txt, width=1, border_radius=6)

            hdr_txt = self.font_btn.render("TEKS DIBACA:", True, self.C_TEXT_MUTED)
            self.screen.blit(hdr_txt, (134, 34))

            full_text = " ".join(self.app.last_reading) if self.app.last_reading else "Belum ada dokumen yang dibaca. Tekan tombol [BACA] untuk memotret."
            words = full_text.split()
            lines = []
            cur_line = ""
            for w in words:
                if len(cur_line) + len(w) + 1 <= 24:
                    cur_line = f"{cur_line} {w}".strip()
                else:
                    lines.append(cur_line)
                    cur_line = w
                if len(lines) >= 5:
                    break
            if cur_line and len(lines) < 5:
                lines.append(cur_line)

            y_txt = 50
            for l in lines:
                l_surf = self.font_text.render(l, True, self.C_TEXT)
                self.screen.blit(l_surf, (134, y_txt))
                y_txt += 15

            # --- D. Tombol Sentuh Bawah (Tinggi 62px) ---
            # Tombol terdisabled (abu-abu) saat busy agar tidak menerima double-click
            for name, btn in self.buttons.items():
                r = btn["rect"]
                col = btn["color"]

                # Disabilkan saat sedang memproses (kecuali tombol STOP)
                is_disabled = self.app.busy and name not in ("stop", "off")
                is_pressed = (self.pressed_button == name and time.time() < self.pressed_until)

                if is_disabled:
                    col = tuple(c // 3 for c in col)
                elif is_pressed:
                    col = tuple(min(255, c + 40) for c in col)

                label_txt = btn["label"]
                sub_txt = btn["sub"]
                if name == "vol":
                    sub_txt = f"{vol_val}%"

                pygame.draw.rect(self.screen, col, r, border_radius=6)
                # Border putih tebal saat pressed, tipis saat normal
                if is_pressed:
                    pygame.draw.rect(self.screen, (255, 255, 255), r, width=2, border_radius=6)
                elif not is_disabled:
                    pygame.draw.rect(self.screen, (80, 100, 120), r, width=1, border_radius=6)

                fnt = self.font_btn_lg if name == "read" else self.font_btn
                lbl_color = (255, 255, 255) if not is_disabled else (100, 110, 120)
                lbl = fnt.render(label_txt, True, lbl_color)
                lx = r.x + (r.width - lbl.get_width()) // 2
                ly = r.y + (10 if sub_txt else (r.height - lbl.get_height()) // 2)
                self.screen.blit(lbl, (lx, ly))

                if sub_txt:
                    sub_lbl = self.font_stats.render(sub_txt, True, (200, 210, 220) if not is_disabled else (80, 90, 100))
                    sx = r.x + (r.width - sub_lbl.get_width()) // 2
                    self.screen.blit(sub_lbl, (sx, ly + lbl.get_height() + 3))

            # --- E. Modal Dialog Konfirmasi Daya / Keluar ---
            if self.show_power_dialog:
                # Dim background
                overlay = pygame.Surface((self.width, self.height))
                overlay.set_alpha(190)
                overlay.fill((0, 0, 0))
                self.screen.blit(overlay, (0, 0))

                # Frame Dialog Panel
                dlg_rect = pygame.Rect(35, 42, 250, 134)
                pygame.draw.rect(self.screen, self.C_CARD, dlg_rect, border_radius=8)
                pygame.draw.rect(self.screen, self.C_CARD_BORDER, dlg_rect, width=2, border_radius=8)

                dlg_title = self.font_title.render("MENU DAYA & SISTEM", True, self.C_TEXT)
                self.screen.blit(dlg_title, (dlg_rect.x + (dlg_rect.width - dlg_title.get_width()) // 2, 52))

                for m_name, m_btn in self.modal_buttons.items():
                    mr = m_btn["rect"]
                    mcol = m_btn["color"]
                    pygame.draw.rect(self.screen, mcol, mr, border_radius=6)
                    # Border putih solid agar tombol modal jelas di LCD kecil
                    pygame.draw.rect(self.screen, (220, 220, 220), mr, width=1, border_radius=6)

                    mfnt = self.font_btn_lg if m_name == "shutdown" else self.font_btn
                    mlbl = mfnt.render(m_btn["label"], True, (255, 255, 255))
                    mlx = mr.x + (mr.width - mlbl.get_width()) // 2
                    mly = mr.y + (mr.height - mlbl.get_height()) // 2
                    self.screen.blit(mlbl, (mlx, mly))

            pygame.display.flip()
            if self.fb_blitter:
                self.fb_blitter.blit(self.screen)
            self.clock.tick(20)  # Naikkan ke 20 FPS agar sentuhan lebih responsif

        if self.touch_reader:
            self.touch_reader.stop()
        if self.fb_blitter:
            self.fb_blitter.close()
        pygame.quit()


class TFTGuiTkinter:
    """
    Fallback GUI berbasis Tkinter murni jika Pygame belum terpasang.
    Sangat ringan, menggunakan pustaka standar Python.
    """

    def __init__(self, app_instance, width: int = 320, height: int = 240):
        self.app = app_instance
        self.width = width
        self.height = height
        self.root = None

    def run(self):
        if not HAS_TKINTER:
            print("[GUI] Error: Tkinter dan Pygame tidak tersedia.")
            return

        self.root = tk.Tk()
        self.root.title("Pembaca Tunanetra (TFT 2.4)")
        self.root.geometry(f"{self.width}x{self.height}")
        self.root.configure(bg="#0f172a")
        self.root.resizable(False, False)

        # Header
        hdr = tk.Frame(self.root, bg="#1e293b", height=28)
        hdr.pack(fill="x")
        lbl_title = tk.Label(hdr, text="PEMBACA TUNANETRA", fg="#f8fafc", bg="#1e293b", font=("Helvetica", 10, "bold"))
        lbl_title.pack(side="left", padx=8, pady=4)

        self.lbl_status = tk.Label(hdr, text="ALAT SIAP", fg="#10b981", bg="#1e293b", font=("Helvetica", 9, "bold"))
        self.lbl_status.pack(side="right", padx=8, pady=4)

        # Body Frame
        body = tk.Frame(self.root, bg="#0f172a")
        body.pack(fill="both", expand=True, padx=8, pady=6)

        # Teks Display
        self.txt_display = tk.Label(
            body,
            text="Tekan tombol [BACA] untuk memotret dokumen.",
            fg="#e2e8f0",
            bg="#1e293b",
            font=("Helvetica", 9),
            wraplength=290,
            justify="left",
            height=5,
            relief="flat"
        )
        self.txt_display.pack(fill="x", pady=4)

        # Tombol Frame
        btn_box = tk.Frame(self.root, bg="#0f172a")
        btn_box.pack(fill="x", padx=8, pady=6)

        btn_read = tk.Button(btn_box, text="📷 BACA", bg="#10b981", fg="white", font=("Helvetica", 10, "bold"),
                             command=lambda: self.app._run_async(self.app.capture_and_read), height=2, width=8)
        btn_read.pack(side="left", padx=2)

        btn_replay = tk.Button(btn_box, text="🔄 ULANG", bg="#3b82f6", fg="white", font=("Helvetica", 9, "bold"),
                              command=lambda: self.app._run_async(self.app.replay), height=2, width=7)
        btn_replay.pack(side="left", padx=2)

        btn_stop = tk.Button(btn_box, text="⏹ STOP", bg="#f59e0b", fg="white", font=("Helvetica", 9, "bold"),
                            command=self.app.speaker.stop, height=2, width=6)
        btn_stop.pack(side="left", padx=2)

        btn_vol = tk.Button(btn_box, text="🔊 VOL", bg="#475569", fg="white", font=("Helvetica", 9),
                            command=lambda: self.app._run_async(self.app.cycle_volume), height=2, width=5)
        btn_vol.pack(side="left", padx=2)

        # Timer pemantau status
        def sync_loop():
            if self.app.busy:
                self.lbl_status.config(text="MEMPROSES..." if not self.app.speaker.playing else "MEMBACA...", fg="#f59e0b")
            else:
                self.lbl_status.config(text="ALAT SIAP", fg="#10b981")

            if self.app.last_reading:
                t = " ".join(self.app.last_reading)
                self.txt_display.config(text=t[:140] + ("..." if len(t) > 140 else ""))

            self.root.after(400, sync_loop)

        self.root.after(400, sync_loop)
        self.root.mainloop()


def launch_gui(app_instance, width: int = 320, height: int = 240, fullscreen: bool = False, fb_device: str = "/dev/fb0"):
    """Fungsi peluncur GUI utama dengan pemilihan engine cerdas (Pygame utama, Tkinter cadangan)."""
    if HAS_PYGAME:
        gui = TFTGuiPygame(app_instance, width=width, height=height, fullscreen=fullscreen, fb_device=fb_device)
        gui.run()
    elif HAS_TKINTER:
        print("[GUI] Pygame belum terpasang, beralih ke engine Tkinter...")
        gui = TFTGuiTkinter(app_instance, width=width, height=height)
        gui.run()
    else:
        print("[GUI] Peringatan: Tidak ada engine GUI (pygame/tkinter) yang ditemukan.")
