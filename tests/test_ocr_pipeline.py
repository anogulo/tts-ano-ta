"""
test_ocr_pipeline.py - Unit Test untuk Modul OCR Pipeline
Bagian dari Rancang Bangun Alat Bantu Baca Tunanetra (Image to Voice)
Sesuai PRD Bagian 19 (T11).
"""

import unittest
import numpy as np
import cv2

from ocr_pipeline import (
    to_gray,
    quality_problem,
    deskew,
    binarize,
    clean_text,
    to_sentences
)


class TestOcrPipeline(unittest.TestCase):

    def setUp(self):
        self.cfg_quality = {
            'min_brightness': 70.0,
            'min_sharpness': 60.0,
            'max_retries': 2
        }

    # ================= 1. Pengujian clean_text =================
    def test_clean_text_hyphenation(self):
        """FR-06: Menguji penggabungan kata terpenggal tanda hubung."""
        input_text = "Proses pem-\nbacaan dokumen ber-\n jalan dengan lancar."
        expected = "Proses pembacaan dokumen berjalan dengan lancar."
        result = clean_text(input_text)
        self.assertEqual(result, expected)

    def test_clean_text_garbage_symbols(self):
        """FR-06: Menguji pembersihan karakter acak/sampah."""
        input_text = "Teks ini [bersih] & tanpa ~simbol^ acak *yang #mengganggu."
        result = clean_text(input_text)
        # Karakter ^ * # ~ harus dibersihkan, huruf dan spasi dipertahankan
        self.assertNotIn('^', result)
        self.assertNotIn('*', result)
        self.assertNotIn('#', result)
        self.assertNotIn('~', result)
        self.assertIn("bersih", result)

    def test_clean_text_abbreviations(self):
        """FR-07: Menguji normalisasi singkatan untuk TTS alami."""
        input_text = "dr. Budi tinggal di Jl. Sudirman No. 10 dengan biaya Rp. 50.000,- di RSUD Bandung s.d. selesai..."
        result = clean_text(input_text)
        self.assertIn("dokter", result)
        self.assertIn("jalan", result)
        self.assertIn("nomor", result)
        self.assertIn("rupiah", result)
        self.assertIn("rumah sakit umum daerah", result)
        self.assertIn("sampai dengan", result)
        self.assertNotIn("...", result)

    def test_clean_text_reduplication(self):
        """Menguji perapian spasi pada kata ulang Bahasa Indonesia."""
        input_text = "Anak - anak dan kupu - kupu sedang bermain bersama."
        result = clean_text(input_text)
        self.assertIn("Anak-anak", result)
        self.assertIn("kupu-kupu", result)

    def test_clean_text_empty(self):
        """Menguji penanganan input kosong atau whitespace."""
        self.assertEqual(clean_text(""), "")
        self.assertEqual(clean_text("   \n\t  "), "")
        self.assertEqual(clean_text([]), "")

    # ================= 2. Pengujian to_sentences =================
    def test_to_sentences_splitting(self):
        """FR-08: Menguji pemecahan teks menjadi potongan kalimat natural."""
        text = "Ini adalah kalimat pertama yang cukup panjang. Dan ini kalimat kedua yang juga lengkap! Apakah ini yang ketiga?"
        sentences = to_sentences(text, min_len=15)
        self.assertGreaterEqual(len(sentences), 2)
        self.assertTrue(any("kalimat pertama" in s for s in sentences))

    def test_to_sentences_merge_short(self):
        """FR-08: Menguji bahwa kalimat yang terlalu pendek digabungkan ke buffer berikutnya."""
        text = "Ya. Tentu saja kami siap membantu Anda hari ini secara penuh."
        sentences = to_sentences(text, min_len=20)
        # "Ya." harus digabung ke kalimat berikutnya karena panjang < 20 karakter
        self.assertEqual(len(sentences), 1)
        self.assertTrue(sentences[0].startswith("Ya. Tentu saja"))

    # ================= 3. Pengujian quality_problem =================
    def test_quality_problem_dark(self):
        """FR-03: Deteksi citra terlalu gelap."""
        dark_img = np.zeros((100, 100), dtype=np.uint8)  # Mean = 0 < 70
        problem = quality_problem(dark_img, self.cfg_quality)
        self.assertEqual(problem, 'gelap')

    def test_quality_problem_blur(self):
        """FR-03: Deteksi citra buram (flat brightness tanpa kontras tepi)."""
        flat_img = np.full((100, 100), 150, dtype=np.uint8)  # Mean = 150 (terang), laplacian var = 0 (buram)
        problem = quality_problem(flat_img, self.cfg_quality)
        self.assertEqual(problem, 'buram')

    def test_quality_problem_pass(self):
        """FR-03: Citra tajam dan terang harus lolos (problem = None)."""
        # Buat gambar papan catur dengan tepi tajam dan kecerahan tinggi
        chess = np.zeros((100, 100), dtype=np.uint8)
        chess[::20, :] = 255
        chess[:, ::20] = 255
        chess = cv2.add(chess, 80)  # Pastikan mean > 70
        problem = quality_problem(chess, self.cfg_quality)
        self.assertIsNone(problem)

    # ================= 4. Pengujian binarize =================
    def test_binarize_modes(self):
        """FR-04: Menguji mode binarisasi gray, otsu, dan adaptive."""
        sample_img = np.random.randint(50, 200, (100, 100), dtype=np.uint8)

        gray_res = binarize(sample_img, 'gray')
        self.assertEqual(gray_res.shape, (100, 100))

        otsu_res = binarize(sample_img, 'otsu')
        self.assertEqual(otsu_res.shape, (100, 100))

        adapt_res = binarize(sample_img, 'adaptive')
        self.assertEqual(adapt_res.shape, (100, 100))


if __name__ == '__main__':
    unittest.main()
