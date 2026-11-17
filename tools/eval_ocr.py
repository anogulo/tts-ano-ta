"""
eval_ocr.py - Evaluasi Akurasi OCR (CER & WER) pada Dataset
Bagian dari Rancang Bangun Alat Bantu Baca Tunanetra (Image to Voice)
Sesuai PRD Bagian 3, Bagian 10 (Dataset Uji 50 Sampel), Bagian 16, dan Bagian 19 (T03, T04).

Menghitung:
- Character Error Rate (CER) - Target: <= 5%
- Word Error Rate (WER) - Target: <= 10%
"""

import argparse
import glob
import os
import sys
import tomllib
from typing import Dict, List, Tuple

import cv2

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from ocr_pipeline import binarize, clean_text, deskew, run_ocr, to_gray


def levenshtein_distance(ref: List[str], hyp: List[str]) -> int:
    """Menghitung jarak Levenshtein antara dua urutan (karakter atau kata)."""
    n, m = len(ref), len(hyp)
    if n == 0:
        return m
    if m == 0:
        return n

    # Alokasi matriks DP baris sebelumnya & saat ini
    prev = list(range(m + 1))
    curr = [0] * (m + 1)

    for i in range(1, n + 1):
        curr[0] = i
        ref_char = ref[i - 1]
        for j in range(1, m + 1):
            cost = 0 if ref_char == hyp[j - 1] else 1
            curr[j] = min(
                prev[j] + 1,       # deletion
                curr[j - 1] + 1,   # insertion
                prev[j - 1] + cost # substitution
            )
        prev, curr = curr, prev

    return prev[m]


def compute_cer(reference: str, hypothesis: str) -> float:
    """Menghitung Character Error Rate (CER)."""
    ref_chars = list(reference)
    hyp_chars = list(hypothesis)
    if not ref_chars:
        return 0.0 if not hyp_chars else 1.0
    dist = levenshtein_distance(ref_chars, hyp_chars)
    return dist / len(ref_chars)


def compute_wer(reference: str, hypothesis: str) -> float:
    """Menghitung Word Error Rate (WER)."""
    ref_words = reference.split()
    hyp_words = hypothesis.split()
    if not ref_words:
        return 0.0 if not hyp_words else 1.0
    dist = levenshtein_distance(ref_words, hyp_words)
    return dist / len(ref_words)


def evaluate_dataset(dataset_dir: str, cfg: dict, mode_override: str = None) -> Dict[str, float]:
    """
    Menjalankan evaluasi pada seluruh sampel dataset gambar + teks acuan (.txt).
    """
    if mode_override:
        cfg['ocr']['mode'] = mode_override

    image_extensions = ('*.png', '*.jpg', '*.jpeg', '*.bmp', '*.tiff')
    image_files = []
    for ext in image_extensions:
        image_files.extend(glob.glob(os.path.join(dataset_dir, ext)))

    if not image_files:
        print(f"Tidak ditemukan berkas gambar di direktori: {dataset_dir}")
        return {"total": 0, "cer": 0.0, "wer": 0.0}

    image_files.sort()
    total_cer = 0.0
    total_wer = 0.0
    valid_samples = 0

    print(f"\n=== Memulai Evaluasi OCR (Mode: {cfg['ocr'].get('mode', 'gray')}) ===")
    print(f"{'No':<4} | {'Berkas':<25} | {'CER (%)':<10} | {'WER (%)':<10} | Status")
    print("-" * 65)

    for idx, img_path in enumerate(image_files, 1):
        base_name = os.path.splitext(img_path)[0]
        gt_path = f"{base_name}.txt"

        if not os.path.exists(gt_path):
            continue

        with open(gt_path, 'r', encoding='utf-8') as f:
            ground_truth = clean_text(f.read())

        img = cv2.imread(img_path)
        if img is None:
            continue

        gray = to_gray(img)
        deskewed = deskew(gray)
        processed = binarize(deskewed, cfg['ocr'].get('mode', 'gray'))

        lines = run_ocr(processed, cfg['ocr'])
        ocr_result = clean_text(lines)

        cer = compute_cer(ground_truth, ocr_result)
        wer = compute_wer(ground_truth, ocr_result)

        total_cer += cer
        total_wer += wer
        valid_samples += 1

        status_str = "LULUS" if cer <= 0.05 else "EVALUASI"
        file_label = os.path.basename(img_path)
        print(f"{idx:<4} | {file_label:<25} | {cer * 100:>8.2f}% | {wer * 100:>8.2f}% | {status_str}")

    if valid_samples == 0:
        print("Tidak ada sampel dengan berkas teks acuan (.txt) yang valid.")
        return {"total": 0, "cer": 0.0, "wer": 0.0}

    mean_cer = total_cer / valid_samples
    mean_wer = total_wer / valid_samples

    print("-" * 65)
    print(f"Total Sampel Terevaluasi : {valid_samples}")
    print(f"Rata-rata CER            : {mean_cer * 100:.2f}% (Target: <= 5.00%) -> {'[MEMENUHI TARGET]' if mean_cer <= 0.05 else '[BELUM MEMENUHI]'}")
    print(f"Rata-rata WER            : {mean_wer * 100:.2f}% (Target: <= 10.00%) -> {'[MEMENUHI TARGET]' if mean_wer <= 0.10 else '[BELUM MEMENUHI]'}")

    return {
        "total": valid_samples,
        "cer": mean_cer,
        "wer": mean_wer
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluasi akurasi OCR pada dataset acuan.")
    parser.add_argument('--dataset', default=os.path.join(BASE_DIR, 'dataset'), help="Direktori dataset uji")
    parser.add_argument('--mode', choices=['gray', 'otsu', 'adaptive'], default=None, help="Eksperimen preprocessing A/B")
    args = parser.parse_args()

    cfg_path = os.path.join(BASE_DIR, 'config.toml')
    with open(cfg_path, 'rb') as f:
        cfg = tomllib.load(f)

    evaluate_dataset(args.dataset, cfg, mode_override=args.mode)


if __name__ == '__main__':
    main()
