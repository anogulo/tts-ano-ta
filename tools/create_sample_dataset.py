"""
create_sample_dataset.py - Pembuat Sampel Dataset Uji Awal (Dokumen & Label)
Bagian dari Rancang Bangun Alat Bantu Baca Tunanetra (Image to Voice)
Sesuai PRD Bagian 10 (Dataset Uji 50 Sampel) dan Bagian 19 (T02).
"""

import os
import cv2
import numpy as np

DATASET_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'dataset')
os.makedirs(DATASET_DIR, exist_ok=True)

SAMPLES = [
    {
        "id": "sampel_01_surat_resmi",
        "lines": [
            "KEMENTERIAN PENDIDIKAN DAN KEBUDAYAAN",
            "UNIVERSITAS INDONESIA - FAKULTAS TEKNIK",
            "Jl. Salemba Raya No. 4 Jakarta Pusat",
            "Nomor Surat: 124/FT/XI/2026",
            "Lampiran: 1 Berkas",
            "Perihal: Undangan Seminar Nasional Teknologi Asistif",
            "Kepada Yth. Bapak dan Ibu Dosen",
            "Dengan hormat, kami mengundang kehadiran Bapak dan Ibu pada seminar nasional.",
        ]
    },
    {
        "id": "sampel_02_label_obat",
        "lines": [
            "APOTEK SEHAT PHARMA",
            "dr. Budi Santoso, Sp.A",
            "Jl. Diponegoro No. 88 Bandung",
            "Pasien: Ananda Rizky (Umur 8 Tahun)",
            "Aturan Pakai: 3 kali sehari 1 sendok takar",
            "Diminum sesudah makan pagi, siang, dan malam.",
            "Harga: Rp. 45.000,-",
            "Kocok dahulu sebelum diminum. Simpan di tempat sejuk.",
        ]
    },
    {
        "id": "sampel_03_kutipan_buku",
        "lines": [
            "BAB 1 PENDAHULUAN",
            "Perkembangan teknologi computer vision dan kecerdasan buatan",
            "telah membuka peluang besar dalam menciptakan perangkat asistif.",
            "Penyandang tunanetra kini dapat membaca dokumen cetak",
            "secara mandiri tanpa perlu bergantung pada bantuan visual orang lain.",
            "Implementasi sistem offline pada perangkat tertanam memastikan",
            "keamanan data serta keandalan penggunaan di segala situasi.",
        ]
    },
    {
        "id": "sampel_04_lembar_soal",
        "lines": [
            "UJIAN TENGAH SEMESTER TAHUN AKADEMIK 2026",
            "Mata Kuliah: Pemrosesan Sinyal Digital",
            "Waktu Ujian: 90 Menit",
            "Sifat Ujian: Buku Terbuka",
            "Petunjuk: Jawablah seluruh pertanyaan dengan singkat dan jelas.",
            "Soal 1. Jelaskan prinsip dasar Discrete Fourier Transform.",
            "Soal 2. Hitung respons frekuensi dari filter digital berikut.",
        ]
    },
    {
        "id": "sampel_05_struk_pembayaran",
        "lines": [
            "TOKO BUKU CAHAYA ILMU",
            "Jl. Malioboro No. 12 Yogyakarta",
            "Struk Pembelian No. 89211",
            "1. Buku Panduan Python Rp. 85.000",
            "2. Buku Algoritma Dasar Rp. 95.000",
            "Total Pembayaran: Rp. 180.000",
            "Tunai: Rp. 200.000",
            "Kembalian: Rp. 20.000",
            "Terima kasih atas kunjungan Anda.",
        ]
    }
]


def render_sample(sample_data: dict):
    file_id = sample_data["id"]
    lines = sample_data["lines"]

    # Buat kanvas 1080p putih bersih (1920x1080)
    img = np.full((1080, 1920, 3), 255, dtype=np.uint8)
    font = cv2.FONT_HERSHEY_SIMPLEX

    y = 180
    for line in lines:
        cv2.putText(img, line, (100, y), font, 1.1, (20, 20, 20), 2, cv2.LINE_AA)
        y += 85

    img_path = os.path.join(DATASET_DIR, f"{file_id}.png")
    txt_path = os.path.join(DATASET_DIR, f"{file_id}.txt")

    cv2.imwrite(img_path, img)
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f"Dibuat: {file_id}.png dan {file_id}.txt")


def main():
    print(f"Membuat sampel awal dataset di: {DATASET_DIR}")
    for s in SAMPLES:
        render_sample(s)
    print("Selesai membuat sampel dataset awal.")


if __name__ == '__main__':
    main()
