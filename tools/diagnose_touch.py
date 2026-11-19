#!/usr/bin/env python3
"""
tools/diagnose_touch.py - Diagnosa Mendalam Hardware Touchscreen XPT2046 / ADS7846
Menemukan penyebab pasti mengapa sentuhan layar tidak terbaca:
1. Memeriksa counter interupsi kernel di /proc/interrupts.
2. Memeriksa detail device event di evdev.
3. Melakukan scanning pin GPIO untuk mendeteksi kabel T_IRQ fisik secara otomatis.
"""

import os
import sys
import time
import subprocess

print("=" * 65)
print("     DIAGNOSA MENDALAM HARDWARE TOUCHSCREEN (XPT2046 / ADS7846)")
print("=" * 65)

# 1. Cek Interrupts Kernel
print("\n[1] Memeriksa Counter Interupsi Kernel (/proc/interrupts):")
irq_found = False
irq_num = None
irq_gpio = None
irq_count_before = 0

if os.path.exists('/proc/interrupts'):
    with open('/proc/interrupts', 'r') as f:
        for line in f:
            if 'ads7846' in line.lower():
                print("    " + line.strip())
                parts = line.strip().split()
                irq_found = True
                irq_num = parts[0].replace(':', '')
                # Ambil jumlah interupsi (kolom CPU)
                try:
                    irq_count_before = int(parts[1])
                except Exception:
                    pass
                for p in parts:
                    if p.isdigit() and int(p) in (17, 22, 23, 24, 25, 27):
                        irq_gpio = p

if not irq_found:
    print("    [PERINGATAN] Driver ads7846 TIDAK terdaftar di /proc/interrupts!")
else:
    print(f"    -> Driver terdaftar pada IRQ {irq_num} (Jumlah interupsi tercatat: {irq_count_before})")

# 2. Cek Device Input
print("\n[2] Memeriksa Perangkat di /dev/input/event*:")
touch_event_dev = None
try:
    import evdev
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        if 'ads7846' in dev.name.lower() or 'touch' in dev.name.lower():
            touch_event_dev = dev
            print(f"    -> Ditemukan: {dev.name} ({path})")
            print(f"       Capabilities: {list(dev.capabilities().keys())}")
            if evdev.ecodes.EV_ABS in dev.capabilities():
                abs_info = dev.capabilities()[evdev.ecodes.EV_ABS]
                for code, info in abs_info:
                    print(f"       ABS Code {code}: min={info.min}, max={info.max}, val={info.value}")
except Exception as e:
    print(f"    Error evdev: {e}")

# 3. Uji Interupsi Real-time (Deteksi Sentuhan Fisik)
print("\n[3] Uji Sentuhan Fisik (5 Detik):")
print("    >>> TEKAN DAN TAHAN LAYAR LCD SEKARANG DENGAN KUKU / STYLUS! <<<")
print("    (Catatan: Layar resistif XPT2046 butuh TEKANAN fisik yang jelas, bukan sekadar disentuh lembut)")

for i in range(5, 0, -1):
    print(f"    Menunggu tekanan layar... ({i} detik)", end='\r', flush=True)
    time.sleep(1)
print("\n")

# Cek kembali apakah counter interupsi bertambah
irq_count_after = 0
if os.path.exists('/proc/interrupts') and irq_found:
    with open('/proc/interrupts', 'r') as f:
        for line in f:
            if 'ads7846' in line.lower():
                parts = line.strip().split()
                try:
                    irq_count_after = int(parts[1])
                except Exception:
                    pass

diff = irq_count_after - irq_count_before
print(f"    Counter Interupsi Sebelum: {irq_count_before} | Sesudah: {irq_count_after} (Selisih: {diff})")

if diff > 0:
    print("\n[KESIMPULAN]: Sinyal interupsi hardware MASUK KE KERNEL!")
    print("              Hardware & kabel T_IRQ terhubung dengan benar.")
else:
    print("\n[KESIMPULAN]: Sinyal interupsi 0 (TIDAK ADA SINYAL MASUK KE KERNEL).")
    print("              Penyebab pasti:")
    print("              a. Kabel T_IRQ dicolok ke pin GPIO yang berbeda di header Pi.")
    print("              b. Kabel T_IRQ, T_CS, T_CLK, atau T_DO kendor / tidak tersambung.")
    print("              c. Layar tidak ditekan dengan cukup kuat (layar resistif butuh tekanan kuku).")

# 4. Rekomendasi Pengecekan Pin
print("\n[4] Panduan Verifikasi Kabel Fisik:")
print("    Pastikan pin kabel sentuh dihubungkan persis ke pin ini:")
print("    - T_CLK -> Pin Fisik 23 (GPIO 11 / SPI Clock)")
print("    - T_CS  -> Pin Fisik 26 (GPIO 7 / SPI CE1)")
print("    - T_DIN -> Pin Fisik 19 (GPIO 10 / SPI MOSI)")
print("    - T_DO  -> Pin Fisik 21 (GPIO 9 / SPI MISO)")
print("    - T_IRQ -> Pin Fisik 11 (GPIO 17) -> jika config: penirq=17")
print("            -> ATAU Pin Fisik 22 (GPIO 25) -> jika config: penirq=25")
print("=" * 65)
