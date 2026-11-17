"""
ocr_pipeline.py - Pipeline Pengolahan Citra dan OCR Bahasa Indonesia
Bagian dari Rancang Bangun Alat Bantu Baca Tunanetra (Image to Voice)
Sesuai PRD Bagian 5 (FR-03, FR-04, FR-05, FR-06, FR-07), Bagian 18, dan Bagian 19 (T13).
"""

import math
import os
import platform
import re
import shutil
from typing import Dict, List, Optional, Union
import cv2
import numpy as np
import pytesseract

# Auto-detect tesseract binary di Windows jika tidak ada di PATH
if platform.system() == "Windows" and not shutil.which("tesseract"):
    standard_win_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"
    ]
    for p in standard_win_paths:
        if os.path.exists(p):
            pytesseract.pytesseract.tesseract_cmd = p
            break

# Kamus normalisasi singkatan umum Bahasa Indonesia (FR-07)
ABBR_RULES: List[tuple] = [
    # Format mata uang Rupiah (termasuk format dengan suffix ,-)
    (r'\bRp\.?\s*([\d.,]+)(?:,-)?', r'\1 rupiah '),
    (r'\bRp\.?\s*', 'rupiah '),
    # Gelar & sebutan
    (r'\bdr\.\s*', 'dokter '),
    (r'\bDr\.\s*', 'doktor '),
    (r'\bProf\.\s*', 'profesor '),
    (r'\bIr\.\s*', 'insinyur '),
    (r'\bNy\.\s*', 'nyonya '),
    (r'\bTn\.\s*', 'tuan '),
    (r'\bSdr\.\s*', 'saudara '),
    (r'\bSdri\.\s*', 'saudari '),
    # Alamat & penomoran
    (r'\bJl\.\s*', 'jalan '),
    (r'\bNo\.\s*', 'nomor '),
    (r'\bRt\.\s*', 'rukun tetangga '),
    (r'\bRw\.\s*', 'rukun warga '),
    (r'\bKel\.\s*', 'kelurahan '),
    (r'\bKec\.\s*', 'kecamatan '),
    (r'\bKab\.\s*', 'kabupaten '),
    # Lembaga & badan usaha
    (r'\bPT\.\s*', 'perseroan terbatas '),
    (r'\bCV\.\s*', 'persekutuan komanditer '),
    (r'\bUD\.\s*', 'usaha dagang '),
    (r'\bRS\.\s*', 'rumah sakit '),
    (r'\bRSUD\b', 'rumah sakit umum daerah'),
    # Singkatan umum dokumen
    (r'\bdkk\.\s*', 'dan kawan-kawan '),
    (r'\bdll\.\s*', 'dan lain-lain '),
    (r'\bdsb\.\s*', 'dan sebagainya '),
    (r'\bdst\.\s*', 'dan seterusnya '),
    (r'\btgl\.\s*', 'tanggal '),
    (r'\bhlm\.\s*', 'halaman '),
    (r'\btlp\.\s*', 'telepon '),
    (r'\bttd\.\s*', 'tanda tangan '),
    (r'\bLamp\.\s*', 'lampiran '),
    (r'\bs\.d\.\s*', 'sampai dengan '),
    (r'\bs\/d\s*', 'sampai dengan '),
    (r'\byth\.\s*', 'yang terhormat '),
    (r'\bYth\.\s*', 'yang terhormat '),
]


def to_gray(img: np.ndarray) -> np.ndarray:
    """Mengubah citra BGR ke Grayscale."""
    if len(img.shape) == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def quality_problem(gray: np.ndarray, cfg: dict) -> Optional[str]:
    """
    FR-03: Quality check sebelum OCR.
    Memeriksa tingkat kecerahan dan ketajaman gambar.
    Kembalikan 'gelap', 'buram', atau None jika lolos.
    """
    min_bright = float(cfg.get('min_brightness', 45.0))
    min_sharp = float(cfg.get('min_sharpness', 20.0))

    mean_brightness = float(gray.mean())
    if mean_brightness < min_bright:
        return 'gelap'

    # Variansi Laplacian untuk mendeteksi blur
    laplacian_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if laplacian_var < min_sharp:
        return 'buram'

    return None


def deskew(gray: np.ndarray, max_angle: float = 45.0) -> np.ndarray:
    """
    T13: Mengoreksi kemiringan (deskew) dokumen bila miring.
    Aman terhadap dokumen horizontal (sudut kecil diabaikan).
    """
    # Inversi citra dan cari koordinat titik non-zero
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(thresh > 0))
    if len(coords) < 100:
        return gray

    # Hitung orientasi area minimum
    rect = cv2.minAreaRect(coords)
    angle = rect[-1]

    # Standarisasi sudut OpenCV
    if angle < -45:
        angle = -(90 + angle)
    elif angle > 45:
        angle = -(angle - 90)
    else:
        angle = -angle

    # Abaikan jika sudut sangat kecil (kurang dari 0.5 derajat) atau terlalu ekstrem
    if abs(angle) < 0.5 or abs(angle) > max_angle:
        return gray

    h, w = gray.shape[:2]
    center = (w // 2, h // 2)
    m = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        gray, m, (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE
    )
    return rotated


def preprocess_camera_image(gray: np.ndarray) -> np.ndarray:
    """
    Preprocessing tingkat lanjut khusus citra tangkapan kamera / webcam.
    Menerapkan:
    1. Bilateral filter untuk membersihkan noise sensor tanpa mengaburkan tepi teks.
    2. CLAHE untuk meratakan kontras pencahayaan tidak seragam (bayangan tangan/lampu).
    3. Unsharp masking untuk mempertegas dan mempertajam kontur huruf.
    """
    # 1. Bilateral filter
    denoised = cv2.bilateralFilter(gray, 5, 50, 50)

    # 2. CLAHE (Contrast Limited Adaptive Histogram Equalization)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    enhanced = clahe.apply(denoised)

    # 3. Unsharp masking
    gaussian = cv2.GaussianBlur(enhanced, (0, 0), 2.0)
    unsharp = cv2.addWeighted(enhanced, 1.5, gaussian, -0.5, 0)
    return unsharp


def binarize(gray: np.ndarray, mode: str = 'camera') -> np.ndarray:
    """
    FR-04: Preprocessing citra sebelum OCR.
    Mode:
    - 'camera' / 'enhanced' : Penajaman kontras adaptif khusus kamera/webcam (Sangat Direkomendasikan).
    - 'gray'                : Citra grayscale mentah.
    - 'otsu'                : Binarisasi biner global Otsu.
    - 'adaptive'            : Binarisasi adaptif Gaussian.
    """
    if mode in ('camera', 'enhanced'):
        return preprocess_camera_image(gray)

    if mode == 'gray':
        return gray

    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    if mode == 'otsu':
        return cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    elif mode == 'adaptive':
        return cv2.adaptiveThreshold(
            blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 31, 15
        )
    return gray


def run_ocr(img: np.ndarray, cfg: dict) -> List[str]:
    """
    FR-05: Ekstraksi teks Tesseract dengan filter confidence dan multi-PSM fallback.
    Mendukung Bahasa Indonesia & istilah asing/teknis (ind+eng).
    Fallback otomatis: PSM 6 -> PSM 3 -> PSM 11 jika teks jarang/label/kemasan.
    """
    tess_config_base = "--oem 1"
    tessdata_dir = cfg.get('tessdata_dir', '')
    if tessdata_dir and os.path.exists(tessdata_dir):
        tess_config_base += f' --tessdata-dir "{tessdata_dir}"'

    target_lang = cfg.get('lang', 'ind+eng')
    min_conf = float(cfg.get('min_conf', 45.0))

    def _ocr_attempt(psm_val: int, lang_to_use: str) -> List[str]:
        full_config = f"{tess_config_base} --psm {psm_val}"
        try:
            d = pytesseract.image_to_data(
                img,
                lang=lang_to_use,
                config=full_config,
                output_type=pytesseract.Output.DICT
            )
        except Exception:
            # Fallback jika kombinasi bahasa gagal, coba bahasa tunggal
            if '+' in lang_to_use:
                try:
                    d = pytesseract.image_to_data(
                        img,
                        lang='ind',
                        config=full_config,
                        output_type=pytesseract.Output.DICT
                    )
                except Exception:
                    return []
            else:
                return []

        lines: Dict[tuple, List[str]] = {}
        for i, word in enumerate(d['text']):
            word_str = word.strip()
            if not word_str:
                continue
            conf_val = float(d['conf'][i])

            # Pertahankan kata jika confidence cukup, atau jika kata cukup panjang (>= 3 karakter) dengan conf >= 38
            is_valid_word = (conf_val >= min_conf) or (len(word_str) >= 3 and conf_val >= 38.0)
            if is_valid_word:
                # Singkirkan karakter acak tunggal non-alfanumerik
                if len(word_str) == 1 and not word_str.isalnum() and word_str not in '.,:;!?()-/':
                    continue
                key = (d['block_num'][i], d['par_num'][i], d['line_num'][i])
                lines.setdefault(key, []).append(word_str)

        return [' '.join(words) for words in lines.values() if words]

    # 1. Coba PSM konfigurasi (default: 6)
    res = _ocr_attempt(cfg.get('psm', 6), target_lang)

    # 2. Jika hasil kosong atau terlalu sedikit (< 10 karakter), coba PSM 3 (Auto layout)
    total_chars = sum(len(line) for line in res)
    if total_chars < 10:
        res_psm3 = _ocr_attempt(3, target_lang)
        if sum(len(line) for line in res_psm3) > total_chars:
            res = res_psm3
            total_chars = sum(len(line) for line in res)

    # 3. Jika masih sedikit, coba PSM 11 (Sparse text, sangat bagus untuk label/kemasan/judul)
    if total_chars < 10:
        res_psm11 = _ocr_attempt(11, target_lang)
        if sum(len(line) for line in res_psm11) > total_chars:
            res = res_psm11

    return res


def clean_text(lines: Union[List[str], str]) -> str:
    """
    FR-06 & FR-07: Pembersihan teks, penggabungan kata terpenggal tanda hubung,
    penghapusan karakter sampah, dan normalisasi singkatan untuk TTS.
    """
    if isinstance(lines, list):
        text = '\n'.join(lines)
    else:
        text = str(lines)

    if not text.strip():
        return ''

    # 1. Sambung kata terpenggal tanda hubung di akhir baris (hyphenation fix)
    text = re.sub(r'(\w+)-\s*\n\s*(\w+)', r'\1\2', text)
    text = re.sub(r'(\w+)-\s+(\w+)', r'\1\2', text)

    # 1b. Rapikan kata ulang Bahasa Indonesia yang memiliki spasi liar di sekitar tanda hubung (anak - anak -> anak-anak)
    text = re.sub(r'\b(\w+)\s*-\s*(\1)\b', r'\1-\2', text, flags=re.IGNORECASE)

    # 2. Hapus simbol asing/karakter sampah yang tidak terbaca oleh TTS
    # Pertahankan huruf, angka, tanda baca umum, dan spasi
    text = re.sub(r'[^\w\s.,;:!?()%/\'\"-]', ' ', text)

    # 2b. Rapikan tanda titik ganda / elipsis menjadi satu titik
    text = re.sub(r'\.{2,}', '.', text)

    # 3. Ganti baris baru menjadi spasi
    text = re.sub(r'\s*\n\s*', ' ', text)

    # 4. Normalisasi singkatan & format dokumen (FR-07)
    for pattern, repl in ABBR_RULES:
        text = re.sub(pattern, repl, text)

    # 5. Rapikan spasi berlebih
    text = re.sub(r'\s{2,}', ' ', text).strip()
    return text


def to_sentences(text: str, min_len: int = 20, max_len: int = 90) -> List[str]:
    """
    FR-08: Memecah teks menjadi potongan kalimat natural untuk TTS pipeline.
    Kalimat yang terlalu pendek digabung agar pengucapan mengalir alami.
    Kalimat yang terlalu panjang dipecah pada tanda jeda natural (: , ; -)
    agar latensi time-to-first-audio sangat rendah (< 0.5 detik).
    """
    if not text or not text.strip():
        return []

    # 1. Pisahkan berdasarkan tanda akhir kalimat (. ? !) diikuti spasi
    primary_parts = [p.strip() for p in re.split(r'(?<=[.!?])\s+', text) if p.strip()]
    if not primary_parts:
        primary_parts = [text.strip()]

    # 2. Jika ada bagian yang melebihi max_len, pecah pada jeda klausa natural
    sub_parts: List[str] = []
    for part in primary_parts:
        if len(part) > max_len:
            chunks = [c.strip() for c in re.split(r'(?<=[,;:])\s+|\s+-\s+', part) if c.strip()]
            sub_parts.extend(chunks if chunks else [part])
        else:
            sub_parts.append(part)

    out: List[str] = []
    buf = ''

    for part in sub_parts:
        if buf:
            buf = f'{buf} {part}'.strip()
        else:
            buf = part

        if len(buf) >= min_len:
            out.append(buf)
            buf = ''

    if buf:
        out.append(buf)

    return out
