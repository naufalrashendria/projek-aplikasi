# firestore_rest.py
# Pengganti firebase_admin / firestore.client() memakai REST API Firestore (library `requests`).
# Tidak butuh grpc/protobuf, jadi bisa di-build menjadi APK Android.
#
# Cara pakai (sudah terpasang di main.py):
#   import firestore_rest as fs
#   fs.configure(PROJECT_ID, lambda: app.id_token, app.refresh_id_token)
#   fs.query("products", [("users_id", "EQUAL", uid)])   -> list of dict (ada kunci 'doc_id')
#
# Autentikasi: semua request memakai id_token user yang login. Bila server membalas 401
# (token kedaluwarsa ~1 jam), fungsi refresh dipanggil lalu request diulang satu kali.
# Hak akses data ditentukan oleh Firestore Security Rules.

import uuid
import datetime
import requests

TIMEOUT = 15
_cfg = {"project_id": "", "get_token": None, "refresh_token": None}


class FirestoreError(Exception):
    """status = kode HTTP (0 bila gagal koneksi); code = kode Firestore, mis. FAILED_PRECONDITION."""

    def __init__(self, status, message, code=""):
        super().__init__(message)
        self.status = status
        self.code = code


# ======================================================================
# Konfigurasi
# ======================================================================

def configure(project_id, get_token, refresh_token=None):
    """project_id    : Project ID Firebase (Console > Project settings > General)
    get_token     : fungsi tanpa argumen -> id_token saat ini
    refresh_token : fungsi tanpa argumen -> True bila id_token berhasil diperbarui"""
    _cfg.update(project_id=project_id or "", get_token=get_token, refresh_token=refresh_token)


def is_ready():
    pid = _cfg["project_id"]
    return bool(pid) and not pid.startswith("GANTI") and _cfg["get_token"] is not None


def _root():
    return f"projects/{_cfg['project_id']}/databases/(default)/documents"


def _url(path=""):
    return f"https://firestore.googleapis.com/v1/{_root()}{path}"


def _name(collection, doc_id):
    # Di body :commit, "name" harus path resource (tanpa https://host)
    return f"{_root()}/{collection}/{doc_id}"


# ======================================================================
# Konversi tipe data Python <-> format Firestore REST
# ======================================================================

def _to_fs(value):
    if isinstance(value, bool):  # harus sebelum int (bool turunan int)
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if value is None:
        return {"nullValue": None}
    if isinstance(value, datetime.datetime):
        return {"timestampValue": value.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"}
    if isinstance(value, dict):
        return {"mapValue": {"fields": {k: _to_fs(v) for k, v in value.items()}}}
    if isinstance(value, (list, tuple)):
        return {"arrayValue": {"values": [_to_fs(v) for v in value]}}
    raise TypeError(f"Tipe data tidak didukung untuk Firestore: {type(value)}")


def _from_fs(v):
    if "stringValue" in v:
        return v["stringValue"]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "doubleValue" in v:
        return v["doubleValue"]
    if "booleanValue" in v:
        return v["booleanValue"]
    if "timestampValue" in v:
        return v["timestampValue"]  # string ISO-8601
    if "mapValue" in v:
        return {k: _from_fs(x) for k, x in v["mapValue"].get("fields", {}).items()}
    if "arrayValue" in v:
        return [_from_fs(x) for x in v["arrayValue"].get("values", [])]
    return None  # nullValue / tipe lain


def _fields(data):
    return {k: _to_fs(v) for k, v in data.items()}


def _to_dict(doc):
    out = {k: _from_fs(v) for k, v in doc.get("fields", {}).items()}
    out["doc_id"] = doc["name"].split("/")[-1]
    return out


# ======================================================================
# HTTP (auto-refresh token, pesan error yang ramah)
# ======================================================================

def _request(method, url, **kwargs):
    r = None
    for attempt in (0, 1):
        headers = {"Authorization": f"Bearer {_cfg['get_token']()}"}
        try:
            r = requests.request(method, url, headers=headers, timeout=TIMEOUT, **kwargs)
        except requests.exceptions.RequestException as e:
            raise FirestoreError(0, "Tidak bisa terhubung ke server. Periksa koneksi internet.") from e

        if r.status_code == 401 and attempt == 0 and _cfg["refresh_token"]:
            try:
                if _cfg["refresh_token"]():
                    continue  # token baru didapat -> ulangi sekali
            except Exception:
                pass
        break

    if not r.ok:
        code = ""
        try:
            err = r.json().get("error", {})
            msg = err.get("message", r.text)
            code = err.get("status", "")
        except Exception:
            msg = r.text
        if r.status_code == 403:
            msg = "Akses ditolak oleh Firestore Security Rules. " + msg
        elif r.status_code == 401:
            msg = "Sesi login habis, silakan login ulang. " + msg
        # Sertakan operasi yang gagal supaya mudah dilacak, mis. "[POST :commit]"
        where = url.split("/documents", 1)[-1] or "/"
        raise FirestoreError(r.status_code, f"{msg} [{method} {where}]", code)
    return r


# ======================================================================
# Operasi dasar
# ======================================================================

def get_doc(collection, doc_id):
    """Return dict (ada 'doc_id') atau None bila dokumen tidak ada."""
    try:
        return _to_dict(_request("GET", _url(f"/{collection}/{doc_id}")).json())
    except FirestoreError as e:
        if e.status == 404:
            return None
        raise


def query(collection, filters):
    """Pengganti db.collection(col).where(...).where(...).stream()
    filters = [(field, op, value), ...]
    op (istilah REST): EQUAL, NOT_EQUAL, LESS_THAN, LESS_THAN_OR_EQUAL,
                       GREATER_THAN, GREATER_THAN_OR_EQUAL, ARRAY_CONTAINS
    Return: list dict, tiap dict punya kunci 'doc_id'."""
    parts = [{"fieldFilter": {"field": {"fieldPath": f}, "op": op, "value": _to_fs(v)}}
             for f, op, v in filters]
    sq = {"from": [{"collectionId": collection}]}
    if len(parts) == 1:
        sq["where"] = parts[0]
    elif parts:
        sq["where"] = {"compositeFilter": {"op": "AND", "filters": parts}}
    items = _request("POST", _url(":runQuery"), json={"structuredQuery": sq}).json()
    return [_to_dict(i["document"]) for i in items if "document" in i]


def add_doc(collection, data):
    """Pengganti db.collection(col).add(data) -> id dokumen baru."""
    r = _request("POST", _url(f"/{collection}"), json={"fields": _fields(data)})
    return r.json()["name"].split("/")[-1]


def update_doc(collection, doc_id, data):
    """Pengganti .document(id).update(data): hanya field di `data` yang berubah.
    Gagal (404) bila dokumen tidak ada."""
    params = [("updateMask.fieldPaths", k) for k in data] + [("currentDocument.exists", "true")]
    _request("PATCH", _url(f"/{collection}/{doc_id}"), params=params,
             json={"fields": _fields(data)})


def delete_doc(collection, doc_id):
    _request("DELETE", _url(f"/{collection}/{doc_id}"))


def delete_many(collection, doc_ids, chunk=400):
    """Hapus banyak dokumen per batch (maks 500 per commit). Return jumlah dokumen."""
    doc_ids = list(doc_ids)
    for i in range(0, len(doc_ids), chunk):
        writes = [{"delete": _name(collection, d)} for d in doc_ids[i:i + chunk]]
        _request("POST", _url(":commit"), json={"writes": writes})
    return len(doc_ids)


# ======================================================================
# Transaksi atomik (pengganti @firestore.transactional)
# ======================================================================
# Tidak memakai :beginTransaction (ditolak untuk akun user biasa oleh Firestore).
# Gantinya "optimistic concurrency": baca dokumen lewat GET biasa (dapat updateTime),
# lalu kirim semua tulisan dalam SATU :commit (atomik: semua berhasil atau semua gagal)
# dengan syarat dokumen belum berubah sejak dibaca. Kalau sudah berubah, diulang otomatis.

class Tx:
    """Dipakai di dalam fungsi transaksi: baca dengan get(), tulis dengan update()/set()."""

    def __init__(self):
        self.writes = []
        self._versions = {}  # (collection, doc_id) -> updateTime saat dibaca

    @staticmethod
    def new_id():
        return uuid.uuid4().hex

    def get(self, collection, doc_id):
        try:
            raw = _request("GET", _url(f"/{collection}/{doc_id}")).json()
        except FirestoreError as e:
            if e.status == 404:
                return None
            raise
        self._versions[(collection, doc_id)] = raw.get("updateTime")
        return _to_dict(raw)

    def update(self, collection, doc_id, data):
        version = self._versions.get((collection, doc_id))
        precond = {"updateTime": version} if version else {"exists": True}
        self.writes.append({
            "update": {"name": _name(collection, doc_id), "fields": _fields(data)},
            "updateMask": {"fieldPaths": list(data.keys())},
            "currentDocument": precond,
        })

    def set(self, collection, doc_id, data, server_timestamps=()):
        """Buat dokumen baru. server_timestamps = nama field yang diisi waktu server."""
        w = {"update": {"name": _name(collection, doc_id), "fields": _fields(data)},
             "currentDocument": {"exists": False}}
        if server_timestamps:
            w["updateTransforms"] = [{"fieldPath": f, "setToServerValue": "REQUEST_TIME"}
                                     for f in server_timestamps]
        self.writes.append(w)


def run_transaction(fn, retries=3):
    """Jalankan fn(tx). Bila dokumen berubah di tengah jalan (FAILED_PRECONDITION/ABORTED),
    fn dijalankan ulang otomatis. Exception dari fn (mis. stok kurang) membatalkan semuanya."""
    for attempt in range(retries):
        tx = Tx()
        result = fn(tx)
        try:
            _request("POST", _url(":commit"), json={"writes": tx.writes})
            return result
        except FirestoreError as e:
            conflict = e.code in ("FAILED_PRECONDITION", "ABORTED") or e.status == 409
            if conflict and attempt < retries - 1:
                continue
            raise