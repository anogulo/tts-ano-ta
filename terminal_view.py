"""
terminal_view.py - Modul Pratinjau Tampilan Kamera Ramah Terminal & Jendela Pop-up OpenCV
Bagian dari Rancang Bangun Alat Bantu Baca Tunanetra (Image to Voice)
Fitur:
1. Pemindaian & Deteksi Kamera Hardware Terhubung (Tanpa spam stderr).
2. Render Kamera Terminal Kontras Tinggi & Anti-Flicker (Teks Dokumen Sangat Jelas).
3. Jendela Pop-up Kamera OpenCV dengan Fitur Pengambilan Gambar Uji Testing (Spasi / Enter).
"""

import datetime
import glob
import os
import platform
import shutil
import sys
import time
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np

# Karakter gradasi kontras tinggi untuk terminal teks
# Latar gelap terminal: nilai piksel gelap (teks tinta hitam) = karakter tebal (@, #, %),
# nilai piksel terang (kertas putih) = spasi/titik halus.
ASCII_RAMP = " .:-=+*#%@"


def enable_windows_ansi():
    """Mengaktifkan Virtual Terminal Processing di konsol Windows (PowerShell / CMD)."""
    if platform.system() == "Windows":
        try:
            os.system("")
        except Exception:
            pass


def _suppress_stderr():
    """Mengarahkan stderr C++ sementara ke /dev/null agar warning internal OpenCV tidak bocor ke terminal."""
    try:
        null_fd = os.open(os.devnull, os.O_RDWR)
        save_fd = os.dup(2)
        os.dup2(null_fd, 2)
        os.close(null_fd)
        return save_fd
    except Exception:
        return None


def _restore_stderr(save_fd):
    """Mengembalikan stderr ke semula."""
    if save_fd is not None:
        try:
            os.dup2(save_fd, 2)
            os.close(save_fd)
        except Exception:
            pass


def scan_cameras(max_devices: int = 6) -> List[dict]:
    """
    Memindai perangkat kamera fisik yang terhubung ke komputer.
    Mencoba membuka indeks 0 sampai max_devices - 1 dengan backend yang sesuai.
    Mengembalikan daftar kamus informasi perangkat yang aktif.
    """
    enable_windows_ansi()

    # Redam warning OpenCV selama proses pemindaian
    try:
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_SILENT)
    except Exception:
        pass

    found = []
    if platform.system() == "Windows":
        backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
    elif platform.system() == "Linux":
        backends = [cv2.CAP_V4L2, cv2.CAP_ANY]
    else:
        backends = [cv2.CAP_ANY]

    fd = _suppress_stderr()
    try:
        for idx in range(max_devices):
            for b in backends:
                cap = None
                try:
                    cap = cv2.VideoCapture(idx, b)
                    if cap is not None and cap.isOpened():
                        ok, frame = cap.read()
                        if ok and frame is not None and frame.size > 0:
                            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                            fps = cap.get(cv2.CAP_PROP_FPS)
                            found.append({
                                "index": idx,
                                "backend": b,
                                "width": w if w > 0 else 640,
                                "height": h if h > 0 else 480,
                                "fps": fps if fps > 0 else 30.0,
                                "name": f"Kamera {idx}"
                            })
                            cap.release()
                            break
                        cap.release()
                except Exception:
                    if cap is not None:
                        try:
                            cap.release()
                        except Exception:
                            pass
    finally:
        _restore_stderr(fd)

    # Kembalikan level log normal
    try:
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_WARNING)
    except Exception:
        pass

    return found


def enhance_frame_for_text(gray_frame: np.ndarray, width: int, height: int) -> np.ndarray:
    """
    Meningkatkan kontras citra dokumen khusus agar teks tulisan pada kertas
    terlihat sangat tajam, jelas, dan terbaca di terminal ASCII.
    """
    resized = cv2.resize(gray_frame, (width, height), interpolation=cv2.INTER_AREA)

    # Adaptive contrast stretching (percentile 3% - 97%)
    p_min = float(np.percentile(resized, 3))
    p_max = float(np.percentile(resized, 97))

    if p_max - p_min > 20:
        stretched = np.clip((resized.astype(np.float32) - p_min) / (p_max - p_min) * 255.0, 0, 255).astype(np.uint8)
    else:
        stretched = resized

    # CLAHE ringan untuk meratakan pencahayaan tidak seragam
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(6, 6))
    enhanced = clahe.apply(stretched)

    return enhanced


def frame_to_ascii_clean(
    gray_frame: np.ndarray,
    width: int = 68,
    height: Optional[int] = None,
    mode: str = "document",
    show_guides: bool = True
) -> str:
    """
    Mengonversi citra grayscale OpenCV menjadi representasi ASCII murni
    yang sangat tajam, bersih, dan 100% kompatibel di terminal Windows & Linux.
    """
    h, w = gray_frame.shape[:2]
    if height is None:
        # Rasio aspek font terminal ~2:1
        aspect = h / w
        height = max(10, int(width * aspect * 0.45))

    if mode == "document":
        proc = enhance_frame_for_text(gray_frame, width, height)
    else:
        proc = cv2.resize(gray_frame, (width, height), interpolation=cv2.INTER_AREA)

    ramp = ASCII_RAMP
    ramp_len = len(ramp)

    lines = []
    border = "+" + "-" * width + "+"
    lines.append(border)

    # Indeks baris/kolom untuk area panduan (crosshair & guide box)
    guide_y1 = int(height * 0.15)
    guide_y2 = int(height * 0.85)
    guide_x1 = int(width * 0.15)
    guide_x2 = int(width * 0.85)
    mid_y = height // 2
    mid_x = width // 2

    for r_idx, row in enumerate(proc):
        chars = []
        for c_idx, val in enumerate(row):
            # Garis pemandu sudut dokumen
            if show_guides and (
                (r_idx in (guide_y1, guide_y2) and c_idx in (guide_x1, guide_x2))
            ):
                chars.append("+")
            elif show_guides and (r_idx == mid_y and c_idx == mid_x):
                chars.append("+")
            else:
                # Piksel gelap (tinta teks) = karakter tebal (@, #, %)
                # Piksel terang (kertas putih) = spasi/titik
                idx = min(ramp_len - 1, int((255 - int(val)) / 256.0 * ramp_len))
                chars.append(ramp[idx])

        lines.append("|" + "".join(chars) + "|")

    lines.append(border)
    return "\n".join(lines)


def print_camera_snapshot(frame: np.ndarray, label: str = "TAMPILAN KAMERA"):
    """
    Mencetak snapshot citra kamera ke konsol terminal dengan teks bersih dan kontras tinggi.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
    mean_bright = float(gray.mean())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    term_width = shutil.get_terminal_size((80, 24)).columns
    render_width = min(68, max(42, term_width - 6))

    status_cahaya = (
        "GELAP TOTAL" if mean_bright < 15.0
        else "KURANG CAHAYA" if mean_bright < 70.0
        else "TERLALU SILAU" if mean_bright > 220.0
        else "OPTIMAL"
    )
    status_fokus = "FOKUS TAJAM" if sharpness >= 60.0 else "BURAM"

    print(f"\n--- [{label}] ---")
    print(
        f"Resolusi: {frame.shape[1]}x{frame.shape[0]} | "
        f"Cahaya: {mean_bright:.1f}/255 [{status_cahaya}] | "
        f"Fokus: {sharpness:.1f} [{status_fokus}]"
    )

    if mean_bright < 15.0:
        print(">>> PERHATIAN: Gambar gelap total. Periksa privacy shutter / penutup kamera!")
    elif mean_bright < 70.0:
        print(">>> PERHATIAN: Kurang cahaya. Disarankan menyalakan lampu baca / LED.")

    print(frame_to_ascii_clean(gray, width=render_width, mode="document"))
    print("-" * (render_width + 2) + "\n")


def _get_fallback_sample_frame() -> Optional[np.ndarray]:
    """Mencari gambar sampel dokumen dari folder dataset untuk mode simulasi jika kamera fisik tidak ada."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = sorted(glob.glob(os.path.join(base_dir, "dataset", "*.png")))
    if candidates:
        frame = cv2.imread(candidates[0])
        if frame is not None:
            return frame
    # Fallback sintetis jika dataset belum ada
    canvas = np.full((720, 1280, 3), 245, dtype=np.uint8)
    cv2.putText(canvas, "MODE SIMULASI DOKUMEN UJI", (280, 200),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (20, 20, 20), 3)
    cv2.putText(canvas, "Letakkan dokumen pada jarak 25 cm dari sensor.", (280, 280),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (80, 80, 80), 2)
    cv2.putText(canvas, "Perangkat Pembaca Tunanetra (Image to Voice)", (280, 360),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (40, 100, 40), 2)
    return canvas


def stream_terminal_camera(cap, max_fps: int = 8):
    """
    Menampilkan video streaming langsung di dalam konsol teks terminal
    menggunakan mode in-place ANSI update (tanpa scrolling berlebihan).
    """
    enable_windows_ansi()
    is_simulation = cap is None or not cap.isOpened()
    sim_frame = None

    if is_simulation:
        sim_frame = _get_fallback_sample_frame()
        print("\n[Terminal View] Info: Kamera fisik tidak terhubung.")
        print("[Terminal View] Membuka Mode Simulasi menggunakan citra dokumen uji.")
        time.sleep(1.0)
    else:
        print("\n[Terminal View] Memulai pratinjau kamera di terminal...")
        time.sleep(0.5)

    term_width = shutil.get_terminal_size((80, 24)).columns
    render_width = min(68, max(42, term_width - 6))
    frame_delay = 1.0 / max_fps

    sys.stdout.write("\033[?25l")  # Sembunyikan cursor teks
    sys.stdout.flush()

    try:
        frame_count = 0
        t_start = time.time()
        fps_live = 0.0

        while True:
            t0 = time.time()

            if is_simulation:
                frame = sim_frame.copy()
            else:
                ok, frame = cap.read()
                if not ok or frame is None:
                    time.sleep(0.1)
                    continue

            frame_count += 1
            if frame_count % 5 == 0:
                elapsed_total = time.time() - t_start
                if elapsed_total > 0:
                    fps_live = frame_count / elapsed_total

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
            mean_b = float(gray.mean())
            sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())

            status_cahaya = "OPTIMAL" if 70.0 <= mean_b <= 220.0 else ("GELAP" if mean_b < 70.0 else "SILAU")
            status_fokus = "FOKUS OK" if sharp >= 60.0 else "BURAM"
            sim_tag = " [SIMULASI]" if is_simulation else ""

            # In-place refresh (kembalikan kursor ke posisi awal)
            sys.stdout.write("\033[H")
            header = (
                f"\n=== PRATINJAU KAMERA TERMINAL{sim_tag} ===\n"
                f" Resolusi: {frame.shape[1]}x{frame.shape[0]} | Kecepatan: {fps_live:4.1f} FPS\n"
                f" Cahaya  : {mean_b:5.1f}/255 [{status_cahaya}] | Fokus: {sharp:5.1f} [{status_fokus}]\n"
                f" Petunjuk: Tekan [Ctrl + C] untuk kembali ke Menu Utama.\n"
            )
            sys.stdout.write(header)
            ascii_frame = frame_to_ascii_clean(gray, width=render_width, mode="document", show_guides=True)
            sys.stdout.write(ascii_frame + "\n")
            sys.stdout.flush()

            elapsed = time.time() - t0
            time.sleep(max(0.04, frame_delay - elapsed))

    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?25h\n")  # Tampilkan kembali cursor teks
        sys.stdout.flush()
        print("\n[Terminal View] Pratinjau kamera terminal selesai.")


def show_opencv_window(
    cap,
    save_dir: Optional[str] = None,
    cue_callback: Optional[Callable[[str], None]] = None
) -> Optional[np.ndarray]:
    """
    Membuka jendela GUI OpenCV real-time dengan overlay interaktif.
    Fitur Uji Testing:
    - Tekan [SPASI] atau [C]: Ambil foto & simpan ke folder uji (dataset/).
    - Tekan [ENTER]: Ambil foto & langsung kirim ke OCR Pipeline + Suara TTS!
    - Tekan [Q] atau [ESC]: Tutup jendela pratinjau.
    Mengembalikan frame asli jika user menekan Enter untuk pengujian langsung.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    if save_dir is None:
        save_dir = os.path.join(base_dir, "dataset")
    os.makedirs(save_dir, exist_ok=True)

    is_simulation = cap is None or not cap.isOpened()
    sim_frame = None

    if is_simulation:
        sim_frame = _get_fallback_sample_frame()
        print("\n[Window View] Info: Kamera fisik tidak terhubung.")
        print("[Window View] Membuka Mode Simulasi interaktif menggunakan citra dataset/.")
    else:
        print("\n[Window View] Membuka jendela tampilan kamera OpenCV...")

    print(" Kontrol Jendela:")
    print("  [SPASI] / [ENTER] / [C] : Ambil Foto & Langsung Eksekusi OCR + Suara TTS (Uji Aktual)")
    print("  [Q] / [ESC]             : Tutup Jendela Pratinjau\n")

    window_name = "Pratinjau Kamera - [SPASI/ENTER]=Ambil Foto & Baca Teks | [Q]=Tutup"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, 960, 540)

    last_saved_msg = ""
    msg_display_until = 0.0
    flash_frames = 0
    selected_for_ocr_frame = None

    fps_count = 0
    t_start = time.time()
    fps_current = 30.0

    try:
        while True:
            t_loop = time.time()
            if is_simulation:
                raw_frame = sim_frame.copy()
            else:
                ok, raw_frame = cap.read()
                if not ok or raw_frame is None:
                    time.sleep(0.05)
                    continue

            h, w = raw_frame.shape[:2]
            display_frame = raw_frame.copy()

            # Hitung FPS
            fps_count += 1
            if fps_count % 10 == 0:
                dt = time.time() - t_start
                if dt > 0:
                    fps_current = fps_count / dt

            # Hitung fokus & kecerahan pada citra grayscale
            gray = cv2.cvtColor(raw_frame, cv2.COLOR_BGR2GRAY)
            sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            brightness = float(gray.mean())

            # 1. Kotak pemandu area baca dokumen A4 (jarak 25 cm)
            x1, y1 = int(w * 0.15), int(h * 0.10)
            x2, y2 = int(w * 0.85), int(h * 0.90)

            box_color = (0, 220, 0) if sharpness >= 60.0 else (0, 165, 255)
            cv2.rectangle(display_frame, (x1, y1), (x2, y2), box_color, 2)

            # Penanda sudut siku-siku
            arm = int(min(w, h) * 0.05)
            cv2.line(display_frame, (x1, y1), (x1 + arm, y1), box_color, 4)
            cv2.line(display_frame, (x1, y1), (x1, y1 + arm), box_color, 4)
            cv2.line(display_frame, (x2, y1), (x2 - arm, y1), box_color, 4)
            cv2.line(display_frame, (x2, y1), (x2, y1 + arm), box_color, 4)
            cv2.line(display_frame, (x1, y2), (x1 + arm, y2), box_color, 4)
            cv2.line(display_frame, (x1, y2), (x1, y2 - arm), box_color, 4)
            cv2.line(display_frame, (x2, y2), (x2 - arm, y2), box_color, 4)
            cv2.line(display_frame, (x2, y2), (x2, y2 - arm), box_color, 4)

            # Crosshair tengah
            cx, cy = w // 2, h // 2
            cv2.drawMarker(display_frame, (cx, cy), box_color, cv2.MARKER_CROSS, 24, 2)

            # 2. Status Bar Bawah Semi-Transparan
            bar_h = 75
            overlay = display_frame.copy()
            cv2.rectangle(overlay, (0, h - bar_h), (w, h), (20, 20, 20), -1)
            cv2.addWeighted(overlay, 0.75, display_frame, 0.25, 0, display_frame)

            # Teks status indikator
            fokus_txt = f"Fokus: {sharpness:.1f} ({'SIAP/TAJAM' if sharpness >= 60.0 else 'BURAM'})"
            cahaya_txt = f"Cahaya: {brightness:.1f}/255 ({'OPTIMAL' if 70 <= brightness <= 220 else 'KURANG'})"
            info_line1 = f"{fokus_txt}  |  {cahaya_txt}  |  FPS: {fps_current:.1f}"

            fokus_color = (0, 255, 0) if sharpness >= 60.0 else (0, 140, 255)
            cv2.putText(display_frame, info_line1, (20, h - bar_h + 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, fokus_color, 2)

            info_line2 = "[SPASI / ENTER] Ambil Foto & Langsung Baca (OCR + Suara)  |  [Q/ESC] Tutup"
            cv2.putText(display_frame, info_line2, (20, h - bar_h + 58),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.60, (230, 230, 230), 1)

            # Header Mode Simulasi jika aktif
            if is_simulation:
                cv2.rectangle(display_frame, (0, 0), (w, 35), (0, 100, 200), -1)
                cv2.putText(display_frame, "MODE SIMULASI DOKUMEN UJI (Kamera fisik tidak aktif)",
                            (20, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)

            # Notifikasi visual saat foto baru saja diambil
            if time.time() < msg_display_until:
                cv2.rectangle(display_frame, (0, 38 if is_simulation else 0), (w, 85 if is_simulation else 50),
                              (0, 160, 0), -1)
                cv2.putText(display_frame, f"v {last_saved_msg}",
                            (20, 72 if is_simulation else 34),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)

            # Efek Flash Putih saat capture
            if flash_frames > 0:
                display_frame = np.full_like(display_frame, 255)
                flash_frames -= 1

            cv2.imshow(window_name, display_frame)

            key = cv2.waitKey(20) & 0xFF

            # [Q] atau [ESC] -> Tutup
            if key == ord('q') or key == 27:
                print("[Window View] Jendela pratinjau ditutup oleh pengguna.")
                break

            # [SPASI], [C], atau [ENTER] -> Ambil Foto & Langsung Eksekusi OCR + Suara TTS
            elif key in (32, ord('c'), ord('C'), 10, 13):
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"uji_kamera_aktual_{timestamp}.png"
                filepath = os.path.join(save_dir, filename)

                # Simpan frame asli (resolusi penuh tanpa gambar overlay)
                cv2.imwrite(filepath, raw_frame)

                if cue_callback:
                    cue_callback('klik')

                print(f"\n[Window View] Foto berhasil diambil: {filepath}")
                print_camera_snapshot(raw_frame, label=f"FOTO UJI DIAMBIL: {filename}")
                print("[Window View] Menutup jendela dan langsung mengeksekusi Pipeline OCR & Sintesis Suara...")
                selected_for_ocr_frame = raw_frame
                break

    finally:
        cv2.destroyAllWindows()

    return selected_for_ocr_frame
