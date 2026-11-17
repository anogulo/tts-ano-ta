"""
gen_prompts.py - Pembuat Berkas Suara Status (Audio Cues)
Bagian dari Rancang Bangun Alat Bantu Baca Tunanetra (Image to Voice)
Sesuai PRD Bagian 15 dan Bagian 17 (Langkah 5).

Membuat semua berkas WAV di folder assets/:
- Nada murni: klik.wav (880 Hz) dan selesai.wav (660 -> 440 Hz)
- Ucapan status disintesis sekali dengan Piper TTS ke format WAV PCM 16-bit.
"""

import math
import os
import struct
import sys
import wave

# Tambahkan direktori root proyek ke sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

ASSETS_DIR = os.path.join(BASE_DIR, 'assets')
MODEL_PATH = os.path.join(BASE_DIR, 'models', 'id_ID-news_tts-medium.onnx')
os.makedirs(ASSETS_DIR, exist_ok=True)

# Daftar teks suara sesuai PRD Bagian 15
PROMPTS = {
    'siap': "Alat siap.",
    'memproses': "Sedang memproses.",
    'gambar_gelap': "Gambar terlalu gelap. Mencoba lagi.",
    'gambar_buram': "Gambar buram. Pastikan dokumen rata dan tidak bergerak.",
    'kamera_error': "Kamera tidak terbaca. Periksa kabel kamera.",
    'tidak_ada_teks': "Teks tidak terdeteksi. Letakkan dokumen di bawah kamera dan coba lagi.",
    'belum_ada_bacaan': "Belum ada bacaan untuk diulang.",
    'suhu_tinggi': "Perangkat panas. Mohon beri jeda.",
    'daya_tidak_stabil': "Daya tidak stabil. Periksa adaptor atau baterai.",
    'mati': "Alat dimatikan.",
    'galat': "Terjadi kesalahan. Coba lagi.",
}


def generate_tone_wav(filepath: str, freqs_and_durations: list, sample_rate: int = 22050):
    """
    Menghasilkan berkas WAV nada sinus sintetis 16-bit PCM mono.
    freqs_and_durations: list of tuple (frequency_hz, duration_seconds)
    """
    all_frames = bytearray()
    max_amp = 28000  # Amplitudo aman untuk 16-bit PCM

    for freq, duration in freqs_and_durations:
        num_samples = int(sample_rate * duration)
        attack_decay = min(int(sample_rate * 0.01), num_samples // 4)  # 10ms ramp agar tidak klik tajam

        for i in range(num_samples):
            # Hitung envelope untuk smoothing
            envelope = 1.0
            if i < attack_decay:
                envelope = i / attack_decay
            elif i > num_samples - attack_decay:
                envelope = (num_samples - i) / attack_decay

            t = float(i) / sample_rate
            val = int(max_amp * envelope * math.sin(2.0 * math.pi * freq * t))
            all_frames.extend(struct.pack('<h', val))

    with wave.open(filepath, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(all_frames)


def generate_speech_wav(voice, text: str, filepath: str):
    """Sintesis ucapan dari teks menggunakan PiperVoice dan simpan ke file WAV."""
    with wave.open(filepath, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(voice.config.sample_rate)

        if hasattr(voice, 'synthesize_stream_raw'):
            for chunk in voice.synthesize_stream_raw(text):
                wf.writeframes(chunk)
        elif hasattr(voice, 'synthesize'):
            for chunk in voice.synthesize(text):
                if hasattr(chunk, 'audio_int16_bytes'):
                    wf.writeframes(chunk.audio_int16_bytes)
                elif isinstance(chunk, bytes):
                    wf.writeframes(chunk)
                elif hasattr(chunk, 'tobytes'):
                    wf.writeframes(chunk.tobytes())


def main():
    print(f"=== Menghasilkan Berkas Suara Status di {ASSETS_DIR} ===")

    # 1. Buat nada klik.wav (880 Hz, 80 ms)
    klik_path = os.path.join(ASSETS_DIR, 'klik.wav')
    print("Membuat nada klik.wav (880 Hz, 80 ms)...")
    generate_tone_wav(klik_path, [(880, 0.080)])

    # 2. Buat nada selesai.wav (turun 660 Hz lalu 440 Hz, masing-masing 100 ms)
    selesai_path = os.path.join(ASSETS_DIR, 'selesai.wav')
    print("Membuat nada selesai.wav (660 Hz -> 440 Hz)...")
    generate_tone_wav(selesai_path, [(660, 0.100), (440, 0.100)])

    # 3. Muat PiperVoice untuk sintesis berkas ucapan
    print(f"Memuat model Piper dari {MODEL_PATH}...")
    voice = None
    try:
        from piper import PiperVoice
        if os.path.exists(MODEL_PATH):
            voice = PiperVoice.load(MODEL_PATH)
        else:
            print(f"Peringatan: Berkas model tidak ditemukan di {MODEL_PATH}")
    except Exception as e:
        print(f"Gagal memuat PiperVoice: {e}")

    # 4. Sintesis setiap teks ucapan status
    for name, text in PROMPTS.items():
        out_path = os.path.join(ASSETS_DIR, f"{name}.wav")
        print(f"Membuat {name}.wav: \"{text}\"")
        if voice:
            try:
                generate_speech_wav(voice, text, out_path)
            except Exception as ex:
                print(f"  Gagal menyintesis {name}: {ex}. Menggunakan fallback nada.")
                generate_tone_wav(out_path, [(500, 0.3)])
        else:
            # Fallback nada jika Piper belum terinstall atau model tidak ada
            generate_tone_wav(out_path, [(500, 0.3)])

    print("Semua berkas audio cues berhasil dibuat!")


if __name__ == '__main__':
    main()
