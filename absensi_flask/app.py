import csv
import hmac
import io
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, Response, g, jsonify, render_template, request

DB = "absensi.db"
WIB = timezone(timedelta(hours=7))

PASSWORD_ADMIN = "admin123"   # ganti dengan password milikmu
HANYA_LAPTOP = True           # True = web rekap hanya bisa dibuka dari laptop server

app = Flask(__name__)


# ===== Database =====
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def tutup_db(_=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    with sqlite3.connect(DB) as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS absensi (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nama TEXT NOT NULL,
                kelas TEXT NOT NULL,
                jurusan TEXT NOT NULL COLLATE NOCASE,
                absen INTEGER NOT NULL,
                tanggal TEXT NOT NULL,
                jam TEXT NOT NULL,
                lat REAL NOT NULL,
                lng REAL NOT NULL,
                akurasi INTEGER,
                UNIQUE(tanggal, kelas, jurusan, absen)
            )"""
        )


# ===== Pengaman web rekap =====
def khusus_admin(f):
    @wraps(f)
    def bungkus(*a, **k):
        # Lewat ngrok, header X-Forwarded-For selalu ada -> tolak.
        # Hanya peramban di laptop (localhost) yang boleh masuk.
        if HANYA_LAPTOP and (
            request.headers.get("X-Forwarded-For")
            or request.remote_addr not in ("127.0.0.1", "::1")
        ):
            return Response("Halaman ini tidak bisa dibuka dari perangkat ini.", 403)
        auth = request.authorization
        if not auth or not hmac.compare_digest(auth.password or "", PASSWORD_ADMIN):
            return Response(
                "Login diperlukan.", 401,
                {"WWW-Authenticate": 'Basic realm="Rekap Absensi"'},
            )
        return f(*a, **k)
    return bungkus


# ===== Halaman rekap (admin saja) =====
@app.get("/")
@khusus_admin
def halaman_rekap():
    return render_template("dashboard.html")


# ===== API: terima absen dari aplikasi siswa (terbuka) =====
@app.post("/api/absen")
def terima_absen():
    d = request.get_json(silent=True) or {}
    nama = str(d.get("nama", "")).strip()
    kelas = str(d.get("kelas", "")).strip()
    jurusan = str(d.get("jurusan", "")).strip()

    try:
        absen = int(d.get("absen"))
        lat = float(d.get("lat"))
        lng = float(d.get("lng"))
    except (TypeError, ValueError):
        return jsonify(error="Data belum lengkap. Isi semua kolom dan bagikan lokasi."), 400

    try:
        akurasi = int(d.get("akurasi")) if d.get("akurasi") is not None else None
    except (TypeError, ValueError):
        akurasi = None

    if len(nama) < 3 or len(nama) > 80:
        return jsonify(error="Nama harus 3–80 karakter."), 400
    if kelas not in ("X", "XI", "XII"):
        return jsonify(error="Kelas tidak valid."), 400
    if not jurusan or len(jurusan) > 30:
        return jsonify(error="Jurusan harus diisi (maksimal 30 karakter)."), 400
    if not 1 <= absen <= 99:
        return jsonify(error="Nomor absen harus 1–99."), 400
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return jsonify(error="Koordinat lokasi tidak valid."), 400

    sekarang = datetime.now(WIB)
    try:
        db = get_db()
        db.execute(
            """INSERT INTO absensi
               (nama, kelas, jurusan, absen, tanggal, jam, lat, lng, akurasi)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (nama, kelas, jurusan, absen,
             sekarang.strftime("%Y-%m-%d"), sekarang.strftime("%H:%M:%S"),
             lat, lng, akurasi),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify(error="Nomor absen ini sudah tercatat hari ini."), 409

    return jsonify(ok=True, pesan=f"Absensi terkirim. Terima kasih, {nama}.")


# ===== API: data rekap (admin saja) =====
def query_data(tanggal):
    sql = "SELECT * FROM absensi"
    params = []
    if tanggal:
        sql += " WHERE tanggal = ?"
        params.append(tanggal)
    sql += " ORDER BY tanggal DESC, kelas, jurusan, absen"
    return get_db().execute(sql, params).fetchall()


@app.get("/api/data")
@khusus_admin
def ambil_data():
    baris = query_data(request.args.get("tanggal", "").strip())
    return jsonify([dict(b) for b in baris])


@app.get("/api/export.csv")
@khusus_admin
def ekspor_csv():
    tanggal = request.args.get("tanggal", "").strip()
    baris = query_data(tanggal)

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Tanggal", "Jam", "Nama", "Kelas", "Jurusan", "No Absen", "Latitude", "Longitude"])
    for b in baris:
        w.writerow([b["tanggal"], b["jam"], b["nama"], b["kelas"], b["jurusan"],
                    b["absen"], b["lat"], b["lng"]])
    return Response(
        "\ufeff" + out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=absensi-{tanggal or 'semua'}.csv"},
    )


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)