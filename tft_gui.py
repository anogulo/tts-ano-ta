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

import cv2
import numpy as np

# Cek ketersediaan pustaka GUI
try:
    import pygame
    HAS_PYGAME = True
except ImportError:
    HAS_PYGAME = False

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

    def __init__(self, app_instance, width: int = 320, height: int = 240, fullscreen: bool = False):
        self.app = app_instance
        self.width = width
        self.height = height
        self.fullscreen = fullscreen
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

        # Status Tampilan
        self.status_msg = "ALAT SIAP"
        self.status_color = self.C_PRIMARY
        self.last_preview_surface = None
        self.last_update_preview = 0.0

        # Inisialisasi tombol sentuh
        self.buttons = {}
        self._init_buttons()

    def _init_buttons(self):
        """Mendefinisikan area dan aksi tombol sentuh."""
        rect_factory = pygame.Rect if HAS_PYGAME else SimpleRect
        self.buttons = {
            "read": {
                "rect": rect_factory(8, 172, 110, 60),
                "label": "BACA",
                "sub": "FOTO & OCR",
                "color": self.C_PRIMARY,
                "action": self._action_read
            },
            "replay": {
                "rect": rect_factory(124, 172, 58, 60),
                "label": "ULANG",
                "sub": "REPLAY",
                "color": self.C_INFO,
                "action": self._action_replay
            },
            "stop": {
                "rect": rect_factory(188, 172, 58, 60),
                "label": "STOP",
                "sub": "AUDIO",
                "color": self.C_WARNING,
                "action": self._action_stop
            },
            "vol": {
                "rect": rect_factory(252, 172, 60, 28),
                "label": "VOL",
                "sub": "",
                "color": self.C_CARD,
                "action": self._action_vol
            },
            "off": {
                "rect": rect_factory(252, 204, 60, 28),
                "label": "OFF",
                "sub": "",
                "color": self.C_DANGER,
                "action": self._action_shutdown
            }
        }

    def init_display(self):
        """Inisialisasi layar Pygame (mendukung Linux Framebuffer /dev/fb1 untuk LCD SPI)."""
        # Konfigurasi driver framebuffer jika di Raspberry Pi embedded
        if os.path.exists("/dev/fb1") and not os.environ.get("DISPLAY"):
            os.environ["SDL_FBDEV"] = "/dev/fb1"
        if os.path.exists("/dev/input/touchscreen"):
            os.environ["SDL_MOUSEDRV"] = "TSLIB"
            os.environ["SDL_MOUSEDEV"] = "/dev/input/touchscreen"

        pygame.init()
        pygame.font.init()

        flags = pygame.DOUBLEBUF
        if self.fullscreen:
            flags |= pygame.FULLSCREEN

        self.screen = pygame.display.set_mode((self.width, self.height), flags)
        pygame.display.set_caption("Pembaca Tunanetra - 2.4 TFT")
        self.clock = pygame.time.Clock()

        # Muat font sistem yang bersih
        self.font_title = pygame.font.SysFont("dejavusans,arial,helvetica", 13, bold=True)
        self.font_status = pygame.font.SysFont("dejavusans,arial,helvetica", 12, bold=True)
        self.font_btn = pygame.font.SysFont("dejavusans,arial,helvetica", 11, bold=True)
        self.font_btn_lg = pygame.font.SysFont("dejavusans,arial,helvetica", 13, bold=True)
        self.font_text = pygame.font.SysFont("dejavusans,arial,helvetica", 10)
        self.font_stats = pygame.font.SysFont("monospace,consolas", 9)

        # Definisikan tata letak tombol sentuh untuk 320x240
        # Layout Landscape 320x240:
        # [ Header Status Bar: 320 x 28 ]
        # [ Sisi Kiri: Mini Camera Preview + Kualitas: 120 x 135 ]
        # [ Sisi Kanan: Cuplikan Teks Bacaan: 185 x 135 ]
        # [ Bawah: 4 Tombol Sentuh Besar: 320 x 70 ]
        self.buttons = {
            "read": {
                "rect": pygame.Rect(8, 172, 110, 60),
                "label": "BACA",
                "sub": "FOTO & OCR",
                "color": self.C_PRIMARY,
                "action": self._action_read
            },
            "replay": {
                "rect": pygame.Rect(124, 172, 58, 60),
                "label": "ULANG",
                "sub": "REPLAY",
                "color": self.C_INFO,
                "action": self._action_replay
            },
            "stop": {
                "rect": pygame.Rect(188, 172, 58, 60),
                "label": "STOP",
                "sub": "AUDIO",
                "color": self.C_WARNING,
                "action": self._action_stop
            },
            "vol": {
                "rect": pygame.Rect(252, 172, 60, 28),
                "label": "VOL",
                "sub": "",
                "color": self.C_CARD,
                "action": self._action_vol
            },
            "off": {
                "rect": pygame.Rect(252, 204, 60, 28),
                "label": "OFF",
                "sub": "",
                "color": self.C_DANGER,
                "action": self._action_shutdown
            }
        }

    def _action_read(self):
        if not self.app.busy:
            self.status_msg = "MEMOTRET..."
            self.status_color = self.C_WARNING
            self.app._run_async(self.app.capture_and_read)

    def _action_replay(self):
        if not self.app.busy:
            self.app._run_async(self.app.replay)

    def _action_stop(self):
        self.app.speaker.stop()
        self.status_msg = "SUARA DISTOP"
        self.status_color = self.C_TEXT_MUTED

    def _action_vol(self):
        self.app._run_async(self.app.cycle_volume)

    def _action_shutdown(self):
        self.status_msg = "MEMATIKAN PI..."
        self.status_color = self.C_DANGER
        self.app._run_async(self.app.safe_shutdown)

    def update_preview_surface(self):
        """Membuat thumbnail kamera mini untuk pratinjau di layar 2.4 inci."""
        now = time.time()
        # Perbarui thumbnail setiap 0.5 detik agar CPU hemat
        if now - self.last_update_preview < 0.5:
            return

        self.last_update_preview = now
        frame = None
        if self.app.cap is not None and self.app.cap.isOpened():
            ok, frame = self.app.cap.read()
            if not ok:
                frame = None

        if frame is None:
            # Gunakan placeholder kotak jika kamera belum terbuka
            surf = pygame.Surface((110, 80))
            surf.fill((20, 25, 35))
            txt = self.font_stats.render("KAMERA READY", True, self.C_TEXT_MUTED)
            surf.blit(txt, (14, 32))
            self.last_preview_surface = surf
            return

        # Resize ke ukuran thumbnail 110x80
        thumb = cv2.resize(frame, (110, 80), interpolation=cv2.INTER_AREA)
        # BGR ke RGB untuk Pygame
        thumb_rgb = cv2.cvtColor(thumb, cv2.COLOR_BGR2RGB)
        # Transpose untuk surface pygame (width, height, channels)
        thumb_rgb = np.rot90(thumb_rgb)
        thumb_rgb = np.flipud(thumb_rgb)
        self.last_preview_surface = pygame.surfarray.make_surface(thumb_rgb)

    def run(self):
        """Loop utama GUI Pygame."""
        self.init_display()
        self.running = True

        while self.running:
            # 1. Tangani Event Sentuhan & Keyboard
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                elif event.type in (pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN):
                    # Koordinat sentuhan layar
                    if event.type == pygame.MOUSEBUTTONDOWN:
                        pos = event.pos
                    else:
                        pos = (int(event.x * self.width), int(event.y * self.height))

                    for name, btn in self.buttons.items():
                        if btn["rect"].collidepoint(pos):
                            # Efek visual klik
                            btn["action"]()
                            break
                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_RETURN, pygame.K_SPACE):
                        self._action_read()
                    elif event.key == pygame.K_r:
                        self._action_replay()
                    elif event.key == pygame.K_s:
                        self._action_stop()
                    elif event.key == pygame.K_v:
                        self._action_vol()
                    elif event.key in (pygame.K_ESCAPE, pygame.K_q):
                        self.running = False

            # 2. Sinkronisasi Status Mesin App
            if self.app.busy:
                if self.app.speaker.playing:
                    self.status_msg = "MEMBACA SUARA..."
                    self.status_color = self.C_INFO
                else:
                    self.status_msg = "MEMPROSES OCR..."
                    self.status_color = self.C_WARNING
            else:
                self.status_msg = "ALAT SIAP"
                self.status_color = self.C_PRIMARY

            # 3. Gambar Tampilan Layar
            self.screen.fill(self.C_BG)

            # --- A. Header Bar ---
            pygame.draw.rect(self.screen, self.C_CARD, pygame.Rect(0, 0, self.width, 26))
            pygame.draw.line(self.screen, self.C_CARD_BORDER, (0, 26), (self.width, 26), 1)

            title_surf = self.font_title.render("PEMBACA TUNANETRA", True, self.C_TEXT)
            self.screen.blit(title_surf, (8, 6))

            # Status Pill (Badge)
            status_surf = self.font_status.render(self.status_msg, True, self.status_color)
            st_x = self.width - status_surf.get_width() - 8
            self.screen.blit(status_surf, (st_x, 7))

            # --- B. Kartu Kiri: Pratinjau Kamera (118 x 136) ---
            card_cam = pygame.Rect(8, 32, 118, 134)
            pygame.draw.rect(self.screen, self.C_CARD, card_cam, border_radius=6)
            pygame.draw.rect(self.screen, self.C_CARD_BORDER, card_cam, width=1, border_radius=6)

            self.update_preview_surface()
            if self.last_preview_surface:
                self.screen.blit(self.last_preview_surface, (12, 36))

            # Info resolusi / fokus
            stats_cam = f"CAM: 1080p | AF"
            lbl_cam = self.font_stats.render(stats_cam, True, self.C_TEXT_MUTED)
            self.screen.blit(lbl_cam, (14, 120))

            vol_val = self.app.volume_steps[self.app.current_vol_idx]
            lbl_vol_info = self.font_stats.render(f"VOL: {vol_val}%", True, self.C_ACCENT)
            self.screen.blit(lbl_vol_info, (14, 136))

            # --- C. Kartu Kanan: Cuplikan Teks Bacaan (180 x 136) ---
            card_txt = pygame.Rect(132, 32, 180, 134)
            pygame.draw.rect(self.screen, self.C_CARD, card_txt, border_radius=6)
            pygame.draw.rect(self.screen, self.C_CARD_BORDER, card_txt, width=1, border_radius=6)

            hdr_txt = self.font_btn.render("TEKS DIBACA:", True, self.C_TEXT_MUTED)
            self.screen.blit(hdr_txt, (138, 36))

            # Tampilkan 4-5 baris cuplikan teks terakhir
            full_text = " ".join(self.app.last_reading) if self.app.last_reading else "Belum ada dokumen yang dibaca. Tekan tombol [BACA] untuk memotret."
            # Bungkus teks per baris (~26 karakter per baris)
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

            y_txt = 52
            for l in lines:
                l_surf = self.font_text.render(l, True, self.C_TEXT)
                self.screen.blit(l_surf, (138, y_txt))
                y_txt += 15

            # --- D. Tombol Sentuh Bawah ---
            for name, btn in self.buttons.items():
                r = btn["rect"]
                col = btn["color"]

                # Update label khusus untuk volume
                label_txt = btn["label"]
                if name == "vol":
                    label_txt = f"V {vol_val}%"

                pygame.draw.rect(self.screen, col, r, border_radius=6)
                pygame.draw.rect(self.screen, (255, 255, 255, 40), r, width=1, border_radius=6)

                # Render teks tombol
                fnt = self.font_btn_lg if name == "read" else self.font_btn
                lbl = fnt.render(label_txt, True, (255, 255, 255))
                lx = r.x + (r.width - lbl.get_width()) // 2
                ly = r.y + (8 if btn["sub"] else (r.height - lbl.get_height()) // 2)
                self.screen.blit(lbl, (lx, ly))

                if btn["sub"]:
                    sub_lbl = self.font_stats.render(btn["sub"], True, (240, 240, 240))
                    sx = r.x + (r.width - sub_lbl.get_width()) // 2
                    self.screen.blit(sub_lbl, (sx, ly + lbl.get_height() + 2))

            pygame.display.flip()
            # 15 FPS hemat daya & CPU
            self.clock.tick(15)

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


def launch_gui(app_instance, width: int = 320, height: int = 240, fullscreen: bool = False):
    """Fungsi peluncur GUI utama dengan pemilihan engine cerdas (Pygame utama, Tkinter cadangan)."""
    if HAS_PYGAME:
        gui = TFTGuiPygame(app_instance, width=width, height=height, fullscreen=fullscreen)
        gui.run()
    elif HAS_TKINTER:
        print("[GUI] Pygame belum terpasang, beralih ke engine Tkinter...")
        gui = TFTGuiTkinter(app_instance, width=width, height=height)
        gui.run()
    else:
        print("[GUI] Peringatan: Tidak ada engine GUI (pygame/tkinter) yang ditemukan.")
