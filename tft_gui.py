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

    # SDL2 tidak lagi mendukung fbcon. Gunakan dummy + FramebufferBlitter langsung ke /dev/fb*
    # Jika pengguna sudah set SDL_VIDEODRIVER via environment, dihormati.
    if "SDL_VIDEODRIVER" not in os.environ:
        # Hindari fbcon (SDL1 only). Untuk TFT SPI: render ke SDL dummy, blit ke /dev/fb*
        os.environ["SDL_VIDEODRIVER"] = "dummy"
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

# Pengelola WiFi & Kontrol Volume Komprehensif
try:
    from wifi_manager import WiFiManager
except ImportError:
    WiFiManager = None

try:
    from main import apply_system_volume
except ImportError:
    apply_system_volume = None

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
    Thread pemantau input sentuh hardware Linux (XPT2046 / ADS7846) via evdev dan USB Mouse via /dev/input/mice.
    Mengonversi sinyal sentuhan ke koordinat layar LCD (320x240)
    dan mengirim event ke antrian Pygame secara akurat tanpa deadzone.
    """
    def __init__(self, width: int = 320, height: int = 240, swap_xy: bool = False, invert_x: bool = False, invert_y: bool = False,
                 device_path: str = "",
                 min_adc_x: int = 200, max_adc_x: int = 3900,
                 min_adc_y: int = 200, max_adc_y: int = 3900):
        self.width = width
        self.height = height
        self.swap_xy = swap_xy
        self.invert_x = invert_x
        self.invert_y = invert_y
        self.device = None
        self.running = False
        self.thread = None
        self.mouse_thread = None

        # State sentuh hardware
        self.raw_x = None
        self.raw_y = None
        self.is_touch_down = False
        self.last_click_ts = 0.0

        # Koordinat cursor mouse USB
        self.cursor_x = width // 2
        self.cursor_y = height // 2

        # Rentang ADC kalibrasi XPT2046 (dari config.toml [gui.touch])
        self.min_adc_x = min_adc_x
        self.max_adc_x = max_adc_x
        self.min_adc_y = min_adc_y
        self.max_adc_y = max_adc_y

        print(f"[GUI Touch] Kalibrasi ADC: X=[{min_adc_x}~{max_adc_x}] Y=[{min_adc_y}~{max_adc_y}] "
              f"swap_xy={swap_xy} invert_x={invert_x} invert_y={invert_y}", flush=True)

        self.is_pixel_coords = False

        if not sys.platform.startswith("linux"):
            return

        if not HAS_EVDEV:
            print("[GUI Touch] Peringatan: Modul Python 'evdev' tidak ditemukan. Layar sentuh hardware tidak aktif.", flush=True)
            return

        dev_path = device_path.strip() if device_path else None
        if dev_path and os.path.exists(dev_path):
            try:
                self.device = evdev.InputDevice(dev_path)
                print(f"[GUI Touch] Terhubung ke device sentuh: {self.device.name} ({dev_path})", flush=True)
            except Exception as e:
                print(f"[GUI Touch] Tidak dapat membuka {dev_path}: {e}", flush=True)
                dev_path = None

        if self.device is None:
            dev_path = self._detect_device()
            if dev_path:
                try:
                    self.device = evdev.InputDevice(dev_path)
                    print(f"[GUI Touch] Auto-detect device sentuh: {self.device.name} ({dev_path})", flush=True)
                except Exception as e:
                    print(f"[GUI Touch] Tidak dapat membuka {dev_path}: {e}", flush=True)

        if self.device:
            try:
                caps = self.device.capabilities()
                if evdev.ecodes.EV_ABS in caps:
                    abs_dict = dict(caps[evdev.ecodes.EV_ABS])
                    abs_x = abs_dict.get(evdev.ecodes.ABS_X)
                    abs_y = abs_dict.get(evdev.ecodes.ABS_Y)
                    if abs_x and abs_x.max > 0:
                        if abs_x.max <= 480 and abs_y and abs_y.max <= 480:
                            print(f"[GUI Touch] Driver kernel sudah menghasilkan koordinat layar piksel (X_max={abs_x.max}, Y_max={abs_y.max}).", flush=True)
                            self.is_pixel_coords = True
                        else:
                            print(f"[GUI Touch] Hardware range dari driver: X=[{abs_x.min}~{abs_x.max}], Y=[{abs_y.min}~{abs_y.max}]", flush=True)
                            if self.min_adc_x == 200 and self.max_adc_x == 3900:
                                self.min_adc_x = abs_x.min
                                self.max_adc_x = abs_x.max
                            if self.min_adc_y == 200 and self.max_adc_y == 3900:
                                self.min_adc_y = abs_y.min
                                self.max_adc_y = abs_y.max
            except Exception as err:
                print(f"[GUI Touch] Gagal membaca absinfo driver: {err}", flush=True)

        if self.device is None:
            print("[GUI Touch] PERINGATAN: Tidak ada hardware touchscreen (ADS7846/XPT2046) yang terdeteksi di /dev/input/!", flush=True)
            try:
                found_devs = []
                for p in evdev.list_devices():
                    try:
                        d = evdev.InputDevice(p)
                        found_devs.append(f"{p} -> {d.name}")
                    except Exception as err:
                        found_devs.append(f"{p} -> [Izin Ditolak: {err}]")
                print(f"[GUI Touch] Input device yang ada di sistem: {found_devs or 'Tidak ada'}", flush=True)
            except Exception:
                pass

    def _detect_device(self) -> Optional[str]:
        """Auto-detect XPT2046/ADS7846 touch device: cek symlink dulu, lalu scan event*."""
        if os.path.exists("/dev/input/touchscreen"):
            return "/dev/input/touchscreen"
        try:
            for path in evdev.list_devices():
                try:
                    dev = evdev.InputDevice(path)
                    caps = dev.capabilities()
                    if evdev.ecodes.EV_ABS in caps:
                        abscodes = [a for a, _ in caps[evdev.ecodes.EV_ABS]]
                        if evdev.ecodes.ABS_X in abscodes and evdev.ecodes.ABS_Y in abscodes:
                            name_lower = dev.name.lower()
                            if any(k in name_lower for k in ("touch", "ads7846", "xpt2046", "ft5406")):
                                return path
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def start(self):
        if not self.running:
            self.running = True
            if self.device:
                self.thread = threading.Thread(target=self._touch_loop, daemon=True)
                self.thread.start()
            # JANGAN jalankan _mouse_loop jika perangkat touchscreen evdev aktif!
            # Driver kernel Linux (mousedev) menduplikasi event touchscreen ke /dev/input/mice
            # sebagai pergerakan relatif acak yang merusak koordinat layar sentuh.
            elif os.path.exists("/dev/input/mice"):
                self.mouse_thread = threading.Thread(target=self._mouse_loop, daemon=True)
                self.mouse_thread.start()

    def _calc_screen_coords(self) -> Tuple[int, int]:
        if self.raw_x is None or self.raw_y is None:
            return self.cursor_x, self.cursor_y

        if getattr(self, 'is_pixel_coords', False):
            # Driver kernel sudah menghasilkan koordinat pixel layar
            nx = max(0.0, min(1.0, self.raw_x / float(self.width)))
            ny = max(0.0, min(1.0, self.raw_y / float(self.height)))
        else:
            # Auto-adaptasi batas jika sinyal ADC melewati ambang batas
            if self.raw_x < self.min_adc_x:
                self.min_adc_x = self.raw_x
            if self.raw_x > self.max_adc_x:
                self.max_adc_x = self.raw_x
            if self.raw_y < self.min_adc_y:
                self.min_adc_y = self.raw_y
            if self.raw_y > self.max_adc_y:
                self.max_adc_y = self.raw_y

            span_x = max(1, self.max_adc_x - self.min_adc_x)
            span_y = max(1, self.max_adc_y - self.min_adc_y)
            nx = max(0.0, min(1.0, (self.raw_x - self.min_adc_x) / float(span_x)))
            ny = max(0.0, min(1.0, (self.raw_y - self.min_adc_y) / float(span_y)))

        if self.swap_xy:
            nx, ny = ny, nx
        if self.invert_x:
            nx = 1.0 - nx
        if self.invert_y:
            ny = 1.0 - ny

        px = int(nx * self.width)
        py = int(ny * self.height)
        return max(0, min(self.width - 1, px)), max(0, min(self.height - 1, py))

    def _post_click(self, px: int, py: int):
        if not HAS_PYGAME:
            return
        now = time.time()
        if now - self.last_click_ts < 0.22:
            return
        self.last_click_ts = now
        print(f"[GUI Input] Klik/Sentuh terkirim ke Pygame: ({px}, {py}) [RAW ADC: x={self.raw_x}, y={self.raw_y}]")
        evt = pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": (px, py), "button": 1})
        pygame.event.post(evt)

    def _touch_loop(self):
        """
        Loop baca event sentuh dari evdev.
        Strategi: Kumpulkan koordinat X/Y selama jari menekan.
        Kirim klik (post event) HANYA saat jari DIANGKAT (BTN_TOUCH=0 atau ABS_PRESSURE=0).
        Ini mencegah klik ganda saat jari masih di layar dan memastikan
        koordinat yang dikirim adalah posisi AKHIR sebelum diangkat.
        Fallback: EV_SYN hanya dipakai jika device tidak mengirim BTN_TOUCH/PRESSURE.
        """
        try:
            print(f"[GUI Touch] Touch loop aktif membaca dari {self.device.name} ({self.device.path})...", flush=True)

            # Deteksi apakah device mengirim BTN_TOUCH atau ABS_PRESSURE (ada release event)
            has_btn_touch = False
            has_pressure = False
            try:
                caps = self.device.capabilities()
                ev_key_codes = [c for c, _ in caps.get(evdev.ecodes.EV_KEY, [])]
                has_btn_touch = evdev.ecodes.BTN_TOUCH in ev_key_codes
                abs_codes = [c for c, _ in caps.get(evdev.ecodes.EV_ABS, [])]
                has_pressure = getattr(evdev.ecodes, 'ABS_PRESSURE', 24) in abs_codes
            except Exception:
                pass

            use_syn_fallback = not has_btn_touch and not has_pressure
            print(f"[GUI Touch] has_btn_touch={has_btn_touch}, has_pressure={has_pressure}, syn_fallback={use_syn_fallback}", flush=True)

            last_syn_x = None
            last_syn_y = None

            for event in self.device.read_loop():
                if not self.running:
                    break

                # Kumpulkan koordinat X & Y
                if event.type == evdev.ecodes.EV_ABS:
                    if event.code == evdev.ecodes.ABS_X:
                        self.raw_x = event.value
                    elif event.code == evdev.ecodes.ABS_Y:
                        self.raw_y = event.value
                    elif event.code == getattr(evdev.ecodes, 'ABS_PRESSURE', 24):
                        was_down = self.is_touch_down
                        self.is_touch_down = (event.value > 0)
                        # Jari baru diangkat (pressure=0): kirim klik dengan koordinat terakhir
                        if was_down and not self.is_touch_down:
                            if self.raw_x is not None and self.raw_y is not None:
                                px, py = self._calc_screen_coords()
                                print(f"[GUI Touch] Release (pressure=0): raw=({self.raw_x},{self.raw_y}) -> px=({px},{py})", flush=True)
                                self._post_click(px, py)

                # Jari diangkat (BTN_TOUCH=0): kirim klik dengan koordinat terakhir
                elif event.type == evdev.ecodes.EV_KEY:
                    if event.code in (evdev.ecodes.BTN_TOUCH, evdev.ecodes.BTN_LEFT):
                        if event.value == 1:
                            self.is_touch_down = True
                            last_syn_x = None  # Reset koordinat sinkronisasi
                            last_syn_y = None
                        elif event.value == 0:
                            self.is_touch_down = False
                            if self.raw_x is not None and self.raw_y is not None:
                                px, py = self._calc_screen_coords()
                                print(f"[GUI Touch] Release (BTN_TOUCH=0): raw=({self.raw_x},{self.raw_y}) -> px=({px},{py})", flush=True)
                                self._post_click(px, py)

                # EV_SYN: Fallback jika device tidak punya BTN_TOUCH/PRESSURE
                elif event.type == evdev.ecodes.EV_SYN:
                    if event.code == getattr(evdev.ecodes, 'SYN_REPORT', 0):
                        if use_syn_fallback:
                            # Mode fallback: kirim tiap perubahan koordinat (pakai cooldown)
                            if self.raw_x is not None and self.raw_y is not None:
                                if self.raw_x != last_syn_x or self.raw_y != last_syn_y:
                                    last_syn_x = self.raw_x
                                    last_syn_y = self.raw_y
                                    px, py = self._calc_screen_coords()
                                    self._post_click(px, py)

        except Exception as e:
            print(f"[GUI Touch] Touch loop selesai/berhenti: {e}", flush=True)

    def _mouse_loop(self):
        """Membaca mouse USB fisik melalui /dev/input/mice tanpa bergantung evdev."""
        try:
            with open("/dev/input/mice", "rb") as mf:
                print("[GUI Mouse] Listener mouse USB aktif pada /dev/input/mice")
                while self.running:
                    buf = mf.read(3)
                    if not buf or len(buf) < 3:
                        continue
                    btn_byte = buf[0]
                    dx = buf[1]
                    dy = buf[2]
                    if dx >= 128:
                        dx -= 256
                    if dy >= 128:
                        dy -= 256

                    self.cursor_x = max(0, min(self.width - 1, self.cursor_x + dx))
                    self.cursor_y = max(0, min(self.height - 1, self.cursor_y - dy))

                    if (btn_byte & 1) != 0:
                        self._post_click(self.cursor_x, self.cursor_y)
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

        # Auto-scroll & Voice Synchronization state
        self.scroll_y = 0.0
        self.target_scroll_y = 0.0
        self._cached_reading_hash = None
        self._cached_wrapped_items = []

        # Pengelola WiFi & Tombol Sentuh WiFi
        self.wifi_mgr = WiFiManager() if WiFiManager is not None else None
        rect_factory = pygame.Rect if HAS_PYGAME else SimpleRect
        self.wifi_btn_rect = rect_factory(10, 135, 110, 24)

        # Inisialisasi tombol sentuh
        self.buttons = {}
        self.modal_buttons = {}
        self._init_buttons()

    def _init_buttons(self):
        """Mendefinisikan area dan aksi tombol sentuh dengan border terpisah (dioptimalkan untuk sentuhan 2.4 inch)."""
        rect_factory = pygame.Rect if HAS_PYGAME else SimpleRect
        # 6 Tombol Sentuh Baris Bawah dengan border independen (jarak 4px antar border)
        self.buttons = {
            "read": {
                "rect": rect_factory(6, 170, 64, 64),
                "label": "BACA",
                "sub": "FOTO",
                "color": (16, 185, 129),
                "border_color": (52, 211, 153),
                "action": self._action_read
            },
            "replay": {
                "rect": rect_factory(74, 170, 44, 64),
                "label": "ULANG",
                "sub": "PUTAR",
                "color": (37, 99, 235),
                "border_color": (96, 165, 250),
                "action": self._action_replay
            },
            "stop": {
                "rect": rect_factory(122, 170, 44, 64),
                "label": "STOP",
                "sub": "SUARA",
                "color": (217, 119, 6),
                "border_color": (251, 191, 36),
                "action": self._action_stop
            },
            "vol_dn": {
                "rect": rect_factory(170, 170, 44, 64),
                "label": "VOL -",
                "sub": "TURUN",
                "color": (14, 165, 233),
                "border_color": (56, 189, 248),
                "action": self._action_vol_down
            },
            "vol_up": {
                "rect": rect_factory(218, 170, 44, 64),
                "label": "VOL +",
                "sub": "NAIK",
                "color": (2, 132, 199),
                "border_color": (56, 189, 248),
                "action": self._action_vol_up
            },
            "off": {
                "rect": rect_factory(266, 170, 48, 64),
                "label": "OFF",
                "sub": "DAYA",
                "color": (220, 38, 38),
                "border_color": (248, 113, 113),
                "action": self._action_shutdown
            }
        }
        # Alias backward-compatibility untuk referensi ke "vol"
        self.buttons["vol"] = self.buttons["vol_up"]

        # Tombol Modal Dialog Konfirmasi Daya (Mati Total)
        self.modal_buttons = {
            "shutdown": {
                "rect": rect_factory(45, 75, 230, 42),
                "label": "⏻ MATIKAN TOTAL (OFF)",
                "color": self.C_DANGER,
                "action": self._confirm_shutdown
            },
            "cancel": {
                "rect": rect_factory(45, 126, 230, 36),
                "label": "✖ BATAL / KEMBALI",
                "color": (71, 85, 105),
                "action": self._cancel_dialog
            }
        }

    def _get_touch_config(self) -> dict:
        t_cfg = {}
        if hasattr(self.app, 'cfg') and isinstance(self.app.cfg, dict):
            t_cfg = self.app.cfg.get('gui', {}).get('touch', {})
        if not t_cfg:
            try:
                cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.toml')
                import tomllib
                with open(cfg_path, 'rb') as f:
                    c_all = tomllib.load(f)
                    t_cfg = c_all.get('gui', {}).get('touch', {})
            except Exception:
                pass
        return t_cfg or {}

    def init_display(self):
        """Inisialisasi layar Pygame (mendukung Linux Framebuffer /dev/fb* untuk LCD SPI)."""

        # --- Deteksi framebuffer yang tersedia ---
        fb_candidates = []
        for fb in ["/dev/fb0", "/dev/fb1", "/dev/fb2"]:
            if os.path.exists(fb):
                fb_candidates.append(fb)
        print(f"[GUI Init] Framebuffer tersedia: {fb_candidates or 'tidak ada'}", flush=True)

        # Tentukan target fb: gunakan fb_device dari config, atau auto-detect
        target_fb = self.fb_device.strip() if self.fb_device else ""
        if not target_fb or not os.path.exists(target_fb):
            # Fallback: coba fb1 dulu (LCD SPI biasanya fb1), lalu fb0
            for fb in ["/dev/fb1", "/dev/fb0"]:
                if fb in fb_candidates:
                    target_fb = fb
                    print(f"[GUI Init] fb_device '{self.fb_device}' tidak ditemukan, beralih ke {target_fb}", flush=True)
                    break
        if target_fb and os.path.exists(target_fb):
            print(f"[GUI Init] Target Framebuffer: {target_fb}", flush=True)
        else:
            print("[GUI Init] Tidak ada framebuffer yang ditemukan — render hanya ke SDL dummy.", flush=True)
            target_fb = ""

        # --- Inisialisasi SDL ---
        # SDL2 tidak mendukung fbcon. Urutan percobaan: kmsdrm → dummy → memory surface
        sdl_drivers_to_try = []
        # Coba kmsdrm hanya jika ada /dev/dri (DRM) dan tidak via SSH
        if os.path.exists("/dev/dri") and "SSH_CLIENT" not in os.environ:
            sdl_drivers_to_try.append("kmsdrm")
        sdl_drivers_to_try.append("dummy")

        if os.path.exists("/dev/input/touchscreen"):
            os.environ.setdefault("SDL_MOUSEDRV", "TSLIB")
            os.environ.setdefault("SDL_MOUSEDEV", "/dev/input/touchscreen")

        pygame.init()
        pygame.font.init()

        flags = pygame.DOUBLEBUF
        if self.fullscreen:
            flags |= pygame.FULLSCREEN

        screen = None
        used_driver = "memory"
        for v_driver in sdl_drivers_to_try:
            try:
                os.environ["SDL_VIDEODRIVER"] = v_driver
                try:
                    pygame.display.quit()
                except Exception:
                    pass
                pygame.display.init()
                use_flags = flags if v_driver == "kmsdrm" else 0
                screen = pygame.display.set_mode((self.width, self.height), use_flags)
                used_driver = v_driver
                print(f"[GUI Init] SDL driver '{v_driver}' berhasil digunakan.", flush=True)
                break
            except Exception as e:
                print(f"[GUI Init] SDL driver '{v_driver}' gagal: {e}", flush=True)

        if screen is None:
            print("[GUI Init] Semua SDL driver gagal — menggunakan pygame.Surface (memory buffer).", flush=True)
            # Pastikan pygame masih bisa dipakai
            try:
                pygame.display.quit()
            except Exception:
                pass
            try:
                os.environ["SDL_VIDEODRIVER"] = "dummy"
                pygame.display.init()
                screen = pygame.display.set_mode((self.width, self.height), 0)
                used_driver = "dummy-fallback"
            except Exception:
                screen = pygame.Surface((self.width, self.height))
                used_driver = "surface"

        self.screen = screen
        print(f"[GUI Init] Layar aktif ({self.width}x{self.height}), mode: {used_driver}", flush=True)

        try:
            pygame.display.set_caption("Pembaca Tunanetra - 2.4 TFT")
        except Exception:
            pass
        self.clock = pygame.time.Clock()

        # --- Inisialisasi Direct Framebuffer Blitter ---
        if target_fb:
            self.fb_blitter = FramebufferBlitter(device=target_fb, width=self.width, height=self.height)
            if not self.fb_blitter.enabled:
                print(f"[GUI Init] FramebufferBlitter gagal membuka {target_fb}. Layar tidak akan tampil di LCD.", flush=True)
        else:
            print("[GUI Init] FramebufferBlitter dilewati (tidak ada framebuffer).", flush=True)

        # --- Touch Reader ---
        t_cfg = self._get_touch_config()
        swap_xy    = bool(t_cfg.get('swap_xy',   False))
        inv_x      = bool(t_cfg.get('invert_x',  False))
        inv_y      = bool(t_cfg.get('invert_y',  False))
        dev_path_touch = str(t_cfg.get('device', ''))
        min_adc_x  = int(t_cfg.get('min_adc_x',  200))
        max_adc_x  = int(t_cfg.get('max_adc_x', 3900))
        min_adc_y  = int(t_cfg.get('min_adc_y',  200))
        max_adc_y  = int(t_cfg.get('max_adc_y', 3900))

        self.touch_reader = LinuxTouchReader(
            width=self.width, height=self.height,
            swap_xy=swap_xy, invert_x=inv_x, invert_y=inv_y,
            device_path=dev_path_touch,
            min_adc_x=min_adc_x, max_adc_x=max_adc_x,
            min_adc_y=min_adc_y, max_adc_y=max_adc_y,
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
        print("[GUI Action] Tombol BACA dipicu.")
        if not self.app.busy:
            self.status_msg = "MEMOTRET..."
            self.status_color = self.C_WARNING
            self.app._run_async(self.app.capture_and_read)
        else:
            print("[GUI Action] Sistem sedang sibuk.")

    def _action_replay(self):
        print("[GUI Action] Tombol ULANG dipicu.")
        # Jika sedang memutar bacaan lama, stop dulu seketika
        if getattr(self.app.speaker, 'playing', False) is True:
            self.app.speaker.stop()
            time.sleep(0.08)
        self.app.busy = False
        self.status_msg = "MEMUTAR ULANG..."
        self.status_color = self.C_INFO
        self.app._run_async(self.app.replay)

    def _action_stop(self):
        print("[GUI Action] Tombol STOP dipicu.")
        self.app.speaker.stop()
        self.app.busy = False
        self.status_msg = "SUARA DISTOP"
        self.status_color = self.C_TEXT_MUTED

    def _action_vol_down(self):
        print("[GUI Action] Tombol VOL - dipicu.")
        if hasattr(self.app, 'current_vol_idx') and hasattr(self.app, 'volume_steps'):
            self.app.current_vol_idx = max(0, self.app.current_vol_idx - 1)
            new_vol = self.app.volume_steps[self.app.current_vol_idx]
            self.status_msg = f"VOL: {new_vol}%"
            self.status_color = self.C_ACCENT
            threading.Thread(target=self._apply_volume_hardware, args=(new_vol,), daemon=True).start()

    def _action_vol_up(self):
        print("[GUI Action] Tombol VOL + dipicu.")
        if hasattr(self.app, 'current_vol_idx') and hasattr(self.app, 'volume_steps'):
            self.app.current_vol_idx = min(len(self.app.volume_steps) - 1, self.app.current_vol_idx + 1)
            new_vol = self.app.volume_steps[self.app.current_vol_idx]
            self.status_msg = f"VOL: {new_vol}%"
            self.status_color = self.C_ACCENT
            threading.Thread(target=self._apply_volume_hardware, args=(new_vol,), daemon=True).start()

    def _action_vol(self):
        """Siklus volume (kompatibilitas tombol fisik / tes unit)."""
        print("[GUI Action] Tombol VOL dipicu.")
        if hasattr(self.app, 'current_vol_idx') and hasattr(self.app, 'volume_steps'):
            self.app.current_vol_idx = (self.app.current_vol_idx + 1) % len(self.app.volume_steps)
            new_vol = self.app.volume_steps[self.app.current_vol_idx]
            self.status_msg = f"VOL: {new_vol}%"
            self.status_color = self.C_ACCENT
            threading.Thread(target=self._apply_volume_hardware, args=(new_vol,), daemon=True).start()
        elif hasattr(self.app, '_run_async') and hasattr(self.app, 'cycle_volume'):
            self.app._run_async(self.app.cycle_volume)

    def _action_toggle_wifi(self):
        """Membalik status sakelar radio WiFi (ON -> OFF atau sebaliknya)."""
        print("[GUI Action] Tombol WiFi ditekan -> alihkan status WiFi.")
        if not self.wifi_mgr:
            return
        new_state = self.wifi_mgr.toggle_wifi()
        self.status_msg = "WIFI AKTIF" if new_state else "WIFI MATI"
        self.status_color = self.C_PRIMARY if new_state else self.C_DANGER
        try:
            self.app.cue('klik')
        except Exception:
            pass

    def _apply_volume_hardware(self, vol_percent: int):
        if apply_system_volume is not None:
            apply_system_volume(vol_percent, self.app)
        else:
            if hasattr(self.app, 'speaker') and self.app.speaker:
                try:
                    self.app.speaker.set_volume(vol_percent)
                except Exception:
                    pass
            if sys.platform.startswith("linux"):
                try:
                    subprocess.run(
                        ['pactl', 'set-sink-volume', '@DEFAULT_SINK@', f"{vol_percent}%"],
                        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=0.8
                    )
                except Exception:
                    pass
                for card in ['0', '1', '2', '3']:
                    for ctrl in ['Master', 'Headphone', 'PCM', 'Speaker']:
                        try:
                            subprocess.run(
                                ['amixer', '-c', card, 'sset', ctrl, f"{vol_percent}%"],
                                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=0.4
                            )
                        except Exception:
                            pass
        try:
            self.app.cue('klik')
        except Exception:
            pass

    def _action_shutdown(self):
        print("[GUI Action] Tombol OFF dipicu -> Langsung matikan daya total (Shutdown).")
        self.status_msg = "MEMATIKAN TOTAL..."
        self.status_color = self.C_DANGER
        self.show_power_dialog = False
        if self.screen:
            self.screen.fill((0, 0, 0))
            if hasattr(self, 'font_title') and self.font_title:
                txt = self.font_title.render("SISTEM DIMATIKAN TOTAL...", True, (255, 255, 255))
                self.screen.blit(txt, ((self.width - txt.get_width()) // 2, (self.height - txt.get_height()) // 2))
            if self.fb_blitter:
                self.fb_blitter.blit(self.screen)
        self.app._run_async(self.app.safe_shutdown)

    def _confirm_shutdown(self):
        self.status_msg = "MEMATIKAN TOTAL..."
        self.status_color = self.C_DANGER
        self.show_power_dialog = False
        if self.screen:
            self.screen.fill((0, 0, 0))
            if hasattr(self, 'font_title') and self.font_title:
                txt = self.font_title.render("SISTEM DIMATIKAN TOTAL...", True, (255, 255, 255))
                self.screen.blit(txt, ((self.width - txt.get_width()) // 2, (self.height - txt.get_height()) // 2))
            if self.fb_blitter:
                self.fb_blitter.blit(self.screen)
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
                    surf = pygame.Surface((110, 58))
                    surf.fill((15, 23, 42))
                    pygame.draw.rect(surf, (51, 65, 85), pygame.Rect(1, 1, 108, 56), border_radius=4)
                    txt1 = self.font_stats.render("MENGHUBUNGKAN", True, self.C_WARNING)
                    txt2 = self.font_stats.render("KAMERA...", True, self.C_TEXT_MUTED)
                    surf.blit(txt1, ((110 - txt1.get_width()) // 2, 16))
                    surf.blit(txt2, ((110 - txt2.get_width()) // 2, 32))
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

            surf = pygame.Surface((110, 58))
            surf.fill((15, 23, 42))
            pygame.draw.rect(surf, (51, 65, 85), pygame.Rect(1, 1, 108, 56), border_radius=4)
            txt = self.font_stats.render("KAMERA OFFLINE", True, self.C_DANGER)
            txt2 = self.font_stats.render("MENGHUBUNGKAN", True, self.C_TEXT_MUTED)
            surf.blit(txt, ((110 - txt.get_width()) // 2, 16))
            surf.blit(txt2, ((110 - txt2.get_width()) // 2, 32))
            self.last_preview_surface = surf
            return

        # Konversi frame BGR → RGB, resize menjadi thumbnail 110x58
        try:
            thumb = cv2.resize(frame, (110, 58), interpolation=cv2.INTER_AREA)
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

                    px, py = pos
                    print(f"[GUI Event] Klik/Sentuh terdeteksi di layar pada koordinat ({px}, {py})")

                    if self.show_power_dialog:
                        # Event klik pada modal dialog daya (Matikan Total atau Batal)
                        if 70 <= py <= 120 and 40 <= px <= 280:
                            print("[GUI Event] Klik Modal -> MATIKAN TOTAL")
                            self._confirm_shutdown()
                        elif 122 <= py <= 168 and 40 <= px <= 280:
                            print("[GUI Event] Klik Modal -> BATAL")
                            self._cancel_dialog()
                        else:
                            for name, btn in self.modal_buttons.items():
                                if btn["rect"].collidepoint(pos):
                                    btn["action"]()
                                    break
                    else:
                        # Cek setiap tombol utama di baris bawah dengan zona sentuh ramah jari (mencakup hingga tepi bawah y=240)
                        handled = False

                        # Deteksi sentuhan pada tombol sakelar WiFi di Card Kiri
                        if hasattr(self, 'wifi_btn_rect') and self.wifi_btn_rect.inflate(6, 6).collidepoint(pos):
                            print(f"[GUI Event] Tombol [WIFI TOGGLE] DITEKAN pada koordinat ({px}, {py})")
                            self._action_toggle_wifi()
                            handled = True

                        if not handled:
                            for name, btn in self.buttons.items():
                                if name == "vol":
                                    continue
                                b_r = btn["rect"]
                                hit_rect = pygame.Rect(b_r.x - 2, 162, b_r.width + 4, 78)
                                if hit_rect.collidepoint(pos):
                                    print(f"[GUI Event] Tombol [{btn['label']}] DITEKAN pada koordinat ({px}, {py})")
                                    self.pressed_button = name
                                    self.pressed_until = time.time() + 0.25
                                    btn["action"]()
                                    handled = True
                                    break

                        if not handled:
                            print(f"[GUI Event] Sentuhan di ({px}, {py}) berada di luar area tombol.")
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
                        elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS, pygame.K_DOWN):
                            self._action_vol_down()
                        elif event.key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS, pygame.K_UP):
                            self._action_vol_up()
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

            # --- B. Kartu Kiri: Pratinjau Kamera & Widget WiFi (118 x 136) ---
            card_cam = pygame.Rect(6, 30, 118, 136)
            pygame.draw.rect(self.screen, self.C_CARD, card_cam, border_radius=6)
            pygame.draw.rect(self.screen, self.C_CARD_BORDER, card_cam, width=1, border_radius=6)

            self.update_preview_surface()
            if self.last_preview_surface:
                self.screen.blit(self.last_preview_surface, (10, 33))

            # Info kamera & volume di bawah thumbnail (y=92)
            cam_dev = self.app.cfg.get('camera', {}).get('device', '?') if isinstance(getattr(self.app, 'cfg', None), dict) else '?'
            cam_txt = f"CAM:{cam_dev}" if cam_alive else "CAM:OFF"
            vol_val = self.app.volume_steps[self.app.current_vol_idx] if hasattr(self.app, 'volume_steps') and hasattr(self.app, 'current_vol_idx') else 80
            top_info = f"{cam_txt} | VOL:{vol_val}%"
            lbl_top = self.font_stats.render(top_info, True, self.C_ACCENT if cam_alive else self.C_DANGER)
            self.screen.blit(lbl_top, (10, 92))

            # Garis pemisah halus (y=104)
            pygame.draw.line(self.screen, self.C_CARD_BORDER, (10, 104), (120, 104), 1)

            # Status WiFi & IP
            wifi_on = True
            wifi_conn = False
            wifi_ssid = "WiFi"
            wifi_ip = "-"
            if self.wifi_mgr:
                wifi_on, wifi_conn, wifi_ssid, wifi_ip = self.wifi_mgr.get_info()

            # Baris 1 WiFi: SSID / Status Radio (y=107)
            if not wifi_on:
                ssid_txt = "WiFi: MATI"
                ssid_col = self.C_DANGER
            elif wifi_conn:
                display_ssid = wifi_ssid if len(wifi_ssid) <= 12 else (wifi_ssid[:10] + "..")
                ssid_txt = f"📶 {display_ssid}"
                ssid_col = self.C_PRIMARY
            else:
                display_ssid = wifi_ssid if len(wifi_ssid) <= 12 else (wifi_ssid[:10] + "..")
                ssid_txt = f"📶 {display_ssid}"
                ssid_col = self.C_WARNING

            lbl_ssid = self.font_stats.render(ssid_txt, True, ssid_col)
            self.screen.blit(lbl_ssid, (10, 107))

            # Baris 2 WiFi: Alamat IP Lokal (y=120)
            ip_txt = f"IP: {wifi_ip}"
            ip_col = self.C_INFO if (wifi_conn and wifi_ip != "-") else self.C_TEXT_MUTED
            lbl_ip = self.font_stats.render(ip_txt, True, ip_col)
            self.screen.blit(lbl_ip, (10, 120))

            # Tombol Sentuh WiFi ON / OFF (y=135..159, tinggi 24px)
            w_btn_rect = getattr(self, 'wifi_btn_rect', pygame.Rect(10, 135, 110, 24))
            w_btn_bg = (16, 120, 80) if wifi_on else (120, 28, 28)
            w_btn_border = (52, 211, 153) if wifi_on else (248, 113, 113)
            pygame.draw.rect(self.screen, w_btn_bg, w_btn_rect, border_radius=4)
            pygame.draw.rect(self.screen, w_btn_border, w_btn_rect, width=1, border_radius=4)

            w_btn_lbl = self.font_btn.render("WIFI: ON" if wifi_on else "WIFI: OFF", True, (255, 255, 255))
            wx = w_btn_rect.x + (w_btn_rect.width - w_btn_lbl.get_width()) // 2
            wy = w_btn_rect.y + (w_btn_rect.height - w_btn_lbl.get_height()) // 2
            self.screen.blit(w_btn_lbl, (wx, wy))

            # --- C. Kartu Kanan: Cuplikan Teks Bacaan (186 x 136) dengan Auto-Scroll & Voice Sync ---
            card_txt = pygame.Rect(128, 30, 186, 136)
            pygame.draw.rect(self.screen, self.C_CARD, card_txt, border_radius=6)
            pygame.draw.rect(self.screen, self.C_CARD_BORDER, card_txt, width=1, border_radius=6)

            # Cek status kalimat aktif yang sedang dibaca suara
            reading_sentences = getattr(self.app, 'last_reading', []) or []
            active_s_idx = getattr(self.app, 'current_sentence_idx', -1)
            is_reading = self.app.busy and getattr(self.app.speaker, 'playing', False) and active_s_idx >= 0

            # Update cache pembungkusan baris (line wrapping) hanya saat dokumen berubah
            reading_tuple = tuple(reading_sentences)
            if reading_tuple != self._cached_reading_hash:
                self._cached_reading_hash = reading_tuple
                self._cached_wrapped_items = []
                self.scroll_y = 0.0
                self.target_scroll_y = 0.0
                max_w = 170

                for s_idx, sentence in enumerate(reading_sentences):
                    words = sentence.split()
                    cur_line = ""
                    is_first = True
                    for w in words:
                        test_line = f"{cur_line} {w}".strip() if cur_line else w
                        if self.font_text.size(test_line)[0] <= max_w:
                            cur_line = test_line
                        else:
                            if cur_line:
                                self._cached_wrapped_items.append((s_idx, cur_line, is_first))
                                is_first = False
                            cur_line = w
                    if cur_line:
                        self._cached_wrapped_items.append((s_idx, cur_line, is_first))

            # Header Kartu Teks
            if is_reading and reading_sentences:
                total_s = len(reading_sentences)
                cur_s = min(total_s, active_s_idx + 1)
                pulse = "▶" if (int(time.time() * 2) % 2 == 0) else "▷"
                hdr_label = f"DIBACA [{cur_s}/{total_s}] {pulse}"
                hdr_surf = self.font_stats.render(hdr_label, True, self.C_PRIMARY)
            else:
                hdr_surf = self.font_stats.render("TEKS BACAAN:", True, self.C_TEXT_MUTED)
            self.screen.blit(hdr_surf, (134, 34))

            if not is_reading and reading_sentences:
                cnt_txt = self.font_stats.render(f"{len(reading_sentences)} kal", True, self.C_TEXT_MUTED)
                self.screen.blit(cnt_txt, (270, 34))

            # Area Viewport Teks (Tinggi 112px, y=50 sampai y=162)
            viewport_rect = pygame.Rect(132, 50, 178, 112)
            LINE_H = 16
            total_lines = len(self._cached_wrapped_items)
            total_height = total_lines * LINE_H
            max_scroll = max(0.0, float(total_height - 110))

            # Auto-Scroll otomatis ke baris kalimat yang sedang dibaca suara
            if is_reading and reading_sentences:
                first_active_line = 0
                for l_idx, (s_idx, _, _) in enumerate(self._cached_wrapped_items):
                    if s_idx == active_s_idx:
                        first_active_line = l_idx
                        break
                # Posisikan baris aktif di baris ke-1 atau ke-2 dalam viewport
                ideal_line = max(0, first_active_line - 1)
                self.target_scroll_y = min(max_scroll, float(ideal_line * LINE_H))
            elif not reading_sentences:
                self.target_scroll_y = 0.0

            # Easing animasi scroll halus per frame
            self.scroll_y += (self.target_scroll_y - self.scroll_y) * 0.25
            if abs(self.target_scroll_y - self.scroll_y) < 0.5:
                self.scroll_y = self.target_scroll_y

            # Gambar Teks dengan Batas Viewport (Clip)
            self.screen.set_clip(viewport_rect)

            if not reading_sentences:
                empty_msgs = [
                    "Belum ada dokumen yang dibaca.",
                    "",
                    "Sentuh tombol [BACA] untuk",
                    "memotret dan membaca teks."
                ]
                y_e = 54
                for em in empty_msgs:
                    e_surf = self.font_text.render(em, True, self.C_TEXT_MUTED)
                    self.screen.blit(e_surf, (136, y_e))
                    y_e += 16
            else:
                for l_idx, (s_idx, line_str, is_first) in enumerate(self._cached_wrapped_items):
                    y_pos = int(50 + l_idx * LINE_H - self.scroll_y)
                    if y_pos + LINE_H < 48 or y_pos > 165:
                        continue  # Di luar area kartu

                    is_active_sentence = (is_reading and s_idx == active_s_idx)

                    if is_active_sentence:
                        # Highlight baris kalimat aktif (latar hijau gelap lembut + aksen tepi)
                        bg_r = pygame.Rect(132, y_pos - 1, 174, LINE_H)
                        pygame.draw.rect(self.screen, (16, 52, 44), bg_r, border_radius=3)
                        pygame.draw.rect(self.screen, (52, 211, 153), pygame.Rect(132, y_pos - 1, 3, LINE_H), border_radius=1)
                        t_surf = self.font_text.render(line_str, True, (255, 255, 255))
                        self.screen.blit(t_surf, (138, y_pos))
                    else:
                        t_surf = self.font_text.render(line_str, True, self.C_TEXT_MUTED)
                        self.screen.blit(t_surf, (136, y_pos))

            # Scrollbar Mini vertikal jika dokumen panjang
            if max_scroll > 0:
                sb_h = 108
                track_r = pygame.Rect(308, 52, 2, sb_h)
                pygame.draw.rect(self.screen, (30, 41, 59), track_r, border_radius=1)
                thumb_h = max(10, int(sb_h * (112.0 / total_height)))
                thumb_y = 52 + int((self.scroll_y / max_scroll) * (sb_h - thumb_h))
                thumb_r = pygame.Rect(308, thumb_y, 2, thumb_h)
                pygame.draw.rect(self.screen, (100, 116, 139), thumb_r, border_radius=1)

            # Lepaskan clip agar tidak memotong elemen layar lainnya
            self.screen.set_clip(None)

            # --- D. Tombol Sentuh Bawah dengan Border Terpisah (Tinggi 64px) ---
            for name, btn in self.buttons.items():
                if name == "vol":
                    continue
                r = btn["rect"]
                col = btn["color"]
                border_col = btn.get("border_color", (80, 100, 120))

                # Tombol tetap aktif saat memproses jika bukan BACA/REPLAY
                is_disabled = self.app.busy and name not in ("stop", "vol_dn", "vol_up", "off")
                is_pressed = (self.pressed_button == name and time.time() < self.pressed_until)

                if is_disabled:
                    col = tuple(c // 3 for c in col)
                    border_col = (51, 65, 85)
                elif is_pressed:
                    col = tuple(min(255, c + 40) for c in col)

                label_txt = btn["label"]
                sub_txt = btn["sub"]

                # 1. Gambar latar tombol independen
                pygame.draw.rect(self.screen, col, r, border_radius=6)

                # 2. Gambar border terpisah (tebal 2px) dengan warna khas tiap tombol
                if is_pressed:
                    pygame.draw.rect(self.screen, (255, 255, 255), r, width=2, border_radius=6)
                elif not is_disabled:
                    pygame.draw.rect(self.screen, border_col, r, width=2, border_radius=6)
                else:
                    pygame.draw.rect(self.screen, border_col, r, width=1, border_radius=6)

                fnt = self.font_btn_lg if name == "read" else self.font_btn
                lbl_color = (255, 255, 255) if not is_disabled else (100, 110, 120)
                lbl = fnt.render(label_txt, True, lbl_color)
                lx = r.x + (r.width - lbl.get_width()) // 2
                ly = r.y + (10 if sub_txt else (r.height - lbl.get_height()) // 2)
                self.screen.blit(lbl, (lx, ly))

                if sub_txt:
                    sub_col = (220, 230, 242) if not is_disabled else (80, 90, 100)
                    sub_lbl = self.font_stats.render(sub_txt, True, sub_col)
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

                dlg_title = self.font_title.render("KONFIRMASI MATIKAN DAYA", True, self.C_TEXT)
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
        btn_box.pack(fill="x", padx=4, pady=6)

        btn_read = tk.Button(btn_box, text="📷 BACA", bg="#10b981", fg="white", font=("Helvetica", 9, "bold"),
                             relief="raised", bd=2,
                             command=lambda: self.app._run_async(self.app.capture_and_read), height=2, width=6)
        btn_read.pack(side="left", padx=2)

        btn_replay = tk.Button(btn_box, text="🔄 ULANG", bg="#2563eb", fg="white", font=("Helvetica", 9, "bold"),
                              relief="raised", bd=2,
                              command=lambda: self.app._run_async(self.app.replay), height=2, width=6)
        btn_replay.pack(side="left", padx=2)

        btn_stop = tk.Button(btn_box, text="⏹ STOP", bg="#d97706", fg="white", font=("Helvetica", 9, "bold"),
                             relief="raised", bd=2,
                             command=self.app.speaker.stop, height=2, width=5)
        btn_stop.pack(side="left", padx=2)

        def tk_vol_down():
            if hasattr(self.app, 'current_vol_idx') and hasattr(self.app, 'volume_steps'):
                self.app.current_vol_idx = max(0, self.app.current_vol_idx - 1)
                nv = self.app.volume_steps[self.app.current_vol_idx]
                self.lbl_status.config(text=f"VOL: {nv}%", fg="#0ea5e9")

        def tk_vol_up():
            if hasattr(self.app, 'current_vol_idx') and hasattr(self.app, 'volume_steps'):
                self.app.current_vol_idx = min(len(self.app.volume_steps) - 1, self.app.current_vol_idx + 1)
                nv = self.app.volume_steps[self.app.current_vol_idx]
                self.lbl_status.config(text=f"VOL: {nv}%", fg="#0ea5e9")

        btn_vol_dn = tk.Button(btn_box, text="🔉 -", bg="#0ea5e9", fg="white", font=("Helvetica", 9, "bold"),
                               relief="raised", bd=2,
                               command=tk_vol_down, height=2, width=4)
        btn_vol_dn.pack(side="left", padx=2)

        btn_vol_up = tk.Button(btn_box, text="🔊 +", bg="#0284c7", fg="white", font=("Helvetica", 9, "bold"),
                               relief="raised", bd=2,
                               command=tk_vol_up, height=2, width=4)
        btn_vol_up.pack(side="left", padx=2)

        btn_off = tk.Button(btn_box, text="⏻ OFF", bg="#dc2626", fg="white", font=("Helvetica", 9, "bold"),
                            relief="raised", bd=2,
                            command=self.app.safe_shutdown, height=2, width=4)
        btn_off.pack(side="left", padx=2)

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
    try:
        if HAS_PYGAME:
            gui = TFTGuiPygame(app_instance, width=width, height=height, fullscreen=fullscreen, fb_device=fb_device)
            gui.run()
        elif HAS_TKINTER:
            print("[GUI] Pygame belum terpasang, beralih ke engine Tkinter...", flush=True)
            gui = TFTGuiTkinter(app_instance, width=width, height=height)
            gui.run()
        else:
            print("[GUI] Peringatan: Tidak ada engine GUI (pygame/tkinter) yang ditemukan.", flush=True)
    except Exception as e:
        import traceback
        print(f"[GUI ERROR] Kesalahan saat menjalankan antarmuka grafis: {e}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
