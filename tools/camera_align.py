"""
camera_align.py - Alat Bantu Kalibrasi dan Penyesuaian Posisi Kamera di Terminal
Bagian dari Rancang Bangun Alat Bantu Baca Tunanetra (Image to Voice)
Sesuai PRD Bagian 4 (Penyangga Jarak Tetap ±25 cm) dan FR-02/FR-03 (Quality & Alignment).
"""

import argparse
import os
import shutil
import sys
import time
from typing import Optional, Tuple

import cv2
import numpy as np

# Karakter gradasi teks bersih tanpa escape code ANSI
ASCII_RAMP = " .:-=+*#%@"


def get_terminal_size() -> Tuple[int, int]:
    size = shutil.get_terminal_size((80, 24))
    return size.columns, size.lines


def frame_to_ascii(gray_frame: np.ndarray, width: int = 68, height: int = 18) -> str:
    """Mengonversi citra frame menjadi ASCII art bersih untuk konsol terminal."""
    resized = cv2.resize(gray_frame, (width, height), interpolation=cv2.INTER_AREA)
    ramp_len = len(ASCII_RAMP)

    lines = []
    border = "+" + "=" * width + "+"
    lines.append(border)
    for row in resized:
        # Piksel gelap = karakter tebal, piksel terang = spasi
        chars_row = [ASCII_RAMP[min(ramp_len - 1, int((255 - p) / 256.0 * ramp_len))] for p in row]
        lines.append("|" + "".join(chars_row) + "|")
    lines.append(border)
    return "\n".join(lines)


def draw_alignment_guides(frame: np.ndarray) -> Tuple[np.ndarray, dict]:
    """Menambahkan garis panduan batas area baca A4 dan mengukur parameter citra."""
    h, w = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame

    mean_brightness = float(gray.mean())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    tilt_angle = 0.0
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(thresh > 0))
    if len(coords) > 200:
        rect = cv2.minAreaRect(coords)
        angle = rect[-1]
        if angle < -45:
            tilt_angle = -(90 + angle)
        elif angle > 45:
            tilt_angle = -(angle - 90)
        else:
            tilt_angle = -angle

    annotated = gray.copy()
    # Garis batas dokumen
    x1, y1 = int(w * 0.15), int(h * 0.10)
    x2, y2 = int(w * 0.85), int(h * 0.90)
    cv2.rectangle(annotated, (x1, y1), (x2, y2), 128, 2)
    # Crosshair tengah
    cx, cy = w // 2, h // 2
    cv2.drawMarker(annotated, (cx, cy), 128, cv2.MARKER_CROSS, 20, 2)

    stats = {
        "brightness": mean_brightness,
        "sharpness": sharpness,
        "tilt": tilt_angle,
        "width": w,
        "height": h
    }
    return annotated, stats


def render_dashboard(ascii_art: str, stats: dict, fps: float) -> str:
    """Menyusun tampilan status kalibrasi posisi kamera secara bersih."""
    b = stats["brightness"]
    s = stats["sharpness"]
    t = stats["tilt"]

    if b < 15.0:
        b_status = "[GELAP TOTAL - BUKA PENUTUP LENSA WEBCAM!]"
    elif b < 70.0:
        b_status = "[KURANG CAHAYA - NYALAKAN LAMPU/LED]"
    elif b > 220.0:
        b_status = "[TERLALU SILAU - KURANGI CAHAYA LANGSUNG]"
    else:
        b_status = "[OPTIMAL]"

    if s < 60.0:
        s_status = "[BURAM / BELUM FOKUS]"
    else:
        s_status = "[FOKUS TAJAM]"

    if abs(t) < 2.0:
        t_status = "[LURUS]"
    elif t > 0:
        t_status = f"[MIRING KANAN {t:.1f}°]"
    else:
        t_status = f"[MIRING KIRI {abs(t):.1f}°]"

    lines = [
        "=" * 70,
        "   MONITOR POSISI KAMERA ALAT BANTU BACA TUNANETRA",
        "=" * 70,
        f" Resolusi Sensor : {stats['width']} x {stats['height']} | Kecepatan: {fps:.1f} FPS",
        f" Kecerahan       : {b:5.1f} / 255.0  {b_status}",
        f" Ketajaman/Fokus : {s:5.1f} (target min 60.0)  {s_status}",
        f" Kemiringan Dok  : {t:5.1f}°  {t_status}",
        "-" * 70,
        " Petunjuk Posisi: Letakkan dokumen di dalam kotak bingkai (+===+).",
        " Tekan [Ctrl + C] untuk kembali ke menu.",
        "-" * 70,
        ascii_art
    ]
    return "\n".join(lines)


def find_working_camera(preferred_device: int = 0) -> Optional[cv2.VideoCapture]:
    """Mencari kamera USB yang aktif."""
    for dev in [preferred_device, 0, 1, 2]:
        cap = cv2.VideoCapture(dev)
        if cap.isOpened():
            ok, _ = cap.read()
            if ok:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
                return cap
            cap.release()
    return None


def run_camera_alignment(device_id: int = 0, demo_mode: bool = False):
    """Menjalankan monitor kamera terminal secara terus menerus."""
    cap = None
    if not demo_mode:
        cap = find_working_camera(device_id)

    if cap is None and not demo_mode:
        print("\n" + "!" * 70)
        print(" [PERHATIAN] Kamera fisik USB belum terdeteksi!")
        print("  - Pastikan kabel webcam USB sudah tercolok kencang ke port USB komputer.")
        print("  - Jika menggunakan webcam laptop, periksa penutup fisik (privacy shutter).")
        print("!" * 70)
        print("\nBeralih ke MODE SIMULASI DOKUMEN CONTOH...\n")
        time.sleep(1.5)
        demo_mode = True

    sample_img = None
    sample_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'dataset', 'sampel_01_surat_resmi.png')
    if os.path.exists(sample_path):
        sample_img = cv2.imread(sample_path)
    else:
        sample_img = np.full((1080, 1920, 3), 255, dtype=np.uint8)

    last_time = time.time()
    fps = 0.0

    try:
        while True:
            t0 = time.time()

            if not demo_mode and cap is not None:
                ok, frame = cap.read()
                if not ok or frame is None:
                    time.sleep(0.3)
                    continue
            else:
                frame = sample_img.copy()

            annotated_frame, stats = draw_alignment_guides(frame)

            term_cols, term_lines = get_terminal_size()
            render_w = min(68, max(40, term_cols - 4))
            render_h = min(18, max(8, term_lines - 16))

            ascii_view = frame_to_ascii(annotated_frame, width=render_w, height=render_h)

            dt = time.time() - last_time
            last_time = time.time()
            if dt > 0:
                fps = 0.8 * fps + 0.2 * (1.0 / dt)

            dashboard_text = render_dashboard(ascii_view, stats, fps)
            print("\n" + dashboard_text)

            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\nMonitor posisi kamera dihentikan.")
    finally:
        if cap is not None:
            cap.release()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Alat kalibrasi dan penyesuaian posisi kamera di terminal.")
    parser.add_argument('--device', type=int, default=0, help="Device index kamera USB (default: 0)")
    parser.add_argument('--demo', action='store_true', help="Gunakan mode demo dokumen jika webcam belum terhubung")
    args = parser.parse_args()

    run_camera_alignment(device_id=args.device, demo_mode=args.demo)
