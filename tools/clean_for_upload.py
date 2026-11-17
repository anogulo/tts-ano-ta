"""
clean_for_upload.py - Pembersih Berkas Sampah & Cache Sebelum Upload ke Raspberry Pi
Menghapus file *.pyc, __pycache__, berkas uji kamera temporer, dan log lama.
"""

import glob
import os
import shutil


def clean_project():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print(f"Membersihkan proyek di: {root}")

    # 1. Hapus __pycache__
    count_pycache = 0
    for root_dir, dirs, _ in os.walk(root):
        for d in dirs:
            if d == '__pycache__':
                full_p = os.path.join(root_dir, d)
                shutil.rmtree(full_p, ignore_errors=True)
                count_pycache += 1
    print(f" -> Menghapus {count_pycache} direktori __pycache__")

    # 2. Hapus file audio temp di logs/
    count_temp_wav = 0
    logs_dir = os.path.join(root, 'logs')
    if os.path.exists(logs_dir):
        for f in glob.glob(os.path.join(logs_dir, '_temp_*.wav')):
            try:
                os.remove(f)
                count_temp_wav += 1
            except Exception:
                pass
    print(f" -> Menghapus {count_temp_wav} berkas audio sementara di logs/")

    # 3. Hapus foto uji kamera sementara di dataset/
    count_test_img = 0
    dataset_dir = os.path.join(root, 'dataset')
    if os.path.exists(dataset_dir):
        for f in glob.glob(os.path.join(dataset_dir, 'uji_kamera_*.png')):
            try:
                os.remove(f)
                count_test_img += 1
            except Exception:
                pass
    print(f" -> Menghapus {count_test_img} foto uji kamera di dataset/")

    print("Pembersihan selesai! Folder siap dikemas / di-upload ke Raspberry Pi.\n")


if __name__ == '__main__':
    clean_project()
