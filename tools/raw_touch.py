#!/usr/bin/env python3
"""
tools/raw_touch.py - Pembaca Nilai RAW ADC Touchscreen XPT2046
=============================================================
Skrip DIAGNOSA untuk melihat nilai mentah (raw) ABS_X, ABS_Y, ABS_PRESSURE
langsung dari kernel evdev tanpa transformasi apapun.

Cara pakai:
  sudo python3 tools/raw_touch.py

Tekan layar di berbagai posisi (pojok, tengah, tiap tombol),
catat nilai ABS_X dan ABS_Y yang muncul untuk kalibrasi.
"""

import os
import sys
import time

# Auto-exec virtual environment (.venv) jika ada
if sys.platform.startswith("linux") and sys.prefix == sys.base_prefix:
    _base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _venv_py = os.path.join(_base_dir, '.venv', 'bin', 'python')
    if os.path.exists(_venv_py) and os.path.realpath(sys.executable) != os.path.realpath(_venv_py):
        os.execv(_venv_py, [_venv_py] + sys.argv)

try:
    import evdev
    from evdev import ecodes
except ImportError:
    print("[ERROR] Modul 'evdev' tidak terpasang.")
    print("  Pasang dengan: sudo apt install python3-evdev")
    print("  Dan: .venv/bin/pip install evdev")
    sys.exit(1)


def find_touch_device():
    """Cari device sentuh XPT2046/ADS7846 di /dev/input/"""
    # Coba symlink dulu
    if os.path.exists("/dev/input/touchscreen"):
        return "/dev/input/touchscreen"

    candidates = []
    for path in sorted(evdev.list_devices()):
        try:
            dev = evdev.InputDevice(path)
            caps = dev.capabilities()
            if ecodes.EV_ABS in caps:
                abs_codes = [a for a, _ in caps[ecodes.EV_ABS]]
                if ecodes.ABS_X in abs_codes and ecodes.ABS_Y in abs_codes:
                    name_lower = dev.name.lower()
                    if any(k in name_lower for k in ("touch", "ads7846", "xpt2046")):
                        return path
                    # Simpan sebagai kandidat fallback
                    candidates.append((path, dev.name))
        except Exception:
            continue

    # Fallback: ambil device ABS pertama yang ditemukan
    if candidates:
        print(f"[INFO] Tidak ada device dengan nama 'touch/ads7846', pakai: {candidates[0]}")
        return candidates[0][0]

    return None


def print_absinfo(dev):
    """Tampilkan info range ABS_X/ABS_Y dari driver."""
    try:
        caps = dev.capabilities()
        abs_dict = dict(caps.get(ecodes.EV_ABS, []))
        for axis_name, axis_code in [("ABS_X", ecodes.ABS_X), ("ABS_Y", ecodes.ABS_Y),
                                      ("ABS_PRESSURE", getattr(ecodes, "ABS_PRESSURE", 24))]:
            info = abs_dict.get(axis_code)
            if info:
                print(f"  {axis_name:12s}: min={info.min}, max={info.max}, fuzz={info.fuzz}, flat={info.flat}")
    except Exception as e:
        print(f"  [Gagal baca absinfo: {e}]")


def main():
    print("=" * 60)
    print("  DIAGNOSA RAW TOUCH - Nilai ADC Mentah XPT2046")
    print("=" * 60)

    # Tampilkan semua device input
    print("\nDevice input yang ada di sistem:")
    all_devs = []
    for path in sorted(evdev.list_devices()):
        try:
            d = evdev.InputDevice(path)
            all_devs.append((path, d.name))
            print(f"  {path}  ->  {d.name}")
        except Exception as e:
            print(f"  {path}  ->  [Izin ditolak: {e}]")

    if not all_devs:
        print("  [Tidak ada device ditemukan. Jalankan dengan sudo?]")
        sys.exit(1)

    dev_path = find_touch_device()
    if not dev_path:
        print("\n[ERROR] Tidak ditemukan device touchscreen!")
        print("Cek: apakah dtoverlay=ads7846 sudah aktif di /boot/firmware/config.txt?")
        sys.exit(1)

    try:
        dev = evdev.InputDevice(dev_path)
    except PermissionError:
        print(f"\n[ERROR] Tidak ada izin untuk membuka {dev_path}")
        print("Jalankan ulang dengan: sudo python3 tools/raw_touch.py")
        sys.exit(1)

    print(f"\n[OK] Terhubung ke: {dev.name} ({dev_path})")
    print(f"\nRange ABS dari driver:")
    print_absinfo(dev)

    print("\n" + "=" * 60)
    print("  INSTRUKSI KALIBRASI:")
    print("=" * 60)
    print("  1. Sentuh POJOK KIRI ATAS layar  -> catat ABS_X dan ABS_Y")
    print("  2. Sentuh POJOK KANAN ATAS layar -> catat ABS_X dan ABS_Y")
    print("  3. Sentuh POJOK KIRI BAWAH layar -> catat ABS_X dan ABS_Y")
    print("  4. Sentuh POJOK KANAN BAWAH layar-> catat ABS_X dan ABS_Y")
    print("  5. Sentuh setiap tombol (BACA, ULANG, STOP, VOL-, VOL+, OFF)")
    print("     Perhatikan nilai mana yang berubah untuk sumbu X (kiri-kanan)")
    print("     dan sumbu mana yang berubah untuk Y (atas-bawah)")
    print()
    print("  Tekan Ctrl+C untuk berhenti.")
    print("=" * 60 + "\n")

    # State untuk tracking
    raw_x = None
    raw_y = None
    raw_p = None
    last_print_time = 0.0
    touch_count = 0

    print(f"{'No':>4}  {'ABS_X':>8}  {'ABS_Y':>8}  {'PRESSURE':>10}  {'Keterangan'}")
    print("-" * 60)

    try:
        for event in dev.read_loop():
            if event.type == ecodes.EV_ABS:
                if event.code == ecodes.ABS_X:
                    raw_x = event.value
                elif event.code == ecodes.ABS_Y:
                    raw_y = event.value
                elif event.code == getattr(ecodes, 'ABS_PRESSURE', 24):
                    raw_p = event.value

            elif event.type == ecodes.EV_KEY:
                # BTN_TOUCH = 330
                if event.code == getattr(ecodes, 'BTN_TOUCH', 330):
                    if event.value == 0 and raw_x is not None and raw_y is not None:
                        # Lepas sentuh -> print koordinat
                        touch_count += 1
                        note = _guess_button(raw_x, raw_y)
                        print(f"{touch_count:>4}  {raw_x:>8}  {raw_y:>8}  {str(raw_p):>10}  {note}")

            elif event.type == ecodes.EV_SYN:
                # Setiap sync event: print kalau ada data baru
                now = time.time()
                if raw_x is not None and raw_y is not None and (now - last_print_time) > 0.3:
                    last_print_time = now
                    # Hitung koordinat piksel dengan config saat ini (swap_xy=T, invert_y=T)
                    _print_calculated(raw_x, raw_y, raw_p)

    except KeyboardInterrupt:
        print("\n\n[SELESAI] Tekan Ctrl+C diterima.")
        _print_summary()
    except Exception as e:
        print(f"\n[ERROR] {e}")


_readings = []  # Simpan semua bacaan untuk ringkasan

def _print_calculated(rx, ry, rp):
    """Hitung dan tampilkan koordinat piksel dengan berbagai kombinasi kalibrasi."""
    global _readings
    _readings.append((rx, ry))

    # Asumsi range ADC: 0-4095
    mn_x, mx_x = 200, 3900
    mn_y, mx_y = 200, 3900
    span_x = max(1, mx_x - mn_x)
    span_y = max(1, mx_y - mn_y)

    nx = max(0.0, min(1.0, (rx - mn_x) / span_x))
    ny = max(0.0, min(1.0, (ry - mn_y) / span_y))

    W, H = 320, 240

    # Kombinasi kalibrasi
    combos = [
        ("swap=T inv_y=T", ny, 1.0 - nx),  # swap xy dulu (nx,ny jadi ny,nx), lalu invert_y
        ("swap=T inv_y=F", ny, nx),
        ("swap=F inv_y=T", nx, 1.0 - ny),
        ("swap=F inv_y=F", nx, ny),
    ]

    best = ""
    # Config aktif: swap_xy=T, invert_x=F, invert_y=T
    # Setelah swap: nx_s=ny, ny_s=nx. Lalu invert_y: ny_s=1-nx
    nx_s, ny_s = ny, nx          # swap
    ny_s = 1.0 - ny_s            # invert_y
    px = int(nx_s * W)
    py = int(ny_s * H)
    px = max(0, min(W-1, px))
    py = max(0, min(H-1, py))

    btn = _guess_button_px(px, py)
    print(f"  RAW: ABS_X={rx:4d} ABS_Y={ry:4d} P={str(rp):4s}  "
          f"-> Pixel: ({px:3d},{py:3d}) [Config aktif] -> Tombol: {btn}")


def _guess_button(rx, ry):
    """Tebak tombol berdasarkan raw ADC (dengan config: swap_xy=T, invert_y=T)."""
    # Range ADC default
    mn_x, mx_x = 200, 3900
    mn_y, mx_y = 200, 3900
    span_x = max(1, mx_x - mn_x)
    span_y = max(1, mx_y - mn_y)

    nx = max(0.0, min(1.0, (rx - mn_x) / span_x))
    ny = max(0.0, min(1.0, (ry - mn_y) / span_y))

    W, H = 320, 240
    # swap_xy=T: tukar nx,ny; invert_y=T: balik y
    nx_s, ny_s = ny, nx
    ny_s = 1.0 - ny_s
    px = max(0, min(W-1, int(nx_s * W)))
    py = max(0, min(H-1, int(ny_s * H)))
    return _guess_button_px(px, py)


def _guess_button_px(px, py):
    """Identifikasi tombol dari koordinat pixel."""
    # Tombol baris bawah: y=170-234
    if 164 <= py <= 240:
        if px <= 76:
            return "BACA"
        elif px <= 124:
            return "ULANG"
        elif px <= 172:
            return "STOP"
        elif px <= 220:
            return "VOL-"
        elif px <= 268:
            return "VOL+"
        else:
            return "OFF"
    elif py < 30:
        return "Header"
    elif 30 <= py <= 170:
        return "Area tengah (kamera/teks)"
    return f"({px},{py})"


def _print_summary():
    """Tampilkan ringkasan nilai min/max yang terdeteksi."""
    if not _readings:
        return
    xs = [r[0] for r in _readings]
    ys = [r[1] for r in _readings]
    print("\n" + "=" * 60)
    print("  RINGKASAN NILAI ADC YANG TERDETEKSI:")
    print("=" * 60)
    print(f"  ABS_X: min={min(xs)}, max={max(xs)}")
    print(f"  ABS_Y: min={min(ys)}, max={max(ys)}")
    print()
    print("  Salin nilai ini ke config.toml [gui.touch]:")
    print(f"  min_adc_x = {min(xs)}")
    print(f"  max_adc_x = {max(xs)}")
    print(f"  min_adc_y = {min(ys)}")
    print(f"  max_adc_y = {max(ys)}")
    print("=" * 60)


if __name__ == '__main__':
    main()
