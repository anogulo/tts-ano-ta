# Alat Bantu Baca Tunanetra Berbasis Computer Vision (Image to Voice)

Sistem perangkat tertanam berbasis **Raspberry Pi 4** yang bekerja **100% offline** untuk membantu tunanetra membaca dokumen cetak berbahasa Indonesia secara mandiri. Mengintegrasikan Computer Vision (OpenCV), Optical Character Recognition (Tesseract LSTM), dan Text-to-Speech (Piper TTS).

---

## 1. Alur Kerja Sistem (Pipeline)

```
[ Boot Perangkat ] → Muat Model OCR & TTS → Ucapkan "Alat siap."
        ↓
[ Tombol Utama Ditekan ]
        ↓
[ Nyalakan LED + Autofocus + Ambil Gambar ] → Nada klik (880 Hz)
        ↓
[ Quality Check ] --gagal--> Instruksi Suara ("Gambar terlalu gelap" / "Gambar buram")
        ↓ lulus              (Ulangi otomatis maks. 2 kali)
[ Preprocessing (Grayscale + Deskew) ]
        ↓
[ OCR Tesseract (ind) + Filter Confidence ]
        ↓
[ Teks Terdeteksi? ] --tidak--> Ucapkan "Teks tidak terdeteksi."
        ↓ ya
[ Post-Processing & Normalisasi Singkatan / Angka ]
        ↓
[ Pecah Teks per Kalimat ]
        ↓
[ Piper TTS (Kalimat 1) ] → [ Pemutar aplay ] ← Sambil menyintesis kalimat berikutnya
        ↓
[ Selesai ] → Nada selesai (660 Hz -> 440 Hz)
*(Tekan tombol utama kapan saja untuk menghentikan pembacaan seketika)*
```

---

## 2. Struktur Direktori Proyek

```
d:\TA-TTS\tts_new/
├── config.toml                 # Konfigurasi ambang batas kamera, quality, ocr, audio, gpio
├── requirements.txt            # Dependensi paket Python yang dipin
├── reader.service              # Berkas konfigurasi systemd autostart
├── ocr_pipeline.py             # Pipeline preprocessing, deskew, OCR, dan normalisasi teks
├── speech.py                   # Piper TTS streaming generator & aplay player
├── main.py                     # Aplikasi utama: kontrol tombol, mesin status, watchdog
├── assets/                     # 13 Berkas suara status (*.wav)
│   ├── siap.wav
│   ├── klik.wav (880 Hz)
│   ├── memproses.wav
│   ├── selesai.wav (660->440 Hz)
│   ├── gambar_gelap.wav
│   ├── gambar_buram.wav
│   ├── kamera_error.wav
│   ├── tidak_ada_teks.wav
│   ├── belum_ada_bacaan.wav
│   ├── suhu_tinggi.wav
│   ├── daya_tidak_stabil.wav
│   ├── mati.wav
│   └── galat.wav
├── models/
│   ├── id_ID-news_tts-medium.onnx        # Model Piper Bahasa Indonesia
│   └── id_ID-news_tts-medium.onnx.json   # Konfigurasi model suara
├── tools/
│   ├── gen_prompts.py          # Generator 13 berkas WAV prompt & nada sintetik
│   ├── bench.py                # Benchmark latensi TTFA, RAM puncak, dan suhu
│   ├── eval_ocr.py             # Evaluasi akurasi CER & WER pada dataset acuan
│   └── create_sample_dataset.py# Generator sampel uji awal (surat, label, buku, dll)
├── dataset/                    # Citra dokumen uji (*.png) dan teks acuan (*.txt)
├── logs/                       # Berkas reader.log berputar
└── tests/                      # Unit test untuk ocr_pipeline & normalisasi
    └── test_ocr_pipeline.py
```

---

## 3. Pemetaan Perangkat Keras & GPIO

| Fungsi | Komponen | Pin GPIO (BCM) | Pin Fisik Pi 4 | Catatan |
|---|---|---|---|---|
| **Tombol Utama** | Push button besar | `GPIO17` | Pin 11 | Kaki lain ke GND (Pin 9), pull-up internal |
| **Tombol Bantu** | Push button | `GPIO27` | Pin 13 | Kaki lain ke GND (Pin 14), pull-up internal |
| **Gate LED** | MOSFET IRLZ44N | `GPIO22` | Pin 15 | Resistor gate 220 Ω, pulldown 10 kΩ |
| **Kamera** | Webcam USB AF 1080p | USB 3.0 Port | - | Penyangga tegak ± 25 cm ke bidang baca |
| **Audio** | USB Audio Card | USB Port | - | Output jack 3.5 mm ke speaker aktif / headphone |
| **Catu Daya** | Adaptor / Power Bank | USB-C Port | - | 5V / 3A stabil |

---

## 4. Skema Interaksi Tombol (Mesin Status)

1. **Tombol Utama (GPIO17)**:
   - **Tekan Singkat**: Mengambil gambar dan memulai proses pembacaan.
   - **Tekan Saat Suara Berjalan**: Menghentikan pemutaran audio seketika tanpa jeda.
   - **Tekan Tahan (≥ 1,5 detik)**: Memutar ulang teks bacaan terakhir (`replay`).
2. **Tombol Bantu (GPIO27)**:
   - **Tekan Singkat**: Menyesuaikan volume siklis (40% → 60% → 80% → 100% → 40%).
   - **Tekan Tahan (≥ 3,0 detik)**: Menjalankan shutdown sistem secara aman (`sudo shutdown -h now`).

---

## 5. Panduan Instalasi di Raspberry Pi 4 (Bookworm 64-bit)

### Langkah 1: Pasang Paket Sistem
```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y tesseract-ocr tesseract-ocr-ind python3-opencv \
  python3-gpiozero python3-venv alsa-utils v4l-utils git
```

### Langkah 2: Buat Virtual Environment
```bash
mkdir -p ~/reader && cd ~/reader
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Langkah 3: Generate File Suara Status (Sekali di Awal)
```bash
python tools/gen_prompts.py
```

### Langkah 4: Autostart Layanan systemd
```bash
# Izinkan shutdown tanpa kata sandi untuk tombol shutdown aman
echo "$USER ALL=(root) NOPASSWD: /usr/sbin/shutdown" | sudo tee /etc/sudoers.d/reader

# Salin service unit
sudo cp reader.service /etc/systemd/system/reader.service
sudo systemctl daemon-reload
sudo systemctl enable --now reader

# Pantau log secara langsung
journalctl -u reader -f
```

---

## 6. Pengujian & Validasi (Gate 1 - Gate 4)

### A. Uji Unit (Unit Testing)
```bash
python -m unittest discover -s tests -p "test_*.py"
```

### B. Evaluasi Akurasi OCR (CER & WER)
Target DoD: **CER ≤ 5%** dan **WER ≤ 10%**.
```bash
python tools/eval_ocr.py
```
Untuk menguji perbandingan A/B binarisasi:
```bash
python tools/eval_ocr.py --mode otsu
python tools/eval_ocr.py --mode adaptive
```

### C. Benchmark Kinerja & TTFA (Time-to-First-Audio)
Target DoD: **Time-to-first-audio ≤ 5 detik** (rata-rata), **P95 ≤ 7 detik**, **RAM ≤ 1,5 GB**.
```bash
# Jalankan 30 iterasi pengujian acuan
python tools/bench.py --iter 30
```

---

## 7. Status Pemenuhan Kebutuhan (Definition of Done)

- [x] **FR-01 (Input Pemicu)**: Debounced button handling, hold detection (1.5s replay, 3s shutdown), instant interrupt.
- [x] **FR-02 (Akuisisi Citra)**: Webcam MJPG 1080p, settle_frames handling, LED flash trigger.
- [x] **FR-03 (Pengecekan Kualitas)**: Deteksi Laplacian variance (blur) & mean brightness (dark), auto retry maks 2x dengan pesan panduan suara.
- [x] **FR-04 (Preprocessing)**: Grayscale default, deskew sudut otomatis, opsi Otsu & Adaptive.
- [x] **FR-05 (OCR Tesseract)**: LSTM mode (`--oem 1`), bahasa Indonesia (`lang=ind`), filter confidence kata, fallback PSM 6 ke 3.
- [x] **FR-06 (Post-Processing)**: Sambung kata terpenggal tanda hubung (`-`), buang simbol sampah.
- [x] **FR-07 (Normalisasi TTS)**: Normalisasi mata uang (`Rp`), gelar (`dr.`), jalan (`Jl.`), nomor (`No.`), dll.
- [x] **FR-08 (Sintesis Suara)**: Piper TTS ONNX in-memory, pemecahan per kalimat via `to_sentences`.
- [x] **FR-09 (Output Audio)**: Pipeline pemutaran streaming via `aplay`, pemotongan seketika saat tombol ditekan.
- [x] **FR-10 (Umpan Balik Audio)**: 13 audio cues lengkap (nada sintetis 880 Hz, 660->440 Hz, dan pesan status Piper).
- [x] **FR-11 (Penanganan Galat & Watchdog)**: Tangkap exception, monitor thermal suhu CPU, deteksi under-voltage via `vcgencmd`.
- [x] **FR-12 (Boot & Autostart)**: Layanan `systemd` autostart dengan restart otomatis.
