"""
bench.py - Benchmark Kinerja: Latensi per Tahap, RAM, Suhu (Gate 1 & Gate 2)
Bagian dari Rancang Bangun Alat Bantu Baca Tunanetra (Image to Voice)
Sesuai PRD Bagian 3, Bagian 8 (Anggaran Latensi), Bagian 10, dan Bagian 19 (T05, T06).

Menguji:
1. Waktu Quality Check & Preprocessing (Target: 0,1 - 0,5 detik)
2. Waktu OCR Tesseract (Target: 1,5 - 3,0 detik)
3. Waktu Post-Processing & Normalisasi (Target: < 0,1 detik)
4. Waktu Sintesis Kalimat Pertama Piper TTS (Target: <= 1,5 detik)
5. Total Time-to-First-Audio (Target: <= 5,0 detik rata-rata, <= 7,0 detik P95)
6. Penggunaan RAM Puncak (Target: <= 1,5 GB)
"""

import argparse
import os
import platform
import subprocess
import sys
import time
import tomllib
from typing import Dict, List

import cv2
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from ocr_pipeline import binarize, clean_text, deskew, quality_problem, run_ocr, to_gray, to_sentences
from speech import Speaker


def get_current_memory_mb() -> float:
    """Mengukur penggunaan memori proses saat ini dalam MB."""
    try:
        import psutil
        process = psutil.Process(os.getpid())
        return process.memory_info().rss / (1024 * 1024)
    except Exception:
        # Fallback jika psutil belum terpasang
        return 0.0


def get_cpu_temp() -> float:
    """Membaca suhu CPU di Raspberry Pi jika tersedia."""
    temp_file = '/sys/class/thermal/thermal_zone0/temp'
    if os.path.exists(temp_file):
        try:
            with open(temp_file, 'r') as f:
                return float(f.read().strip()) / 1000.0
        except Exception:
            pass
    return 0.0


def create_synthetic_test_image(text: str = "PENGUJIAN BENCHMARK ALAT BANTU BACA TUNANETRA") -> np.ndarray:
    """Membuat citra sintetis beresolusi 1920x1080 untuk benchmarking."""
    img = np.full((1080, 1920, 3), 255, dtype=np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX

    lines = [
        "RESEP DOKTER DAN PANDUAN PENGOBATAN",
        "dr. Ahmad Santoso - RS Sehat Sentosa",
        "Jl. Merdeka No. 45 Jakarta Pusat",
        "Tanggal: 15 November 2026",
        "Biaya Administrasi: Rp. 50.000,-",
        "Aturan Pakai: 3 kali sehari setelah makan.",
        "Simpan obat pada tempat kering dan sejuk, hindarkan dari sinar matahari langsung.",
        "Semoga lekas sembuh dan sehat selalu."
    ]

    y = 200
    for line in lines:
        cv2.putText(img, line, (120, y), font, 1.2, (20, 20, 20), 2, cv2.LINE_AA)
        y += 80

    return img


def run_benchmark(iterations: int = 30, image_path: str = None) -> Dict:
    """Menjalankan benchmark sebanyak N iterasi."""
    cfg_path = os.path.join(BASE_DIR, 'config.toml')
    with open(cfg_path, 'rb') as f:
        cfg = tomllib.load(f)

    # Siapkan citra uji
    if image_path and os.path.exists(image_path):
        img_bgr = cv2.imread(image_path)
    else:
        print("Menggunakan citra dokumen uji standar 1080p...")
        img_bgr = create_synthetic_test_image()

    # Siapkan speaker
    model_path = os.path.join(BASE_DIR, cfg['tts']['model'])
    print(f"Memuat Piper TTS dari: {model_path}")
    speaker = Speaker(model_path, cfg['audio']['device'])

    latencies_qc: List[float] = []
    latencies_ocr: List[float] = []
    latencies_post: List[float] = []
    latencies_tts_first: List[float] = []
    latencies_total_ttfa: List[float] = []
    peak_ram_mb: float = 0.0

    print(f"\n=== Menjalankan Benchmark {iterations} Iterasi ===")
    print(f"{'No':<4} | {'QC+Prep':<10} | {'OCR':<10} | {'Post-Proc':<10} | {'TTS-1st':<10} | {'TTFA Total':<10}")
    print("-" * 65)

    for i in range(1, iterations + 1):
        t_start = time.monotonic()

        # 1. Quality Check & Preprocessing
        t0 = time.monotonic()
        gray = to_gray(img_bgr)
        _ = quality_problem(gray, cfg['quality'])
        deskewed = deskew(gray)
        processed = binarize(deskewed, cfg['ocr'].get('mode', 'gray'))
        t_qc = time.monotonic() - t0
        latencies_qc.append(t_qc)

        # 2. OCR Tesseract
        t0 = time.monotonic()
        lines = run_ocr(processed, cfg['ocr'])
        t_ocr = time.monotonic() - t0
        latencies_ocr.append(t_ocr)

        # 3. Post-Processing & Normalisasi
        t0 = time.monotonic()
        text = clean_text(lines)
        sentences = to_sentences(text)
        t_post = time.monotonic() - t0
        latencies_post.append(t_post)

        # 4. Sintesis Kalimat Pertama Piper TTS
        t0 = time.monotonic()
        first_sentence = sentences[0] if sentences else "Teks tidak terdeteksi."
        _ = list(speaker._pcm(first_sentence))
        t_tts = time.monotonic() - t0
        latencies_tts_first.append(t_tts)

        # Total Time-to-first-audio
        ttfa = time.monotonic() - t_start
        latencies_total_ttfa.append(ttfa)

        ram = get_current_memory_mb()
        if ram > peak_ram_mb:
            peak_ram_mb = ram

        print(f"{i:<4} | {t_qc * 1000:>7.1f} ms | {t_ocr:>8.3f} s | {t_post * 1000:>7.2f} ms | {t_tts:>8.3f} s | {ttfa:>8.3f} s")

    # Statistik
    mean_ttfa = sum(latencies_total_ttfa) / len(latencies_total_ttfa)
    p95_ttfa = float(np.percentile(latencies_total_ttfa, 95))
    mean_ocr = sum(latencies_ocr) / len(latencies_ocr)
    mean_tts = sum(latencies_tts_first) / len(latencies_tts_first)

    print("\n" + "=" * 65)
    print("HASIL REKAPITULASI BENCHMARK (Definition of Done Bagian 20):")
    print(f"- Rata-rata Time-to-First-Audio : {mean_ttfa:.2f} s  (Target: <= 5.0 s)  -> {'[LOLOS]' if mean_ttfa <= 5.0 else '[EVALUASI]'}")
    print(f"- P95 Time-to-First-Audio      : {p95_ttfa:.2f} s  (Target: <= 7.0 s)  -> {'[LOLOS]' if p95_ttfa <= 7.0 else '[EVALUASI]'}")
    print(f"- Rata-rata OCR Tesseract      : {mean_ocr:.2f} s  (Target: 1.5 - 3.0 s)")
    print(f"- Rata-rata TTS Kalimat ke-1   : {mean_tts:.2f} s  (Target: <= 1.5 s)  -> {'[LOLOS]' if mean_tts <= 1.5 else '[EVALUASI]'}")
    if peak_ram_mb > 0:
        print(f"- Penggunaan RAM Puncak        : {peak_ram_mb:.1f} MB (Target: <= 1500 MB) -> {'[LOLOS]' if peak_ram_mb <= 1500 else '[EVALUASI]'}")
    cpu_t = get_cpu_temp()
    if cpu_t > 0:
        print(f"- Suhu CPU Saat Ini            : {cpu_t:.1f} °C (Target: < 75.0 °C)")
    print("=" * 65)

    return {
        "mean_ttfa": mean_ttfa,
        "p95_ttfa": p95_ttfa,
        "mean_ocr": mean_ocr,
        "mean_tts": mean_tts
    }


def main():
    parser = argparse.ArgumentParser(description="Benchmark latensi pipeline pembaca tunanetra.")
    parser.add_argument('--iter', type=int, default=10, help="Jumlah iterasi uji (default 10 untuk quick check, 30 untuk gate test)")
    parser.add_argument('--image', type=str, default=None, help="Path citra uji khusus jika ada")
    args = parser.parse_args()

    run_benchmark(iterations=args.iter, image_path=args.image)


if __name__ == '__main__':
    main()
