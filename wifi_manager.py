"""
wifi_manager.py - Pengelola Status dan Sakelar WiFi Raspberry Pi
Bagian dari Rancang Bangun Alat Bantu Baca Tunanetra (Image to Voice)

Mendukung:
1. Pemantauan status radio WiFi (Aktif / Nonaktif via nmcli & rfkill).
2. Deteksi SSID jaringan yang terhubung (iwgetid & nmcli).
3. Deteksi alamat IP lokal (wlan0 & hostname -I).
4. Sakelar sentuh ON/OFF tanpa memblokir thread GUI.
"""

import os
import re
import subprocess
import sys
import threading
import time
from typing import Tuple


class WiFiManager:
    """
    Kelas pemantau dan sakelar WiFi Raspberry Pi OS (Bookworm / NetworkManager).
    Semua operasi pemindaian dan perubahan status berjalan secara asinkron
    sehingga tidak menyebabkan latensi pada antarmuka GUI Pygame.
    """

    def __init__(self):
        self.enabled: bool = True
        self.connected: bool = False
        self.ssid: str = ""
        self.ip: str = ""
        self._last_refresh: float = 0.0
        self._refresh_lock = threading.Lock()
        self._is_refreshing: bool = False

        # Inisialisasi awal
        self.refresh_sync()

    def get_info(self) -> Tuple[bool, bool, str, str]:
        """
        Mengembalikan tuple (enabled, connected, ssid, ip).
        Secara otomatis memicu pembaruan asinkron di latar belakang
        jika data sudah berusia lebih dari 2.5 detik.
        """
        now = time.time()
        if now - self._last_refresh > 2.5 and not self._is_refreshing:
            self.refresh_async()
        return self.enabled, self.connected, self.ssid, self.ip

    def refresh_async(self):
        """Memicu pembaruan status di latar belakang."""
        if self._is_refreshing:
            return
        threading.Thread(target=self._do_refresh, daemon=True).start()

    def refresh_sync(self):
        """Pembaruan status secara langsung."""
        self._do_refresh()

    def _do_refresh(self):
        if not self._refresh_lock.acquire(blocking=False):
            return
        self._is_refreshing = True
        try:
            if not sys.platform.startswith("linux"):
                # Lingkungan pengujian / Mock Windows
                self.enabled = True
                self.connected = True
                self.ssid = "WiFi-Lab"
                self.ip = "192.168.1.100"
                self._last_refresh = time.time()
                return

            # 1. Cek status radio WiFi
            wifi_on = True
            try:
                res_nm = subprocess.run(
                    ['nmcli', 'radio', 'wifi'],
                    capture_output=True, text=True, timeout=1.5
                )
                out = res_nm.stdout.strip().lower()
                if 'disabled' in out:
                    wifi_on = False
                elif 'enabled' in out:
                    wifi_on = True
                else:
                    # Fallback rfkill
                    res_rf = subprocess.run(
                        ['rfkill', 'list', 'wifi'],
                        capture_output=True, text=True, timeout=1.5
                    )
                    if 'Soft blocked: yes' in res_rf.stdout or 'Hard blocked: yes' in res_rf.stdout:
                        wifi_on = False
            except Exception:
                pass

            self.enabled = wifi_on

            if not wifi_on:
                self.connected = False
                self.ssid = "WiFi Nonaktif"
                self.ip = "-"
                self._last_refresh = time.time()
                return

            # 2. Ambil SSID jaringan yang terhubung
            ssid = ""
            try:
                # iwgetid sangat cepat (< 5ms)
                res_iw = subprocess.run(
                    ['iwgetid', '-r'],
                    capture_output=True, text=True, timeout=1.5
                )
                ssid = res_iw.stdout.strip()
            except Exception:
                pass

            if not ssid:
                # Coba via nmcli
                try:
                    res_dev = subprocess.run(
                        ['nmcli', '-t', '-f', 'active,ssid', 'dev', 'wifi'],
                        capture_output=True, text=True, timeout=2.0
                    )
                    for line in res_dev.stdout.splitlines():
                        if line.startswith('yes:'):
                            ssid = line.split(':', 1)[1].strip()
                            break
                except Exception:
                    pass

            # 3. Ambil Alamat IP pada antarmuka wlan0
            ip = ""
            try:
                res_ip = subprocess.run(
                    ['ip', '-4', 'addr', 'show', 'wlan0'],
                    capture_output=True, text=True, timeout=1.5
                )
                m = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)', res_ip.stdout)
                if m:
                    ip = m.group(1)
            except Exception:
                pass

            if not ip:
                try:
                    res_h = subprocess.run(
                        ['hostname', '-I'],
                        capture_output=True, text=True, timeout=1.5
                    )
                    ips = [p for p in res_h.stdout.strip().split() if not p.startswith('127.')]
                    if ips:
                        ip = ips[0]
                except Exception:
                    pass

            self.ssid = ssid if ssid else ("Terhubung" if ip else "Terputus")
            self.ip = ip if ip else "-"
            self.connected = bool(ip and ip != "-")
            self._last_refresh = time.time()

        finally:
            self._is_refreshing = False
            self._refresh_lock.release()

    def toggle_wifi(self) -> bool:
        """
        Membalik status sakelar WiFi (ON -> OFF atau OFF -> ON).
        Mengembalikan status baru (True jika ON, False jika OFF).
        """
        new_state = not self.enabled
        self.enabled = new_state
        if not new_state:
            self.connected = False
            self.ssid = "Mematikan..."
            self.ip = "-"
        else:
            self.ssid = "Menyalakan..."
            self.ip = "Mencari..."

        threading.Thread(target=self._apply_toggle, args=(new_state,), daemon=True).start()
        return new_state

    def _apply_toggle(self, turn_on: bool):
        """Mengeksekusi perintah nmcli & rfkill untuk mengubah status radio."""
        if not sys.platform.startswith("linux"):
            self.enabled = turn_on
            self.connected = turn_on
            self.ssid = "WiFi-Lab" if turn_on else "WiFi Nonaktif"
            self.ip = "192.168.1.100" if turn_on else "-"
            return

        cmd_arg = 'on' if turn_on else 'off'
        try:
            subprocess.run(['nmcli', 'radio', 'wifi', cmd_arg], check=False, timeout=3)
        except Exception:
            pass

        rf_arg = 'unblock' if turn_on else 'block'
        try:
            subprocess.run(['rfkill', rf_arg, 'wifi'], check=False, timeout=3)
        except Exception:
            pass

        # Beri jeda sejenak agar subsistem nirkabel merespons sebelum refresh
        time.sleep(1.8 if turn_on else 0.5)
        self.refresh_sync()
