"""
Sistem Monitoring K3 - PLN UP2D Jawa Timur
============================================
Fitur:
  - Deteksi helm & rompi real-time via webcam (YOLOv8)
  - Notifikasi otomatis ke Telegram pengawas HSSE
  - Penyimpanan log pelanggaran ke SQLite
  - Screenshot otomatis setiap pelanggaran

Jalankan: python k3_monitor_pln.py
"""

import cv2
import time
import datetime
import sqlite3
import requests
from ultralytics import YOLO
from pathlib import Path

# ─────────────────────────────────────────────
#  KONFIGURASI — sesuaikan jika perlu
# ─────────────────────────────────────────────
TELEGRAM_TOKEN   = "8314164796:AAHHiCZETUZR9Is6YKPXKJ42idimmqbqL0k"
TELEGRAM_CHAT_ID = "5730111227"

MODEL_PATH   = r"D:\PROJEK AKHIR MAGANG\runs\detect\runs\helmet\weights\best.pt"
WEBCAM_INDEX = 0

CONFIDENCE        = 0.50
VIOLATION_MIN     = 5
ALERT_COOLDOWN    = 60
FRAME_SKIP        = 2

SAVE_DIR = Path("pelanggaran_apd")
DB_PATH  = "k3_pln_log.db"
SAVE_DIR.mkdir(exist_ok=True)

COLOR_AMAN   = (34, 197, 94)
COLOR_BAHAYA = (34, 34, 220)
COLOR_TEKS   = (255, 255, 255)
LOKASI_GARDU = "Gardu Induk PLN UP2D Jawa Timur"

# Kelas yang dianggap pelanggaran
KELAS_PELANGGARAN = {"no_helmet", "no_vest"}
# Kelas yang dianggap aman
KELAS_AMAN = {"helmet", "vest", "person"}


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
            terkirim    INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()
    print("[v] Database siap.")

def simpan_log(waktu, jenis, confidence, foto):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO log_pelanggaran (waktu, jenis, confidence, foto, terkirim) VALUES (?,?,?,?,1)",
        (waktu, jenis, confidence, foto)
    )
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────
#  TELEGRAM
# ─────────────────────────────────────────────
def kirim_alert_telegram(foto_path: str, pelanggaran: list, confidence: float, waktu: str) -> bool:
    jenis_str = "\n".join([f"  - {p.replace('_', ' ').upper()}" for p in pelanggaran])
    pesan = (
        f"PELANGGARAN APD TERDETEKSI!\n"
        f"========================\n"
        f"Lokasi: {LOKASI_GARDU}\n"
        f"Waktu: {waktu}\n"
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
            print("[v] Alert Telegram terkirim!")
            return True
        else:
            print(f"[x] Telegram error: {resp.status_code}")
            return False
    except Exception as e:
        print(f"[x] Gagal kirim Telegram: {e}")
        return False

def kirim_notif_aktif():
    sekarang = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pesan = (
        f"SISTEM K3 MONITOR AKTIF\n"
        f"========================\n"
        f"Lokasi: {LOKASI_GARDU}\n"
        f"Waktu Aktif: {sekarang}\n"
        f"Model: YOLOv8 APD Detector\n"
        f"Kamera: Webcam Index {WEBCAM_INDEX}\n"
        f"Deteksi: Helm & Rompi APD\n"
        f"========================\n"
        f"Sistem siap memantau kepatuhan APD."
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID, "text": pesan
        }, timeout=10)
        print("[v] Notifikasi sistem aktif terkirim ke Telegram.")
    except Exception as e:
        print(f"[!] Tidak bisa kirim notif aktif: {e}")


# ─────────────────────────────────────────────
#  ANOTASI FRAME
# ─────────────────────────────────────────────
def anotasi_frame(frame, results, pelanggaran: list):
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

    # Timestamp pojok kiri bawah
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cv2.putText(frame, ts, (10, frame.shape[0] - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

    return frame


# ─────────────────────────────────────────────
#  MAIN LOOP
# ─────────────────────────────────────────────
def main():
    init_db()

    print(f"[*] Memuat model: {MODEL_PATH}")
    model = YOLO(MODEL_PATH)
    print(f"[v] Kelas yang dikenali: {model.names}")

    print(f"[*] Membuka kamera index {WEBCAM_INDEX}...")
    cap = cv2.VideoCapture(WEBCAM_INDEX, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    if not cap.isOpened():
        print("[x] Kamera tidak bisa dibuka!")
        return

    kirim_notif_aktif()
    print("[v] Sistem monitoring aktif. Tekan 'q' untuk keluar, 's' untuk screenshot.\n")

    frame_count      = 0
    violation_streak = 0
    last_alert_time  = 0
    pelanggaran      = []

    while True:
        ret, frame = cap.read()
        if not ret:
            print("[!] Frame tidak terbaca. Mencoba ulang...")
            time.sleep(1)
            continue

        frame_count += 1
        if frame_count % FRAME_SKIP != 0:
            continue

        # Inferensi
        results = model(frame, conf=CONFIDENCE, verbose=False)

        labels_terdeteksi  = set()
        max_conf_violation = 0.0

        for box in results[0].boxes:
            cls_id = int(box.cls[0])
            label  = results[0].names[cls_id]
            conf   = float(box.conf[0])
            labels_terdeteksi.add(label)
            if label in KELAS_PELANGGARAN and conf > max_conf_violation:
                max_conf_violation = conf

        # Cek semua jenis pelanggaran APD
        pelanggaran = []
        if "no_helmet" in labels_terdeteksi:
            pelanggaran.append("no_helmet")
        if "no_vest" in labels_terdeteksi:
            pelanggaran.append("no_vest")

        # Hitung streak pelanggaran
        if pelanggaran:
            violation_streak += 1
        else:
            violation_streak = 0

        # Kirim alert jika memenuhi syarat
        sekarang = time.time()
        if (violation_streak >= VIOLATION_MIN
                and (sekarang - last_alert_time) > ALERT_COOLDOWN):

            waktu_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            nama_foto = SAVE_DIR / f"pelanggaran_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"

            frame_anotasi = anotasi_frame(frame.copy(), results, pelanggaran)
            cv2.imwrite(str(nama_foto), frame_anotasi)

            jenis_log = ", ".join(pelanggaran)
            print(f"\n[!] PELANGGARAN: {jenis_log} -- {waktu_str}")
            kirim_alert_telegram(str(nama_foto), pelanggaran, max_conf_violation, waktu_str)
            simpan_log(waktu_str, jenis_log, max_conf_violation, str(nama_foto))

            last_alert_time  = sekarang
            violation_streak = 0

        # Tampilan
        frame_tampil = anotasi_frame(frame.copy(), results, pelanggaran)

        fps_text = f"FPS: {1/(time.time()-sekarang+1e-6):.1f}"
        cv2.putText(frame_tampil, fps_text,
                    (frame_tampil.shape[1] - 100, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)

        cv2.imshow("Monitor K3 PLN UP2D | Tekan Q untuk keluar", frame_tampil)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        elif key == ord("s"):
            nama = f"screenshot_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            cv2.imwrite(nama, frame_tampil)
            print(f"[v] Screenshot: {nama}")

    cap.release()
    cv2.destroyAllWindows()

    # Notif sistem dimatikan
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        print("[*] Mengirim notifikasi sistem off...")
        resp = requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": (
                f"SISTEM K3 MONITOR DIMATIKAN\n"
                f"{LOKASI_GARDU}\n"
                f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            ),
        }, timeout=10)
        time.sleep(2)
        if resp.status_code == 200:
            print("[v] Notifikasi off terkirim.")
        else:
            print(f"[x] Gagal: {resp.status_code}")
    except Exception as e:
        print(f"[x] Error: {e}")

    print("[*] Sistem dihentikan.")


if __name__ == "__main__":
    main()