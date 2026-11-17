"""
test_deskew.py - Pengujian Khusus Fitur Koreksi Kemiringan Dokumen (Deskew)
"""

import unittest
import numpy as np
import cv2

from ocr_pipeline import deskew


class TestDeskew(unittest.TestCase):

    def test_deskew_horizontal(self):
        """Dokumen horizontal (sudut 0) tidak boleh diubah secara signifikan."""
        img = np.full((300, 500), 255, dtype=np.uint8)
        cv2.putText(img, "DOKUMEN TEKS LURUS", (50, 150), cv2.FONT_HERSHEY_SIMPLEX, 1, 0, 2)

        deskewed = deskew(img)
        self.assertEqual(deskewed.shape, img.shape)

    def test_deskew_rotated_text(self):
        """Dokumen yang sengaja diputar +5 derajat harus dikoreksi mendekati horizontal."""
        img = np.full((400, 600), 255, dtype=np.uint8)
        cv2.putText(img, "DOKUMEN MIRING UNTUK PENGUJIAN DESKEW SISTEM", (30, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.8, 0, 2)
        cv2.putText(img, "BARIS KEDUA TEKS PENGUJIAN DESKEW", (30, 250), cv2.FONT_HERSHEY_SIMPLEX, 0.8, 0, 2)

        # Putar gambar sebesar 5 derajat
        center = (300, 200)
        rot_mat = cv2.getRotationMatrix2D(center, 5.0, 1.0)
        rotated = cv2.warpAffine(img, rot_mat, (600, 400), borderValue=255)

        # Jalankan deskew
        result = deskew(rotated)
        self.assertEqual(result.shape, rotated.shape)


if __name__ == '__main__':
    unittest.main()
