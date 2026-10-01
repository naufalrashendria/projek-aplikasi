[app]
title = POS Naufal
package.name = posnaufal
package.domain = org.naufal
source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,atlas,ttf
version = 1.0

# requests + firestore_rest.py (REST API) -> tidak butuh firebase-admin/grpc.
# reportlab dipakai untuk struk PDF (opsional di kode Anda, tapi disertakan
# supaya fiturnya tetap jalan di APK).
# cv2, pyzbar, numpy SENGAJA TIDAK dimasukkan: belum stabil di python-for-android
# dan build biasanya gagal di sini. Kode Anda sudah menangani ini dengan
# try/except, jadi app tetap jalan tanpa scan kamera (scan barcode fisik/USB
# via HardwareScanner tetap berfungsi).
requirements = python3,kivy==2.3.0,requests,reportlab,pyjnius,android

orientation = portrait
fullscreen = 0

# Kamera (kalau nanti cv2/pyzbar berhasil ditambahkan) + Bluetooth untuk printer.
android.permissions = INTERNET,CAMERA,BLUETOOTH,BLUETOOTH_ADMIN,BLUETOOTH_CONNECT,BLUETOOTH_SCAN,ACCESS_FINE_LOCATION,ACCESS_COARSE_LOCATION

# WAJIB untuk GoogleSignIn (com.google.android.gms.auth.api.signin.*) yang dipakai
# di main.py. Tanpa ini, class-nya tidak ditemukan saat runtime -> Google Sign-In
# akan crash/gagal di APK meski di kode terlihat baik-baik saja.
android.gradle_dependencies = com.google.android.gms:play-services-auth:20.7.0
android.enable_androidx = True

android.api = 33
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a
android.accept_sdk_license = True

# Ikon & splash (opsional). Hapus 2 baris ini kalau belum punya filenya.
# icon.filename = %(source.dir)s/logo.png
# presplash.filename = %(source.dir)s/logo.png

# --- Signing untuk build RELEASE (wajib sebelum publish / bagi ke kasir lain) ---
# JANGAN taruh path/password keystore langsung di file ini kalau file ini ikut
# di-commit ke Git. Gunakan environment variable saat menjalankan build:
#
#   export P4A_RELEASE_KEYSTORE=/path/aman/posnaufal-release.keystore
#   export P4A_RELEASE_KEYSTORE_PASSWD="passwordAnda"
#   export P4A_RELEASE_KEYALIAS=posnaufal
#   export P4A_RELEASE_KEYALIAS_PASSWD="passwordAnda"
#   buildozer android release
#
# Cara membuat keystore-nya ada di penjelasan chat (langkah 2).

[buildozer]
log_level = 2
warn_on_root = 1
