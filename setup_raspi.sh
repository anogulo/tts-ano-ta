#!/bin/bash
# ==============================================================================
# setup_raspi.sh - Skrip Otomasi Instalasi & Optimasi Alat Bantu Baca Tunanetra
# Target: Raspberry Pi 4 Model B (Raspberry Pi OS 64-bit / Debian Bookworm)
# ==============================================================================

set -e

echo "======================================================================"
echo "    MEMULAI INSTALASI ALAT BANTU BACA TUNANETRA DI RASPBERRY PI 4"
echo "======================================================================"

# 1. Update paket sistem & install dependensi inti
echo "-> [1/7] Memperbarui repositori dan memasang dependensi sistem..."
sudo apt update
sudo apt install -y \
    tesseract-ocr \
    tesseract-ocr-ind \
    tesseract-ocr-eng \
    python3-opencv \
    python3-gpiozero \
    python3-numpy \
    python3-pygame \
    python3-tk \
    python3-pil \
    python3-pip \
    python3-venv \
    alsa-utils \
    v4l-utils \
    python3-evdev \
    evtest \
    tslib \
    libts-bin \
    git

# 2. Tambahkan user ke grup hardware (audio, video, gpio)
echo "-> [2/7] Mengonfigurasi hak akses grup pengguna ($USER)..."
sudo usermod -a -G audio,video,gpio $USER

# 3. Buat Virtual Environment dengan akses pustaka sistem (NEON-accelerated)
echo "-> [3/7] Membuat virtual environment Python (.venv)..."
if [ ! -d ".venv" ]; then
    python3 -m venv --system-site-packages .venv
fi

# Pastikan system-site-packages aktif di pyvenv.cfg
sed -i 's/include-system-site-packages = false/include-system-site-packages = true/g' .venv/pyvenv.cfg

# 4. Pasang pustaka Python yang dibutuhkan
echo "-> [4/7] Memasang dependensi pip (piper-tts, pytesseract, onnxruntime)..."
source .venv/bin/activate
pip install --upgrade pip
pip install pytesseract>=0.3.10 piper-tts>=1.2.0 onnxruntime>=1.15.0 evdev>=1.6.0

# 5. Konfigurasi Izin Shutdown Aman tanpa Password (untuk tombol bantu GPIO27 & tombol OFF GUI)
echo "-> [5/7] Mengonfigurasi izin shutdown aman tanpa password..."
sudo bash -c "echo '$USER ALL=(root) NOPASSWD: /usr/sbin/shutdown, /usr/bin/systemctl poweroff*, /sbin/poweroff, /usr/sbin/poweroff' > /etc/sudoers.d/reader_shutdown"
sudo chmod 0440 /etc/sudoers.d/reader_shutdown

# 6. Generate dan Verifikasi Aset Audio Status (*.wav)
echo "-> [6/7] Memeriksa dan membuat aset suara status..."
python tools/gen_prompts.py

# 7. Konfigurasi Autostart Layanan systemd
echo "-> [7/7] Mengonfigurasi layanan latar belakang systemd (reader.service)..."
SERVICE_PATH="/etc/systemd/system/reader.service"
CURRENT_DIR=$(pwd)

# Perbarui path direktori dan user pada berkas service sesuai lokasi saat ini
sudo cp reader.service $SERVICE_PATH
sudo sed -i "s|WorkingDirectory=.*|WorkingDirectory=$CURRENT_DIR|g" $SERVICE_PATH
sudo sed -i "s|ExecStart=.*|ExecStart=$CURRENT_DIR/.venv/bin/python main.py --gui|g" $SERVICE_PATH
sudo sed -i "s|User=.*|User=$USER|g" $SERVICE_PATH

sudo systemctl daemon-reload
sudo systemctl enable reader.service

echo "======================================================================"
echo " [SELESAI] Instalasi dan konfigurasi Raspberry Pi berhasil!"
echo "======================================================================"
echo " Petunjuk Pengoperasian:"
echo " 1. Jalankan manual untuk pengujian pertama:"
echo "      source .venv/bin/activate && python main.py"
echo " 2. Atau mulai layanan otomatis systemd sekarang:"
echo "      sudo systemctl start reader.service"
echo " 3. Pantau log sistem secara live:"
echo "      journalctl -u reader.service -f"
echo "======================================================================"
