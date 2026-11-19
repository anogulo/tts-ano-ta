#!/usr/bin/env python3
"""
tools/test_lcd.py - Skrip Khusus Uji Layar & Kalibrasi Touchscreen LCD TFT 2.4"
Bagian dari Rancang Bangun Alat Bantu Baca Tunanetra (Image to Voice)

Fitur:
1. Otomatis cek dan hentikan sementara reader.service jika sedang aktif.
2. Render antarmuka visual pengujian pada 320x240 (Landscape).
3. Menampilkan posisi sentuh secara visual (titik/crosshair) dan koordinat (px, py).
4. Menampilkan tombol-tombol yang sama persis seperti pada aplikasi utama.
5. Memungkinkan kalibrasi interaktif secara real-time:
   - Tekan tombol [S] di keyboard atau sentuh tombol [TUKAR XY] untuk mengubah swap_xy.
   - Tekan tombol [X] di keyboard atau sentuh tombol [BALIK X] untuk mengubah invert_x.
   - Tekan tombol [Y] di keyboard atau sentuh tombol [BALIK Y] untuk mengubah invert_y.
   - Tekan tombol [W] di keyboard atau sentuh tombol [SIMPAN] untuk menyimpan ke config.toml!
   - Tekan [Q] atau Ctrl+C untuk keluar.
"""

import os
import sys

# Auto-exec virtual environment (.venv) jika ada
if sys.platform.startswith("linux") and sys.prefix == sys.base_prefix:
    _base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _venv_py = os.path.join(_base_dir, '.venv', 'bin', 'python')
    if os.path.exists(_venv_py) and os.path.realpath(sys.executable) != os.path.realpath(_venv_py):
        os.environ["VIRTUAL_ENV"] = os.path.dirname(os.path.dirname(_venv_py))
        os.execv(_venv_py, [_venv_py] + sys.argv)

# Tambahkan direktori root ke sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import re
import subprocess
import time
import tomllib

try:
    import pygame
    HAS_PYGAME = True
except ImportError:
    HAS_PYGAME = False

from tft_gui import FramebufferBlitter, LinuxTouchReader

CONFIG_PATH = os.path.join(BASE_DIR, 'config.toml')


def stop_service_if_running():
    """Hentikan reader.service sementara jika sedang berjalan."""
    if sys.platform.startswith("linux"):
        try:
            res = subprocess.run(['systemctl', 'is-active', 'reader.service'], capture_output=True, text=True)
            if res.stdout.strip() == 'active':
                print("\n[PERINGATAN] Layanan latar belakang 'reader.service' terdeteksi sedang aktif.", flush=True)
                print("Menghentikan layanan sementara agar pengujian LCD tidak bentrok...", flush=True)
                subprocess.run(['sudo', 'systemctl', 'stop', 'reader.service'], check=False)
                time.sleep(1)
                print("[OK] Layanan dihentikan sementara.\n", flush=True)
        except Exception as e:
            print(f"[INFO] Cek status service: {e}", flush=True)


def load_config() -> dict:
    """Muat konfigurasi toml."""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'rb') as f:
                return tomllib.load(f)
        except Exception as e:
            print(f"[ERROR] Gagal memuat config.toml: {e}", flush=True)
    return {}


def save_touch_calibration(swap_xy: bool, invert_x: bool, invert_y: bool, fb_device: str):
    """Menyimpan konfigurasi kalibrasi ke config.toml secara permanen."""
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            content = f.read()

        # Update swap_xy
        content = re.sub(r'(?m)^(\s*swap_xy\s*=\s*)(true|false)', rf'\g<1>{"true" if swap_xy else "false"}', content)
        # Update invert_x
        content = re.sub(r'(?m)^(\s*invert_x\s*=\s*)(true|false)', rf'\g<1>{"true" if invert_x else "false"}', content)
        # Update invert_y
        content = re.sub(r'(?m)^(\s*invert_y\s*=\s*)(true|false)', rf'\g<1>{"true" if invert_y else "false"}', content)
        # Update fb_device jika ada
        if fb_device:
            content = re.sub(r'(?m)^(\s*fb_device\s*=\s*)".*?"', rf'\g<1>"{fb_device}"', content)

        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            f.write(content)

        print("\n" + "=" * 60, flush=True)
        print(" [SUKSES] Konfigurasi kalibrasi berhasil disimpan ke config.toml!", flush=True)
        print(f"  swap_xy  = {'true' if swap_xy else 'false'}", flush=True)
        print(f"  invert_x = {'true' if invert_x else 'false'}", flush=True)
        print(f"  invert_y = {'true' if invert_y else 'false'}", flush=True)
        print("=" * 60 + "\n", flush=True)
        return True
    except Exception as e:
        print(f"[ERROR] Gagal menyimpan ke config.toml: {e}", flush=True)
        return False


def run_lcd_test():
    """Jalankan program visual pengujian LCD & Sentuh."""
    stop_service_if_running()

    cfg = load_config()
    gui_cfg = cfg.get('gui', {})
    touch_cfg = gui_cfg.get('touch', {})

    width = int(gui_cfg.get('width', 320))
    height = int(gui_cfg.get('height', 240))

    # Konfigurasi kalibrasi aktif
    swap_xy = bool(touch_cfg.get('swap_xy', True))
    invert_x = bool(touch_cfg.get('invert_x', False))
    invert_y = bool(touch_cfg.get('invert_y', True))
    dev_path_touch = str(touch_cfg.get('device', ''))

    min_adc_x = int(touch_cfg.get('min_adc_x', 200))
    max_adc_x = int(touch_cfg.get('max_adc_x', 3900))
    min_adc_y = int(touch_cfg.get('min_adc_y', 200))
    max_adc_y = int(touch_cfg.get('max_adc_y', 3900))

    # Deteksi Framebuffer
    fb_candidates = [fb for fb in ["/dev/fb1", "/dev/fb0"] if os.path.exists(fb)]
    target_fb = gui_cfg.get('fb_device', '')
    if not target_fb or not os.path.exists(target_fb):
        target_fb = fb_candidates[0] if fb_candidates else ""

    print("=" * 65, flush=True)
    print("      PROGRAM UJI & KALIBRASI LCD TFT 2.4\" (320x240)", flush=True)
    print("=" * 65, flush=True)
    print(f" Framebuffer Terdeteksi : {target_fb or 'Tidak ada (Headless)'}", flush=True)
    print(f" Status Awal Kalibrasi  : swap_xy={swap_xy}, invert_x={invert_x}, invert_y={invert_y}", flush=True)
    print(" -------------------------------------------------------------", flush=True)
    print(" Petunjuk:", flush=True)
    print("  1. Sentuh tombol di layar LCD untuk melihat respons tombol.", flush=True)
    print("  2. Jika posisi terbalik/tertukar, tekan:", flush=True)
    print("     [S] = Tukar sumbu X dan Y (Swap XY)", flush=True)
    print("     [X] = Balik arah Horizontal (Invert X)", flush=True)
    print("     [Y] = Balik arah Vertikal (Invert Y)", flush=True)
    print("     [W] = Simpan hasil kalibrasi ke config.toml", flush=True)
    print("     [Q] = Keluar dari program", flush=True)
    print("=" * 65 + "\n", flush=True)

    if not HAS_PYGAME:
        print("[FATAL] Pygame tidak terpasang. Jalankan: pip install pygame", flush=True)
        return

    # Inisialisasi SDL
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    pygame.init()
    pygame.font.init()

    screen = None
    try:
        screen = pygame.display.set_mode((width, height), 0)
    except Exception:
        screen = pygame.Surface((width, height))

    clock = pygame.time.Clock()

    # Framebuffer Blitter
    fb_blitter = None
    if target_fb and os.path.exists(target_fb):
        fb_blitter = FramebufferBlitter(device=target_fb, width=width, height=height)

    # Inisialisasi Touch Reader
    touch_reader = LinuxTouchReader(
        width=width, height=height,
        swap_xy=swap_xy, invert_x=invert_x, invert_y=invert_y,
        device_path=dev_path_touch,
        min_adc_x=min_adc_x, max_adc_x=max_adc_x,
        min_adc_y=min_adc_y, max_adc_y=max_adc_y
    )
    touch_reader.start()

    font_title = pygame.font.SysFont("dejavusans,arial,helvetica", 12, bold=True)
    font_btn = pygame.font.SysFont("dejavusans,arial,helvetica", 11, bold=True)
    font_status = pygame.font.SysFont("dejavusans,arial,helvetica", 10)
    font_stats = pygame.font.SysFont("monospace,consolas", 9)

    # Definisi Tombol Aplikasi Utama (Baris Bawah)
    buttons = {
        "read": {"rect": pygame.Rect(6, 170, 64, 64), "label": "BACA", "color": (16, 185, 129), "border": (52, 211, 153)},
        "replay": {"rect": pygame.Rect(74, 170, 44, 64), "label": "ULANG", "color": (37, 99, 235), "border": (96, 165, 250)},
        "stop": {"rect": pygame.Rect(122, 170, 44, 64), "label": "STOP", "color": (217, 119, 6), "border": (251, 191, 36)},
        "vol_dn": {"rect": pygame.Rect(170, 170, 44, 64), "label": "VOL -", "color": (14, 165, 233), "border": (56, 189, 248)},
        "vol_up": {"rect": pygame.Rect(218, 170, 44, 64), "label": "VOL +", "color": (2, 132, 199), "border": (56, 189, 248)},
        "off": {"rect": pygame.Rect(266, 170, 48, 64), "label": "OFF", "color": (220, 38, 38), "border": (248, 113, 113)},
    }

    # Definisi Tombol Kontrol Kalibrasi On-Screen (Baris Atas)
    calib_buttons = {
        "swap": {"rect": pygame.Rect(6, 76, 72, 34), "label": "TUKAR [S]", "color": (51, 65, 85)},
        "inv_x": {"rect": pygame.Rect(82, 76, 72, 34), "label": "BALIK [X]", "color": (51, 65, 85)},
        "inv_y": {"rect": pygame.Rect(158, 76, 72, 34), "label": "BALIK [Y]", "color": (51, 65, 85)},
        "save": {"rect": pygame.Rect(234, 76, 80, 34), "label": "SIMPAN [W]", "color": (13, 148, 136)},
    }

    last_touch_pos = None
    last_touch_time = 0.0
    last_button_hit = None
    save_msg_until = 0.0
    save_status_text = ""

    running = True
    try:
        while running:
            # Update konfigurasi reader jika diubah
            touch_reader.swap_xy = swap_xy
            touch_reader.invert_x = invert_x
            touch_reader.invert_y = invert_y

            # Tangani Event
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False

                elif event.type in (pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN):
                    if event.type == pygame.MOUSEBUTTONDOWN:
                        pos = event.pos
                    else:
                        pos = (int(event.x * width), int(event.y * height))

                    px, py = pos
                    last_touch_pos = (px, py)
                    last_touch_time = time.time()
                    last_button_hit = None

                    # Cek tombol kalibrasi on-screen
                    if calib_buttons["swap"]["rect"].collidepoint(pos):
                        swap_xy = not swap_xy
                        print(f"[ACTION] Sumbu XY ditukar -> swap_xy={swap_xy}", flush=True)
                    elif calib_buttons["inv_x"]["rect"].collidepoint(pos):
                        invert_x = not invert_x
                        print(f"[ACTION] Sumbu X dibalik -> invert_x={invert_x}", flush=True)
                    elif calib_buttons["inv_y"]["rect"].collidepoint(pos):
                        invert_y = not invert_y
                        print(f"[ACTION] Sumbu Y dibalik -> invert_y={invert_y}", flush=True)
                    elif calib_buttons["save"]["rect"].collidepoint(pos):
                        ok = save_touch_calibration(swap_xy, invert_x, invert_y, target_fb)
                        save_status_text = "TERSMPAN!" if ok else "GAGAL SIMPAN"
                        save_msg_until = time.time() + 2.5
                    else:
                        # Cek tombol fungsi aplikasi
                        hit = False
                        for b_id, btn in buttons.items():
                            if btn["rect"].inflate(4, 4).collidepoint(pos):
                                last_button_hit = b_id
                                print(f"\n>>> [HIT TOMBOL!] Anda berhasil menekan: [{btn['label']}] di koordinat ({px}, {py})", flush=True)
                                hit = True
                                break
                        if not hit:
                            print(f"[SENTUH] Layar disentuh di koordinat ({px}, {py}) [RAW ADC: x={touch_reader.raw_x}, y={touch_reader.raw_y}]", flush=True)

                elif event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_q, pygame.K_ESCAPE):
                        running = False
                    elif event.key == pygame.K_s:
                        swap_xy = not swap_xy
                        print(f"[KEYBOARD] swap_xy diubah ke: {swap_xy}", flush=True)
                    elif event.key == pygame.K_x:
                        invert_x = not invert_x
                        print(f"[KEYBOARD] invert_x diubah ke: {invert_x}", flush=True)
                    elif event.key == pygame.K_y:
                        invert_y = not invert_y
                        print(f"[KEYBOARD] invert_y diubah ke: {invert_y}", flush=True)
                    elif event.key == pygame.K_w:
                        ok = save_touch_calibration(swap_xy, invert_x, invert_y, target_fb)
                        save_status_text = "TERSMPAN!" if ok else "GAGAL SIMPAN"
                        save_msg_until = time.time() + 2.5

            # ================= RENDER TAMPILAN =================
            screen.fill((15, 23, 42))  # Latar Belakang Slate 900

            # 1. Header Judul
            pygame.draw.rect(screen, (30, 41, 59), pygame.Rect(0, 0, width, 26))
            txt_t = font_title.render("UJI & KALIBRASI LCD SENTUH 2.4\"", True, (248, 250, 252))
            screen.blit(txt_t, (8, 6))

            # 2. Panel Info Status Kalibrasi
            cfg_text = f"SwapXY={swap_xy} | InvX={invert_x} | InvY={invert_y}"
            t_cfg_surf = font_status.render(cfg_text, True, (56, 189, 248))
            screen.blit(t_cfg_surf, (8, 32))

            # Info Posisi Sentuh Terakhir
            if last_touch_pos is not None:
                pos_str = f"Sentuh: ({last_touch_pos[0]}, {last_touch_pos[1]}) | ADC: ({touch_reader.raw_x}, {touch_reader.raw_y})"
                t_pos_surf = font_stats.render(pos_str, True, (251, 191, 36))
                screen.blit(t_pos_surf, (8, 52))
            else:
                t_pos_surf = font_stats.render("Sentuh layar LCD untuk menguji koordinat...", True, (148, 163, 184))
                screen.blit(t_pos_surf, (8, 52))

            # Pesan Notifikasi Simpan
            if time.time() < save_msg_until:
                s_surf = font_btn.render(save_status_text, True, (52, 211, 153))
                screen.blit(s_surf, (width - s_surf.get_width() - 8, 32))

            # 3. Tombol Kalibrasi Cepat
            for b_id, btn in calib_buttons.items():
                r = btn["rect"]
                pygame.draw.rect(screen, btn["color"], r, border_radius=4)
                pygame.draw.rect(screen, (100, 116, 139), r, width=1, border_radius=4)
                lbl = font_btn.render(btn["label"], True, (255, 255, 255))
                screen.blit(lbl, (r.x + (r.width - lbl.get_width()) // 2, r.y + (r.height - lbl.get_height()) // 2))

            # 4. Garis Pembatas
            pygame.draw.line(screen, (51, 65, 85), (0, 120), (width, 120), 1)

            # Target 4 Sudut Layar (Crosshair untuk Panduan Akurasi)
            targets = [
                ((20, 135), "KIRI ATAS"),
                ((width - 20, 135), "KANAN ATAS"),
            ]
            for pt, name in targets:
                pygame.draw.circle(screen, (71, 85, 105), pt, 10, width=1)
                pygame.draw.circle(screen, (148, 163, 184), pt, 2)

            t_info_lbl = font_stats.render("Coba sentuh tombol di bawah:", True, (203, 213, 225))
            screen.blit(t_info_lbl, (8, 148))

            # 5. Tombol-Tombol Aplikasi (Baris Bawah)
            for b_id, btn in buttons.items():
                r = btn["rect"]
                is_hit = (last_button_hit == b_id) and (time.time() - last_touch_time < 0.4)

                bg_col = (255, 255, 255) if is_hit else btn["color"]
                border_col = (255, 255, 255) if is_hit else btn["border"]

                pygame.draw.rect(screen, bg_col, r, border_radius=6)
                pygame.draw.rect(screen, border_col, r, width=2, border_radius=6)

                txt_color = (0, 0, 0) if is_hit else (255, 255, 255)
                lbl = font_btn.render(btn["label"], True, txt_color)
                screen.blit(lbl, (r.x + (r.width - lbl.get_width()) // 2, r.y + (r.height - lbl.get_height()) // 2))

            # 6. Gambar Crosshair / Titik Sentuh Terakhir
            if last_touch_pos and (time.time() - last_touch_time < 2.0):
                tx, ty = last_touch_pos
                pygame.draw.circle(screen, (239, 68, 68), (tx, ty), 8, width=2)
                pygame.draw.circle(screen, (251, 191, 36), (tx, ty), 3)
                pygame.draw.line(screen, (239, 68, 68), (tx - 12, ty), (tx + 12, ty), 1)
                pygame.draw.line(screen, (239, 68, 68), (tx, ty - 12), (tx, ty + 12), 1)

            # Update ke Framebuffer Fisik
            pygame.display.flip()
            if fb_blitter:
                fb_blitter.blit(screen)

            clock.tick(20)

    except KeyboardInterrupt:
        print("\n[INFO] Uji LCD selesai.", flush=True)
    finally:
        if touch_reader:
            touch_reader.stop()
        if fb_blitter:
            fb_blitter.close()
        pygame.quit()
        print("\nSelesai. Anda dapat menjalankan program kembali dengan: python3 main.py --gui\n", flush=True)


if __name__ == '__main__':
    run_lcd_test()
