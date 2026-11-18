"""
main.py - Aplikasi Utama: Kontrol Tombol, Mesin Status, & Watchdog
Bagian dari Rancang Bangun Alat Bantu Baca Tunanetra (Image to Voice)
Sesuai PRD Bagian 5 (FR-01, FR-10, FR-11, FR-12), Bagian 14, 15, dan 18.
"""

import sys
import shutil
import logging
import os
import platform
import subprocess
import threading
import time
import tomllib
from typing import List, Optional

import cv2

try:
    from gpiozero import Button, DigitalOutputDevice
    HAS_GPIO = True
except (ImportError, Exception):
    HAS_GPIO = False

from ocr_pipeline import (
    binarize,
    clean_text,
    deskew,
    quality_problem,
    run_ocr,
    to_gray,
    to_sentences
)
from speech import Speaker
from terminal_view import (
    print_camera_snapshot,
    scan_cameras,
    show_opencv_window,
    stream_terminal_camera
)
from tft_gui import launch_gui

# Muat konfigurasi toml
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.toml')
with open(CONFIG_PATH, 'rb') as f:
    cfg = tomllib.load(f)

# Pastikan folder logs dan assets tersedia
LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(ASSETS_DIR, exist_ok=True)

# Konfigurasi logging ganda (berkas log berputar + konsol terminal)
LOG_FILE = os.path.join(LOGS_DIR, 'reader.log')
log = logging.getLogger('reader')
log.setLevel(logging.INFO)

# File handler
fh = logging.FileHandler(LOG_FILE, encoding='utf-8')
fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
log.addHandler(fh)

# Console handler agar pengguna melihat proses langsung di layar
ch = logging.StreamHandler()
ch.setFormatter(logging.Formatter('[%(levelname)s] %(message)s'))
log.addHandler(ch)


def save_camera_device_to_config(new_device_id: int):
    """Menyimpan device kamera baru ke berkas config.toml secara aman."""
    import re
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
        new_content = re.sub(r'(?m)^(\s*device\s*=\s*)\d+', rf'\g<1>{new_device_id}', content)
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            f.write(new_content)
        log.info("Konfigurasi camera.device diperbarui ke %d di config.toml", new_device_id)
    except Exception as e:
        log.warning("Gagal memperbarui config.toml: %s", e)



class MockButton:
    """Mock Button untuk lingkungan pengujian non-Raspberry Pi."""
    def __init__(self, pin, bounce_time=0.05, hold_time=1.5):
        self.pin = pin
        self.hold_time = hold_time
        self.when_pressed = None
        self.when_held = None
        self.when_released = None


class MockOutputDevice:
    """Mock Output Device (LED) untuk lingkungan non-Raspberry Pi."""
    def __init__(self, pin):
        self.pin = pin
        self.state = False

    def on(self):
        self.state = True

    def off(self):
        self.state = False


class App:
    """
    Mesin status utama perangkat pembaca tunanetra.
    State: BOOT -> IDLE <-> CAPTURING <-> PROCESSING <-> SPEAKING -> OFF
    """

    def __init__(self, cap=None, init_cue=True):
        self.cfg = cfg
        print("-> [1/3] Memuat model Piper TTS ke memori...")
        model_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), cfg['tts']['model'])
        self.speaker = Speaker(model_path, cfg['audio']['device'])

        print("-> [2/3] Menginisialisasi kamera...")
        self.cap_lock = threading.Lock()
        self.cap = cap if cap is not None else self._open_camera()

        # Inisialisasi GPIO (LED & Tombol) dengan proteksi pin <= 0 dan penanganan fallback
        led_pin = int(cfg['gpio'].get('led', 0))
        btn_main_pin = int(cfg['gpio'].get('btn_main', 23))
        btn_aux_pin = int(cfg['gpio'].get('btn_aux', 0))

        if HAS_GPIO and led_pin > 0:
            try:
                self.led = DigitalOutputDevice(led_pin)
            except Exception as e:
                log.warning("GPIO LED (pin %s) sibuk atau tidak tersedia: %s. Menggunakan Mock.", led_pin, e)
                self.led = MockOutputDevice(led_pin)
        else:
            self.led = MockOutputDevice(led_pin)

        if HAS_GPIO and btn_main_pin > 0:
            try:
                self.btn_main = Button(btn_main_pin, bounce_time=0.05, hold_time=1.5)
            except Exception as e:
                log.warning("GPIO btn_main (pin %s) sibuk atau tidak tersedia: %s. Menggunakan Mock.", btn_main_pin, e)
                self.btn_main = MockButton(btn_main_pin, hold_time=1.5)
        else:
            self.btn_main = MockButton(btn_main_pin, hold_time=1.5)

        if HAS_GPIO and btn_aux_pin > 0:
            try:
                self.btn_aux = Button(btn_aux_pin, bounce_time=0.05, hold_time=3.0)
            except Exception as e:
                log.warning("GPIO btn_aux (pin %s) sibuk atau tidak tersedia: %s. Menggunakan Mock.", btn_aux_pin, e)
                self.btn_aux = MockButton(btn_aux_pin, hold_time=3.0)
        else:
            self.btn_aux = MockButton(btn_aux_pin, hold_time=3.0)

        # Status mesin
        self.last_reading: List[str] = []
        self.busy = False
        self._ignore_press = False
        self._main_held = False
        self._aux_held = False

        # Volume levels
        self.volume_steps = cfg['audio'].get('volume_steps', [40, 60, 80, 100])
        self.current_vol_idx = 2  # Default: 80%

        # Kaitkan event tombol utama (FR-01)
        self.btn_main.when_pressed = self.on_main_press
        self.btn_main.when_held = self.on_main_hold
        self.btn_main.when_released = self.on_main_release

        # Kaitkan event tombol bantu (FR-01, T16)
        self.btn_aux.when_pressed = self.on_aux_press
        self.btn_aux.when_held = self.on_aux_hold
        self.btn_aux.when_released = self.on_aux_release

        # Indikasi sistem siap (BOOT -> IDLE)
        if init_cue:
            print("-> [3/3] Memutar audio status 'Alat siap'...")
            log.info("Perangkat diinisialisasi. Memutar status: siap.")
            self.cue('siap')
        print("-> Sistem siap!")

    def _open_camera(self, device_id: Optional[int] = None) -> Optional[cv2.VideoCapture]:
        """Membuka kamera USB sesuai konfigurasi Bagian 13 dengan auto-discovery & validasi stream."""
        c = cfg['camera']
        preferred_id = device_id if device_id is not None else c.get('device', 0)

        # Cari pada preferred_id terlebih dahulu, lalu coba device lain jika gagal
        candidates = [preferred_id]
        for alt_id in (0, 1, 2, 3):
            if alt_id not in candidates:
                candidates.append(alt_id)

        for dev_id in candidates:
            cap = None
            if platform.system() == "Linux":
                cap = cv2.VideoCapture(dev_id, cv2.CAP_V4L2)
                if not cap.isOpened():
                    cap = cv2.VideoCapture(dev_id)
            elif platform.system() == "Windows":
                cap = cv2.VideoCapture(dev_id, cv2.CAP_DSHOW)
                if not cap.isOpened():
                    cap = cv2.VideoCapture(dev_id)
            else:
                cap = cv2.VideoCapture(dev_id)

            if cap is not None and cap.isOpened():
                try:
                    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, c.get('width', 1920))
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, c.get('height', 1080))
                    cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)
                    if hasattr(cv2, 'CAP_PROP_BUFFERSIZE'):
                        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass

                # Pastikan stream benar-benar bisa menghasilkan gambar (bukan dummy/codec device)
                ok, test_f = cap.read()
                if ok and test_f is not None and test_f.size > 0:
                    if dev_id != preferred_id:
                        log.info("Kamera dialihkan otomatis dari device %s ke device %s", preferred_id, dev_id)
                        c['device'] = dev_id
                    log.info("Kamera aktif pada device %s (Target Resolusi: %dx%d)", dev_id, c.get('width', 1920), c.get('height', 1080))
                    return cap
                else:
                    cap.release()

        log.warning("Kamera fisik tidak dapat dibuka pada device %s maupun alternatifnya.", preferred_id)
        return None

    def select_camera(self):
        """Menu interaktif untuk memindai dan memilih kamera hardware yang terhubung."""
        print("\n" + "=" * 65)
        print("          PILIH KAMERA YANG TERHUBUNG (HARDWARE WEBCAM)")
        print("=" * 65)
        print(" Memindai port kamera hardware yang terhubung...")
        devices = scan_cameras(max_devices=6)

        current_dev = cfg['camera'].get('device', 0)
        print(f"\n Indeks kamera saat ini : {current_dev}")
        if self.cap is not None and self.cap.isOpened():
            w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            print(f" Status kamera aktif    : TERHUBUNG & SIAP ({w}x{h})")
        else:
            print(" Status kamera aktif    : BELUM TERHUBUNG / TIDAK AKTIF")

        print("\n Daftar Kamera Terdeteksi:")
        if devices:
            for d in devices:
                active_mark = " <-- [SEDANG DIGUNAKAN]" if d['index'] == current_dev else ""
                print(f"  [{d['index']}] {d['name']} - Resolusi: {d['width']}x{d['height']}, {d['fps']:.0f} FPS{active_mark}")
        else:
            print("  [!] Tidak ada kamera fisik yang otomatis merespons pada indeks 0-5.")
            print("      Tips:")
            print("      - Pastikan kabel USB webcam tercolok kuat ke port komputer.")
            print("      - Periksa izin kamera pada Windows Settings (Privacy & Security -> Camera).")
            print("      - Anda tetap dapat memilih nomor indeks kamera secara manual di bawah ini.")

        print("-" * 65)
        print(" Pilihan:")
        print("  [0-9] : Masukkan nomor indeks kamera untuk beralih dan mengaktifkannya")
        print("  [S]   : Uji snapshot dari kamera yang sedang aktif")
        print("  [B]   : Kembali ke menu utama")
        print("=" * 65)

        try:
            pilihan = input("Masukkan pilihan kamera [0-9 / S / B]: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nBatal.")
            return

        if pilihan in ('b', ''):
            print("Kembali ke menu utama.")
            return

        if pilihan == 's':
            if self.cap is not None and self.cap.isOpened():
                frame = self.grab_frame()
                if frame is not None:
                    print_camera_snapshot(frame, label=f"SNAPSHOT KAMERA INDEKS {current_dev}")
                else:
                    print("[!] Gagal membaca frame dari kamera aktif.")
            else:
                print("[!] Kamera aktif tidak tersedia untuk snapshot.")
            return

        if pilihan.isdigit():
            new_id = int(pilihan)
            print(f"\n[Kamera] Menguji dan membuka kamera indeks {new_id}...")
            with self.cap_lock:
                if self.cap is not None:
                    try:
                        self.cap.release()
                    except Exception:
                        pass

                new_cap = self._open_camera(device_id=new_id)
                if new_cap is not None and new_cap.isOpened():
                    self.cap = new_cap
                    cfg['camera']['device'] = new_id
                    save_camera_device_to_config(new_id)
                    print(f"[v] SUKSES: Kamera indeks {new_id} berhasil diaktifkan dan disimpan ke config.toml!")

                    ok, test_frame = self.cap.read()
                    if ok and test_frame is not None:
                        print_camera_snapshot(test_frame, label=f"KAMERA {new_id} AKTIF")
                else:
                    print(f"[X] GAGAL: Kamera indeks {new_id} tidak dapat dibuka.")
                    print(f"Mencoba memulihkan kembali kamera indeks {current_dev}...")
                    self.cap = self._open_camera(device_id=current_dev)
        else:
            print("Pilihan tidak valid.")

    def cue(self, name: str, wait: bool = False):
        """Memutar file suara status dari folder assets/."""
        path = os.path.join(ASSETS_DIR, f"{name}.wav")
        self.speaker.cue(path, wait=wait)

    # ================= Tombol Utama (FR-01) =================
    def on_main_press(self):
        """
        Tekan singkat tombol utama:
        - Jika sedang berbicara: hentikan audio seketika (FR-09).
        """
        self._main_held = False
        self._ignore_press = self.speaker.playing
        if self._ignore_press:
            log.info("Tombol utama ditekan saat bicara -> stop pemutaran.")
            self.speaker.stop()

    def on_main_hold(self):
        """Tekan tahan >= 1.5 detik: ulangi bacaan terakhir."""
        if not self._ignore_press:
            self._main_held = True
            log.info("Tombol utama ditahan -> ulangi bacaan terakhir.")
            self._run_async(self.replay)

    def on_main_release(self):
        """Lepas tombol utama: jika bukan stop & bukan hold -> ambil dan baca."""
        if not (self._ignore_press or self._main_held):
            log.info("Tombol utama ditekan singkat -> capture and read.")
            self._run_async(self.capture_and_read)

    # ================= Tombol Bantu (FR-01 & T16) =================
    def on_aux_press(self):
        """Tekan tombol bantu."""
        self._aux_held = False

    def on_aux_hold(self):
        """Tahan tombol bantu >= 3 detik: shutdown aman."""
        self._aux_held = True
        log.info("Tombol bantu ditahan >= 3 detik -> shutdown aman.")
        self._run_async(self.safe_shutdown)

    def on_aux_release(self):
        """Tekan singkat tombol bantu: naikkan volume siklis."""
        if not self._aux_held:
            log.info("Tombol bantu ditekan singkat -> ubah volume.")
            self._run_async(self.cycle_volume)

    def cycle_volume(self):
        """Menaikkan volume (40%, 60%, 80%, 100%, lalu kembali ke 40%)."""
        self.current_vol_idx = (self.current_vol_idx + 1) % len(self.volume_steps)
        new_vol = self.volume_steps[self.current_vol_idx]
        log.info("Mengatur volume ke %d%%", new_vol)

        # Atur ALSA mixer jika di Linux
        if platform.system() == "Linux":
            mixer = cfg['audio'].get('mixer', 'Headphone')
            dev_str = cfg['audio'].get('device', 'plughw:2,0')
            card_num = '2'
            if ':' in dev_str and ',' in dev_str:
                try:
                    card_num = dev_str.split(':')[1].split(',')[0]
                except Exception:
                    card_num = '2'
            try:
                subprocess.run(
                    ['amixer', '-c', card_num, 'sset', mixer, f"{new_vol}%"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception as e:
                log.warning("Gagal mengatur amixer: %s", e)

        # Beri feedback nada klik pada volume baru
        self.cue('klik', wait=True)

    def safe_shutdown(self):
        """Mematikan sistem Raspberry Pi secara aman."""
        self.cue('mati', wait=True)
        time.sleep(1.0)
        if platform.system() == "Linux":
            try:
                subprocess.run(['sudo', '/usr/sbin/shutdown', '-h', 'now'], check=False)
            except Exception as e:
                log.error("Gagal mengeksekusi shutdown: %s", e)
        else:
            log.info("[Mock Shutdown] Perangkat dimatikan.")

    # ================= Alur Pemrosesan Inti =================
    def _run_async(self, fn):
        """Menjalankan fungsi di background thread dengan perlindungan anti-double-trigger."""
        if self.busy:
            log.info("Permintaan diabaikan: sistem sedang sibuk.")
            return
        self.busy = True
        t = threading.Thread(target=self._guard_execution, args=(fn,), daemon=True)
        t.start()

    def _guard_execution(self, fn):
        try:
            fn()
        except Exception:
            log.exception("Galat tak terduga dalam alur eksekusi:")
            self.cue('galat', wait=True)
        finally:
            self.busy = False

    def grab_frame(self) -> Optional[cv2.Mat]:
        """
        Nyalakan LED, buang frame settle_frames untuk autofocus,
        lalu tangkap frame gambar berkualitas penuh. Dilengkapi thread-lock & auto-reconnect.
        """
        self.led.on()
        try:
            with self.cap_lock:
                for retry in range(2):
                    if self.cap is None or not self.cap.isOpened():
                        self.cap = self._open_camera()

                    if self.cap is None or not self.cap.isOpened():
                        return None

                    settle = int(cfg['camera'].get('settle_frames', 8))
                    for _ in range(settle):
                        self.cap.read()

                    ok, frame = self.cap.read()
                    if ok and frame is not None and frame.size > 0:
                        return frame

                    # Jika pembacaan gagal, reset kamera dan coba sekali lagi
                    log.warning("Gagal membaca frame kamera (percobaan %d/2). Menghubungkan ulang kamera...", retry + 1)
                    try:
                        self.cap.release()
                    except Exception:
                        pass
                    self.cap = None
                    time.sleep(0.3)

                return None
        finally:
            self.led.off()

    def capture_and_read(self, test_frame: Optional[cv2.Mat] = None):
        """
        FR-02 hingga FR-10: Alur lengkap tombol ditekan -> potret -> quality check ->
        preprocessing -> OCR -> sintesis suara.
        """
        t0 = time.monotonic()
        self.cue('klik', wait=True)

        max_retries = int(cfg['quality'].get('max_retries', 2)) if test_frame is None else 0
        gray = None
        frame = None

        # Loop quality check dengan percobaan ulang otomatis jika menggunakan kamera live
        for attempt in range(max_retries + 1):
            frame = test_frame if test_frame is not None else self.grab_frame()
            if frame is None:
                log.error("Kamera tidak terbaca atau frame kosong.")
                self.cue('kamera_error', wait=True)
                return

            label_name = "FOTO UJI AKTUAL" if test_frame is not None else f"FOTO KAMERA (Percobaan {attempt + 1})"
            print_camera_snapshot(frame, label=label_name)

            gray = to_gray(frame)
            problem = quality_problem(gray, cfg['quality'])
            if problem is None:
                break

            # Jika frame dari capture manual (test_frame), berikan peringatan tapi tetap lanjutkan OCR
            if test_frame is not None:
                log.warning("Peringatan kualitas citra (%s), tetap mencoba OCR...", problem)
                break

            log.warning("Quality check gagal (percobaan %d/%d): %s", attempt + 1, max_retries + 1, problem)
            self.cue(f'gambar_{problem}', wait=True)
            if attempt < max_retries:
                time.sleep(0.5)
        else:
            log.warning("Semua percobaan quality check gagal. Proses dihentikan.")
            return

        # Preprocessing & OCR
        self.cue('memproses')
        preprocessed = deskew(gray)
        ocr_mode = cfg['ocr'].get('mode', 'camera')
        preprocessed = binarize(preprocessed, ocr_mode)

        lines = run_ocr(preprocessed, cfg['ocr'])
        text = clean_text(lines)
        t_ocr = time.monotonic() - t0
        log.info("OCR selesai dalam %.2f detik, karakter terdeteksi: %d", t_ocr, len(text))

        if len(text) < 3:
            log.info("Teks tidak terdeteksi (panjang < 3 karakter).")
            print("\n" + "!" * 65)
            print(" [PERHATIAN] Tidak ada teks yang berhasil dibaca dari kamera.")
            print(" Tips agar teks terbaca jelas:")
            print(" - Pastikan dokumen berada di dalam kotak pemandu (jarak ~25 cm).")
            print(" - Periksa pencahayaan (hindari bayangan gelap di atas tulisan).")
            print(" - Pastikan lensa kamera bersih dan fokus tajam.")
            print("!" * 65 + "\n")
            self.cue('tidak_ada_teks', wait=True)
            return

        self.last_reading = to_sentences(text)

        # Cetak hasil pembacaan teks ke layar terminal
        print("\n" + "=" * 65)
        print("          HASIL PEMBACAAN TEKS DARI KAMERA (OCR)")
        print("=" * 65)
        print(text)
        print("=" * 65)
        print(f" Jumlah Karakter : {len(text)} | Waktu OCR: {t_ocr:.2f} detik")
        print(f" Jumlah Kalimat  : {len(self.last_reading)} kalimat")
        print(" Status Suara    : Membacakan teks melalui Piper TTS...")
        print("=" * 65 + "\n")

        self.read_sentences(self.last_reading, t0=t0)

    def replay(self):
        """Memutar ulang teks bacaan terakhir."""
        if self.last_reading:
            log.info("Memutar ulang %d kalimat bacaan terakhir.", len(self.last_reading))
            self.read_sentences(self.last_reading)
        else:
            log.info("Belum ada bacaan untuk diulang.")
            self.cue('belum_ada_bacaan', wait=True)

    def read_sentences(self, sentences: List[str], t0: Optional[float] = None):
        """
        Sintesis dan baca daftar kalimat secara pipeline.
        Catat waktu time-to-first-audio pada log.
        """
        def on_first():
            if t0 is not None:
                first_latency = time.monotonic() - t0
                log.info("TIME_TO_FIRST_AUDIO: %.2f detik", first_latency)

        success = self.speaker.say(sentences, on_first=on_first)
        if success:
            log.info("Pembacaan selesai seluruhnya.")
            self.cue('selesai', wait=True)
        else:
            log.info("Pembacaan dihentikan sebelum selesai.")


def watchdog_loop(app: App):
    """
    FR-11: Thread pemantau suhu CPU dan status daya (under-voltage) secara berkala.
    """
    warned = set()
    while True:
        try:
            now_warnings = set()

            # 1. Cek suhu CPU
            temp = None
            if os.path.exists('/sys/class/thermal/thermal_zone0/temp'):
                try:
                    with open('/sys/class/thermal/thermal_zone0/temp', 'r') as tf:
                        temp = int(tf.read().strip()) / 1000.0
                except Exception:
                    pass

            if temp is not None:
                max_temp = float(cfg['safety'].get('max_temp_c', 75.0))
                if temp > max_temp:
                    now_warnings.add('suhu_tinggi')

            # 2. Cek under-voltage via vcgencmd di Raspberry Pi
            if platform.system() == "Linux" and shutil.which('vcgencmd'):
                try:
                    out = subprocess.check_output(['vcgencmd', 'get_throttled'], timeout=2).decode()
                    if '=' in out:
                        flags = int(out.split('=')[1].strip(), 16)
                        if flags & 0x1:  # Bit 0: Under-voltage terdeteksi saat ini
                            now_warnings.add('daya_tidak_stabil')
                except Exception:
                    pass

            # Ucapkan peringatan baru hanya saat tidak sedang sibuk membaca/memproses
            new_warnings = now_warnings - warned
            if new_warnings and not app.busy and not app.speaker.playing:
                for warn_cue in new_warnings:
                    log.warning("Peringatan keselamatan: %s", warn_cue)
                    app.cue(warn_cue, wait=True)
            elif not new_warnings:
                new_warnings = set()

            warned = (warned | new_warnings) & now_warnings

        except Exception as e:
            log.error("Galat pada watchdog: %s", e)

        time.sleep(30)


if __name__ == '__main__':
    log.info("=== Menjalankan Pembaca Tunanetra (Reader Service) ===")
    app = App()

    # Jalankan background watchdog thread
    w_thread = threading.Thread(target=watchdog_loop, args=(app,), daemon=True)
    w_thread.start()

    # Cek apakah mode GUI aktif dari argumen CLI (--gui) atau dari config.toml
    use_gui = ('--gui' in sys.argv) or bool(cfg.get('gui', {}).get('enabled', False))
    gui_w = int(cfg.get('gui', {}).get('width', 320))
    gui_h = int(cfg.get('gui', {}).get('height', 240))
    gui_fs = bool(cfg.get('gui', {}).get('fullscreen', False))
    gui_fb = str(cfg.get('gui', {}).get('fb_device', '/dev/fb0'))

    if use_gui:
        print(f"\n[Reader Service] Membuka Antarmuka GUI TFT 2.4\" ({gui_w}x{gui_h}) di {gui_fb}...")
        launch_gui(app, width=gui_w, height=gui_h, fullscreen=gui_fs, fb_device=gui_fb)
    elif not sys.stdin.isatty() and HAS_GPIO:
        # Berjalan sebagai background service tanpa terminal interaktif (misal systemd)
        print("[Reader Service] Mode Background Systemd Aktif. Menunggu tombol GPIO...")
        try:
            from signal import pause
            pause()
        except KeyboardInterrupt:
            log.info("Layanan dimatikan.")
            print("\nLayanan berhenti.")
    else:
        # Berjalan dari terminal interaktif (SSH / Konsol / Terminal Raspi / PC)
        print("\n" + "=" * 65)
        print(" [Reader Service] Mode Interaktif Terminal Aktif")
        print(" =" * 65)
        print(" Kontrol Terminal:")
        print("  [Enter]  : Ambil gambar kamera & baca teks (Auto Tampil di Terminal)")
        print("  [G]      : Buka Layar GUI Touchscreen TFT 2.4\" (Visual Modern)")
        print("  [K]      : Pilih / Ganti Kamera Hardware yang Terhubung")
        print("  [P]      : Pratinjau Live Video Kamera di Teks Terminal")
        print("  [W]      : Pratinjau Jendela Pop-up Kamera (OpenCV Window + Ambil Foto Uji)")
        print("  [T]      : Uji baca gambar contoh dari dataset/ (Mode Tanpa Kamera)")
        print("  [R]      : Replay bacaan terakhir")
        print("  [S]      : Stop pemutaran audio seketika")
        print("  [V]      : Ubah volume siklis")
        print("  [Q]      : Keluar dari program")
        if HAS_GPIO:
            print(" *(Info: Tombol fisik GPIO & Layar TFT juga tetap aktif di background)*")
        print(" =" * 65 + "\n")

        import glob
        try:
            while True:
                cmd = input("Perintah [Enter=Foto & Baca, G=GUI, K=Kamera, P=Terminal, W=Jendela, T=Test, Q=Keluar]: ").strip().lower()
                if cmd == 'q':
                    print("Keluar...")
                    break
                elif cmd == 'g':
                    # Buka antarmuka GUI TFT touchscreen
                    launch_gui(app, width=gui_w, height=gui_h, fullscreen=gui_fs, fb_device=gui_fb)
                elif cmd in ('k', 'c'):
                    # Menu pilih kamera yang terhubung
                    app.select_camera()
                elif cmd == 'p':
                    # Pratinjau live video langsung di terminal
                    stream_terminal_camera(app.cap)
                elif cmd == 'w':
                    # Pratinjau jendela pop-up grafis dengan fitur ambil foto uji
                    captured = show_opencv_window(app.cap, cue_callback=app.cue)
                    if captured is not None:
                        print("\n[Menu] Menjalankan OCR & Pembacaan Suara dari foto uji yang baru diambil...")
                        app._run_async(lambda: app.capture_and_read(test_frame=captured))
                elif cmd == 's':
                    print("Menghentikan audio...")
                    app.speaker.stop()
                elif cmd == 'r':
                    print("Memutar ulang bacaan terakhir...")
                    app._run_async(app.replay)
                elif cmd == 'v':
                    app._run_async(app.cycle_volume)
                elif cmd == 't':
                    # Uji sampel gambar dari dataset
                    samples = sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dataset', '*.png')))
                    if samples:
                        sample_img = cv2.imread(samples[0])
                        print(f"\nMembaca sampel: {os.path.basename(samples[0])}...")
                        app._run_async(lambda: app.capture_and_read(test_frame=sample_img))
                    else:
                        print("Tidak ada sampel di folder dataset/.")
                else:
                    print("Mengambil foto dari kamera dan memproses...")
                    app._run_async(app.capture_and_read)
        except (KeyboardInterrupt, EOFError):
            print("\nLayanan berhenti.")
