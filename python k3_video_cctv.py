"""
Sistem Monitoring K3 - PLN UP2D Jawa Timur
Mode: Analisis Video CCTV Recording
============================================
Fitur:
  - Input dari file video CCTV (.mp4, .avi, .mkv)
  - Deteksi helm & rompi APD via YOLOv8
  - Pause/Resume dengan tombol SPASI
  - Maju/mundur video dengan tombol panah
  - Progress bar video
  - Notifikasi Telegram saat pelanggaran
  - Simpan log ke SQLite
  - Screenshot otomatis pelanggaran

Kontrol:
  SPASI     = Pause / Resume
  S         = Screenshot manual
  ->        = Maju 10 detik
  <-        = Mundur 10 detik
  Q         = Keluar

Jalankan: python k3_video_cctv.py
"""

import cv2
import time
import datetime
import sqlite3
import requests
import tkinter as tk
from tkinter import filedialog
from ultralytics import YOLO
from pathlib import Path

# ─────────────────────────────────────────────
#  KONFIGURASI
# ─────────────────────────────────────────────
TELEGRAM_TOKEN   = "8314164796:AAHHiCZETUZR9Is6YKPXKJ42idimmqbqL0k"
TELEGRAM_CHAT_ID = "5730111227"

MODEL_PATH = r"D:\PROJEK AKHIR MAGANG\runs\detect\runs\helmet\weights\best.pt"

CONFIDENCE          = 0.50
CONFIDENCE_NO_VEST  = 0.85   # threshold lebih tinggi untuk no_vest
                              # agar rompi orange PLN tidak salah deteksi
VIOLATION_MIN       = 5
ALERT_COOLDOWN      = 30
FRAME_SKIP          = 2

SAVE_DIR = Path("pelanggaran_apd")
DB_PATH  = "k3_pln_log.db"
SAVE_DIR.mkdir(exist_ok=True)

COLOR_AMAN   = (34, 197, 94)
COLOR_BAHAYA = (34, 34, 220)
COLOR_TEKS   = (255, 255, 255)
LOKASI_GARDU = "Gardu Induk PLN UP2D Jawa Timur"

KELAS_PELANGGARAN = {"no_helmet", "no_vest"}


# ─────────────────────────────────────────────
#  PILIH FILE VIDEO
# ─────────────────────────────────────────────
def pilih_file_video():
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    file_path = filedialog.askopenfilename(
        title="Pilih File Video CCTV",
        filetypes=[
            ("File Video", "*.mp4 *.avi *.mkv *.mov *.MOV *.MP4 *.AVI"),
            ("Semua File", "*.*")
        ]
    )
    root.destroy()
    return file_path


# ─────────────────────────────────────────────
#  DATABASE
# ─────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS log_pelanggaran (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            waktu       TEXT NOT NULL,
            jenis       TEXT NOT NULL,
            confidence  REAL,
            foto        TEXT,
            sumber      TEXT,
            terkirim    INTEGER DEFAULT 0
        )
    """)
    # Tambah kolom sumber jika belum ada (untuk database lama)
    try:
        conn.execute("ALTER TABLE log_pelanggaran ADD COLUMN sumber TEXT")
    except:
        pass
    conn.commit()
    conn.close()

def simpan_log(waktu, jenis, confidence, foto, sumber):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO log_pelanggaran (waktu, jenis, confidence, foto, sumber, terkirim) VALUES (?,?,?,?,?,1)",
        (waktu, jenis, confidence, foto, sumber)
    )
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────
#  TELEGRAM
# ─────────────────────────────────────────────
def kirim_alert_telegram(foto_path, pelanggaran, confidence, waktu, nama_video, timestamp_video):
    jenis_str = "\n".join([f"  - {p.replace('_', ' ').upper()}" for p in pelanggaran])
    pesan = (
        f"PELANGGARAN APD TERDETEKSI!\n"
        f"========================\n"
        f"Lokasi: {LOKASI_GARDU}\n"
        f"Sumber: Rekaman CCTV\n"
        f"File Video: {nama_video}\n"
        f"Timestamp Video: {timestamp_video}\n"
        f"Waktu Analisis: {waktu}\n"
        f"========================\n"
        f"Jenis Pelanggaran:\n{jenis_str}\n"
        f"Confidence: {confidence:.1%}\n"
        f"========================\n"
        f"Segera lakukan tindakan korektif!\n"
        f"- Sistem K3 PLN UP2D Auto Monitor"
    )
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
        with open(foto_path, "rb") as img:
            resp = requests.post(
                url,
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": pesan},
                files={"photo": img},
                timeout=15
            )
        if resp.status_code == 200:
            print(f"[v] Alert Telegram terkirim!")
            return True
        else:
            print(f"[x] Telegram error: {resp.status_code}")
            return False
    except Exception as e:
        print(f"[x] Gagal kirim: {e}")
        return False


# ─────────────────────────────────────────────
#  FORMAT WAKTU VIDEO
# ─────────────────────────────────────────────
def format_durasi(detik):
    h = int(detik // 3600)
    m = int((detik % 3600) // 60)
    s = int(detik % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# ─────────────────────────────────────────────
#  PROGRESS BAR
# ─────────────────────────────────────────────
def gambar_progress_bar(frame, frame_sekarang, total_frame, fps_video):
    h, w = frame.shape[:2]
    bar_h     = 30
    bar_y     = h - bar_h
    progress  = frame_sekarang / max(total_frame, 1)
    bar_w_fill = int(w * progress)

    cv2.rectangle(frame, (0, bar_y), (w, h), (30, 30, 30), -1)
    cv2.rectangle(frame, (0, bar_y + 5), (bar_w_fill, h - 5), (0, 140, 255), -1)
    cv2.rectangle(frame, (0, bar_y + 5), (w, h - 5), (80, 80, 80), 1)

    waktu_skrg  = format_durasi(frame_sekarang / max(fps_video, 1))
    waktu_total = format_durasi(total_frame / max(fps_video, 1))
    cv2.putText(frame, f"{waktu_skrg} / {waktu_total}",
                (w - 135, bar_y + 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return frame


# ─────────────────────────────────────────────
#  ANOTASI FRAME
# ─────────────────────────────────────────────
def anotasi_frame(frame, results, pelanggaran, paused=False):
    for box in results[0].boxes:
        cls_id = int(box.cls[0])
        label  = results[0].names[cls_id]
        conf   = float(box.conf[0])
        x1, y1, x2, y2 = map(int, box.xyxy[0])

        warna = COLOR_BAHAYA if label in KELAS_PELANGGARAN else COLOR_AMAN
        cv2.rectangle(frame, (x1, y1), (x2, y2), warna, 2)

        teks = f"{label} {conf:.0%}"
        (tw, th), _ = cv2.getTextSize(teks, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        cv2.rectangle(frame, (x1, y1 - th - 10), (x1 + tw + 8, y1), warna, -1)
        cv2.putText(frame, teks, (x1 + 4, y1 - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLOR_TEKS, 2)

    # Banner status atas
    ada_deteksi = len(results[0].boxes) > 0

    if pelanggaran:
        jenis_str = " & ".join([p.replace("_", " ").upper() for p in pelanggaran])
        cv2.rectangle(frame, (0, 0), (frame.shape[1], 45), (30, 30, 200), -1)
        cv2.putText(frame, f"PELANGGARAN: {jenis_str}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
    elif ada_deteksi:
        cv2.rectangle(frame, (0, 0), (frame.shape[1], 45), (20, 140, 40), -1)
        cv2.putText(frame, "AMAN: APD Lengkap Terdeteksi",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    else:
        cv2.rectangle(frame, (0, 0), (frame.shape[1], 45), (80, 80, 80), -1)
        cv2.putText(frame, "Menunggu deteksi...",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

    # Indikator PAUSE
    if paused:
        cv2.rectangle(frame,
                      (frame.shape[1]//2 - 60, 60),
                      (frame.shape[1]//2 + 60, 95),
                      (0, 0, 0), -1)
        cv2.putText(frame, "|| PAUSED",
                    (frame.shape[1]//2 - 50, 85),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)

    # Timestamp analisis
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cv2.putText(frame, f"Analisis: {ts}",
                (10, frame.shape[0] - 35),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)

    return frame


# ─────────────────────────────────────────────
#  CEK PELANGGARAN (dengan threshold berbeda)
# ─────────────────────────────────────────────
def cek_pelanggaran(results, model):
    """
    Cek pelanggaran APD dari hasil deteksi.
    no_helmet : confidence >= 0.50 (normal)
    no_vest   : confidence >= 0.85 (lebih ketat)
                agar rompi orange PLN tidak salah deteksi
    """
    pelanggaran        = []
    max_conf_violation = 0.0

    no_helmet_conf = 0.0
    no_vest_conf   = 0.0

    for box in results[0].boxes:
        cls_id = int(box.cls[0])
        label  = model.names[cls_id]
        conf   = float(box.conf[0])

        if label == "no_helmet" and conf >= CONFIDENCE:
            if conf > no_helmet_conf:
                no_helmet_conf = conf
        if label == "no_vest" and conf >= CONFIDENCE_NO_VEST:
            if conf > no_vest_conf:
                no_vest_conf = conf

    if no_helmet_conf > 0:
        pelanggaran.append("no_helmet")
        max_conf_violation = max(max_conf_violation, no_helmet_conf)
    if no_vest_conf > 0:
        pelanggaran.append("no_vest")
        max_conf_violation = max(max_conf_violation, no_vest_conf)

    return pelanggaran, max_conf_violation


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def main():
    init_db()

    # Pilih file video
    print("[*] Pilih file video CCTV...")
    video_path = pilih_file_video()

    if not video_path:
        print("[x] Tidak ada file dipilih. Keluar.")
        return

    nama_video = Path(video_path).name
    print(f"[v] File dipilih: {nama_video}")

    # Muat model
    print(f"[*] Memuat model: {MODEL_PATH}")
    model = YOLO(MODEL_PATH)
    print(f"[v] Kelas: {model.names}")

    # Buka video
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"[x] Tidak bisa membuka video: {video_path}")
        return

    total_frame = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps_video   = cap.get(cv2.CAP_PROP_FPS) or 25
    delay_ms    = max(1, int(1000 / fps_video))

    print(f"[v] Video: {total_frame} frame, {fps_video:.1f} FPS")
    print(f"[v] Durasi: {format_durasi(total_frame / fps_video)}")
    print(f"[v] Threshold no_helmet : {CONFIDENCE:.0%}")
    print(f"[v] Threshold no_vest   : {CONFIDENCE_NO_VEST:.0%}")
    print("\nKontrol:")
    print("  SPASI = Pause/Resume")
    print("  S     = Screenshot")
    print("  ->    = Maju 10 detik")
    print("  <-    = Mundur 10 detik")
    print("  Q     = Keluar\n")

    frame_count      = 0
    violation_streak = 0
    last_alert_time  = 0
    pelanggaran      = []
    last_results     = None
    paused           = False
    frame            = None

    while True:
        if not paused:
            ret, frame = cap.read()
            if not ret:
                print("\n[v] Video selesai.")
                break

            frame_count += 1

            if frame_count % FRAME_SKIP == 0:
                results      = model(frame, conf=CONFIDENCE, verbose=False)
                last_results = results

                # Cek pelanggaran dengan threshold berbeda
                pelanggaran, max_conf_violation = cek_pelanggaran(results, model)

                if pelanggaran:
                    violation_streak += 1
                else:
                    violation_streak = 0

                # Kirim alert
                sekarang = time.time()
                if (violation_streak >= VIOLATION_MIN
                        and (sekarang - last_alert_time) > ALERT_COOLDOWN):

                    waktu_str  = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    ts_video   = format_durasi(frame_count / fps_video)
                    nama_foto  = SAVE_DIR / f"pelanggaran_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"

                    frame_anotasi = anotasi_frame(frame.copy(), results, pelanggaran)
                    cv2.imwrite(str(nama_foto), frame_anotasi)

                    jenis_log = ", ".join(pelanggaran)
                    print(f"[!] PELANGGARAN: {jenis_log} | Video: {ts_video} | {waktu_str}")
                    kirim_alert_telegram(str(nama_foto), pelanggaran,
                                         max_conf_violation, waktu_str,
                                         nama_video, ts_video)
                    simpan_log(waktu_str, jenis_log, max_conf_violation,
                               str(nama_foto), nama_video)

                    last_alert_time  = sekarang
                    violation_streak = 0

        # Tampilan
        if frame is not None and last_results is not None:
            frame_tampil = anotasi_frame(frame.copy(), last_results, pelanggaran, paused)
        elif frame is not None:
            frame_tampil = frame.copy()
        else:
            continue

        # Progress bar
        frame_tampil = gambar_progress_bar(frame_tampil, frame_count, total_frame, fps_video)

        # Info nama file
        cv2.putText(frame_tampil, f"File: {nama_video}",
                    (10, frame_tampil.shape[0] - 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)

        # Resize agar tidak terlalu besar di layar
        h, w = frame_tampil.shape[:2]
        max_w, max_h = 1280, 720
        scale = min(max_w / w, max_h / h, 1.0)
        if scale < 1.0:
            frame_tampil = cv2.resize(frame_tampil,
                                      (int(w * scale), int(h * scale)))

        cv2.imshow("Monitor K3 PLN UP2D | Analisis CCTV", frame_tampil)

        key = cv2.waitKey(1 if paused else delay_ms) & 0xFF

        if key == ord("q"):
            break
        elif key == ord(" "):
            paused = not paused
            print(f"[*] Video {'PAUSED' if paused else 'RESUMED'}")
        elif key == ord("s"):
            nama = f"screenshot_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            cv2.imwrite(nama, frame_tampil)
            print(f"[v] Screenshot: {nama}")
        elif key == 83:  # Panah kanan = maju 10 detik
            frame_baru = min(frame_count + int(fps_video * 10), total_frame - 1)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_baru)
            frame_count = frame_baru
            print(f"[*] Maju ke {format_durasi(frame_count / fps_video)}")
        elif key == 81:  # Panah kiri = mundur 10 detik
            frame_baru = max(frame_count - int(fps_video * 10), 0)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_baru)
            frame_count = frame_baru
            print(f"[*] Mundur ke {format_durasi(frame_count / fps_video)}")

    cap.release()
    cv2.destroyAllWindows()
    print(f"\n[v] Analisis selesai!")
    print(f"[v] Total frame diproses : {frame_count}")
    print(f"[v] Log tersimpan di     : {DB_PATH}")
    print(f"[v] Foto pelanggaran di  : {SAVE_DIR}/")


if __name__ == "__main__":
    main()