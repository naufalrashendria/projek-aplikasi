# 🧾 POS Naufal App

Aplikasi **Point of Sale (kasir) mobile** berbasis **Python + Kivy** dengan backend **Firebase (Authentication & Firestore via REST API)**. Mendukung scan barcode (kamera, IP camera, dan alat scan USB/Bluetooth), cetak struk ke printer thermal, serta struk PDF.

Bisa dijalankan di **PC/desktop** (untuk pengembangan dan tes tampilan) dan di-build menjadi **APK Android** lewat Buildozer.

---

## ✨ Fitur

- **Autentikasi** — registrasi dan login email/password, login dengan Google, reset password, ganti password, dan auto-login memakai refresh token tersimpan.
- **Katalog produk** — daftar produk dengan filter kategori, indikator stok (merah bila stok < 5).
- **Manajemen produk** — tambah, ubah, hapus produk (nama, kategori, harga, stok, barcode).
- **Keranjang & checkout** — validasi stok, ubah jumlah, dan checkout dalam **satu transaksi atomik Firestore** (stok tidak akan "nyangkut" terpotong jika terjadi kegagalan).
- **Scan barcode**
  - Kamera bawaan perangkat
  - Kamera HP via WiFi (mis. aplikasi *IP Webcam*)
  - Alat scan fisik USB/Bluetooth (mode *keyboard wedge*, tanpa driver tambahan)
- **Struk**
  - Tampil di layar
  - PDF otomatis (ReportLab)
  - Cetak ke printer thermal **ESC/POS**, kertas **58mm / 80mm**, cetak otomatis setelah checkout
- **Printer** — Bluetooth SPP di Android; di desktop lewat printer Windows (`pywin32`) atau port serial/COM (`pyserial`). Otomatis tersambung ulang ke printer terakhir.
- **Laporan & riwayat** — ringkasan omzet, jumlah transaksi, produk terjual, riwayat transaksi, dan reset laporan.
- **Pengaturan** — nama kasir, nama/alamat toko, lebar kertas, URL IP camera, pemindaian otomatis.
- **Performa** — cache data lokal dan proses jaringan/printer di thread terpisah agar UI tetap lancar.

---

## 🧰 Teknologi

| Komponen | Teknologi |
|---|---|
| UI | [Kivy](https://kivy.org) (KV language) |
| Backend | Firebase Authentication + Cloud Firestore (REST API) |
| Barcode | OpenCV, NumPy, pyzbar |
| PDF | ReportLab |
| Printer | ESC/POS, `pyjnius` (Android Bluetooth), `pywin32`, `pyserial` |
| Build Android | Buildozer |

---

## 📁 Struktur Proyek

```
.
├── main.py              # Aplikasi utama (UI, logika kasir, printer, scanner)
├── firestore_rest.py    # Modul klien Firestore REST (wajib ada)
├── logo.png             # Logo aplikasi & struk
├── buildozer.spec       # Konfigurasi build Android (opsional)
└── struk/               # Hasil PDF struk saat berjalan di desktop (otomatis dibuat)
```

> Pastikan `firestore_rest.py` dan `logo.png` ikut di-commit karena diimpor/dipakai oleh `main.py`.

---

## 🚀 Menjalankan di PC (Desktop)

### 1. Clone repository

```bash
git clone https://github.com/<username>/<nama-repo>.git
cd <nama-repo>
```

### 2. Buat virtual environment (disarankan)

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Linux / macOS
source venv/bin/activate
```

### 3. Install dependensi

```bash
pip install kivy requests
```

Library opsional sesuai fitur yang dibutuhkan:

```bash
pip install opencv-python numpy pyzbar   # scan barcode lewat kamera
pip install reportlab                    # struk PDF
pip install pywin32                      # printer Windows (khusus Windows)
pip install pyserial                     # printer via port COM / serial
```

> Fitur yang library-nya tidak terpasang akan otomatis nonaktif dan menampilkan pesan, bukan membuat aplikasi crash.

### 4. Atur konfigurasi Firebase

Aplikasi membaca konfigurasi dari environment variable:

```bash
# Linux / macOS
export FIREBASE_PROJECT_ID="id-project-firebase-anda"
export FIREBASE_WEB_API_KEY="web-api-key-anda"

# Windows (PowerShell)
$env:FIREBASE_PROJECT_ID="id-project-firebase-anda"
$env:FIREBASE_WEB_API_KEY="web-api-key-anda"
```

Cara mendapatkannya: **Firebase Console → Project settings → General**.

Untuk Google Sign-In, isi `GOOGLE_WEB_CLIENT_ID` di `main.py` dengan **Web Client ID** (bukan Android Client ID) dari Google Cloud Console yang terhubung ke project Firebase Anda.

### 5. Jalankan

```bash
python main.py
```

Di desktop, jendela otomatis berukuran 360×740 untuk mensimulasikan layar HP.

---

## 🔥 Setup Firebase

1. Buat project di [Firebase Console](https://console.firebase.google.com).
2. Aktifkan **Authentication** → metode **Email/Password** (dan **Google** bila dipakai).
3. Buat database **Cloud Firestore**.
4. Aplikasi memakai dua koleksi, dan setiap dokumen memiliki field `users_id` (UID pemilik):
   - `products` — data produk (`nama`, `kategori`, `harga`, `stok`, `barcode`, `users_id`)
   - `transactions` — riwayat penjualan (`users_id`, dan data item transaksi)
5. Atur **Firestore Security Rules** agar pengguna hanya bisa membaca/menulis data miliknya sendiri, contoh:

```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    match /{collection}/{docId} {
      allow create: if request.auth != null
                    && request.resource.data.users_id == request.auth.uid;
      allow read, update, delete: if request.auth != null
                    && resource.data.users_id == request.auth.uid;
    }
  }
}
```

> Sesuaikan rules di atas dengan kebutuhan Anda sebelum dipakai di produksi.

---

## 📱 Build APK Android

Fitur **Bluetooth printer** dan **Google Sign-In** membutuhkan API native Android, sehingga hanya berjalan penuh pada APK hasil build.

1. Install Buildozer (disarankan di Linux/WSL):

   ```bash
   pip install buildozer
   ```

2. Pada `buildozer.spec`, pastikan `requirements` mencakup minimal:

   ```
   requirements = python3,kivy,requests,pyjnius,android,opencv,numpy,pyzbar,reportlab
   ```

   dan permission berikut:

   ```
   android.permissions = INTERNET,CAMERA,BLUETOOTH,BLUETOOTH_ADMIN,BLUETOOTH_CONNECT,BLUETOOTH_SCAN,ACCESS_FINE_LOCATION
   ```

3. Build:

   ```bash
   buildozer -v android debug
   ```

4. Pasang APK dari folder `bin/` ke perangkat Android.

> Konfigurasi di atas adalah titik awal. Sesuaikan dengan `buildozer.spec` milik Anda, terutama recipe untuk OpenCV dan pyzbar.

---

## 🖨️ Printer & Alat Scan

**Printer thermal**
- Android: pairing printer lewat pengaturan Bluetooth, lalu buka **Pengaturan → Printer Struk → Cari & Sambungkan**.
- Desktop: pilih printer Windows atau port serial/COM yang tersedia.
- Gunakan **Tes Cetak** untuk memastikan koneksi, dan atur lebar kertas **58mm** (32 kolom) atau **80mm** (48 kolom).

**Alat scan barcode USB/Bluetooth**
- Sambungkan atau pairing seperti keyboard biasa, lalu aktifkan **Pemindaian otomatis** di Pengaturan.
- Aplikasi membedakan scan dari ketikan manual lewat kecepatan input, sehingga scan bekerja di layar mana pun tanpa perlu menekan kolom input.

**IP Camera (kamera HP via WiFi)**
- Jalankan aplikasi seperti *IP Webcam* di HP, lalu isi URL di Pengaturan, contoh: `http://192.168.1.X:8080/video`.
- Kosongkan URL untuk memakai kamera bawaan.

---

## 🔒 Catatan Keamanan

- **Jangan commit kredensial** (API key, Client ID, file rahasia) ke repository publik. Gunakan environment variable atau file konfigurasi yang masuk `.gitignore`.
- Firebase Web API Key memang bukan rahasia mutlak, tetapi **batasi penggunaannya** (API restrictions/App restrictions di Google Cloud Console) dan **wajib** mengamankan data dengan Firestore Security Rules.
- Sesi login disimpan lokal di `session.json` (refresh token) pada direktori data aplikasi.

---

## 🤝 Kontribusi

Pull request dan issue sangat diterima. Untuk perubahan besar, silakan buka issue terlebih dahulu untuk didiskusikan.

## 📄 Lisensi

Tentukan lisensi proyek Anda (mis. MIT) dan tambahkan file `LICENSE`.

## 👤 Pembuat

**Naufal** — Kediri, Jawa Timur