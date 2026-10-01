<<<<<<< HEAD
import os
import sys
import json
import time
import socket
import shutil
import textwrap
import threading
import subprocess
import unicodedata
import requests
from urllib.parse import urlparse, urlunparse
import datetime
import firestore_rest as fs

from kivy.app import App
from kivy.lang import Builder
from kivy.uix.screenmanager import ScreenManager, Screen, SlideTransition, FadeTransition, NoTransition
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.spinner import Spinner
from kivy.uix.popup import Popup
from kivy.uix.image import Image
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget
from kivy.properties import ListProperty, NumericProperty, StringProperty, ObjectProperty, BooleanProperty
from kivy.clock import Clock
from kivy.graphics.texture import Texture
from kivy.graphics import Color, RoundedRectangle
from kivy.core.window import Window
from kivy.factory import Factory
from kivy.animation import Animation
from kivy.storage.jsonstore import JsonStore
from kivy.utils import platform
from kivy.metrics import dp

# --- DETEKSI PLATFORM ANDROID (untuk Bluetooth & Google Sign-In) ---
# Fitur Bluetooth printer & Google Sign-In hanya berjalan penuh saat di-build
# menjadi APK Android (lewat buildozer), karena keduanya butuh API native Android.
# Saat dijalankan di PC/desktop untuk pengetesan tampilan, kedua fitur ini akan
# menampilkan pesan "tidak tersedia di perangkat ini" alih-alih error/crash.
ANDROID = (platform == 'android')

HAS_ANDROID_BT = False
HAS_ANDROID_GSI = False

if ANDROID:
    try:
        from jnius import autoclass, cast
        from android import activity, mActivity
        from android.permissions import request_permissions, check_permission, Permission
        HAS_ANDROID_BT = True
        HAS_ANDROID_GSI = True
    except Exception as e:
        print("Modul Android (pyjnius) tidak lengkap:", e)

# --- SIMULASI WINDOW UKURAN HP (hanya saat jalan di PC) ---
if not ANDROID:
    Window.size = (360, 740)

# --- HARDWARE & PDF INTEGRATION ---
try:
    import cv2
    import numpy as np
    from pyzbar import pyzbar
    HAS_BARCODE = True
except ImportError:
    HAS_BARCODE = False

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

# --- PRINTER DESKTOP (opsional) ---
# pywin32  : printer yang ter-install di Windows (USB/Bluetooth)   -> pip install pywin32
# pyserial : printer lewat port COM / serial / Bluetooth SPP       -> pip install pyserial
HAS_WIN32PRINT = False
HAS_SERIAL = False
if not ANDROID:
    try:
        import win32print
        HAS_WIN32PRINT = True
    except ImportError:
        pass
    try:
        import serial
        from serial.tools import list_ports as serial_ports
        HAS_SERIAL = True
    except ImportError:
        pass

# --- FIREBASE SETUP (REST API, tanpa firebase_admin) ---
# Project ID: Firebase Console > Project settings > General > Project ID
FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "kasir-mobile-app")

FIREBASE_WEB_API_KEY = os.environ.get("FIREBASE_WEB_API_KEY", "AIzaSyCVnHYAdmAevjNaN2uCmO0bqz4cPcDmjI0")

# Ganti dengan Web Client ID (bukan Android Client ID) dari Google Cloud Console
# yang sudah dihubungkan sebagai OAuth Client ke project Firebase ini.
GOOGLE_WEB_CLIENT_ID = "1021803262867-l65002t15lh51lugl1u1p2fqg2m7sos7.apps.googleusercontent.com"

SPP_UUID = "00001101-0000-1000-8000-00805F9B34FB"  # UUID standar Serial Port Profile

DEFAULT_STORE_NAME = "POS NAUFAL APP"
DEFAULT_STORE_ADDRESS = "Jl. Veteran No.99, Kediri, Jawa Timur"


def rupiah(value):
    """Format angka ala Indonesia: 1500000 -> '1.500.000'."""
    try:
        return format(int(value), ',').replace(',', '.')
    except (TypeError, ValueError):
        return str(value)


def run_in_thread(work, on_done=None):
    """Jalankan work() di thread terpisah supaya UI tidak macet (koneksi printer bisa lambat).
    on_done(result, error) dipanggil kembali di thread utama Kivy."""
    def runner():
        result, error = None, None
        try:
            result = work()
        except Exception as e:  # noqa: BLE001
            error = e
        if on_done:
            Clock.schedule_once(lambda dt: on_done(result, error), 0)

    threading.Thread(target=runner, daemon=True).start()


CACHE_MAX_AGE = 20  # detik: data produk/laporan yang lebih baru dari ini tidak diambil ulang


def products_signature(products):
    """Ringkasan isi daftar produk, untuk mendeteksi apakah data berubah (dan perlu digambar ulang)."""
    return tuple((p.get('doc_id'), p.get('nama'), p.get('harga'), p.get('stok'),
                  p.get('kategori'), p.get('barcode')) for p in products)


def show_popup(title, message, kind='info'):
    """Popup pesan bergaya seragam: teks otomatis membungkus, tinggi menyesuaikan isi,
    ada tombol OK. kind: 'ok' (hijau) | 'error' (merah) | 'warn' (kuning) | 'info'."""
    colors = {'ok': (0.063, 0.725, 0.506, 1), 'error': (0.937, 0.267, 0.267, 1),
              'warn': (0.937, 0.675, 0.208, 1), 'info': (0.95, 0.96, 0.98, 1)}
    content = BoxLayout(orientation='vertical', padding=dp(14), spacing=dp(12))
    lbl = Label(text=message, halign='center', valign='middle', font_size='13sp',
                color=(0.95, 0.96, 0.98, 1), size_hint_y=None)
    lbl.bind(width=lambda w, v: setattr(w, 'text_size', (v, None)))
    lbl.bind(texture_size=lambda w, ts: setattr(w, 'height', ts[1]))
    btn = Factory.PrimaryButton(text='OK', size_hint_y=None, height=dp(44))
    content.add_widget(lbl)
    content.add_widget(btn)

    popup = Popup(title=title, title_color=colors.get(kind, colors['info']),
                  content=content, size_hint=(0.86, None), height=dp(200))

    def fit(*_):
        popup.height = min(lbl.height + dp(150), Window.height * 0.9)

    lbl.bind(height=fit)
    btn.bind(on_press=popup.dismiss)
    popup.open()
    return popup


# ---------------------------------------------------------------------------
# ALAT SCAN BARCODE FISIK (USB / Bluetooth) - mode "keyboard wedge"
# ---------------------------------------------------------------------------
class HardwareScanner:
    """
    Hampir semua alat scan barcode USB atau Bluetooth (termasuk yang di-pairing
    lewat Pengaturan Bluetooth Windows/Android) bekerja sebagai "keyboard wedge":
    alat itu berpura-pura jadi keyboard, mengetikkan karakter barcode lalu
    menekan Enter. Karena itu TIDAK butuh driver atau library tambahan - cukup
    disambungkan/di-pairing di OS seperti keyboard biasa.

    Class ini mendengarkan ketikan dari perangkat manapun di seluruh aplikasi
    (bukan cuma satu kolom input), lalu membedakan scan asli dari ketikan
    manual memakai kecepatan: alat scan mengetik jauh lebih cepat daripada
    manusia (umumnya < 30ms per karakter untuk kode 8-13 digit).
    """
    MAX_GAP = 0.05    # jeda maksimal antar karakter agar dianggap satu scan (detik)
    MIN_LENGTH = 4    # panjang kode minimal supaya ketikan manual tidak salah terdeteksi

    def __init__(self, on_scan):
        self.on_scan = on_scan
        self.enabled = True
        self._buffer = ''
        self._last_time = 0
        Window.bind(on_textinput=self._on_textinput)
        Window.bind(on_key_down=self._on_key_down)

    def _on_textinput(self, window, text):
        now = time.time()
        if now - self._last_time > self.MAX_GAP:
            self._buffer = ''
        self._buffer += text
        self._last_time = now

    def _on_key_down(self, window, key, scancode, codepoint, modifiers):
        if key not in (13, 271):  # Enter (utama / numpad)
            return
        now = time.time()
        code, gap_ok = self._buffer, (now - self._last_time) <= self.MAX_GAP
        self._buffer = ''
        if self.enabled and gap_ok and len(code) >= self.MIN_LENGTH:
            self.on_scan(code)


# ---------------------------------------------------------------------------
# PEMBUAT STRUK (dipakai bersama oleh layar, printer thermal, dan tes cetak)
# ---------------------------------------------------------------------------
class ReceiptBuilder:
    """
    Menyusun isi struk dalam lebar kolom tetap (32 kolom = kertas 58mm,
    48 kolom = kertas 80mm), lalu bisa dirender jadi teks biasa (untuk layar)
    atau byte ESC/POS (untuk printer thermal).
    """
    PAPER_COLUMNS = {'58mm': 32, '80mm': 48}
    ESC = b'\x1b'
    GS = b'\x1d'

    def __init__(self, store_name, store_address, paper='58mm'):
        self.store_name = store_name or DEFAULT_STORE_NAME
        self.store_address = store_address or ''
        self.cols = self.PAPER_COLUMNS.get(paper, 32)

    @staticmethod
    def _ascii(text):
        # Printer thermal umumnya tidak paham UTF-8; buang aksen/karakter khusus.
        norm = unicodedata.normalize('NFKD', str(text))
        return norm.encode('ascii', 'ignore').decode('ascii')

    def _wrap(self, text):
        return textwrap.wrap(self._ascii(text), self.cols) or ['']

    def _two_col(self, left, right):
        left, right = self._ascii(left), self._ascii(right)
        gap = self.cols - len(left) - len(right)
        if gap >= 1:
            return [left + ' ' * gap + right]
        return [left, right.rjust(self.cols)]

    def lines(self, kasir_name, now_dt, items, total):
        """Kembalikan list (align, bold, teks). align: 'c' tengah, 'l' kiri."""
        rule = '-' * self.cols
        out = []
        for ln in self._wrap(self.store_name):
            out.append(('c', True, ln))
        if self.store_address:
            for ln in self._wrap(self.store_address):
                out.append(('c', False, ln))
        out.append(('l', False, rule))
        for ln in self._wrap(f"Kasir : {kasir_name}"):
            out.append(('l', False, ln))
        out.append(('l', False, f"Tgl   : {now_dt.strftime('%d/%m/%Y %H:%M:%S')}"))
        out.append(('l', False, rule))

        for item in items:
            qty, harga = int(item['qty']), int(item['harga'])
            for ln in self._wrap(item['nama']):
                out.append(('l', False, ln))
            for ln in self._two_col(f"  {qty} x {rupiah(harga)}", rupiah(qty * harga)):
                out.append(('l', False, ln))

        out.append(('l', False, rule))
        for ln in self._two_col("TOTAL", f"Rp {rupiah(total)}"):
            out.append(('l', True, ln))
        out.append(('l', False, rule))
        out.append(('c', False, 'Terima Kasih!'))
        return out

    def as_text(self, kasir_name, now_dt, items, total):
        rows = []
        for align, _bold, text in self.lines(kasir_name, now_dt, items, total):
            rows.append(text.center(self.cols).rstrip() if align == 'c' else text)
        return '\n'.join(rows)

    def as_escpos(self, kasir_name, now_dt, items, total):
        ESC, GS = self.ESC, self.GS
        buf = bytearray(ESC + b'@')  # init printer
        for align, bold, text in self.lines(kasir_name, now_dt, items, total):
            buf += ESC + b'a' + (b'\x01' if align == 'c' else b'\x00')
            buf += ESC + b'E' + (b'\x01' if bold else b'\x00')
            buf += text.encode('ascii', 'ignore') + b'\n'
        buf += ESC + b'E\x00' + ESC + b'a\x00'
        buf += b'\n\n\n\n' + GS + b'V\x42\x00'  # feed + potong kertas (diabaikan jika tak ada cutter)
        return bytes(buf)


# ---------------------------------------------------------------------------
# PRINTER
# ---------------------------------------------------------------------------
class BasePrinter:
    """Antarmuka umum: Bluetooth (Android) dan Desktop (Windows/Linux/macOS)."""
    kind = 'base'

    def __init__(self):
        self._lock = threading.Lock()

    def is_supported(self):
        return False

    def is_connected(self):
        return False

    def disconnect(self):
        pass

    def send(self, data: bytes):
        raise NotImplementedError

    def print_bytes(self, data: bytes):
        """Kirim byte ESC/POS ke printer. Aman dipanggil dari thread. Return (ok, pesan)."""
        if not self.is_connected():
            return False, "Printer belum terhubung. Sambungkan dulu lewat menu Akun."
        try:
            with self._lock:
                self.send(data)
            return True, "Struk berhasil dicetak."
        except Exception as e:  # noqa: BLE001
            return False, f"Gagal mencetak: {e}"


class BluetoothPrinterManager(BasePrinter):
    """
    Printer struk thermal Bluetooth (ESC/POS) lewat profil Serial Port (SPP).
    Hanya aktif di Android (pakai pyjnius).
    """
    kind = 'bluetooth'
    CHUNK = 512

    def __init__(self):
        super().__init__()
        self.socket = None
        self.output_stream = None
        self.device_name = None
        self.device_address = None

    def is_supported(self):
        return HAS_ANDROID_BT

    def has_permissions(self):
        if not HAS_ANDROID_BT:
            return False
        try:
            needed = [Permission.BLUETOOTH_CONNECT, Permission.BLUETOOTH_SCAN]
            return all(check_permission(p) for p in needed)
        except Exception:
            return True  # Android lama: izin diberikan saat instalasi

    def request_permissions(self, callback=None):
        if not HAS_ANDROID_BT:
            return
        try:
            perms = [Permission.BLUETOOTH_CONNECT, Permission.BLUETOOTH_SCAN,
                     Permission.ACCESS_FINE_LOCATION]
            request_permissions(perms, callback)
        except Exception as e:
            print("Gagal minta izin bluetooth:", e)

    def list_paired_devices(self):
        """List tuple (nama, alamat_mac) perangkat yang sudah di-pairing."""
        if not HAS_ANDROID_BT:
            return []
        try:
            BluetoothAdapter = autoclass('android.bluetooth.BluetoothAdapter')
            adapter = BluetoothAdapter.getDefaultAdapter()
            if adapter is None:
                return []
            return [(d.getName(), d.getAddress()) for d in adapter.getBondedDevices().toArray()]
        except Exception as e:
            print("Gagal ambil daftar perangkat bluetooth:", e)
            return []

    def connect(self, address):
        if not HAS_ANDROID_BT:
            return False, "Bluetooth hanya tersedia di aplikasi Android."
        self.disconnect()
        try:
            BluetoothAdapter = autoclass('android.bluetooth.BluetoothAdapter')
            UUID = autoclass('java.util.UUID')
            adapter = BluetoothAdapter.getDefaultAdapter()
            device = adapter.getRemoteDevice(address)
            uuid = UUID.fromString(SPP_UUID)
            adapter.cancelDiscovery()

            try:
                sock = device.createRfcommSocketToServiceRecord(uuid)
                sock.connect()
            except Exception:
                # Sebagian printer murah hanya mau lewat soket "insecure"
                sock = device.createInsecureRfcommSocketToServiceRecord(uuid)
                sock.connect()

            self.socket = sock
            self.output_stream = sock.getOutputStream()
            self.device_address = address
            self.device_name = device.getName()
            return True, f"Terhubung ke {self.device_name}"
        except Exception as e:
            self.socket = None
            self.output_stream = None
            return False, f"Gagal konek printer: {e}"

    def disconnect(self):
        try:
            if self.socket is not None:
                self.socket.close()
        except Exception:
            pass
        self.socket = None
        self.output_stream = None

    def is_connected(self):
        try:
            return self.output_stream is not None and self.socket is not None and bool(self.socket.isConnected())
        except Exception:
            return self.output_stream is not None

    def _write_chunks(self, data):
        if self.output_stream is None:
            raise RuntimeError("Printer belum terhubung.")
        # Dikirim per potongan kecil: buffer printer Bluetooth kecil, data besar bisa terpotong.
        for i in range(0, len(data), self.CHUNK):
            self.output_stream.write(data[i:i + self.CHUNK])
            self.output_stream.flush()
            time.sleep(0.02)

    def send(self, data):
        try:
            self._write_chunks(data)
        except Exception:
            # Koneksi mungkin terputus (printer sempat mati/sleep): sambung ulang sekali.
            addr = self.device_address
            if not addr:
                raise
            ok, msg = self.connect(addr)
            if not ok:
                raise RuntimeError(msg)
            self._write_chunks(data)


class DesktopPrinterManager(BasePrinter):
    """
    Printer struk thermal ESC/POS dari laptop/PC. Mode yang didukung:
      - windows : printer yang sudah ter-install di Windows (USB / Bluetooth) - butuh pywin32
      - cups    : printer CUPS di Linux/macOS (lewat perintah `lp`)
      - network : printer LAN/WiFi lewat TCP port 9100 (IP:9100)
      - serial  : port serial/COM (printer Bluetooth SPP yang di-pair biasanya jadi COM port) - butuh pyserial
    """
    kind = 'desktop'

    HINTS = {
        'windows': "Pilih printer yang sudah ter-install di Windows\n(USB / Bluetooth). Kirim data mentah (RAW).",
        'cups': "Pilih nama printer CUPS (Linux / macOS).",
        'network': "Ketik IP printer, contoh: 192.168.1.50\natau 192.168.1.50:9100",
        'serial': "Pilih port. Printer Bluetooth yang sudah di-pair\nbiasanya muncul sebagai port COM.",
    }

    def __init__(self):
        super().__init__()
        self.mode = None
        self.target = ''
        self.baud = 9600
        self._connected = False

    def is_supported(self):
        return True

    def is_connected(self):
        return self._connected

    def disconnect(self):
        self._connected = False

    def available_modes(self):
        modes = []
        if HAS_WIN32PRINT:
            modes.append(('windows', 'Printer Windows (USB / Bluetooth)'))
        if shutil.which('lp'):
            modes.append(('cups', 'Printer CUPS (Linux / macOS)'))
        modes.append(('network', 'Jaringan LAN / WiFi (IP:9100)'))
        if HAS_SERIAL:
            modes.append(('serial', 'Port Serial / COM (Bluetooth)'))
        return modes

    def list_targets(self, mode):
        """List (nilai, label) target yang terdeteksi untuk mode tertentu."""
        try:
            if mode == 'windows' and HAS_WIN32PRINT:
                flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
                return [(p[2], p[2]) for p in win32print.EnumPrinters(flags)]
            if mode == 'cups' and shutil.which('lpstat'):
                out = subprocess.run(['lpstat', '-e'], capture_output=True, text=True, timeout=5).stdout
                return [(n.strip(), n.strip()) for n in out.splitlines() if n.strip()]
            if mode == 'serial' and HAS_SERIAL:
                return [(p.device, f"{p.device}  {p.description}") for p in serial_ports.comports()]
        except Exception as e:  # noqa: BLE001
            print("Gagal mendeteksi printer:", e)
        return []

    @staticmethod
    def _parse_host(target):
        host, _, port = target.partition(':')
        return host.strip(), int(port) if port.strip() else 9100

    def connect(self, mode, target):
        target = (target or '').strip()
        if not target:
            return False, "Pilih atau isi nama/alamat printer dulu."
        try:
            if mode == 'network':
                host, port = self._parse_host(target)
                with socket.create_connection((host, port), timeout=4):
                    pass
            elif mode == 'serial':
                if not HAS_SERIAL:
                    return False, "Library pyserial belum terpasang (pip install pyserial)."
                serial.Serial(target, self.baud, timeout=2).close()
            elif mode == 'windows':
                if not HAS_WIN32PRINT:
                    return False, "Library pywin32 belum terpasang (pip install pywin32)."
                win32print.ClosePrinter(win32print.OpenPrinter(target))
            elif mode == 'cups':
                if not shutil.which('lp'):
                    return False, "Perintah 'lp' (CUPS) tidak ditemukan."
            else:
                return False, "Mode printer tidak dikenal."
        except Exception as e:  # noqa: BLE001
            self._connected = False
            return False, f"Gagal terhubung ke printer: {e}"

        self.mode, self.target, self._connected = mode, target, True
        return True, f"Terhubung: {target}"

    def send(self, data):
        if self.mode == 'windows':
            handle = win32print.OpenPrinter(self.target)
            try:
                win32print.StartDocPrinter(handle, 1, ("Struk POS", None, "RAW"))
                try:
                    win32print.StartPagePrinter(handle)
                    win32print.WritePrinter(handle, data)
                    win32print.EndPagePrinter(handle)
                finally:
                    win32print.EndDocPrinter(handle)
            finally:
                win32print.ClosePrinter(handle)
        elif self.mode == 'cups':
            subprocess.run(['lp', '-d', self.target, '-o', 'raw'], input=data,
                           check=True, capture_output=True, timeout=20)
        elif self.mode == 'network':
            host, port = self._parse_host(self.target)
            with socket.create_connection((host, port), timeout=5) as s:
                s.sendall(data)
        elif self.mode == 'serial':
            with serial.Serial(self.target, self.baud, timeout=3, write_timeout=5) as ser:
                ser.write(data)
                ser.flush()
        else:
            raise RuntimeError("Printer belum dikonfigurasi.")


class GoogleSignInHelper:
    """
    Membungkus alur Google Sign-In native Android, lalu menukar id_token Google
    tersebut ke Firebase lewat endpoint REST accounts:signInWithIdp.
    """
    RC_SIGN_IN = 9001

    def __init__(self):
        self._callback = None
        if HAS_ANDROID_GSI:
            try:
                activity.bind(on_activity_result=self._on_activity_result)
            except Exception as e:
                print("Gagal bind activity result:", e)

    def is_supported(self):
        return HAS_ANDROID_GSI

    def sign_in(self, on_result):
        """on_result(success: bool, data_or_error)"""
        self._callback = on_result

        if not HAS_ANDROID_GSI:
            on_result(False, "Google Sign-In hanya tersedia di aplikasi Android.")
            return

        try:
            GoogleSignInOptions = autoclass('com.google.android.gms.auth.api.signin.GoogleSignInOptions')
            GoogleSignInOptionsBuilder = autoclass('com.google.android.gms.auth.api.signin.GoogleSignInOptions$Builder')
            GoogleSignIn = autoclass('com.google.android.gms.auth.api.signin.GoogleSignIn')
            DEFAULT_SIGN_IN = GoogleSignInOptions.DEFAULT_SIGN_IN

            gso_builder = GoogleSignInOptionsBuilder(DEFAULT_SIGN_IN)
            gso_builder.requestIdToken(GOOGLE_WEB_CLIENT_ID)
            gso_builder.requestEmail()
            gso = gso_builder.build()

            client = GoogleSignIn.getClient(mActivity, gso)
            intent = client.getSignInIntent()
            mActivity.startActivityForResult(intent, self.RC_SIGN_IN)
        except Exception as e:
            on_result(False, f"Gagal membuka Google Sign-In: {e}")

    def _on_activity_result(self, request_code, result_code, intent):
        if request_code != self.RC_SIGN_IN or self._callback is None:
            return
        try:
            GoogleSignIn = autoclass('com.google.android.gms.auth.api.signin.GoogleSignIn')
            task = GoogleSignIn.getSignedInAccountFromIntent(intent)
            ApiException = autoclass('com.google.android.gms.common.api.ApiException')
            account = task.getResult(ApiException)
            id_token = account.getIdToken()
            self._exchange_with_firebase(id_token)
        except Exception as e:
            self._callback(False, f"Login Google dibatalkan atau gagal: {e}")

    def _exchange_with_firebase(self, google_id_token):
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithIdp?key={FIREBASE_WEB_API_KEY}"
        post_body = f"id_token={google_id_token}&providerId=google.com"
        payload = {
            "postBody": post_body,
            "requestUri": "http://localhost",
            "returnIdpCredential": True,
            "returnSecureToken": True
        }
        try:
            res = requests.post(url, json=payload, timeout=15)
            data = res.json()
            if "error" in data:
                self._callback(False, data["error"]["message"])
            else:
                self._callback(True, data)
        except Exception as e:
            self._callback(False, f"Koneksi ke server gagal: {e}")


# --- PALET WARNA (dipakai kode Python; versi KV ada di `#:set` di bawah) ---
CLR_BG = (0.059, 0.09, 0.165, 1)
CLR_SURFACE = (0.118, 0.161, 0.231, 1)
CLR_SURFACE2 = (0.18, 0.23, 0.32, 1)
CLR_PRIMARY = (0.231, 0.51, 0.965, 1)
CLR_PRIMARY_DARK = (0.18, 0.4, 0.78, 1)
CLR_SUCCESS = (0.063, 0.725, 0.506, 1)
CLR_DANGER = (0.937, 0.267, 0.267, 1)
CLR_WARN = (0.937, 0.675, 0.208, 1)
CLR_TEXT = (0.95, 0.96, 0.98, 1)
CLR_MUTED = (0.5, 0.57, 0.67, 1)


# --- WIDGET DASAR UI ---
class AppScreen(Screen):
    """Dasar semua layar: latar belakang seragam (diatur di KV)."""


class TopBar(BoxLayout):
    """Bilah atas berlatar permukaan + garis pemisah di bawahnya."""


class ScreenHeader(TopBar):
    """TopBar dengan judul di kiri. Widget anak yang ditambahkan di KV muncul di kanan judul."""
    title = StringProperty('')


class RoundedButton(Button):
    """Tombol sudut membulat. Warna diatur lewat bg_color / bg_color_down."""
    bg_color = ListProperty(list(CLR_PRIMARY))
    bg_color_down = ListProperty(list(CLR_PRIMARY_DARK))
    corner_radius = NumericProperty(dp(10))


class NavItem(Button):
    """Satu tab pada bilah navigasi bawah."""
    active = BooleanProperty(False)


class ToggleRow(BoxLayout):
    """Baris 'teks + saklar'. Pakai:  ToggleRow: text: '...'; active: ...; on_toggle: fn(args[1])"""
    text = StringProperty('')
    active = BooleanProperty(False)
    __events__ = ('on_toggle',)

    def on_toggle(self, value):
        pass


# --- KIVY UI STYLING ---
KV = '''
#:import Window kivy.core.window.Window
#:import dp kivy.metrics.dp

#:set C_BG [0.059, 0.09, 0.165, 1]
#:set C_SURFACE [0.118, 0.161, 0.231, 1]
#:set C_SURFACE2 [0.18, 0.23, 0.32, 1]
#:set C_BORDER [0.2, 0.27, 0.38, 0.7]
#:set C_PRIMARY [0.231, 0.51, 0.965, 1]
#:set C_PRIMARY_DARK [0.18, 0.4, 0.78, 1]
#:set C_PRIMARY_SOFT [0.231, 0.51, 0.965, 0.16]
#:set C_SUCCESS [0.063, 0.725, 0.506, 1]
#:set C_DANGER [0.937, 0.267, 0.267, 1]
#:set C_WARN [0.937, 0.675, 0.208, 1]
#:set C_TEXT [0.95, 0.96, 0.98, 1]
#:set C_MUTED [0.5, 0.57, 0.67, 1]
#:set C_DISABLED [0.22, 0.27, 0.36, 1]

# ---------------------------------------------------------------------------
# DASAR: layar, popup, label
# ---------------------------------------------------------------------------
<AppScreen>:
    canvas.before:
        Color:
            rgba: C_BG
        Rectangle:
            pos: self.pos
            size: self.size

<Popup>:
    title_color: C_TEXT
    title_size: '15sp'
    separator_color: C_PRIMARY
    separator_height: dp(1.4)
    background: ''
    background_color: 0, 0, 0, 0
    overlay_color: 0.02, 0.03, 0.07, 0.72
    canvas.before:
        Color:
            rgba: C_SURFACE
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(18)]
        Color:
            rgba: C_BORDER
        Line:
            width: dp(1.2)
            rounded_rectangle: [self.x, self.y, self.width, self.height, dp(18)]

<LeftLabel@Label>:
    color: C_TEXT
    halign: 'left'
    valign: 'middle'
    text_size: self.size

<RightLabel@Label>:
    color: C_TEXT
    halign: 'right'
    valign: 'middle'
    text_size: self.size

<MutedLabel@LeftLabel>:
    color: C_MUTED
    font_size: '12sp'

<FieldLabel@MutedLabel>:
    size_hint_y: None
    height: dp(18)

<SectionTitle@LeftLabel>:
    size_hint_y: None
    height: dp(26)
    bold: True
    font_size: '15sp'

<HintLabel@Label>:
    size_hint_y: None
    height: self.texture_size[1] + dp(2)
    text_size: self.width, None
    halign: 'left'
    valign: 'top'
    font_size: '11sp'
    color: C_MUTED

<StatusChip@Label>:
    size_hint_y: None
    height: dp(36)
    font_size: '12sp'
    bold: True
    halign: 'left'
    valign: 'middle'
    padding: [dp(12), 0]
    text_size: self.size
    shorten: True
    shorten_from: 'right'
    canvas.before:
        Color:
            rgba: C_BG
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(8),]

<Divider@Widget>:
    size_hint_y: None
    height: dp(1)
    canvas:
        Color:
            rgba: C_BORDER
        Rectangle:
            pos: self.pos
            size: self.size

# ---------------------------------------------------------------------------
# KARTU
# ---------------------------------------------------------------------------
<CustomCard@BoxLayout>:
    orientation: 'vertical'
    padding: dp(12)
    spacing: dp(6)
    size_hint_y: None
    height: dp(165)
    canvas.before:
        Color:
            rgba: C_SURFACE
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(14),]
        Color:
            rgba: C_BORDER
        Line:
            rounded_rectangle: (self.x, self.y, self.width, self.height, dp(14))
            width: 1

<SectionCard@CustomCard>:
    padding: dp(16)
    spacing: dp(10)
    height: self.minimum_height

# ---------------------------------------------------------------------------
# TOMBOL
# ---------------------------------------------------------------------------
<RoundedButton>:
    background_normal: ''
    background_down: ''
    background_disabled_normal: ''
    background_disabled_down: ''
    background_color: 0, 0, 0, 0
    color: 1, 1, 1, 1
    disabled_color: 1, 1, 1, 0.45
    bold: True
    font_size: '13sp'
    halign: 'center'
    valign: 'middle'
    canvas.before:
        Color:
            rgba: C_DISABLED if self.disabled else (self.bg_color_down if self.state == 'down' else self.bg_color)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [self.corner_radius,]

<PrimaryButton@RoundedButton>:
    bg_color: C_PRIMARY
    bg_color_down: C_PRIMARY_DARK

<SecondaryButton@RoundedButton>:
    bg_color: C_SURFACE2
    bg_color_down: [0.25, 0.31, 0.42, 1]

<SuccessButton@RoundedButton>:
    bg_color: C_SUCCESS
    bg_color_down: [0.05, 0.6, 0.42, 1]

<DangerButton@RoundedButton>:
    bg_color: C_DANGER
    bg_color_down: [0.75, 0.2, 0.2, 1]

<WarnButton@RoundedButton>:
    bg_color: C_WARN
    bg_color_down: [0.8, 0.56, 0.15, 1]
    color: 0.1, 0.1, 0.1, 1

<GhostButton@RoundedButton>:
    bg_color: [0, 0, 0, 0]
    bg_color_down: [1, 1, 1, 0.08]
    color: C_PRIMARY
    bold: False
    font_size: '12sp'

<GoogleButton@RoundedButton>:
    bg_color: [1, 1, 1, 1]
    bg_color_down: [0.88, 0.88, 0.88, 1]
    color: C_BG

# ---------------------------------------------------------------------------
# INPUT
# ---------------------------------------------------------------------------
<ModernTextInput@TextInput>:
    multiline: False
    size_hint_y: None
    height: dp(46)
    background_normal: ''
    background_active: ''
    background_disabled_normal: ''
    background_color: C_SURFACE
    foreground_color: C_TEXT
    hint_text_color: C_MUTED
    cursor_color: C_PRIMARY
    selection_color: [0.231, 0.51, 0.965, 0.35]
    font_size: '14sp'
    padding: [dp(14), dp(13), dp(14), dp(13)]
    write_tab: False
    canvas.after:
        Color:
            rgba: C_PRIMARY if self.focus else C_BORDER
        Line:
            rounded_rectangle: (self.x, self.y, self.width, self.height, dp(8))
            width: 1.2 if self.focus else 1

<ModernSpinnerOption@SpinnerOption>:
    background_normal: ''
    background_down: ''
    background_color: C_SURFACE2 if self.state == 'normal' else C_PRIMARY_DARK
    color: C_TEXT
    font_size: '13sp'
    size_hint_y: None
    height: dp(42)

<ModernSpinner@Spinner>:
    option_cls: 'ModernSpinnerOption'
    background_normal: ''
    background_down: ''
    background_color: 0, 0, 0, 0
    color: C_TEXT
    font_size: '13sp'
    halign: 'left'
    valign: 'middle'
    text_size: self.size
    padding: [dp(14), 0]
    canvas.before:
        Color:
            rgba: C_SURFACE
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(8),]
        Color:
            rgba: C_BORDER
        Line:
            rounded_rectangle: (self.x, self.y, self.width, self.height, dp(8))
            width: 1

# ---------------------------------------------------------------------------
# BILAH ATAS, NAVIGASI, BARIS SAKLAR
# ---------------------------------------------------------------------------
<TopBar>:
    size_hint_y: None
    height: dp(60)
    padding: [dp(16), dp(8)]
    spacing: dp(10)
    canvas.before:
        Color:
            rgba: C_SURFACE
        Rectangle:
            pos: self.pos
            size: self.size
        Color:
            rgba: C_BORDER
        Line:
            points: [self.x, self.y, self.right, self.y]
            width: 1

<ScreenHeader>:
    height: dp(56)
    LeftLabel:
        text: root.title
        bold: True
        font_size: '18sp'

<NavItem>:
    background_normal: ''
    background_down: ''
    background_color: 0, 0, 0, 0
    font_size: '11sp'
    halign: 'center'
    bold: self.active
    color: C_PRIMARY if self.active else C_MUTED
    canvas.before:
        Color:
            rgba: C_PRIMARY_SOFT if self.active else [0, 0, 0, 0]
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(10),]

<NavBar@BoxLayout>:
    size_hint_y: None
    height: dp(60)
    spacing: dp(4)
    padding: [dp(6), dp(6)]
    canvas.before:
        Color:
            rgba: C_SURFACE
        Rectangle:
            pos: self.pos
            size: self.size
        Color:
            rgba: C_BORDER
        Line:
            points: [self.x, self.top, self.right, self.top]
            width: 1

    NavItem:
        text: 'Katalog'
        active: app.current_screen == 'catalog'
        on_press: app.root.current = 'catalog'

    NavItem:
        text: f'Keranjang\\n({len(app.cart)})' if app.cart else 'Keranjang'
        active: app.current_screen == 'cart'
        on_press: app.root.current = 'cart'

    NavItem:
        text: 'Kelola'
        active: app.current_screen == 'product_edit'
        on_press: app.root.current = 'product_edit'

    NavItem:
        text: 'Laporan'
        active: app.current_screen == 'report'
        on_press: app.root.current = 'report'

    NavItem:
        text: 'Akun'
        active: app.current_screen == 'settings'
        on_press: app.root.current = 'settings'

<ToggleRow>:
    size_hint_y: None
    height: dp(44)
    spacing: dp(8)
    LeftLabel:
        text: root.text
        font_size: '13sp'
    Switch:
        size_hint_x: None
        width: dp(90)
        active: root.active
        on_active: root.dispatch('on_toggle', self.active)

# ---------------------------------------------------------------------------
# LAYAR
# ---------------------------------------------------------------------------
ScreenManager:
    id: screen_manager
    LoginScreen:
    CatalogScreen:
    CartScreen:
    ProductEditScreen:
    ReportScreen:
    HistoryScreen:
    SettingsScreen:
    ScannerScreen:

<LoginScreen>:
    name: 'login'

    BoxLayout:
        orientation: 'vertical'
        padding: [dp(28), dp(24)]
        spacing: dp(12)

        Widget:
            size_hint_y: 0.06

        Image:
            source: 'logo.png'
            size_hint_y: None
            height: dp(88)
            allow_stretch: True
            keep_ratio: True

        Label:
            text: 'POS NAUFAL APP'
            font_size: '24sp'
            bold: True
            color: C_PRIMARY
            size_hint_y: None
            height: dp(36)

        Label:
            id: mode_title
            text: 'Silakan masuk ke akun Anda'
            font_size: '13sp'
            color: C_MUTED
            size_hint_y: None
            height: dp(20)

        Widget:
            size_hint_y: 0.03

        ModernTextInput:
            id: email_input
            hint_text: 'Email Kasir / Toko'

        ModernTextInput:
            id: password_input
            hint_text: 'Password'
            password: True

        PrimaryButton:
            id: btn_submit
            text: 'MASUK SEKARANG'
            size_hint_y: None
            height: dp(48)
            on_press: root.do_auth()

        BoxLayout:
            size_hint_y: None
            height: dp(24)
            spacing: dp(10)
            Widget:
                canvas:
                    Color:
                        rgba: C_BORDER
                    Line:
                        points: [self.x, self.center_y, self.right, self.center_y]
            Label:
                text: 'atau'
                size_hint_x: None
                width: dp(28)
                font_size: '11sp'
                color: C_MUTED
            Widget:
                canvas:
                    Color:
                        rgba: C_BORDER
                    Line:
                        points: [self.x, self.center_y, self.right, self.center_y]

        GoogleButton:
            id: btn_google
            text: 'Masuk dengan Google'
            size_hint_y: None
            height: dp(48)
            on_press: root.sign_in_with_google()

        BoxLayout:
            size_hint_y: None
            height: dp(38)
            spacing: dp(6)
            GhostButton:
                id: btn_toggle_mode
                text: 'Buat Akun Baru'
                on_press: root.toggle_mode()

            GhostButton:
                id: btn_forgot_pwd
                text: 'Lupa Password?'
                color: C_WARN
                on_press: root.reset_password()

        Label:
            id: status_label
            text: ''
            color: C_DANGER
            font_size: '12sp'
            halign: 'center'
            valign: 'middle'
            text_size: self.width, None
            size_hint_y: None
            height: dp(36)

        Widget:

<CatalogScreen>:
    name: 'catalog'
    on_enter:
        app.current_screen = 'catalog'
        root.load_products()

    BoxLayout:
        orientation: 'vertical'

        TopBar:
            Image:
                source: 'logo.png'
                size_hint_x: None
                width: dp(40)
                allow_stretch: True
                keep_ratio: True

            BoxLayout:
                orientation: 'vertical'
                MutedLabel:
                    text: 'Kasir aktif'
                    font_size: '11sp'
                LeftLabel:
                    text: app.kasir_name
                    bold: True
                    font_size: '15sp'
                    color: C_SUCCESS

            PrimaryButton:
                text: 'Scan'
                size_hint_x: None
                width: dp(76)
                on_press:
                    app.scan_mode = 'cart'
                    app.previous_screen = 'catalog'
                    root.manager.current = 'scanner'

        BoxLayout:
            size_hint_y: None
            height: dp(62)
            padding: [dp(14), dp(8)]

            ModernTextInput:
                id: search_input
                hint_text: 'Cari produk atau barcode...'
                on_text: root.filter_products()

        ScrollView:
            size_hint_y: None
            height: dp(44)
            do_scroll_x: True
            do_scroll_y: False
            bar_width: 0
            BoxLayout:
                id: category_chips_layout
                orientation: 'horizontal'
                size_hint_x: None
                width: self.minimum_width
                padding: [dp(14), dp(6)]
                spacing: dp(8)

        ScrollView:
            do_scroll_x: False
            do_scroll_y: True
            GridLayout:
                id: product_grid
                cols: 2
                spacing: dp(12)
                padding: dp(14)
                size_hint_y: None
                height: self.minimum_height

        NavBar:

<CartScreen>:
    name: 'cart'
    on_enter:
        app.current_screen = 'cart'
        root.update_cart_ui()

    BoxLayout:
        orientation: 'vertical'

        ScreenHeader:
            title: 'Keranjang Belanja'

        ScrollView:
            do_scroll_x: False
            BoxLayout:
                id: cart_list
                orientation: 'vertical'
                padding: dp(14)
                spacing: dp(10)
                size_hint_y: None
                height: self.minimum_height

        BoxLayout:
            orientation: 'vertical'
            size_hint_y: None
            height: dp(120)
            padding: [dp(16), dp(12)]
            spacing: dp(10)
            canvas.before:
                Color:
                    rgba: C_SURFACE
                Rectangle:
                    pos: self.pos
                    size: self.size
                Color:
                    rgba: C_BORDER
                Line:
                    points: [self.x, self.top, self.right, self.top]
                    width: 1

            BoxLayout:
                size_hint_y: None
                height: dp(34)
                LeftLabel:
                    text: 'Total'
                    color: C_MUTED
                    font_size: '14sp'
                RightLabel:
                    id: total_label
                    text: 'Rp 0'
                    font_size: '22sp'
                    bold: True
                    color: C_SUCCESS

            SuccessButton:
                id: btn_checkout
                text: 'PROSES CHECKOUT'
                size_hint_y: None
                height: dp(48)
                disabled: len(app.cart) == 0
                on_press: root.process_checkout()

        NavBar:

<ProductEditScreen>:
    name: 'product_edit'
    on_enter:
        app.current_screen = 'product_edit'
        root.load_manage_products()

    BoxLayout:
        orientation: 'vertical'

        ScreenHeader:
            title: 'Kelola Produk'
            PrimaryButton:
                text: '+ Tambah'
                font_size: '12sp'
                size_hint_x: None
                width: dp(92)
                on_press: root.open_edit_popup(None)

        BoxLayout:
            size_hint_y: None
            height: dp(62)
            padding: [dp(14), dp(8)]

            ModernTextInput:
                id: manage_search_input
                hint_text: 'Cari produk atau barcode...'
                on_text: root.filter_manage_products()

        ScrollView:
            size_hint_y: None
            height: dp(44)
            do_scroll_x: True
            do_scroll_y: False
            bar_width: 0
            BoxLayout:
                id: manage_category_chips_layout
                orientation: 'horizontal'
                size_hint_x: None
                width: self.minimum_width
                padding: [dp(14), dp(6)]
                spacing: dp(8)

        ScrollView:
            do_scroll_x: False
            BoxLayout:
                id: manage_list
                orientation: 'vertical'
                padding: dp(14)
                spacing: dp(10)
                size_hint_y: None
                height: self.minimum_height

        NavBar:

<ReportScreen>:
    name: 'report'
    on_enter:
        app.current_screen = 'report'
        root.load_financial_report()

    BoxLayout:
        orientation: 'vertical'

        ScreenHeader:
            title: 'Laporan Ringkasan'
            SecondaryButton:
                text: 'Riwayat'
                font_size: '12sp'
                size_hint_x: None
                width: dp(76)
                on_press: root.manager.current = 'history'
            DangerButton:
                text: 'Reset'
                font_size: '12sp'
                size_hint_x: None
                width: dp(72)
                on_press: root.confirm_reset_report()

        ScrollView:
            do_scroll_x: False
            BoxLayout:
                id: report_content_layout
                orientation: 'vertical'
                padding: dp(14)
                spacing: dp(10)
                size_hint_y: None
                height: self.minimum_height

        NavBar:

<HistoryScreen>:
    name: 'history'
    on_enter:
        app.current_screen = 'report'
        root.load_history()

    BoxLayout:
        orientation: 'vertical'

        ScreenHeader:
            title: 'Riwayat Pembelian'
            SecondaryButton:
                text: 'Kembali'
                font_size: '12sp'
                size_hint_x: None
                width: dp(84)
                on_press: root.manager.current = 'report'

        ScrollView:
            do_scroll_x: False
            BoxLayout:
                id: history_list
                orientation: 'vertical'
                padding: dp(14)
                spacing: dp(10)
                size_hint_y: None
                height: self.minimum_height

<SettingsScreen>:
    name: 'settings'
    on_enter: app.current_screen = 'settings'

    BoxLayout:
        orientation: 'vertical'

        ScreenHeader:
            title: 'Pengaturan'

        ScrollView:
            do_scroll_x: False
            bar_width: dp(3)

            BoxLayout:
                orientation: 'vertical'
                padding: [dp(14), dp(14), dp(14), dp(24)]
                spacing: dp(14)
                size_hint_y: None
                height: self.minimum_height

                # --- Profil kasir ---
                SectionCard:
                    SectionTitle:
                        text: 'Profil Kasir'
                        color: C_PRIMARY
                    MutedLabel:
                        text: f'Masuk sebagai {app.user_email}' if app.user_email else ''
                        size_hint_y: None
                        height: dp(18)
                    FieldLabel:
                        text: 'Nama petugas kasir'
                    ModernTextInput:
                        id: kasir_name_input
                        text: app.kasir_name
                        hint_text: 'Nama kasir'
                    PrimaryButton:
                        text: 'Simpan Nama Kasir'
                        size_hint_y: None
                        height: dp(44)
                        on_press: root.save_kasir_name()

                # --- Kamera scanner ---
                SectionCard:
                    SectionTitle:
                        text: 'Kamera Scanner'
                        color: C_PRIMARY
                    HintLabel:
                        text: 'Pakai kamera HP lewat WiFi (mis. aplikasi IP Webcam) sebagai pemindai barcode. Kosongkan untuk memakai kamera bawaan.'
                    FieldLabel:
                        text: 'URL IP Camera (opsional)'
                    ModernTextInput:
                        id: ip_camera_input
                        text: app.ip_camera_url
                        hint_text: 'http://192.168.1.X:8080/video'
                    PrimaryButton:
                        text: 'Simpan URL Kamera'
                        size_hint_y: None
                        height: dp(44)
                        on_press: root.save_ip_camera_url()

                # --- Alat scan barcode fisik ---
                SectionCard:
                    SectionTitle:
                        text: 'Alat Scan Barcode'
                        color: C_PRIMARY
                    HintLabel:
                        text: 'Sambungkan atau pairing alat scan USB / Bluetooth seperti keyboard biasa, lalu langsung scan tanpa perlu klik apa pun.'
                    StatusChip:
                        id: hw_scan_status_label
                        text: app.hw_scan_status
                        color: C_SUCCESS if app.hw_scan_enabled else C_MUTED
                    ToggleRow:
                        text: 'Aktifkan pemindaian otomatis'
                        active: app.hw_scan_enabled
                        on_toggle: app.set_hw_scan_enabled(args[1])

                # --- Printer ---
                SectionCard:
                    SectionTitle:
                        text: 'Printer Struk'
                        color: C_PRIMARY
                    StatusChip:
                        id: printer_status_label
                        text: app.printer_status
                        color: C_SUCCESS if app.printer_connected else C_DANGER
                    BoxLayout:
                        size_hint_y: None
                        height: dp(44)
                        spacing: dp(10)
                        PrimaryButton:
                            text: 'Cari & Sambungkan'
                            font_size: '12sp'
                            on_press: root.open_printer_dialog()
                        SecondaryButton:
                            text: 'Tes Cetak'
                            font_size: '12sp'
                            on_press: root.test_print()
                    ToggleRow:
                        text: 'Cetak otomatis setelah checkout'
                        active: app.auto_print
                        on_toggle: app.set_auto_print(args[1])

                # --- Info struk ---
                SectionCard:
                    SectionTitle:
                        text: 'Info Struk'
                        color: C_PRIMARY
                    FieldLabel:
                        text: 'Nama toko'
                    ModernTextInput:
                        id: store_name_input
                        text: app.store_name
                        hint_text: 'Nama Toko'
                    FieldLabel:
                        text: 'Alamat toko'
                    ModernTextInput:
                        id: store_address_input
                        text: app.store_address
                        hint_text: 'Alamat Toko'
                    FieldLabel:
                        text: 'Lebar kertas'
                    ModernSpinner:
                        id: paper_spinner
                        text: app.paper_width
                        values: ['58mm', '80mm']
                        size_hint_y: None
                        height: dp(44)
                    PrimaryButton:
                        text: 'Simpan Info Struk'
                        size_hint_y: None
                        height: dp(44)
                        on_press: root.save_receipt_info()

                # --- Keamanan ---
                SectionCard:
                    SectionTitle:
                        text: 'Keamanan'
                        color: C_PRIMARY
                    FieldLabel:
                        text: 'Password baru'
                    ModernTextInput:
                        id: new_password_input
                        hint_text: 'Minimal 6 karakter'
                        password: True
                    WarnButton:
                        text: 'Ganti Password'
                        size_hint_y: None
                        height: dp(44)
                        on_press: root.change_password()

                DangerButton:
                    text: 'KELUAR (LOGOUT)'
                    size_hint_y: None
                    height: dp(48)
                    on_press: app.logout()

        NavBar:

<ScannerScreen>:
    name: 'scanner'

    BoxLayout:
        orientation: 'vertical'

        ScreenHeader:
            title: 'Scan Barcode'
            SecondaryButton:
                text: 'Batal'
                font_size: '12sp'
                size_hint_x: None
                width: dp(80)
                on_press: root.cancel_scan()

        FloatLayout:
            Image:
                id: camera_preview
                size_hint: 1, 1
                pos_hint: {'x': 0, 'y': 0}

            Widget:
                size_hint: 0.8, None
                height: self.width * 0.55
                pos_hint: {'center_x': 0.5, 'center_y': 0.55}
                canvas:
                    Color:
                        rgba: C_PRIMARY
                    Line:
                        rounded_rectangle: (self.x, self.y, self.width, self.height, dp(16))
                        width: dp(2)

            Label:
                id: camera_status
                text: ''
                size_hint: 0.9, None
                height: self.texture_size[1] + dp(20)
                pos_hint: {'center_x': 0.5, 'center_y': 0.55}
                halign: 'center'
                text_size: self.width, None

            Label:
                text: 'Arahkan kamera ke barcode'
                font_size: '12sp'
                size_hint: None, None
                size: self.texture_size[0] + dp(28), dp(34)
                pos_hint: {'center_x': 0.5, 'y': 0.05}
                canvas.before:
                    Color:
                        rgba: [0.059, 0.09, 0.165, 0.75]
                    RoundedRectangle:
                        pos: self.pos
                        size: self.size
                        radius: [dp(17),]
'''

# --- SCREEN CLASSES ---

class LoginScreen(AppScreen):
    is_register_mode = False

    def toggle_mode(self):
        self.is_register_mode = not self.is_register_mode
        if self.is_register_mode:
            self.ids.mode_title.text = "Pendaftaran Akun Baru"
            self.ids.btn_submit.text = "DAFTAR AKUN"
            self.ids.btn_toggle_mode.text = "Sudah ada akun? Login"
        else:
            self.ids.mode_title.text = "Silakan masuk ke akun Anda"
            self.ids.btn_submit.text = "MASUK SEKARANG"
            self.ids.btn_toggle_mode.text = "Buat Akun Baru"
        self.ids.status_label.text = ""

    def do_auth(self):
        email = self.ids.email_input.text.strip()
        password = self.ids.password_input.text.strip()

        if not email or not password:
            self.ids.status_label.text = "Email dan password wajib diisi!"
            return

        if self.is_register_mode:
            url = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={FIREBASE_WEB_API_KEY}"
        else:
            url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_WEB_API_KEY}"

        payload = {"email": email, "password": password, "returnSecureToken": True}

        try:
            res = requests.post(url, json=payload, timeout=10)
            data = res.json()

            if "error" in data:
                err_msg = data['error']['message']
                if err_msg == "EMAIL_EXISTS":
                    err_msg = "Email sudah terdaftar!"
                elif err_msg in ["INVALID_PASSWORD", "EMAIL_NOT_FOUND", "INVALID_LOGIN_CREDENTIALS"]:
                    err_msg = "Email atau Password salah!"
                elif "WEAK_PASSWORD" in err_msg:
                    err_msg = "Password minimal 6 karakter!"
                self.ids.status_label.text = f"Gagal: {err_msg}"
            else:
                app = App.get_running_app()
                app.id_token = data["idToken"]
                app.user_email = email
                app.user_uid = data["localId"]
                app.kasir_name = email.split('@')[0].capitalize()
                app.cart = []
                app.save_session(data.get("refreshToken", ""))

                self.manager.current = 'catalog'
        except Exception as e:
            self.ids.status_label.text = f"Koneksi Gagal: {e}"

    def sign_in_with_google(self):
        app = App.get_running_app()
        helper = app.google_signin

        if not helper.is_supported():
            Popup(
                title='Tidak Tersedia',
                content=Label(text='Login Google hanya berfungsi di aplikasi\nAndroid (APK), belum di mode pengetesan PC.', halign='center'),
                size_hint=(0.85, 0.3)
            ).open()
            return

        self.ids.status_label.text = "Membuka Google Sign-In..."

        def on_result(success, data):
            def update_ui(dt):
                if not success:
                    self.ids.status_label.text = f"Login Google gagal: {data}"
                    return
                app.id_token = data["idToken"]
                app.user_email = data.get("email", "")
                app.user_uid = data["localId"]
                app.kasir_name = (app.user_email.split('@')[0].capitalize()
                                  if app.user_email else "Kasir Google")
                app.cart = []
                app.save_session(data.get("refreshToken", ""))
                self.manager.current = 'catalog'
            Clock.schedule_once(update_ui, 0)

        helper.sign_in(on_result)

    def reset_password(self):
        email = self.ids.email_input.text.strip()
        if not email:
            self.ids.status_label.text = "Isi email dulu untuk reset password!"
            return

        url = f"https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={FIREBASE_WEB_API_KEY}"
        payload = {"requestType": "PASSWORD_RESET", "email": email}

        try:
            res = requests.post(url, json=payload, timeout=10)
            data = res.json()

            if "error" in data:
                self.ids.status_label.text = f"Reset Gagal: {data['error']['message']}"
            else:
                Popup(
                    title="Email Dikirim",
                    content=Label(text=f"Link reset password telah dikirim ke:\n{email}"),
                    size_hint=(0.85, 0.3)
                ).open()
        except Exception as e:
            self.ids.status_label.text = f"Koneksi Gagal: {e}"


class SettingsScreen(AppScreen):
    # ------------------------------------------------------------------
    # PRINTER
    # ------------------------------------------------------------------
    def _show_result(self, ok, msg):
        show_popup('Sukses' if ok else 'Gagal', msg, 'ok' if ok else 'error')

    def open_printer_dialog(self):
        app = App.get_running_app()
        if app.printer.kind == 'bluetooth':
            self.scan_bluetooth_printers()
        else:
            self.open_desktop_printer_dialog()

    # --- Android: Bluetooth ---
    def scan_bluetooth_printers(self):
        app = App.get_running_app()
        printer = app.printer

        if not printer.is_supported():
            show_popup('Tidak Tersedia',
                       'Bluetooth printer hanya berfungsi di\naplikasi Android (APK).', 'warn')
            return

        def do_scan(*_):
            devices = printer.list_paired_devices()
            if not devices:
                show_popup('Tidak Ada Perangkat',
                           'Belum ada printer yang di-pairing.\nPasangkan dulu lewat Pengaturan Bluetooth HP.',
                           'warn')
                return

            content = BoxLayout(orientation='vertical', padding=dp(12), spacing=dp(10))
            scroll = ScrollView()
            list_box = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(8))
            list_box.bind(minimum_height=list_box.setter('height'))

            popup = Popup(title='Pilih Printer', content=content, size_hint=(0.88, 0.6))

            for name, address in devices:
                btn = Factory.SecondaryButton(
                    text=f"{name}\n{address}",
                    font_size='12sp',
                    size_hint_y=None,
                    height=dp(56)
                )

                def make_connect(addr=address, popup_ref=popup):
                    def _connect(inst):
                        popup_ref.dismiss()
                        app.printer_status = 'Menyambung...'

                        def done(result, err):
                            ok, msg = result if err is None else (False, f"Gagal konek printer: {err}")
                            app.printer_connected = ok
                            app.printer_status = msg if ok else 'Belum terhubung'
                            if ok:
                                app.save_printer_settings()
                            self._show_result(ok, msg)

                        run_in_thread(lambda: printer.connect(addr), done)
                    return _connect

                btn.bind(on_press=make_connect())
                list_box.add_widget(btn)

            scroll.add_widget(list_box)
            btn_cancel = Factory.SecondaryButton(text='Batal', size_hint_y=None, height=dp(44))
            btn_cancel.bind(on_press=popup.dismiss)
            content.add_widget(scroll)
            content.add_widget(btn_cancel)
            popup.open()

        if printer.has_permissions():
            do_scan()
        else:
            def on_perm(perms, grants):
                if grants and all(grants):
                    Clock.schedule_once(do_scan, 0.2)
                else:
                    Clock.schedule_once(lambda dt: self._show_result(
                        False, 'Izin Bluetooth ditolak.\nAktifkan lewat Pengaturan aplikasi.'), 0)
            printer.request_permissions(on_perm)

    # --- Laptop / PC: USB, Bluetooth (COM), LAN/WiFi ---
    def open_desktop_printer_dialog(self):
        app = App.get_running_app()
        printer = app.printer
        modes = printer.available_modes()
        label_to_key = {label: key for key, label in modes}
        current_label = next((label for key, label in modes if key == printer.mode), modes[0][1])

        content = BoxLayout(orientation='vertical', padding=dp(14), spacing=dp(10))

        spinner = Factory.ModernSpinner(text=current_label, values=[label for _, label in modes],
                                        size_hint_y=None, height=dp(44))
        hint = Factory.HintLabel(text='')
        target_input = Factory.ModernTextInput(hint_text='Nama / alamat printer', text=printer.target)

        scroll = ScrollView()
        list_box = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(8))
        list_box.bind(minimum_height=list_box.setter('height'))
        scroll.add_widget(list_box)

        btn_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(10))
        btn_cancel = Factory.SecondaryButton(text='Batal')
        btn_connect = Factory.PrimaryButton(text='Sambungkan')
        btn_row.add_widget(btn_cancel)
        btn_row.add_widget(btn_connect)

        content.add_widget(spinner)
        content.add_widget(hint)
        content.add_widget(target_input)
        content.add_widget(scroll)
        if sys.platform.startswith('win') and not HAS_WIN32PRINT:
            content.add_widget(Factory.HintLabel(
                text="Mode 'Printer Windows' butuh:  pip install pywin32", color=CLR_WARN))
        content.add_widget(btn_row)

        popup = Popup(title='Sambungkan Printer Struk', content=content, size_hint=(0.92, 0.85))

        def refresh(*_):
            key = label_to_key.get(spinner.text)
            hint.text = printer.HINTS.get(key, '')
            list_box.clear_widgets()
            found = printer.list_targets(key)
            for value, label in found:
                b = Factory.SecondaryButton(text=label, font_size='12sp',
                                            size_hint_y=None, height=dp(44))
                b.bind(on_press=lambda inst, v=value: setattr(target_input, 'text', v))
                list_box.add_widget(b)
            if not found:
                msg = 'Ketik alamat IP printer di kolom atas.' if key == 'network' else 'Tidak ada printer terdeteksi.'
                list_box.add_widget(Factory.MutedLabel(text=msg, halign='center',
                                                       size_hint_y=None, height=dp(40)))

        def on_mode_change(*_):
            key = label_to_key.get(spinner.text)
            if key != printer.mode:
                target_input.text = ''
            refresh()

        spinner.bind(text=on_mode_change)
        refresh()

        def do_connect(inst):
            key = label_to_key.get(spinner.text)
            target = target_input.text.strip()
            btn_connect.disabled = True
            btn_connect.text = 'Menyambung...'

            def done(result, err):
                btn_connect.disabled = False
                btn_connect.text = 'Sambungkan'
                ok, msg = result if err is None else (False, str(err))
                app.printer_connected = ok
                app.printer_status = msg if ok else 'Belum terhubung'
                if ok:
                    app.save_printer_settings()
                    popup.dismiss()
                self._show_result(ok, msg)

            run_in_thread(lambda: printer.connect(key, target), done)

        btn_connect.bind(on_press=do_connect)
        btn_cancel.bind(on_press=popup.dismiss)
        popup.open()

    def test_print(self):
        app = App.get_running_app()

        if not app.printer.is_connected():
            show_popup('Belum Terhubung', 'Sambungkan printer dulu sebelum tes cetak.', 'warn')
            return

        dummy_items = [{'nama': 'Contoh Produk', 'harga': 10000, 'qty': 1}]
        app.print_receipt_async(datetime.datetime.now(), dummy_items, 10000, self._show_result)

    def save_receipt_info(self):
        app = App.get_running_app()
        app.store_name = self.ids.store_name_input.text.strip() or DEFAULT_STORE_NAME
        app.store_address = self.ids.store_address_input.text.strip()
        app.paper_width = self.ids.paper_spinner.text
        app.save_printer_settings()
        show_popup('Sukses', 'Info struk berhasil disimpan!', 'ok')

    # ------------------------------------------------------------------
    # AKUN & KAMERA
    # ------------------------------------------------------------------
    def save_kasir_name(self):
        app = App.get_running_app()
        new_name = self.ids.kasir_name_input.text.strip()
        if not new_name:
            show_popup('Peringatan', 'Nama kasir tidak boleh kosong.', 'warn')
            return
        app.kasir_name = new_name
        app.persist_kasir_name()
        show_popup('Sukses', 'Nama kasir berhasil diperbarui!', 'ok')

    def save_ip_camera_url(self):
        app = App.get_running_app()
        # Rapikan input: tambah http:// dan /video bila belum ada
        url = normalize_camera_url(self.ids.ip_camera_input.text)
        app.ip_camera_url = url
        self.ids.ip_camera_input.text = url
        app.save_camera_settings()
        msg = f"URL IP Camera disimpan:\n{url}" if url else "URL IP Camera dikosongkan.\n(Memakai kamera bawaan)"
        show_popup('Sukses', msg, 'ok')

    def change_password(self):
        app = App.get_running_app()
        new_pwd = self.ids.new_password_input.text.strip()

        if len(new_pwd) < 6:
            show_popup('Gagal', 'Password minimal 6 karakter!', 'error')
            return

        url = f"https://identitytoolkit.googleapis.com/v1/accounts:update?key={FIREBASE_WEB_API_KEY}"
        payload = {
            "idToken": app.id_token,
            "password": new_pwd,
            "returnSecureToken": True
        }

        try:
            res = requests.post(url, json=payload, timeout=10)
            data = res.json()

            if "error" in data:
                show_popup('Gagal', f"Gagal ganti password: {data['error']['message']}", 'error')
            else:
                app.id_token = data["idToken"]
                app.save_session(data.get("refreshToken", ""))
                self.ids.new_password_input.text = ""
                show_popup('Sukses', 'Password berhasil diubah!', 'ok')
        except Exception as e:
            show_popup('Error', f"Gagal koneksi: {e}", 'error')


class CatalogScreen(AppScreen):
    all_products = []
    selected_category = "Semua"
    category_names = ["Semua"]

    _render_ev = None
    _data_sig = None

    def load_products(self, force=False):
        app = App.get_running_app()
        if not fs.is_ready() or not app.user_uid:
            self._data_sig = None
            self.all_products = []
            self.render_products([])
            return

        cached = app.get_cached_products()
        if cached is not None:
            self._apply_products(cached)  # instan; dilewati kalau datanya sama dengan yang tampil
        else:
            self._data_sig = None
            self._render_message("Memuat produk...")
        app.refresh_products(self._apply_products, on_error=self._on_load_error, force=force)

    def _on_load_error(self, error):
        if self._data_sig is None:
            self._render_message("Gagal memuat produk.\nPeriksa koneksi internet,\nlalu buka ulang menu ini.")

    def _apply_products(self, products):
        sig = products_signature(products)
        if sig == self._data_sig:
            return
        self._data_sig = sig
        self.all_products = list(products)

        categories_set = {p.get('kategori', '').strip() for p in products}
        categories_set.discard('')
        self.category_names = ["Semua"] + sorted(categories_set)
        if self.selected_category not in self.category_names:
            self.selected_category = "Semua"

        self._build_chips()
        self.filter_products()

    def _build_chips(self):
        chips_layout = self.ids.category_chips_layout
        chips_layout.clear_widgets()

        for cat_name in self.category_names:
            is_active = (cat_name == self.selected_category)
            btn = Factory.RoundedButton(
                text=cat_name,
                size_hint=(None, None),
                height=dp(32),
                pos_hint={'center_y': 0.5},
                corner_radius=dp(16),
                font_size='12sp',
                bold=is_active,
                color=(1, 1, 1, 1) if is_active else CLR_MUTED,
                bg_color=list(CLR_PRIMARY if is_active else CLR_SURFACE),
                bg_color_down=list(CLR_PRIMARY_DARK if is_active else CLR_SURFACE2),
            )
            btn.texture_update()
            btn.width = max(btn.texture_size[0] + dp(28), dp(64))
            btn.bind(on_press=lambda inst, c=cat_name: self.select_category(c))
            chips_layout.add_widget(btn)

    def _update_chip_rect(self, instance, value):
        if hasattr(instance, 'rect'):
            instance.rect.pos = instance.pos
            instance.rect.size = instance.size

    def select_category(self, cat_name):
        # Cukup filter ulang data yang sudah dimuat - tidak perlu query Firestore lagi.
        self.selected_category = cat_name
        self._build_chips()
        self.filter_products()

    def filter_products(self, *args):
        query = self.ids.search_input.text.lower().strip()

        filtered = []
        for p in self.all_products:
            matches_query = (
                not query or 
                query in p.get('nama', '').lower() or 
                query in str(p.get('barcode', '')).lower()
            )
            matches_category = (
                self.selected_category == "Semua" or 
                p.get('kategori', 'Umum') == self.selected_category
            )

            if matches_query and matches_category:
                filtered.append(p)

        self.render_products(filtered)

    def _cancel_render(self):
        if self._render_ev is not None:
            self._render_ev.cancel()
            self._render_ev = None

    def _render_message(self, text):
        grid = self.ids.product_grid
        self._cancel_render()
        grid.clear_widgets()
        grid.cols = 1  # pesan harus selebar layar, bukan separuh
        grid.add_widget(Factory.MutedLabel(text=text, halign="center", font_size='13sp',
                                           size_hint_y=None, height=dp(200)))

    def render_products(self, products_list):
        if not products_list:
            self._render_message("Produk tidak ditemukan." if self.all_products
                                 else "Belum ada produk.\nSilakan tambah di menu 'Kelola'.")
            return

        # Kartu digambar bertahap (beberapa per frame) supaya UI tidak macet saat produk banyak
        grid = self.ids.product_grid
        self._cancel_render()
        grid.clear_widgets()
        grid.cols = 2
        it = iter(products_list)

        def step(dt):
            for _ in range(6):
                p = next(it, None)
                if p is None:
                    self._render_ev = None
                    return False
                grid.add_widget(self._make_product_card(p))
            return True

        if step(0):
            self._render_ev = Clock.schedule_interval(step, 0)

    def _make_product_card(self, p):
        doc_id = p['doc_id']
        stok_val = int(p.get('stok', 0))
        card = Factory.CustomCard()

        lbl_name = Factory.LeftLabel(
            text=p.get('nama', '-'), bold=True, font_size='13sp',
            size_hint_y=None, height=dp(22), shorten=True, shorten_from='right')
        lbl_stock = Factory.LeftLabel(
            text=f"Stok: {stok_val}",
            color=CLR_DANGER if stok_val < 5 else CLR_SUCCESS,
            font_size='11sp', bold=True, size_hint_y=None, height=dp(18))
        lbl_price = Factory.LeftLabel(
            text=f"Rp {rupiah(int(p.get('harga', 0)))}",
            font_size='15sp', bold=True, size_hint_y=None, height=dp(24))
        btn = Factory.PrimaryButton(text='+ Keranjang', size_hint_y=None, height=dp(36), font_size='12sp')
        btn.bind(on_press=lambda inst, item=p, pid=doc_id: self.add_to_cart(item, pid))

        card.add_widget(lbl_name)
        card.add_widget(lbl_stock)
        card.add_widget(lbl_price)
        card.add_widget(Widget())
        card.add_widget(btn)
        return card

    def handle_hardware_barcode(self, code):
        """Dipanggil App saat alat scan fisik (USB/Bluetooth) membaca kode,
        walau kolom pencarian sedang tidak difokuskan sama sekali."""
        match = next(
            (p for p in self.all_products if str(p.get('barcode', '')).strip() == code),
            None
        )
        if 'search_input' in self.ids:
            self.ids.search_input.text = ''
        if match:
            self.add_to_cart(match, match['doc_id'])
        else:
            Popup(
                title='Tidak Ditemukan',
                content=Label(text=f'Barcode: {code}\ntidak terdaftar pada akun ini'),
                size_hint=(0.8, 0.25)
            ).open()

    def add_to_cart(self, item, doc_id):
        app = App.get_running_app()
        stok_tersedia = int(item.get('stok', 0))

        if stok_tersedia <= 0:
            Popup(title='Stok Habis', content=Label(text='Stok produk ini habis!'), size_hint=(0.8, 0.25)).open()
            return

        for cart_item in app.cart:
            if cart_item['id'] == doc_id:
                if cart_item['qty'] + 1 > stok_tersedia:
                    Popup(
                        title='Peringatan Stok Limit',
                        content=Label(text=f"Stok tidak mencukupi!\nMaksimal tersedia: {stok_tersedia} pcs", halign='center'),
                        size_hint=(0.85, 0.28)
                    ).open()
                else:
                    cart_item['qty'] += 1
                    Popup(title='Berhasil', content=Label(text='Jumlah ditambah!'), size_hint=(0.7, 0.22)).open()
                return

        app.cart.append({
            'id': doc_id,
            'nama': item.get('nama', ''),
            'harga': int(item.get('harga', 0)),
            'stok': stok_tersedia,
            'qty': 1
        })
        Popup(title='Berhasil', content=Label(text='Masuk ke keranjang!'), size_hint=(0.7, 0.22)).open()


class StockError(Exception):
    """Stok tidak cukup / produk sudah dihapus saat checkout."""


def checkout_transaction(cart, user_uid, kasir_nama, kasir_email):
    """Cek stok + kurangi stok + simpan transaksi dalam SATU transaksi Firestore (REST).
    Kalau ada yang gagal di tengah jalan, tidak ada stok yang "nyangkut" terpotong."""

    def body(tx):
        snaps = [tx.get("products", item['id']) for item in cart]  # semua READ dulu

        new_stocks, total = [], 0
        for item, snap in zip(cart, snaps):
            if snap is None:
                raise StockError(f"Produk '{item['nama']}' sudah tidak ada.")
            current = int(snap.get('stok', 0))
            if item['qty'] > current:
                raise StockError(f"Stok '{item['nama']}' tersisa {current} pcs.\nSilakan sesuaikan jumlah belanja.")
            new_stocks.append(current - item['qty'])
            total += item['harga'] * item['qty']

        for item, new_stock in zip(cart, new_stocks):  # baru WRITE
            tx.update("products", item['id'], {'stok': new_stock})

        tx.set("transactions", tx.new_id(), {
            'users_id': user_uid,
            'kasir_nama': kasir_nama,
            'kasir_email': kasir_email,
            'total': total,
            'items': cart,
        }, server_timestamps=['timestamp'])
        return total

    return fs.run_transaction(body)


class CartScreen(AppScreen):
    def update_cart_ui(self):
        app = App.get_running_app()
        cart_list = self.ids.cart_list
        cart_list.clear_widgets()

        if not app.cart:
            cart_list.add_widget(Factory.MutedLabel(
                text="Keranjang masih kosong.\nTambahkan produk dari menu Katalog.",
                halign='center', font_size='13sp', size_hint_y=None, height=dp(140)))

        total = 0
        for idx, item in enumerate(app.cart):
            subtotal = item['harga'] * item['qty']
            total += subtotal

            row = Factory.CustomCard(size_hint_y=None, height=dp(84), orientation='horizontal',
                                     padding=dp(12), spacing=dp(8))

            info_box = BoxLayout(orientation='vertical', spacing=dp(2))
            info_box.add_widget(Factory.LeftLabel(text=item['nama'], bold=True, font_size='14sp',
                                                  shorten=True, shorten_from='right'))
            info_box.add_widget(Factory.MutedLabel(text=f"Rp {rupiah(item['harga'])} x {item['qty']}"))
            info_box.add_widget(Factory.LeftLabel(text=f"Rp {rupiah(subtotal)}", bold=True,
                                                  font_size='13sp', color=CLR_SUCCESS))

            qty_box = BoxLayout(size_hint=(None, None), size=(dp(122), dp(38)), spacing=dp(4),
                                pos_hint={'center_y': 0.5})

            btn_minus = Factory.SecondaryButton(text='-', font_size='16sp', size_hint_x=None, width=dp(34))
            btn_minus.bind(on_press=lambda inst, i=idx: self.change_qty(i, -1))

            txt_qty = Factory.ModernTextInput(text=str(item['qty']), input_filter='int',
                                              size_hint=(None, None), size=(dp(46), dp(38)),
                                              halign='center', padding=[dp(4), dp(9), dp(4), dp(9)])
            txt_qty.bind(on_text_validate=lambda inst, i=idx: self.on_qty_input_changed(i, inst.text))
            txt_qty.bind(focus=lambda inst, focused, i=idx: self.on_qty_input_changed(i, inst.text) if not focused else None)

            btn_plus = Factory.SecondaryButton(text='+', font_size='16sp', size_hint_x=None, width=dp(34))
            btn_plus.bind(on_press=lambda inst, i=idx: self.change_qty(i, 1))

            qty_box.add_widget(btn_minus)
            qty_box.add_widget(txt_qty)
            qty_box.add_widget(btn_plus)

            row.add_widget(info_box)
            row.add_widget(qty_box)
            cart_list.add_widget(row)

        self.ids.total_label.text = f"Rp {rupiah(total)}"

    def change_qty(self, index, delta):
        app = App.get_running_app()
        if 0 <= index < len(app.cart):
            item = app.cart[index]
            max_stok = item.get('stok', 0)

            if delta > 0 and (item['qty'] + delta) > max_stok:
                Popup(
                    title='Peringatan Stok Limit',
                    content=Label(text=f"Stok '{item['nama']}' terbatas!\nMaksimal tersedia: {max_stok} pcs", halign='center'),
                    size_hint=(0.85, 0.28)
                ).open()
                return

            item['qty'] += delta
            if item['qty'] <= 0:
                app.cart.pop(index)
            self.update_cart_ui()

    def on_qty_input_changed(self, index, val):
        app = App.get_running_app()
        if index >= len(app.cart):
            return

        item = app.cart[index]
        max_stok = item.get('stok', 0)

        if not val.strip() or int(val) <= 0:
            app.cart.pop(index)
        else:
            inputted_qty = int(val)
            if inputted_qty > max_stok:
                item['qty'] = max_stok
                Popup(
                    title='Peringatan Stok Limit',
                    content=Label(text=f"Jumlah melebihi stok!\nOtomatis disesuaikan ke maksimal: {max_stok} pcs", halign='center'),
                    size_hint=(0.85, 0.28)
                ).open()
            else:
                item['qty'] = inputted_qty

        self.update_cart_ui()

    def process_checkout(self):
        app = App.get_running_app()
        app.cart = [item for item in app.cart if item['qty'] > 0]

        if not app.cart:
            return
        if not fs.is_ready():
            Popup(title='Firebase Belum Siap',
                  content=Label(text='Koneksi database belum dikonfigurasi.\nPeriksa FIREBASE_PROJECT_ID di main.py.', halign='center'),
                  size_hint=(0.85, 0.28)).open()
            return

        cart_snapshot = [dict(item) for item in app.cart]

        # Kunci tombol checkout + kasih tahu kasir sedang diproses, supaya:
        # 1) UI tidak terasa "diam" saat menunggu Firestore (proses jalan di background thread)
        # 2) tombol tidak bisa ditekan berkali-kali (mencegah transaksi ganda ke-submit)
        btn = self.ids.get('btn_checkout')
        if btn:
            btn.disabled = True

        processing_popup = Popup(
            title='Memproses...',
            content=Label(text='Sedang memproses transaksi,\nmohon tunggu sebentar.', halign='center'),
            size_hint=(0.75, 0.22),
            auto_dismiss=False
        )
        processing_popup.open()

        def work():
            # Cek stok + kurangi stok + simpan transaksi dalam SATU transaksi atomik:
            # kalau ada yang gagal di tengah jalan, tidak ada stok yang "nyangkut" terpotong.
            return checkout_transaction(
                cart_snapshot, app.user_uid, app.kasir_name, app.user_email
            )

        def done(total_bayar, error):
            processing_popup.dismiss()
            if btn:
                btn.disabled = len(app.cart) == 0

            if error is not None:
                if isinstance(error, StockError):
                    Popup(title='Checkout Gagal', content=Label(text=str(error), halign='center'),
                          size_hint=(0.85, 0.28)).open()
                else:
                    print("Checkout error:", error)
                    Popup(title='Gagal', content=Label(text=f'Error: {error}'), size_hint=(0.85, 0.3)).open()
                return

            now_dt = datetime.datetime.now()
            pdf_path = None
            try:
                pdf_path = self.generate_pdf_receipt(total_bayar, cart_snapshot, now_dt)
            except Exception as e:
                print("Gagal membuat PDF struk:", e)

            app.apply_sale_to_cache(cart_snapshot)  # stok di katalog langsung ikut turun
            app.cart = []
            self.update_cart_ui()
            self.show_onscreen_receipt(total_bayar, cart_snapshot, now_dt, pdf_path)

        run_in_thread(work, done)

    def show_onscreen_receipt(self, total, items, now_dt, pdf_path):
        app = App.get_running_app()
        builder = app.make_receipt_builder()
        content = BoxLayout(orientation='vertical', padding=dp(14), spacing=dp(12))

        receipt_text = builder.as_text(app.kasir_name, now_dt, items, total)
        if pdf_path:
            receipt_text += f"\n\nPDF: {os.path.basename(pdf_path)}"

        scroll = ScrollView()
        lbl_receipt = Label(
            text=receipt_text,
            font_size='12sp' if builder.cols <= 32 else '9sp',
            font_name='data/fonts/RobotoMono-Regular.ttf',  # monospace agar kolom rata seperti di printer
            color=(0.9, 0.9, 0.9, 1),
            size_hint_y=None,
            halign='left',
            valign='top'
        )
        lbl_receipt.bind(texture_size=lbl_receipt.setter('size'))
        scroll.add_widget(lbl_receipt)

        btn_row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(10))

        btn_print = Factory.SuccessButton(text='Cetak Struk')

        def do_print(inst):
            if not app.printer.is_connected():
                Popup(
                    title='Printer Belum Terhubung',
                    content=Label(text='Sambungkan printer dulu\nlewat menu Akun > Printer Struk.', halign='center'),
                    size_hint=(0.85, 0.3)
                ).open()
                return
            app.print_receipt_async(now_dt, items, total, self._print_result)

        btn_print.bind(on_press=do_print)

        btn_close = Factory.PrimaryButton(text='Selesai')

        btn_row.add_widget(btn_print)
        btn_row.add_widget(btn_close)

        content.add_widget(scroll)
        content.add_widget(btn_row)

        popup = Popup(
            title='Struk Transaksi Digital',
            content=content,
            size_hint=(0.88, 0.8),
            auto_dismiss=False
        )
        btn_close.bind(on_press=popup.dismiss)
        popup.open()

        # Cetak otomatis jika printer sudah tersambung & fitur cetak otomatis aktif.
        # Untuk cetak otomatis, popup hanya muncul kalau GAGAL (supaya kasir tidak terganggu).
        if app.auto_print and app.printer.is_connected():
            app.print_receipt_async(
                now_dt, items, total,
                lambda ok, msg: None if ok else self._print_result(ok, msg)
            )

    @staticmethod
    def _print_result(ok, msg):
        Popup(title='Sukses' if ok else 'Gagal Cetak',
              content=Label(text=msg, halign='center'),
              size_hint=(0.85, 0.28)).open()

    def generate_pdf_receipt(self, total, items, now_dt):
        if not HAS_REPORTLAB:
            return None

        app = App.get_running_app()

        # Android: simpan di folder data aplikasi (folder kerja tidak bisa ditulis).
        folder = app.user_data_dir if ANDROID else os.path.join(os.getcwd(), 'struk')
        os.makedirs(folder, exist_ok=True)
        filename = os.path.join(folder, f"struk_{now_dt.strftime('%Y%m%d_%H%M%S')}.pdf")

        page_height = max(380 + (len(items) * 25), 440)
        c = canvas.Canvas(filename, pagesize=(220, page_height))

        # --- LOGO STRUK PDF ---
        logo_path = "logo.png"
        if os.path.exists(logo_path):
            c.drawImage(logo_path, 85, page_height - 55, width=50, height=50, preserveAspectRatio=True, mask='auto')
            y_offset = 65
        else:
            y_offset = 30

        c.setFont("Helvetica-Bold", 12)
        c.drawCentredString(110, page_height - y_offset, app.store_name)

        c.setFont("Helvetica", 7)
        c.drawCentredString(110, page_height - y_offset - 12, app.store_address)

        c.setFont("Helvetica", 8)
        c.drawCentredString(110, page_height - y_offset - 24, f"Kasir: {app.kasir_name}")
        c.drawCentredString(110, page_height - y_offset - 34, f"Tgl  : {now_dt.strftime('%d/%m/%Y %H:%M')}")

        c.setLineWidth(0.5)
        c.line(15, page_height - y_offset - 42, 205, page_height - y_offset - 42)

        y = page_height - y_offset - 57
        c.setFont("Helvetica-Bold", 8)
        c.drawString(15, y, "ITEM")
        c.drawRightString(205, y, "TOTAL")

        y -= 12
        c.setFont("Helvetica", 8)

        for item in items:
            c.drawString(15, y, item['nama'])
            y -= 10
            c.drawString(20, y, f"{item['qty']} x Rp {rupiah(item['harga'])}")
            c.drawRightString(205, y, f"Rp {rupiah(item['harga'] * item['qty'])}")
            y -= 15

        c.line(15, y + 5, 205, y + 5)
        y -= 10
        c.setFont("Helvetica-Bold", 10)
        c.drawString(15, y, "TOTAL BAYAR:")
        c.drawRightString(205, y, f"Rp {rupiah(total)}")

        y -= 30
        c.setFont("Helvetica-Oblique", 8)
        c.drawCentredString(110, y, "--- Terima Kasih ---")
        c.drawCentredString(110, y - 10, "Barang yang dibeli tidak dapat ditukar")

        c.save()
        return filename


class ProductEditScreen(AppScreen):
    temp_form_data = {}
    _active_barcode_input = None

    all_products = []
    selected_category = "Semua"
    category_names = ["Semua"]

    def fill_scanned_barcode(self, code):
        """Dipanggil App saat alat scan fisik membaca kode ketika popup
        Tambah/Edit Produk sedang terbuka. Return False kalau popup tertutup,
        supaya App tahu belum ada yang menampung hasil scan ini."""
        if self._active_barcode_input is None:
            return False
        self._active_barcode_input.text = code
        return True

    _manage_sig = None
    _manage_ev = None

    def _cancel_manage_render(self):
        if self._manage_ev is not None:
            self._manage_ev.cancel()
            self._manage_ev = None

    def _manage_message(self, text):
        manage_list = self.ids.manage_list
        self._cancel_manage_render()
        manage_list.clear_widgets()
        manage_list.add_widget(Factory.MutedLabel(text=text, halign='center', font_size='13sp',
                                                  size_hint_y=None, height=dp(140)))

    def load_manage_products(self, force=False):
        app = App.get_running_app()
        if not fs.is_ready() or not app.user_uid:
            self._manage_sig = None
            self._cancel_manage_render()
            self.ids.manage_list.clear_widgets()
            self.all_products = []
            self.selected_category = "Semua"
            self.category_names = ["Semua"]
            self.ids.manage_category_chips_layout.clear_widgets()
            return

        cached = app.get_cached_products()
        if cached is not None:
            self._apply_manage(cached)  # instan; dilewati kalau datanya sama dengan yang tampil
        else:
            self._manage_sig = None
            self._manage_message("Memuat produk...")
        app.refresh_products(self._apply_manage, on_error=self._on_manage_error, force=force)

    def _on_manage_error(self, error):
        if self._manage_sig is None:
            self._manage_message("Gagal memuat produk.\nPeriksa koneksi internet,\nlalu buka ulang menu ini.")

    def _apply_manage(self, products):
        sig = products_signature(products)
        if sig == self._manage_sig:
            return
        self._manage_sig = sig
        self.all_products = list(products)

        categories_set = {p.get('kategori', '').strip() for p in products}
        categories_set.discard('')
        self.category_names = ["Semua"] + sorted(categories_set)
        if self.selected_category not in self.category_names:
            self.selected_category = "Semua"

        self._build_chips()
        self.filter_manage_products()

    def _build_chips(self):
        chips_layout = self.ids.manage_category_chips_layout
        chips_layout.clear_widgets()

        for cat_name in self.category_names:
            is_active = (cat_name == self.selected_category)
            btn = Factory.RoundedButton(
                text=cat_name,
                size_hint=(None, None),
                height=dp(32),
                pos_hint={'center_y': 0.5},
                corner_radius=dp(16),
                font_size='12sp',
                bold=is_active,
                color=(1, 1, 1, 1) if is_active else CLR_MUTED,
                bg_color=list(CLR_PRIMARY if is_active else CLR_SURFACE),
                bg_color_down=list(CLR_PRIMARY_DARK if is_active else CLR_SURFACE2),
            )
            btn.texture_update()
            btn.width = max(btn.texture_size[0] + dp(28), dp(64))
            btn.bind(on_press=lambda inst, c=cat_name: self.select_category(c))
            chips_layout.add_widget(btn)

    def select_category(self, cat_name):
        # Cukup filter ulang data yang sudah dimuat - tidak perlu query Firestore lagi.
        self.selected_category = cat_name
        self._build_chips()
        self.filter_manage_products()

    def filter_manage_products(self, *args):
        query = self.ids.manage_search_input.text.lower().strip()

        filtered = []
        for p in self.all_products:
            matches_query = (
                not query or
                query in p.get('nama', '').lower() or
                query in str(p.get('barcode', '')).lower()
            )
            matches_category = (
                self.selected_category == "Semua" or
                p.get('kategori', 'Umum') == self.selected_category
            )

            if matches_query and matches_category:
                filtered.append(p)

        self.render_manage_products(filtered)

    def render_manage_products(self, products_list):
        if not products_list:
            self._manage_message(
                "Produk tidak ditemukan." if self.all_products
                else "Belum ada produk.\nKetuk '+ Tambah' untuk menambah produk."
            )
            return

        # Baris digambar bertahap (beberapa per frame) supaya UI tidak macet saat produk banyak
        manage_list = self.ids.manage_list
        self._cancel_manage_render()
        manage_list.clear_widgets()
        it = iter(products_list)

        def step(dt):
            for _ in range(5):
                p = next(it, None)
                if p is None:
                    self._manage_ev = None
                    return False
                manage_list.add_widget(self._make_manage_row(p))
            return True

        if step(0):
            self._manage_ev = Clock.schedule_interval(step, 0)

    def _make_manage_row(self, p):
        row = Factory.CustomCard(size_hint_y=None, height=dp(84), orientation='horizontal',
                                 padding=dp(12), spacing=dp(8))

        info_box = BoxLayout(orientation='vertical', spacing=dp(2))
        info_box.add_widget(Factory.LeftLabel(text=p.get('nama', '-'), bold=True, font_size='14sp',
                                              shorten=True, shorten_from='right'))
        info_box.add_widget(Factory.MutedLabel(
            text=f"Rp {rupiah(int(p.get('harga', 0)))} | Stok: {p.get('stok', 0)}"))
        info_box.add_widget(Factory.MutedLabel(text=f"Kategori: {p.get('kategori', 'Umum')}"))

        btn_box = BoxLayout(size_hint=(None, None), size=(dp(132), dp(38)), spacing=dp(6),
                            pos_hint={'center_y': 0.5})

        btn_edit = Factory.PrimaryButton(text='Edit', font_size='12sp')
        btn_edit.bind(on_press=lambda inst, item=p: self.open_edit_popup(item))

        btn_delete = Factory.DangerButton(text='Hapus', font_size='12sp')
        btn_delete.bind(on_press=lambda inst, item=p: self.confirm_delete_product(item))

        btn_box.add_widget(btn_edit)
        btn_box.add_widget(btn_delete)

        row.add_widget(info_box)
        row.add_widget(btn_box)
        return row

    def confirm_delete_product(self, item):
        content = BoxLayout(orientation='vertical', padding=dp(14), spacing=dp(12))
        content.add_widget(Label(
            text=f"Apakah Anda yakin ingin menghapus\n'{item.get('nama', 'produk ini')}'?",
            halign='center',
            valign='middle'
        ))

        btn_box = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(10))
        btn_cancel = Factory.SecondaryButton(text='Batal')
        btn_yes = Factory.DangerButton(text='Ya, Hapus')

        btn_box.add_widget(btn_cancel)
        btn_box.add_widget(btn_yes)
        content.add_widget(btn_box)

        popup = Popup(title='Konfirmasi Hapus', content=content, size_hint=(0.85, 0.3))

        btn_cancel.bind(on_press=popup.dismiss)
        btn_yes.bind(on_press=lambda inst: self.delete_product(item.get('doc_id'), popup))
        popup.open()

    def delete_product(self, doc_id, popup_instance):
        if not fs.is_ready() or not doc_id:
            return

        try:
            fs.delete_doc("products", doc_id)
            App.get_running_app().drop_cached_product(doc_id)
            popup_instance.dismiss()
            self.load_manage_products()
            Popup(title='Sukses', content=Label(text='Produk berhasil dihapus!'), size_hint=(0.8, 0.25)).open()
        except Exception as e:
            print("Gagal menghapus produk:", e)
            Popup(title='Gagal', content=Label(text=f'Gagal menghapus: {e}'), size_hint=(0.85, 0.3)).open()

    def open_edit_popup(self, item=None, scanned_code=None):
        is_edit = item is not None or self.temp_form_data.get('doc_id') is not None
        title_text = "Edit Produk" if is_edit else "Tambah Produk"

        default_nama = item.get('nama', '') if item else self.temp_form_data.get('nama', '')
        default_harga = str(item.get('harga', '')) if item else self.temp_form_data.get('harga', '')
        default_stok = str(item.get('stok', '')) if item else self.temp_form_data.get('stok', '')
        default_kategori = item.get('kategori', '') if item else self.temp_form_data.get('kategori', '')

        if scanned_code:
            default_barcode = scanned_code
        else:
            default_barcode = str(item.get('barcode', '')) if item else self.temp_form_data.get('barcode', '')

        current_doc_id = item.get('doc_id') if item else self.temp_form_data.get('doc_id')

        content = BoxLayout(orientation='vertical', padding=dp(14), spacing=dp(6))

        in_name = Factory.ModernTextInput(hint_text='Contoh: Kopi Susu', text=default_nama)
        in_category = Factory.ModernTextInput(hint_text='Contoh: Makanan, Minuman', text=default_kategori)
        in_price = Factory.ModernTextInput(hint_text='Harga (Rp)', text=default_harga, input_filter='int')
        in_stock = Factory.ModernTextInput(hint_text='Jumlah stok', text=default_stok, input_filter='int')

        barcode_box = BoxLayout(orientation='horizontal', spacing=dp(8), size_hint_y=None, height=dp(46))
        in_barcode = Factory.ModernTextInput(hint_text='Ketik atau scan barcode', text=default_barcode)

        btn_scan_barcode = Factory.PrimaryButton(text='Scan', size_hint_x=None, width=dp(72))

        barcode_box.add_widget(in_barcode)
        barcode_box.add_widget(btn_scan_barcode)

        btn_save = Factory.SuccessButton(
            text='SIMPAN' if is_edit else 'TAMBAH PRODUK',
            size_hint_y=None,
            height=dp(48)
        )

        for label_text, widget in (('Nama produk', in_name), ('Kategori', in_category),
                                   ('Harga (Rp)', in_price), ('Stok', in_stock),
                                   ('Barcode', barcode_box)):
            content.add_widget(Factory.FieldLabel(text=label_text))
            content.add_widget(widget)
        content.add_widget(Widget())
        content.add_widget(btn_save)

        popup = Popup(title=title_text, content=content, size_hint=(0.9, 0.85))
        self._active_barcode_input = in_barcode
        popup.bind(on_dismiss=lambda *_: setattr(self, '_active_barcode_input', None))

        def trigger_scan(inst):
            self.temp_form_data = {
                'doc_id': current_doc_id,
                'nama': in_name.text,
                'kategori': in_category.text,
                'harga': in_price.text,
                'stok': in_stock.text,
                'barcode': in_barcode.text
            }
            popup.dismiss()
            
            app = App.get_running_app()
            app.scan_mode = 'fill_input'
            app.previous_screen = 'product_edit'
            self.manager.current = 'scanner'

        btn_scan_barcode.bind(on_press=trigger_scan)

        def save_action(inst):
            app = App.get_running_app()
            name = in_name.text.strip()
            category = in_category.text.strip().title()
            price = in_price.text.strip()
            stock = in_stock.text.strip()
            barcode = in_barcode.text.strip()

            if not name or not price or not stock or not fs.is_ready():
                Popup(title='Peringatan', content=Label(text='Nama, Harga, dan Stok wajib diisi!'), size_hint=(0.8, 0.25)).open()
                return

            try:
                data = {
                    'users_id': app.user_uid,
                    'nama': name,
                    'kategori': category if category else "Umum",
                    'harga': int(price),
                    'stok': int(stock),
                    'barcode': barcode
                }

                if current_doc_id:
                    fs.update_doc("products", current_doc_id, data)
                    app.upsert_cached_product(current_doc_id, data)
                else:
                    new_id = fs.add_doc("products", data)
                    app.upsert_cached_product(new_id, data)

                self.temp_form_data = {}
                popup.dismiss()
                self.load_manage_products()
                Popup(title='Sukses', content=Label(text='Data produk berhasil disimpan!'), size_hint=(0.8, 0.25)).open()
            except Exception as e:
                print("Gagal simpan produk:", e)

        btn_save.bind(on_press=save_action)
        popup.open()


class ReportScreen(AppScreen):
    def confirm_reset_report(self):
        app = App.get_running_app()
        if not fs.is_ready() or not app.user_uid:
            return

        content = BoxLayout(orientation='vertical', padding=dp(14), spacing=dp(12))
        content.add_widget(Label(
            text="Reset laporan akan MENGHAPUS SEMUA\nriwayat transaksi Anda secara permanen.\nStok produk TIDAK akan dikembalikan.\n\nLanjutkan?",
            halign='center',
            valign='middle'
        ))

        btn_box = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(10))
        btn_cancel = Factory.SecondaryButton(text='Batal')
        btn_yes = Factory.DangerButton(text='Ya, Reset Semua')

        btn_box.add_widget(btn_cancel)
        btn_box.add_widget(btn_yes)
        content.add_widget(btn_box)

        popup = Popup(title='Konfirmasi Reset Laporan', content=content, size_hint=(0.88, 0.4))

        btn_cancel.bind(on_press=popup.dismiss)
        btn_yes.bind(on_press=lambda inst: self.reset_report(popup))
        popup.open()

    def reset_report(self, popup_instance):
        app = App.get_running_app()
        popup_instance.dismiss()

        if not fs.is_ready() or not app.user_uid:
            return

        try:
            docs = fs.query("transactions", [("users_id", "EQUAL", app.user_uid)])
            deleted = fs.delete_many("transactions", [d['doc_id'] for d in docs])

            app.report_cache = (0, 0, {})  # laporan kosong tampil seketika
            app.report_cache_uid = app.user_uid
            app.report_cache_time = time.time()
            app.history_cache = []
            app.history_cache_uid = app.user_uid
            app.history_cache_time = time.time()
            app._data_version += 1
            self.load_financial_report()
            Popup(
                title='Sukses',
                content=Label(text=f'Laporan berhasil direset.\n{deleted} riwayat transaksi dihapus.', halign='center'),
                size_hint=(0.85, 0.3)
            ).open()
        except Exception as e:
            print("Gagal reset laporan:", e)
            Popup(title='Gagal', content=Label(text=f'Gagal reset laporan: {e}'), size_hint=(0.85, 0.3)).open()

    _report_sig = None
    _report_loading = False

    def _report_message(self, text):
        layout = self.ids.report_content_layout
        layout.clear_widgets()
        layout.add_widget(Factory.MutedLabel(text=text, halign='center', font_size='13sp',
                                             size_hint_y=None, height=dp(100)))

    def load_financial_report(self, force=False):
        app = App.get_running_app()
        if not fs.is_ready() or not app.user_uid:
            return
        uid = app.user_uid

        cached = app.report_cache if app.report_cache_uid == uid else None
        if cached is not None:
            self._render_report(cached)  # instan; dilewati kalau datanya sama dengan yang tampil
            if not force and time.time() - app.report_cache_time < CACHE_MAX_AGE:
                return
        else:
            self._report_sig = None
            self._report_message("Memuat laporan...")

        if self._report_loading:
            return
        self._report_loading = True
        version = app._data_version

        def work():
            docs = fs.query("transactions", [("users_id", "EQUAL", uid)])
            total_omzet = 0
            total_transaksi = 0
            items_terjual = {}
            for t in docs:
                total_omzet += t.get('total', 0)
                total_transaksi += 1
                for item in t.get('items', []):
                    nama = item.get('nama', 'Produk')
                    items_terjual[nama] = items_terjual.get(nama, 0) + item.get('qty', 0)
            return (total_omzet, total_transaksi, items_terjual)

        def done(result, error):
            self._report_loading = False
            if error is not None:
                print("Gagal memuat laporan:", error)
                if self._report_sig is None:
                    self._report_message("Gagal memuat laporan.\nPeriksa koneksi internet,\nlalu buka ulang menu ini.")
                return
            if uid != app.user_uid:
                return
            if version != app._data_version:  # ada transaksi baru saat menunggu -> ambil ulang
                self.load_financial_report(force=True)
                return
            app.report_cache, app.report_cache_uid, app.report_cache_time = result, uid, time.time()
            self._render_report(result)

        run_in_thread(work, done)

    def _render_report(self, summary):
        total_omzet, total_transaksi, items_terjual = summary
        sig = (total_omzet, total_transaksi, tuple(sorted(items_terjual.items())))
        if sig == self._report_sig:
            return
        self._report_sig = sig

        layout = self.ids.report_content_layout
        layout.clear_widgets()

        summary_card = Factory.CustomCard(size_hint_y=None, height=dp(128), padding=dp(16), spacing=dp(4))
        summary_card.add_widget(Factory.MutedLabel(text="Total Pendapatan", font_size='13sp',
                                                   size_hint_y=None, height=dp(20)))
        summary_card.add_widget(Factory.LeftLabel(text=f"Rp {rupiah(total_omzet)}", bold=True,
                                                  font_size='26sp', color=CLR_SUCCESS,
                                                  size_hint_y=None, height=dp(42)))
        summary_card.add_widget(Factory.MutedLabel(text=f"{total_transaksi} transaksi tercatat",
                                                   size_hint_y=None, height=dp(20)))
        layout.add_widget(summary_card)

        layout.add_widget(Factory.SectionTitle(text="Produk Terjual"))

        if not items_terjual:
            layout.add_widget(Factory.MutedLabel(text="Belum ada data penjualan.", halign='center',
                                                 font_size='13sp', size_hint_y=None, height=dp(60)))
            return

        for nama_prod, qty_total in sorted(items_terjual.items(), key=lambda x: x[1], reverse=True):
            item_card = Factory.CustomCard(size_hint_y=None, height=dp(52), orientation='horizontal',
                                           padding=[dp(14), dp(8)], spacing=dp(8))
            item_card.add_widget(Factory.LeftLabel(text=nama_prod, shorten=True, shorten_from='right'))
            item_card.add_widget(Factory.RightLabel(text=f"{qty_total} pcs", bold=True, color=CLR_PRIMARY,
                                                    size_hint_x=None, width=dp(80)))
            layout.add_widget(item_card)


def format_transaction_time(raw):
    """Ubah nilai timestamp transaksi (format apapun yang dikembalikan Firestore)
    jadi teks 'dd/mm/yyyy HH:MM' yang enak dibaca. Kalau gagal parse, tampilkan
    versi ringkas dari nilai aslinya supaya tidak error di layar."""
    if raw is None or raw == '':
        return '-'
    if isinstance(raw, datetime.datetime):
        dt = raw
    elif isinstance(raw, (int, float)):
        try:
            dt = datetime.datetime.fromtimestamp(raw)
        except Exception:
            return str(raw)
    elif isinstance(raw, str):
        try:
            dt = datetime.datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if dt.tzinfo is not None:
                dt = dt.astimezone()
        except Exception:
            return raw.replace('T', ' ')[:16]
    else:
        return str(raw)
    return dt.strftime('%d/%m/%Y %H:%M')


class HistoryScreen(AppScreen):
    """Daftar transaksi yang sudah pernah tercatat, terbaru di paling atas.
    Data diambil dari koleksi 'transactions' yang sama dengan yang dipakai
    ReportScreen untuk ringkasan, jadi tidak perlu query tambahan di server."""

    _history_loading = False
    _history_sig = None
    _history_ev = None

    def _cancel_history_render(self):
        if self._history_ev is not None:
            self._history_ev.cancel()
            self._history_ev = None

    def _history_message(self, text):
        layout = self.ids.history_list
        self._cancel_history_render()
        layout.clear_widgets()
        layout.add_widget(Factory.MutedLabel(text=text, halign='center', font_size='13sp',
                                             size_hint_y=None, height=dp(120)))

    def load_history(self, force=False):
        app = App.get_running_app()
        if not fs.is_ready() or not app.user_uid:
            self._history_sig = None
            self._history_message("Belum login.")
            return
        uid = app.user_uid

        cached = app.history_cache if app.history_cache_uid == uid else None
        if cached is not None:
            self._render_history(cached)  # instan; dilewati kalau datanya sama dengan yang tampil
            if not force and time.time() - app.history_cache_time < CACHE_MAX_AGE:
                return
        else:
            self._history_sig = None
            self._history_message("Memuat riwayat transaksi...")

        if self._history_loading:
            return
        self._history_loading = True
        version = app._data_version

        def work():
            docs = fs.query("transactions", [("users_id", "EQUAL", uid)])
            docs.sort(key=lambda t: str(t.get('timestamp', '')), reverse=True)  # terbaru dulu
            return docs

        def done(result, error):
            self._history_loading = False
            if error is not None:
                print("Gagal memuat riwayat:", error)
                if self._history_sig is None:
                    self._history_message("Gagal memuat riwayat.\nPeriksa koneksi internet,\nlalu buka ulang menu ini.")
                return
            if uid != app.user_uid:  # akun berganti selama menunggu
                return
            if version != app._data_version:  # ada transaksi baru saat menunggu -> ambil ulang
                self.load_history(force=True)
                return
            app.history_cache, app.history_cache_uid, app.history_cache_time = result, uid, time.time()
            self._render_history(result)

        run_in_thread(work, done)

    def _render_history(self, docs):
        sig = tuple(d.get('doc_id') for d in docs)
        if sig == self._history_sig:
            return
        self._history_sig = sig

        layout = self.ids.history_list
        self._cancel_history_render()
        layout.clear_widgets()

        if not docs:
            layout.add_widget(Factory.MutedLabel(text="Belum ada transaksi yang tercatat.", halign='center',
                                                 font_size='13sp', size_hint_y=None, height=dp(120)))
            return

        # Baris digambar bertahap (beberapa per frame) supaya UI tidak macet saat riwayat banyak
        it = iter(docs)

        def step(dt):
            for _ in range(6):
                t = next(it, None)
                if t is None:
                    self._history_ev = None
                    return False
                layout.add_widget(self._make_history_row(t))
            return True

        if step(0):
            self._history_ev = Clock.schedule_interval(step, 0)

    def _make_history_row(self, t):
        jumlah_item = sum(item.get('qty', 0) for item in t.get('items', []))
        row = Factory.CustomCard(size_hint_y=None, height=dp(78), orientation='horizontal',
                                 padding=dp(12), spacing=dp(8))

        info_box = BoxLayout(orientation='vertical', spacing=dp(2))
        info_box.add_widget(Factory.LeftLabel(
            text=format_transaction_time(t.get('timestamp')), bold=True, font_size='13sp',
            size_hint_y=None, height=dp(22)))
        info_box.add_widget(Factory.MutedLabel(
            text=f"Kasir: {t.get('kasir_nama', '-')}", size_hint_y=None, height=dp(18)))
        info_box.add_widget(Factory.MutedLabel(
            text=f"{jumlah_item} item terjual", size_hint_y=None, height=dp(18)))

        right_box = BoxLayout(orientation='vertical', size_hint_x=None, width=dp(112), spacing=dp(4))
        right_box.add_widget(Factory.RightLabel(
            text=f"Rp {rupiah(int(t.get('total', 0)))}", bold=True, color=CLR_SUCCESS, font_size='14sp'))
        btn_detail = Factory.SecondaryButton(text='Lihat Detail', font_size='11sp',
                                             size_hint_y=None, height=dp(30))
        btn_detail.bind(on_press=lambda inst, tx=t: self.show_detail(tx))
        right_box.add_widget(btn_detail)

        row.add_widget(info_box)
        row.add_widget(right_box)
        return row

    def show_detail(self, t):
        content = BoxLayout(orientation='vertical', padding=dp(14), spacing=dp(8))
        content.add_widget(Factory.MutedLabel(
            text=format_transaction_time(t.get('timestamp')), size_hint_y=None, height=dp(20)))
        content.add_widget(Factory.MutedLabel(
            text=f"Kasir: {t.get('kasir_nama', '-')}", size_hint_y=None, height=dp(20)))

        scroll = ScrollView(size_hint_y=1, do_scroll_x=False)
        items_box = BoxLayout(orientation='vertical', spacing=dp(4), size_hint_y=None)
        items_box.bind(minimum_height=items_box.setter('height'))

        for item in t.get('items', []):
            subtotal = int(item.get('harga', 0)) * int(item.get('qty', 0))
            line = BoxLayout(size_hint_y=None, height=dp(28))
            line.add_widget(Factory.LeftLabel(
                text=f"{item.get('nama', '-')} x{item.get('qty', 0)}", font_size='12sp',
                shorten=True, shorten_from='right'))
            line.add_widget(Factory.RightLabel(
                text=f"Rp {rupiah(subtotal)}", font_size='12sp', size_hint_x=None, width=dp(90)))
            items_box.add_widget(line)

        scroll.add_widget(items_box)
        content.add_widget(scroll)

        content.add_widget(Factory.LeftLabel(
            text=f"Total: Rp {rupiah(int(t.get('total', 0)))}", bold=True, color=CLR_SUCCESS,
            font_size='16sp', size_hint_y=None, height=dp(30)))

        btn_close = Factory.SecondaryButton(text='Tutup', size_hint_y=None, height=dp(40))
        content.add_widget(btn_close)

        popup = Popup(title='Detail Transaksi', content=content, size_hint=(0.9, 0.7))
        btn_close.bind(on_press=popup.dismiss)
        popup.open()


def normalize_camera_url(raw):
    """Rapikan URL kamera yang diketik user.
      '192.168.1.5:8080'         -> 'http://192.168.1.5:8080/video'
      'http://192.168.1.5:8080'  -> 'http://192.168.1.5:8080/video'
    URL yang sudah punya path (mis. /shot.jpg atau rtsp://...) dibiarkan apa adanya."""
    url = (raw or '').strip()
    if not url:
        return ''
    if '://' not in url:
        url = 'http://' + url
    parts = urlparse(url)
    if parts.scheme.lower() in ('http', 'https') and parts.path in ('', '/'):
        url = urlunparse(parts._replace(path='/video'))
    return url


class CameraError(Exception):
    """Error kamera dengan pesan yang sudah ramah dibaca user."""


class CameraReader(threading.Thread):
    """
    Membaca frame kamera di THREAD TERPISAH, sehingga UI Kivy tidak membeku
    walau koneksi ke HP lambat / IP salah.

    - URL http(s) (IP Webcam, DroidCam, dll) dibaca langsung lewat `requests`
      (stream MJPEG atau snapshot JPEG) -> tidak bergantung pada FFmpeg milik OpenCV
      dan punya timeout yang jelas.
    - Sumber lain (indeks kamera 0, rtsp://, ...) memakai cv2.VideoCapture.
    - Hanya frame TERBARU yang disimpan, jadi gambar tidak "tertinggal" (lag).
    """

    def __init__(self, source):
        super().__init__(daemon=True)
        self.source = source
        self.error = ''
        self.got_frame = False
        self._frame = None
        self._is_new = False
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._resp = None

    # --- API untuk thread utama ---------------------------------------------
    def get_frame(self):
        """Kembalikan frame baru (sekali saja) atau None kalau belum ada yang baru."""
        with self._lock:
            if not self._is_new:
                return None
            self._is_new = False
            return self._frame

    def stop(self):
        self._stop_evt.set()
        resp = self._resp
        if resp is not None:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass

    # --- thread -----------------------------------------------------------
    def run(self):
        try:
            if isinstance(self.source, str) and self.source.lower().startswith(('http://', 'https://')):
                self._run_http()
            else:
                self._run_capture()
        except Exception as e:  # noqa: BLE001
            if not self._stop_evt.is_set():
                self.error = self._explain(e)
                print("Kamera error:", repr(e))

    def _set_frame(self, frame):
        with self._lock:
            self._frame = frame
            self._is_new = True
        self.got_frame = True

    def _push_jpeg(self, jpg):
        frame = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
        if frame is not None:
            self._set_frame(frame)

    # --- kamera IP lewat HTTP -------------------------------------------------
    def _candidates(self):
        """URL yang dicoba berurutan. Kalau /video gagal (404), coba path lain
        yang umum dipakai aplikasi IP camera."""
        parts = urlparse(self.source)
        urls = [self.source]
        if parts.path == '/video':
            for alt in ('/videofeed', '/mjpegfeed', '/shot.jpg'):
                urls.append(urlunparse(parts._replace(path=alt)))
        return urls

    def _run_http(self):
        last_err = None
        for url in self._candidates():
            retries = 0
            while not self._stop_evt.is_set():
                try:
                    self._stream_url(url)
                    return
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    if self._stop_evt.is_set():
                        return
                    if self.got_frame and retries < 3:
                        # sempat jalan lalu putus (WiFi goyang) -> sambung ulang
                        retries += 1
                        time.sleep(1)
                        continue
                    break
            if self._stop_evt.is_set():
                return
            # HP tidak terjangkau -> percuma mencoba path lain
            if self.got_frame or isinstance(last_err, requests.exceptions.ConnectionError):
                break
        if last_err is not None:
            raise last_err

    def _stream_url(self, url):
        session = requests.Session()
        try:
            resp = session.get(url, stream=True, timeout=(5, 8))
            self._resp = resp
            if resp.status_code == 401:
                raise CameraError("Kamera meminta username/password.\n"
                                  "Tulis di URL: http://user:password@IP:PORT/video")
            if resp.status_code != 200:
                raise CameraError(f"Kamera menjawab HTTP {resp.status_code}.\n"
                                  "Periksa alamat/path URL (untuk IP Webcam: /video).")
            ctype = resp.headers.get('Content-Type', '').lower()
            if ctype.startswith('image/'):
                self._poll_snapshot(session, url, resp)
            elif ctype.startswith('text/'):
                raise CameraError("Alamat itu membuka halaman web, bukan video.\n"
                                  "Gunakan alamat video, mis. http://IP:8080/video")
            else:
                self._read_mjpeg(resp)
        finally:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass

    def _read_mjpeg(self, resp):
        buf = b''
        for chunk in resp.iter_content(chunk_size=8192):
            if self._stop_evt.is_set():
                return
            if not chunk:
                continue
            buf += chunk
            end = buf.rfind(b'\xff\xd9')          # akhir JPEG (EOI)
            if end == -1:
                if len(buf) > 4_000_000:          # jaga-jaga data sampah
                    buf = b''
                continue
            start = buf.rfind(b'\xff\xd8', 0, end)  # awal JPEG (SOI)
            if start != -1:
                self._push_jpeg(buf[start:end + 2])  # ambil yang TERBARU saja
            buf = buf[end + 2:]
        if self._stop_evt.is_set():
            return
        if self.got_frame:
            raise CameraError("Koneksi ke kamera terputus.")
        raise CameraError("Alamat itu tidak mengirim video MJPEG.\n"
                          "Untuk IP Webcam gunakan http://IP:8080/video")

    def _poll_snapshot(self, session, url, resp):
        while not self._stop_evt.is_set():
            self._push_jpeg(resp.content)
            time.sleep(0.03)
            resp = session.get(url, timeout=(5, 8))
            self._resp = resp
            if resp.status_code != 200:
                raise CameraError(f"Kamera menjawab HTTP {resp.status_code}.")

    # --- sumber lain: webcam / rtsp ------------------------------------------
    def _run_capture(self):
        cap = cv2.VideoCapture(self.source)
        try:
            if not cap.isOpened():
                raise CameraError(f"Gagal membuka kamera: {self.source}")
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:  # noqa: BLE001
                pass
            fails = 0
            while not self._stop_evt.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    fails += 1
                    if fails > 50:
                        raise CameraError("Kamera berhenti mengirim gambar.")
                    time.sleep(0.05)
                    continue
                fails = 0
                self._set_frame(frame)
        finally:
            cap.release()

    @staticmethod
    def _explain(exc):
        if isinstance(exc, CameraError):
            return str(exc)
        rq = requests.exceptions
        if isinstance(exc, rq.SSLError):
            return "Koneksi aman (https) gagal.\nCoba gunakan http:// bukan https://"
        if isinstance(exc, rq.ConnectionError):
            return ("Tidak bisa terhubung ke kamera.\n"
                    "Pastikan HP & perangkat ini di WiFi yang sama, aplikasi kamera di HP "
                    "sudah dijalankan (Start server), serta IP dan port sudah benar.")
        if isinstance(exc, rq.Timeout):
            return "Kamera tidak mengirim gambar (timeout).\nCoba mulai ulang aplikasi kamera di HP."
        if isinstance(exc, (rq.InvalidURL, rq.MissingSchema, rq.InvalidSchema)):
            return "Format URL kamera tidak valid."
        return f"{type(exc).__name__}: {exc}"


class ScannerScreen(AppScreen):
    reader = None
    _texture = None
    _scan_busy = False
    _active = False
    _source = 0

    def _set_status(self, text):
        try:
            self.ids.camera_status.text = text
        except Exception:  # noqa: BLE001
            pass

    def on_enter(self):
        if not HAS_BARCODE:
            Popup(title='Error', content=Label(text='Library OpenCV/Pyzbar/Numpy belum terinstall!'), size_hint=(0.8, 0.25)).open()
            Clock.schedule_once(lambda dt: self.cancel_scan(), 0)
            return

        app = App.get_running_app()
        raw = app.ip_camera_url.strip()
        self._source = normalize_camera_url(raw) if raw else 0

        self._active = True
        self._scan_busy = False
        self._texture = None
        self.ids.camera_preview.texture = None
        self._set_status('Menghubungkan ke kamera...')

        # Koneksi dibuka di thread terpisah -> UI tidak membeku
        self.reader = CameraReader(self._source)
        self.reader.start()
        Clock.schedule_interval(self.update_frame, 1.0 / 30.0)

    def update_frame(self, dt):
        reader = self.reader
        if reader is None or not self._active:
            return

        frame = reader.get_frame()
        if frame is None:
            if reader.error:
                self._fail(reader.error)
            return

        try:
            self._set_status('')
            h, w = frame.shape[:2]
            if self._texture is None or tuple(self._texture.size) != (w, h):
                self._texture = Texture.create(size=(w, h), colorfmt='bgr')
                self._texture.flip_vertical()
                self.ids.camera_preview.texture = self._texture
            self._texture.blit_buffer(frame.tobytes(), colorfmt='bgr', bufferfmt='ubyte')
            self.ids.camera_preview.canvas.ask_update()

            # Decode barcode di thread lain; frame yang datang saat sibuk dilewati
            if not self._scan_busy:
                self._scan_busy = True
                run_in_thread(lambda f=frame: self._decode(f), self._on_decoded)
        except Exception as e:  # noqa: BLE001
            print("Frame error:", e)

    @staticmethod
    def _decode(frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        for barcode in pyzbar.decode(gray):
            return barcode.data.decode('utf-8', 'ignore')
        return None

    def _on_decoded(self, result, err):
        self._scan_busy = False
        if err is not None:
            print("Decode error:", err)
            return
        if result and self._active:
            self.stop_camera()
            self.handle_scanned_barcode(result)

    def _fail(self, message):
        self.stop_camera()
        if self._source:
            message = f"{message}\n\nURL: {self._source}"
        show_popup('Error Kamera', message, 'error')
        self.cancel_scan()

    def handle_scanned_barcode(self, code):
        app = App.get_running_app()

        if app.scan_mode == 'fill_input':
            edit_screen = self.manager.get_screen('product_edit')
            self.manager.current = 'product_edit'
            edit_screen.open_edit_popup(scanned_code=code)
        else:
            self.search_product_by_barcode(code)

    def search_product_by_barcode(self, code):
        app = App.get_running_app()
        if not fs.is_ready() or not app.user_uid:
            self.manager.current = 'catalog'
            return

        # Cari di data yang sudah dimuat dulu (instan, tanpa internet)
        cached = app.get_cached_products()
        if cached is not None:
            match = next((p for p in cached if str(p.get('barcode', '')).strip() == code), None)
            if match:
                self.manager.get_screen('catalog').add_to_cart(match, match['doc_id'])
                self.manager.current = 'catalog'
                return

        try:
            docs = fs.query("products", [("users_id", "EQUAL", app.user_uid),
                                         ("barcode", "EQUAL", code)])
            found = False
            for p in docs:
                found = True
                self.manager.get_screen('catalog').add_to_cart(p, p['doc_id'])
                break

            if not found:
                Popup(title='Tidak Ditemukan', content=Label(text=f'Barcode: {code}\ntidak terdaftar pada akun ini'), size_hint=(0.8, 0.25)).open()
        except Exception as e:
            print("Error barcode:", e)
        
        self.manager.current = 'catalog'

    def cancel_scan(self):
        self.stop_camera()
        app = App.get_running_app()
        if app.scan_mode == 'fill_input':
            self.manager.current = 'product_edit'
            self.manager.get_screen('product_edit').open_edit_popup()
        else:
            self.manager.current = app.previous_screen or 'catalog'

    def stop_camera(self):
        self._active = False
        Clock.unschedule(self.update_frame)
        if self.reader is not None:
            self.reader.stop()
            self.reader = None
        self._set_status('')

    def on_leave(self):
        self.stop_camera()


class POSNaufalApp(App):
    cart = ListProperty([])
    id_token = StringProperty('')
    user_email = StringProperty('')
    user_uid = StringProperty('')
    kasir_name = StringProperty('Kasir 1')
    ip_camera_url = StringProperty('')
    hw_scan_enabled = BooleanProperty(True)
    hw_scan_status = StringProperty('Siap memindai otomatis')
    current_screen = StringProperty('login')
    previous_screen = StringProperty('catalog')
    scan_mode = StringProperty('cart')

    printer_status = StringProperty('Belum terhubung')
    printer_connected = BooleanProperty(False)

    store_name = StringProperty(DEFAULT_STORE_NAME)
    store_address = StringProperty(DEFAULT_STORE_ADDRESS)
    paper_width = StringProperty('58mm')
    auto_print = BooleanProperty(True)

    # ------------------------------------------------------------------
    # CACHE DATA: pindah halaman langsung menampilkan data yang sudah ada,
    # sementara pembaruan dari Firestore berjalan di thread terpisah.
    # ------------------------------------------------------------------
    products_cache = None      # list produk terakhir (atau None)
    products_cache_uid = ''
    products_cache_time = 0.0
    report_cache = None        # (total_omzet, total_transaksi, {nama: qty})
    report_cache_uid = ''
    report_cache_time = 0.0
    history_cache = None       # list transaksi terakhir (urut terbaru dulu)
    history_cache_uid = ''
    history_cache_time = 0.0
    _data_version = 0          # naik tiap ada perubahan data lokal (hasil fetch lama dibuang)
    _products_loading = False

    def get_cached_products(self):
        if self.products_cache is not None and self.products_cache_uid == self.user_uid:
            return self.products_cache
        return None

    def clear_data_cache(self):
        self.products_cache = None
        self.report_cache = None
        self.history_cache = None
        self._data_version += 1

    def refresh_products(self, callback=None, on_error=None, force=False):
        """Ambil produk dari Firestore di thread terpisah. callback(products) dipanggil di
        thread utama hanya bila data pertama kali dimuat atau ternyata berubah."""
        uid = self.user_uid
        if not fs.is_ready() or not uid:
            return
        fresh = (self.get_cached_products() is not None
                 and time.time() - self.products_cache_time < CACHE_MAX_AGE)
        if fresh and not force:
            return
        self._products_waiters.append((callback, on_error))
        if self._products_loading:
            return
        self._start_products_fetch(uid)

    def _start_products_fetch(self, uid):
        self._products_loading = True
        version = self._data_version

        def work():
            return fs.query("products", [("users_id", "EQUAL", uid)])

        def done(result, error):
            self._products_loading = False
            if uid != self.user_uid:  # akun berganti selama menunggu
                self._products_waiters = []
                return
            if error is None and version != self._data_version:
                self._start_products_fetch(uid)  # ada perubahan lokal -> ambil ulang yang terbaru
                return
            waiters, self._products_waiters = self._products_waiters, []
            if error is not None:
                print("Gagal mengambil produk:", error)
                for _cb, err_cb in waiters:
                    if err_cb:
                        err_cb(error)
                return
            old = self.get_cached_products()
            self.products_cache, self.products_cache_uid = result, uid
            self.products_cache_time = time.time()
            if old is None or products_signature(old) != products_signature(result):
                for cb, _err in waiters:
                    if cb:
                        cb(result)

        run_in_thread(work, done)

    def upsert_cached_product(self, doc_id, data):
        """Terapkan hasil tambah/edit produk ke cache lokal, supaya tampil seketika."""
        cache = self.get_cached_products()
        if cache is None:
            return
        rest = [p for p in cache if p.get('doc_id') != doc_id]
        old = next((p for p in cache if p.get('doc_id') == doc_id), {})
        rest.append({**old, **data, 'doc_id': doc_id})
        rest.sort(key=lambda p: p['doc_id'])
        self.products_cache = rest
        self._data_version += 1

    def drop_cached_product(self, doc_id):
        cache = self.get_cached_products()
        if cache is None:
            return
        self.products_cache = [p for p in cache if p.get('doc_id') != doc_id]
        self._data_version += 1

    def apply_sale_to_cache(self, items):
        """Setelah checkout: kurangi stok di cache dan tandai laporan perlu dimuat ulang."""
        cache = self.get_cached_products()
        if cache is not None:
            qty = {i['id']: i['qty'] for i in items}
            self.products_cache = [
                {**p, 'stok': int(p.get('stok', 0)) - qty[p['doc_id']]} if p.get('doc_id') in qty else p
                for p in cache
            ]
        self.report_cache_time = 0.0
        self._data_version += 1

    def build(self):
        self._products_waiters = []
        Window.clearcolor = CLR_BG
        if ANDROID:
            # Keyboard layar tidak menutupi kolom isian (login, pengaturan, form produk)
            Window.softinput_mode = 'below_target'

        # Android -> printer Bluetooth. Laptop/PC -> USB/Windows, LAN/WiFi, atau COM/Bluetooth.
        self.printer = BluetoothPrinterManager() if ANDROID else DesktopPrinterManager()
        self.google_signin = GoogleSignInHelper()

        # Penyimpanan sesi login supaya user tidak perlu login ulang tiap buka app
        self.store = JsonStore(os.path.join(self.user_data_dir, 'session.json'))
        fs.configure(FIREBASE_PROJECT_ID, lambda: self.id_token, self.refresh_id_token)
        self.load_printer_settings()
        self.load_camera_settings()
        self.load_hw_scan_settings()
        self.hw_scanner = HardwareScanner(self.handle_hardware_scan)
        self.hw_scanner.enabled = self.hw_scan_enabled

        root = Builder.load_string(KV)
        # Perpindahan tab instan (tanpa animasi) supaya terasa responsif
        root.transition = NoTransition()
        return root

    def on_start(self):
        self.try_auto_login()
        self.restore_printer_connection()

    # ------------------------------------------------------------------
    # PRINTER: simpan / muat pengaturan, cetak
    # ------------------------------------------------------------------
    def load_printer_settings(self):
        self._saved_printer = {}
        try:
            if not self.store.exists('printer'):
                return
            d = self.store.get('printer')
            self._saved_printer = d
            self.store_name = d.get('store_name') or DEFAULT_STORE_NAME
            self.store_address = d.get('store_address', DEFAULT_STORE_ADDRESS)
            self.paper_width = d.get('paper', '58mm')
            self.auto_print = bool(d.get('auto_print', True))
        except Exception as e:
            print("Gagal memuat pengaturan printer:", e)

    def save_printer_settings(self):
        try:
            data = dict(self._saved_printer)
            data.update(
                store_name=self.store_name,
                store_address=self.store_address,
                paper=self.paper_width,
                auto_print=self.auto_print,
            )
            p = self.printer
            if p.kind == 'bluetooth':
                if p.device_address:
                    data['address'] = p.device_address
            elif p.mode and p.target:
                data['mode'], data['target'] = p.mode, p.target
            self.store.put('printer', **data)
            self._saved_printer = data
        except Exception as e:
            print("Gagal menyimpan pengaturan printer:", e)

    def restore_printer_connection(self):
        """Sambungkan ulang otomatis ke printer terakhir yang dipakai (di background)."""
        d = self._saved_printer
        if not d or not self.printer.is_supported():
            return

        if self.printer.kind == 'bluetooth':
            addr = d.get('address')
            if not addr or not self.printer.has_permissions():
                return
            work = lambda: self.printer.connect(addr)
        else:
            mode, target = d.get('mode'), d.get('target')
            if not mode or not target:
                return
            work = lambda: self.printer.connect(mode, target)

        def done(result, err):
            ok, msg = result if err is None else (False, str(err))
            self.printer_connected = ok
            self.printer_status = msg if ok else 'Belum terhubung'

        run_in_thread(work, done)

    # ------------------------------------------------------------------
    # KAMERA IP: simpan / muat URL (bertahan setelah app ditutup)
    # ------------------------------------------------------------------
    def load_camera_settings(self):
        try:
            if self.store.exists('camera'):
                self.ip_camera_url = self.store.get('camera').get('ip_url', '')
        except Exception as e:
            print("Gagal memuat pengaturan kamera:", e)

    def save_camera_settings(self):
        try:
            self.store.put('camera', ip_url=self.ip_camera_url)
        except Exception as e:
            print("Gagal menyimpan pengaturan kamera:", e)

    # ------------------------------------------------------------------
    # ALAT SCAN BARCODE FISIK: simpan / muat pengaturan, tangani hasil scan
    # ------------------------------------------------------------------
    def load_hw_scan_settings(self):
        try:
            if self.store.exists('hw_scan'):
                self.hw_scan_enabled = bool(self.store.get('hw_scan').get('enabled', True))
                if not self.hw_scan_enabled:
                    self.hw_scan_status = 'Nonaktif'
        except Exception as e:
            print("Gagal memuat pengaturan alat scan:", e)

    def save_hw_scan_settings(self):
        try:
            self.store.put('hw_scan', enabled=self.hw_scan_enabled)
        except Exception as e:
            print("Gagal menyimpan pengaturan alat scan:", e)

    def set_hw_scan_enabled(self, value):
        self.hw_scan_enabled = value
        self.hw_scanner.enabled = value
        self.hw_scan_status = 'Siap memindai otomatis' if value else 'Nonaktif'
        self.save_hw_scan_settings()

    def handle_hardware_scan(self, code):
        """Dipanggil begitu HardwareScanner mendeteksi kode dari alat scan fisik,
        dari layar manapun sedang aktif saat itu."""
        code = code.strip()
        if not code or not self.root:
            return
        self.hw_scan_status = f"Barcode terbaca: {code}"

        screen = self.current_screen
        if screen == 'product_edit':
            edit_screen = self.root.get_screen('product_edit')
            if edit_screen.fill_scanned_barcode(code):
                return
        if screen in ('catalog', 'cart'):
            self.root.get_screen('catalog').handle_hardware_barcode(code)

    def set_auto_print(self, value):
        if value != self.auto_print:
            self.auto_print = value
            self.save_printer_settings()

    def make_receipt_builder(self):
        return ReceiptBuilder(self.store_name, self.store_address, self.paper_width)

    def print_receipt_async(self, now_dt, items, total, callback=None):
        """Cetak struk di background. callback(ok, pesan) dipanggil di thread utama."""
        data = self.make_receipt_builder().as_escpos(self.kasir_name, now_dt, items, total)

        def done(result, err):
            ok, msg = result if err is None else (False, f"Gagal mencetak: {err}")
            if callback:
                callback(ok, msg)

        run_in_thread(lambda: self.printer.print_bytes(data), done)

    def try_auto_login(self):
        """Cek sesi tersimpan; kalau ada refresh token yang valid, login otomatis
        tanpa menampilkan layar login sama sekali."""
        if not self.store.exists('session'):
            return

        data = self.store.get('session')
        refresh_token = data.get('refresh_token', '')
        if not refresh_token:
            return

        url = f"https://securetoken.googleapis.com/v1/token?key={FIREBASE_WEB_API_KEY}"
        payload = {"grant_type": "refresh_token", "refresh_token": refresh_token}

        try:
            res = requests.post(url, data=payload, timeout=10)
            result = res.json()

            if "error" in result:
                # Refresh token kadaluarsa/dicabut -> hapus sesi, minta login manual
                self.clear_session()
                return

            self.id_token = result["id_token"]
            self.user_uid = result["user_id"]
            self.user_email = data.get('email', '')
            self.kasir_name = data.get('kasir_name', 'Kasir 1')
            self.cart = []

            # Simpan lagi (refresh_token biasanya tetap sama, tapi jaga-jaga jika berubah)
            self.save_session(result.get("refresh_token", refresh_token))

            if self.root:
                self.root.current = 'catalog'
        except Exception as e:
            print("Auto-login gagal (mungkin tidak ada koneksi):", e)

    def refresh_id_token(self):
        """Tukar refresh token tersimpan menjadi id_token baru (id_token hanya berlaku ~1 jam).
        Dipanggil otomatis oleh firestore_rest saat server membalas 401."""
        try:
            if not self.store.exists('session'):
                return False
            refresh_token = self.store.get('session').get('refresh_token', '')
            if not refresh_token:
                return False
            url = f"https://securetoken.googleapis.com/v1/token?key={FIREBASE_WEB_API_KEY}"
            res = requests.post(url, data={"grant_type": "refresh_token",
                                           "refresh_token": refresh_token}, timeout=10)
            result = res.json()
            if "error" in result:
                return False
            self.id_token = result["id_token"]
            self.save_session(result.get("refresh_token", refresh_token))
            return True
        except Exception as e:
            print("Gagal refresh token:", e)
            return False

    def save_session(self, refresh_token):
        """Simpan refresh token secara lokal agar sesi bertahan meski app ditutup."""
        if not refresh_token:
            return
        try:
            self.store.put(
                'session',
                refresh_token=refresh_token,
                email=self.user_email,
                kasir_name=self.kasir_name
            )
        except Exception as e:
            print("Gagal simpan sesi:", e)

    def persist_kasir_name(self):
        """Simpan nama kasir ke sesi lokal supaya tidak kembali ke nama lama saat app dibuka ulang."""
        try:
            if self.store.exists('session'):
                data = dict(self.store.get('session'))
                data['kasir_name'] = self.kasir_name
                self.store.put('session', **data)
        except Exception as e:
            print("Gagal simpan nama kasir:", e)

    def clear_session(self):
        try:
            if self.store.exists('session'):
                self.store.delete('session')
        except Exception as e:
            print("Gagal hapus sesi:", e)

    def logout(self):
        self.id_token = ''
        self.user_email = ''
        self.user_uid = ''
        self.kasir_name = 'Kasir 1'
        self.cart = []
        self.clear_data_cache()
        self.clear_session()
        self.root.current = 'login'


if __name__ == '__main__':
=======
import os
import json
import time
import requests
import datetime
import firebase_admin
from firebase_admin import credentials, firestore

from kivy.app import App
from kivy.lang import Builder
from kivy.uix.screenmanager import ScreenManager, Screen, SlideTransition, FadeTransition
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.popup import Popup
from kivy.uix.image import Image
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget
from kivy.properties import ListProperty, NumericProperty, StringProperty, ObjectProperty, BooleanProperty
from kivy.clock import Clock
from kivy.graphics.texture import Texture
from kivy.graphics import Color, RoundedRectangle
from kivy.core.window import Window
from kivy.factory import Factory
from kivy.animation import Animation
from kivy.storage.jsonstore import JsonStore
from kivy.utils import platform

# --- DETEKSI PLATFORM ANDROID (untuk Bluetooth & Google Sign-In) ---
# Fitur Bluetooth printer & Google Sign-In hanya berjalan penuh saat di-build
# menjadi APK Android (lewat buildozer), karena keduanya butuh API native Android.
# Saat dijalankan di PC/desktop untuk pengetesan tampilan, kedua fitur ini akan
# menampilkan pesan "tidak tersedia di perangkat ini" alih-alih error/crash.
ANDROID = (platform == 'android')

HAS_ANDROID_BT = False
HAS_ANDROID_GSI = False

if ANDROID:
    try:
        from jnius import autoclass, cast
        from android import activity, mActivity
        from android.permissions import request_permissions, Permission
        HAS_ANDROID_BT = True
        HAS_ANDROID_GSI = True
    except Exception as e:
        print("Modul Android (pyjnius) tidak lengkap:", e)

# --- SIMULASI WINDOW UKURAN HP ---
Window.size = (360, 740)

# --- HARDWARE & PDF INTEGRATION ---
try:
    import cv2
    from pyzbar import pyzbar
    HAS_BARCODE = True
except ImportError:
    HAS_BARCODE = False

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

# --- FIREBASE SETUP ---
CREDENTIALS_PATH = "firebase_credentials.json"
if os.path.exists(CREDENTIALS_PATH):
    cred = credentials.Certificate(CREDENTIALS_PATH)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
else:
    print(f"Warning: {CREDENTIALS_PATH} tidak ditemukan.")
    db = None

FIREBASE_WEB_API_KEY = "AIzaSyCVnHYAdmAevjNaN2uCmO0bqz4cPcDmjI0"

# Ganti dengan Web Client ID (bukan Android Client ID) dari Google Cloud Console
# yang sudah dihubungkan sebagai OAuth Client ke project Firebase ini.
GOOGLE_WEB_CLIENT_ID = "GANTI_DENGAN_WEB_CLIENT_ID_ANDA.apps.googleusercontent.com"

SPP_UUID = "00001101-0000-1000-8000-00805F9B34FB"  # UUID standar Serial Port Profile


class BluetoothPrinterManager:
    """
    Mengelola koneksi & pencetakan ke printer struk thermal Bluetooth (ESC/POS)
    lewat profil Serial Port (SPP). Hanya aktif di Android (pakai pyjnius).
    """

    def __init__(self):
        self.socket = None
        self.output_stream = None
        self.device_name = None
        self.device_address = None

    def is_supported(self):
        return HAS_ANDROID_BT

    def request_permissions(self, callback=None):
        if not HAS_ANDROID_BT:
            return
        try:
            perms = [Permission.BLUETOOTH_CONNECT, Permission.BLUETOOTH_SCAN,
                     Permission.ACCESS_FINE_LOCATION]
            request_permissions(perms, callback)
        except Exception as e:
            print("Gagal minta izin bluetooth:", e)

    def list_paired_devices(self):
        """Kembalikan list tuple (nama, alamat_mac) printer/perangkat yang sudah di-pairing."""
        if not HAS_ANDROID_BT:
            return []
        try:
            BluetoothAdapter = autoclass('android.bluetooth.BluetoothAdapter')
            adapter = BluetoothAdapter.getDefaultAdapter()
            if adapter is None:
                return []
            paired = adapter.getBondedDevices().toArray()
            return [(d.getName(), d.getAddress()) for d in paired]
        except Exception as e:
            print("Gagal ambil daftar perangkat bluetooth:", e)
            return []

    def connect(self, address):
        """Sambungkan ke printer berdasarkan alamat MAC-nya."""
        if not HAS_ANDROID_BT:
            return False, "Bluetooth hanya tersedia di aplikasi Android."
        try:
            BluetoothAdapter = autoclass('android.bluetooth.BluetoothAdapter')
            UUID = autoclass('java.util.UUID')
            adapter = BluetoothAdapter.getDefaultAdapter()
            device = adapter.getRemoteDevice(address)
            uuid = UUID.fromString(SPP_UUID)

            self.socket = device.createRfcommSocketToServiceRecord(uuid)
            adapter.cancelDiscovery()
            self.socket.connect()
            self.output_stream = self.socket.getOutputStream()
            self.device_address = address
            self.device_name = device.getName()
            return True, f"Terhubung ke {self.device_name}"
        except Exception as e:
            self.socket = None
            self.output_stream = None
            return False, f"Gagal konek printer: {e}"

    def disconnect(self):
        try:
            if self.socket is not None:
                self.socket.close()
        except Exception:
            pass
        self.socket = None
        self.output_stream = None
        self.device_name = None
        self.device_address = None

    def is_connected(self):
        return self.output_stream is not None

    def _write_bytes(self, data: bytes):
        JByteArray = None
        try:
            # pyjnius otomatis mengonversi bytes python -> byte[] Java pada put/write
            self.output_stream.write(data)
            self.output_stream.flush()
        except Exception as e:
            print("Gagal kirim data ke printer:", e)
            raise

    def print_receipt(self, kasir_name, now_dt, items, total, alamat_toko="Jl. Veteran No.99, Kediri, Jawa Timur"):
        """Cetak struk memakai perintah ESC/POS dasar (potong kertas otomatis di akhir)."""
        if not self.is_connected():
            return False, "Printer belum terhubung. Sambungkan dulu lewat menu Akun."

        try:
            ESC = b'\x1b'
            GS = b'\x1d'
            INIT = ESC + b'@'
            ALIGN_CENTER = ESC + b'a' + b'\x01'
            ALIGN_LEFT = ESC + b'a' + b'\x00'
            BOLD_ON = ESC + b'E' + b'\x01'
            BOLD_OFF = ESC + b'E' + b'\x00'
            FEED_CUT = b'\n\n\n\n' + GS + b'V' + b'\x42' + b'\x00'

            buf = bytearray()
            buf += INIT
            buf += ALIGN_CENTER
            buf += BOLD_ON + b"POS NAUFAL APP\n" + BOLD_OFF
            buf += (alamat_toko + "\n").encode('utf-8', errors='ignore')
            buf += b"--------------------------------\n"
            buf += ALIGN_LEFT
            buf += f"Kasir : {kasir_name}\n".encode('utf-8')
            buf += f"Tgl   : {now_dt.strftime('%d/%m/%Y %H:%M:%S')}\n".encode('utf-8')
            buf += b"--------------------------------\n"

            for item in items:
                sub = item['harga'] * item['qty']
                buf += f"{item['nama']}\n".encode('utf-8', errors='ignore')
                line = f"  {item['qty']} x Rp {item['harga']:,} = Rp {sub:,}\n"
                buf += line.encode('utf-8', errors='ignore')

            buf += b"--------------------------------\n"
            buf += BOLD_ON
            buf += f"TOTAL: Rp {total:,}\n".encode('utf-8', errors='ignore')
            buf += BOLD_OFF
            buf += ALIGN_CENTER
            buf += b"--------------------------------\n"
            buf += b"Terima Kasih!\n"
            buf += FEED_CUT

            self._write_bytes(bytes(buf))
            return True, "Struk berhasil dicetak."
        except Exception as e:
            return False, f"Gagal mencetak: {e}"


class GoogleSignInHelper:
    """
    Membungkus alur Google Sign-In native Android, lalu menukar id_token Google
    tersebut ke Firebase lewat endpoint REST accounts:signInWithIdp.
    """
    RC_SIGN_IN = 9001

    def __init__(self):
        self._callback = None
        if HAS_ANDROID_GSI:
            try:
                activity.bind(on_activity_result=self._on_activity_result)
            except Exception as e:
                print("Gagal bind activity result:", e)

    def is_supported(self):
        return HAS_ANDROID_GSI

    def sign_in(self, on_result):
        """on_result(success: bool, data_or_error)"""
        self._callback = on_result

        if not HAS_ANDROID_GSI:
            on_result(False, "Google Sign-In hanya tersedia di aplikasi Android.")
            return

        try:
            GoogleSignInOptions = autoclass('com.google.android.gms.auth.api.signin.GoogleSignInOptions')
            GoogleSignInOptionsBuilder = autoclass('com.google.android.gms.auth.api.signin.GoogleSignInOptions$Builder')
            GoogleSignIn = autoclass('com.google.android.gms.auth.api.signin.GoogleSignIn')
            DEFAULT_SIGN_IN = GoogleSignInOptions.DEFAULT_SIGN_IN

            gso_builder = GoogleSignInOptionsBuilder(DEFAULT_SIGN_IN)
            gso_builder.requestIdToken(GOOGLE_WEB_CLIENT_ID)
            gso_builder.requestEmail()
            gso = gso_builder.build()

            client = GoogleSignIn.getClient(mActivity, gso)
            intent = client.getSignInIntent()
            mActivity.startActivityForResult(intent, self.RC_SIGN_IN)
        except Exception as e:
            on_result(False, f"Gagal membuka Google Sign-In: {e}")

    def _on_activity_result(self, request_code, result_code, intent):
        if request_code != self.RC_SIGN_IN or self._callback is None:
            return
        try:
            GoogleSignIn = autoclass('com.google.android.gms.auth.api.signin.GoogleSignIn')
            task = GoogleSignIn.getSignedInAccountFromIntent(intent)
            account = task.getResult(Exception)
            id_token = account.getIdToken()
            self._exchange_with_firebase(id_token)
        except Exception as e:
            self._callback(False, f"Login Google dibatalkan atau gagal: {e}")

    def _exchange_with_firebase(self, google_id_token):
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithIdp?key={FIREBASE_WEB_API_KEY}"
        post_body = f"id_token={google_id_token}&providerId=google.com"
        payload = {
            "postBody": post_body,
            "requestUri": "http://localhost",
            "returnIdpCredential": True,
            "returnSecureToken": True
        }
        try:
            res = requests.post(url, json=payload, timeout=15)
            data = res.json()
            if "error" in data:
                self._callback(False, data["error"]["message"])
            else:
                self._callback(True, data)
        except Exception as e:
            self._callback(False, f"Koneksi ke server gagal: {e}")


# --- KIVY UI STYLING ---
KV = '''
#:import Window kivy.core.window.Window

<CustomCard@BoxLayout>:
    orientation: 'vertical'
    padding: 12
    spacing: 6
    size_hint_y: None
    height: 165
    canvas.before:
        Color:
            rgba: (0.118, 0.161, 0.231, 1)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [14,]
        Color:
            rgba: (0.2, 0.27, 0.38, 0.4)
        Line:
            rounded_rectangle: (self.x, self.y, self.width, self.height, 14)
            width: 1

<PrimaryButton@Button>:
    background_normal: ''
    background_color: (0.231, 0.51, 0.965, 1)
    color: (1, 1, 1, 1)
    bold: True
    canvas.before:
        Color:
            rgba: (0.231, 0.51, 0.965, 1) if self.state == 'normal' else (0.18, 0.4, 0.78, 1)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [10,]

<GoogleButton@Button>:
    background_normal: ''
    background_color: (1, 1, 1, 1) if self.state == 'normal' else (0.9, 0.9, 0.9, 1)
    color: (0.118, 0.161, 0.231, 1)
    bold: True
    canvas.before:
        Color:
            rgba: (1, 1, 1, 1) if self.state == 'normal' else (0.9, 0.9, 0.9, 1)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [10,]
        Color:
            rgba: (0.2, 0.27, 0.38, 0.5)
        Line:
            rounded_rectangle: (self.x, self.y, self.width, self.height, 10)
            width: 1

<ModernTextInput@TextInput>:
    multiline: False
    size_hint_y: None
    height: 44
    background_normal: ''
    background_color: (0.118, 0.161, 0.231, 1)
    foreground_color: (0.95, 0.96, 0.98, 1)
    hint_text_color: (0.5, 0.57, 0.67, 1)
    padding: [14, 12, 14, 12]
    canvas.after:
        Color:
            rgba: (0.2, 0.27, 0.38, 0.6)
        Line:
            rounded_rectangle: (self.x, self.y, self.width, self.height, 8)
            width: 1

<NavBar@BoxLayout>:
    size_hint_y: None
    height: 62
    spacing: 2
    padding: [4, 4, 4, 4]
    canvas.before:
        Color:
            rgba: (0.059, 0.09, 0.165, 1)
        Rectangle:
            pos: self.pos
            size: self.size
        Color:
            rgba: (0.2, 0.27, 0.38, 0.4)
        Line:
            points: [self.x, self.y + self.height, self.x + self.width, self.y + self.height]
            width: 1

    Button:
        text: 'Katalog'
        font_size: '11sp'
        bold: True if app.current_screen == 'catalog' else False
        color: (0.231, 0.51, 0.965, 1) if app.current_screen == 'catalog' else (0.5, 0.57, 0.67, 1)
        background_normal: ''
        background_color: (0.118, 0.161, 0.231, 0.6) if app.current_screen == 'catalog' else (0, 0, 0, 0)
        on_press: app.root.current = 'catalog'

    Button:
        text: f'Keranjang ({len(app.cart)})'
        font_size: '11sp'
        bold: True if app.current_screen == 'cart' else False
        color: (0.231, 0.51, 0.965, 1) if app.current_screen == 'cart' else (0.5, 0.57, 0.67, 1)
        background_normal: ''
        background_color: (0.118, 0.161, 0.231, 0.6) if app.current_screen == 'cart' else (0, 0, 0, 0)
        on_press: app.root.current = 'cart'

    Button:
        text: 'Kelola'
        font_size: '11sp'
        bold: True if app.current_screen == 'product_edit' else False
        color: (0.231, 0.51, 0.965, 1) if app.current_screen == 'product_edit' else (0.5, 0.57, 0.67, 1)
        background_normal: ''
        background_color: (0.118, 0.161, 0.231, 0.6) if app.current_screen == 'product_edit' else (0, 0, 0, 0)
        on_press: app.root.current = 'product_edit'

    Button:
        text: 'Laporan'
        font_size: '11sp'
        bold: True if app.current_screen == 'report' else False
        color: (0.231, 0.51, 0.965, 1) if app.current_screen == 'report' else (0.5, 0.57, 0.67, 1)
        background_normal: ''
        background_color: (0.118, 0.161, 0.231, 0.6) if app.current_screen == 'report' else (0, 0, 0, 0)
        on_press: app.root.current = 'report'

    Button:
        text: 'Akun'
        font_size: '11sp'
        bold: True if app.current_screen == 'settings' else False
        color: (0.231, 0.51, 0.965, 1) if app.current_screen == 'settings' else (0.5, 0.57, 0.67, 1)
        background_normal: ''
        background_color: (0.118, 0.161, 0.231, 0.6) if app.current_screen == 'settings' else (0, 0, 0, 0)
        on_press: app.root.current = 'settings'

ScreenManager:
    id: screen_manager
    LoginScreen:
    CatalogScreen:
    CartScreen:
    ProductEditScreen:
    ReportScreen:
    SettingsScreen:
    ScannerScreen:

<LoginScreen>:
    name: 'login'
    canvas.before:
        Color:
            rgba: (0.059, 0.09, 0.165, 1)
        Rectangle:
            pos: self.pos
            size: self.size

    BoxLayout:
        orientation: 'vertical'
        padding: 28
        spacing: 10

        Widget:
            size_hint_y: 0.05

        Image:
            source: 'logo.png'
            size_hint_y: None
            height: 90
            allow_stretch: True
            keep_ratio: True

        Label:
            text: 'POS NAUFAL APP'
            font_size: '24sp'
            bold: True
            color: (0.231, 0.51, 0.965, 1)
            size_hint_y: None
            height: 35

        Label:
            id: mode_title
            text: 'Silakan masuk ke akun Anda'
            font_size: '13sp'
            color: (0.5, 0.57, 0.67, 1)
            size_hint_y: None
            height: 20

        Widget:
            size_hint_y: 0.03

        ModernTextInput:
            id: email_input
            hint_text: 'Email Kasir / Toko'

        ModernTextInput:
            id: password_input
            hint_text: 'Password'
            password: True

        Widget:
            size_hint_y: 0.02

        PrimaryButton:
            id: btn_submit
            text: 'MASUK SEKARANG'
            size_hint_y: None
            height: 46
            on_press: root.do_auth()

        BoxLayout:
            size_hint_y: None
            height: 24
            padding: [0, 8]
            Widget:
                canvas.before:
                    Color:
                        rgba: (0.2, 0.27, 0.38, 0.5)
                    Line:
                        points: [self.x, self.center_y, self.x + self.width * 0.42, self.center_y]
            Label:
                text: 'atau'
                size_hint_x: None
                width: 40
                font_size: '11sp'
                color: (0.5, 0.57, 0.67, 1)
            Widget:
                canvas.before:
                    Color:
                        rgba: (0.2, 0.27, 0.38, 0.5)
                    Line:
                        points: [self.x + self.width * 0.58, self.center_y, self.right, self.center_y]

        GoogleButton:
            id: btn_google
            text: 'Masuk dengan Google'
            size_hint_y: None
            height: 46
            on_press: root.sign_in_with_google()

        BoxLayout:
            size_hint_y: None
            height: 35
            spacing: 5
            Button:
                id: btn_toggle_mode
                text: 'Buat Akun Baru'
                background_normal: ''
                background_color: (0, 0, 0, 0)
                color: (0.231, 0.51, 0.965, 1)
                font_size: '12sp'
                on_press: root.toggle_mode()
            
            Button:
                id: btn_forgot_pwd
                text: 'Lupa Password?'
                background_normal: ''
                background_color: (0, 0, 0, 0)
                color: (0.937, 0.675, 0.208, 1)
                font_size: '12sp'
                on_press: root.reset_password()

        Label:
            id: status_label
            text: ''
            color: (0.937, 0.267, 0.267, 1)
            font_size: '12sp'
            size_hint_y: None
            height: 30

        Widget:

<CatalogScreen>:
    name: 'catalog'
    on_enter: 
        app.current_screen = 'catalog'
        root.load_products()
    canvas.before:
        Color:
            rgba: (0.059, 0.09, 0.165, 1)
        Rectangle:
            pos: self.pos
            size: self.size

    BoxLayout:
        orientation: 'vertical'

        BoxLayout:
            size_hint_y: None
            height: 60
            padding: [12, 8]
            spacing: 10
            canvas.before:
                Color:
                    rgba: (0.118, 0.161, 0.231, 1)
                Rectangle:
                    pos: self.pos
                    size: self.size

            Image:
                source: 'logo.png'
                size_hint_x: None
                width: 40
                allow_stretch: True
                keep_ratio: True

            BoxLayout:
                orientation: 'vertical'
                Label:
                    text: f'Kasir: {app.kasir_name}'
                    bold: True
                    font_size: '14sp'
                    color: (0.063, 0.725, 0.506, 1)
                    halign: 'left'
                    text_size: self.size
                    valign: 'middle'

            PrimaryButton:
                text: 'Scan'
                size_hint_x: None
                width: 75
                on_press: 
                    app.scan_mode = 'cart'
                    app.previous_screen = 'catalog'
                    root.manager.current = 'scanner'

        BoxLayout:
            size_hint_y: None
            height: 52
            padding: [14, 6]
            canvas.before:
                Color:
                    rgba: (0.059, 0.09, 0.165, 1)
                Rectangle:
                    pos: self.pos
                    size: self.size

            ModernTextInput:
                id: search_input
                hint_text: 'Cari produk atau barcode...'
                on_text: root.filter_products()

        ScrollView:
            size_hint_y: None
            height: 48
            do_scroll_x: True
            do_scroll_y: False
            bar_width: 0
            BoxLayout:
                id: category_chips_layout
                orientation: 'horizontal'
                size_hint_x: None
                width: self.minimum_width
                padding: [14, 4]
                spacing: 8

        ScrollView:
            do_scroll_x: False
            do_scroll_y: True
            GridLayout:
                id: product_grid
                cols: 2
                spacing: 12
                padding: 14
                size_hint_y: None
                height: self.minimum_height

        NavBar:

<CartScreen>:
    name: 'cart'
    on_enter: 
        app.current_screen = 'cart'
        root.update_cart_ui()
    canvas.before:
        Color:
            rgba: (0.059, 0.09, 0.165, 1)
        Rectangle:
            pos: self.pos
            size: self.size

    BoxLayout:
        orientation: 'vertical'

        BoxLayout:
            size_hint_y: None
            height: 55
            padding: [16, 10]
            canvas.before:
                Color:
                    rgba: (0.118, 0.161, 0.231, 1)
                Rectangle:
                    pos: self.pos
                    size: self.size

            Label:
                text: 'Keranjang Belanja'
                bold: True
                font_size: '16sp'
                halign: 'left'
                text_size: self.size
                valign: 'middle'

        ScrollView:
            BoxLayout:
                id: cart_list
                orientation: 'vertical'
                padding: 14
                spacing: 10
                size_hint_y: None
                height: self.minimum_height

        BoxLayout:
            orientation: 'vertical'
            size_hint_y: None
            height: 115
            padding: 14
            spacing: 8
            canvas.before:
                Color:
                    rgba: (0.118, 0.161, 0.231, 1)
                Rectangle:
                    pos: self.pos
                    size: self.size

            Label:
                id: total_label
                text: 'Total: Rp 0'
                font_size: '18sp'
                bold: True
                color: (0.063, 0.725, 0.506, 1)

            PrimaryButton:
                text: 'PROSES CHECKOUT'
                size_hint_y: None
                height: 44
                on_press: root.process_checkout()

        NavBar:

<ProductEditScreen>:
    name: 'product_edit'
    on_enter: 
        app.current_screen = 'product_edit'
        root.load_manage_products()
    canvas.before:
        Color:
            rgba: (0.059, 0.09, 0.165, 1)
        Rectangle:
            pos: self.pos
            size: self.size

    BoxLayout:
        orientation: 'vertical'

        BoxLayout:
            size_hint_y: None
            height: 55
            padding: [16, 10]
            canvas.before:
                Color:
                    rgba: (0.118, 0.161, 0.231, 1)
                Rectangle:
                    pos: self.pos
                    size: self.size

            Label:
                text: 'Kelola Produk'
                bold: True
                font_size: '16sp'
                halign: 'left'
                text_size: self.size
                valign: 'middle'

            PrimaryButton:
                text: '+ Tambah'
                size_hint_x: None
                width: 90
                on_press: root.open_edit_popup(None)

        ScrollView:
            BoxLayout:
                id: manage_list
                orientation: 'vertical'
                padding: 14
                spacing: 10
                size_hint_y: None
                height: self.minimum_height

        NavBar:

<ReportScreen>:
    name: 'report'
    on_enter: 
        app.current_screen = 'report'
        root.load_financial_report()
    canvas.before:
        Color:
            rgba: (0.059, 0.09, 0.165, 1)
        Rectangle:
            pos: self.pos
            size: self.size

    BoxLayout:
        orientation: 'vertical'

        BoxLayout:
            size_hint_y: None
            height: 55
            padding: [16, 10]
            canvas.before:
                Color:
                    rgba: (0.118, 0.161, 0.231, 1)
                Rectangle:
                    pos: self.pos
                    size: self.size

            Label:
                text: 'Laporan Ringkasan'
                bold: True
                font_size: '16sp'
                halign: 'left'
                text_size: self.size
                valign: 'middle'

            Button:
                text: 'Reset'
                size_hint_x: None
                width: 66
                background_normal: ''
                background_color: (0.937, 0.267, 0.267, 1)
                bold: True
                font_size: '12sp'
                canvas.before:
                    Color:
                        rgba: (0.937, 0.267, 0.267, 1)
                    RoundedRectangle:
                        pos: self.pos
                        size: self.size
                        radius: [8,]
                on_press: root.confirm_reset_report()

        ScrollView:
            BoxLayout:
                id: report_content_layout
                orientation: 'vertical'
                padding: 16
                spacing: 12
                size_hint_y: None
                height: self.minimum_height

        NavBar:

<SettingsScreen>:
    name: 'settings'
    on_enter: app.current_screen = 'settings'
    canvas.before:
        Color:
            rgba: (0.059, 0.09, 0.165, 1)
        Rectangle:
            pos: self.pos
            size: self.size

    BoxLayout:
        orientation: 'vertical'

        BoxLayout:
            size_hint_y: None
            height: 55
            padding: [16, 10]
            canvas.before:
                Color:
                    rgba: (0.118, 0.161, 0.231, 1)
                Rectangle:
                    pos: self.pos
                    size: self.size

            Label:
                text: 'Pengaturan Akun & Kamera'
                bold: True
                font_size: '16sp'
                halign: 'left'
                text_size: self.size
                valign: 'middle'

        ScrollView:
            BoxLayout:
                orientation: 'vertical'
                padding: 20
                spacing: 12
                size_hint_y: None
                height: self.minimum_height

                Label:
                    text: 'Nama Petugas Kasir:'
                    size_hint_y: None
                    height: 20
                    color: (0.5, 0.57, 0.67, 1)
                    halign: 'left'
                    text_size: self.size

                ModernTextInput:
                    id: kasir_name_input
                    text: app.kasir_name

                PrimaryButton:
                    text: 'Simpan Nama Kasir'
                    size_hint_y: None
                    height: 42
                    on_press: root.save_kasir_name()

                Widget:
                    size_hint_y: None
                    height: 10

                Label:
                    text: 'URL IP Camera HP (Opsional):'
                    size_hint_y: None
                    height: 20
                    color: (0.5, 0.57, 0.67, 1)
                    halign: 'left'
                    text_size: self.size

                ModernTextInput:
                    id: ip_camera_input
                    text: app.ip_camera_url
                    hint_text: 'http://192.168.1.X:8080/video'

                PrimaryButton:
                    text: 'Simpan URL IP Camera'
                    size_hint_y: None
                    height: 42
                    on_press: root.save_ip_camera_url()

                Widget:
                    size_hint_y: None
                    height: 10

                Label:
                    text: 'Printer Struk Bluetooth:'
                    size_hint_y: None
                    height: 20
                    color: (0.5, 0.57, 0.67, 1)
                    halign: 'left'
                    text_size: self.size

                Label:
                    id: printer_status_label
                    text: f'Status: {app.printer_status}'
                    size_hint_y: None
                    height: 24
                    bold: True
                    color: (0.063, 0.725, 0.506, 1) if app.printer_connected else (0.937, 0.267, 0.267, 1)
                    halign: 'left'
                    text_size: self.size

                PrimaryButton:
                    text: 'Cari & Sambungkan Printer'
                    size_hint_y: None
                    height: 42
                    on_press: root.scan_bluetooth_printers()

                Button:
                    text: 'Tes Cetak Struk'
                    size_hint_y: None
                    height: 42
                    background_normal: ''
                    background_color: (0.18, 0.23, 0.32, 1)
                    color: (1, 1, 1, 1)
                    bold: True
                    canvas.before:
                        Color:
                            rgba: (0.18, 0.23, 0.32, 1)
                        RoundedRectangle:
                            pos: self.pos
                            size: self.size
                            radius: [8,]
                    on_press: root.test_print()

                Widget:
                    size_hint_y: None
                    height: 10

                Label:
                    text: 'Ubah Password:'
                    size_hint_y: None
                    height: 20
                    color: (0.5, 0.57, 0.67, 1)
                    halign: 'left'
                    text_size: self.size

                ModernTextInput:
                    id: new_password_input
                    hint_text: 'Password Baru (min 6 karakter)'
                    password: True

                Button:
                    text: 'Ganti Password'
                    size_hint_y: None
                    height: 42
                    background_normal: ''
                    background_color: (0.937, 0.675, 0.208, 1)
                    bold: True
                    canvas.before:
                        Color:
                            rgba: (0.937, 0.675, 0.208, 1)
                        RoundedRectangle:
                            pos: self.pos
                            size: self.size
                            radius: [8,]
                    on_press: root.change_password()

                Widget:
                    size_hint_y: None
                    height: 20

                Button:
                    text: 'LOGOUT (KELUAR)'
                    size_hint_y: None
                    height: 44
                    background_normal: ''
                    background_color: (0.937, 0.267, 0.267, 1)
                    bold: True
                    canvas.before:
                        Color:
                            rgba: (0.937, 0.267, 0.267, 1)
                        RoundedRectangle:
                            pos: self.pos
                            size: self.size
                            radius: [8,]
                    on_press: app.logout()

        NavBar:

<ScannerScreen>:
    name: 'scanner'
    BoxLayout:
        orientation: 'vertical'

        BoxLayout:
            size_hint_y: None
            height: 50
            padding: 10
            canvas.before:
                Color:
                    rgba: (0.118, 0.161, 0.231, 1)
                Rectangle:
                    pos: self.pos
                    size: self.size

            Button:
                text: 'Batal'
                size_hint_x: None
                width: 80
                on_press: root.cancel_scan()

            Label:
                text: 'Arahkan Kamera ke Barcode'
                bold: True

        Image:
            id: camera_preview
'''

# --- SCREEN CLASSES ---

class LoginScreen(Screen):
    is_register_mode = False

    def toggle_mode(self):
        self.is_register_mode = not self.is_register_mode
        if self.is_register_mode:
            self.ids.mode_title.text = "Pendaftaran Akun Baru"
            self.ids.btn_submit.text = "DAFTAR AKUN"
            self.ids.btn_toggle_mode.text = "Sudah ada akun? Login"
        else:
            self.ids.mode_title.text = "Silakan masuk ke akun Anda"
            self.ids.btn_submit.text = "MASUK SEKARANG"
            self.ids.btn_toggle_mode.text = "Buat Akun Baru"
        self.ids.status_label.text = ""

    def do_auth(self):
        email = self.ids.email_input.text.strip()
        password = self.ids.password_input.text.strip()

        if not email or not password:
            self.ids.status_label.text = "Email dan password wajib diisi!"
            return

        if self.is_register_mode:
            url = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={FIREBASE_WEB_API_KEY}"
        else:
            url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_WEB_API_KEY}"

        payload = {"email": email, "password": password, "returnSecureToken": True}

        try:
            res = requests.post(url, json=payload, timeout=10)
            data = res.json()

            if "error" in data:
                err_msg = data['error']['message']
                if err_msg == "EMAIL_EXISTS":
                    err_msg = "Email sudah terdaftar!"
                elif err_msg in ["INVALID_PASSWORD", "EMAIL_NOT_FOUND", "INVALID_LOGIN_CREDENTIALS"]:
                    err_msg = "Email atau Password salah!"
                elif "WEAK_PASSWORD" in err_msg:
                    err_msg = "Password minimal 6 karakter!"
                self.ids.status_label.text = f"Gagal: {err_msg}"
            else:
                app = App.get_running_app()
                app.id_token = data["idToken"]
                app.user_email = email
                app.user_uid = data["localId"]
                app.kasir_name = email.split('@')[0].capitalize()
                app.cart = []
                app.save_session(data.get("refreshToken", ""))

                self.manager.current = 'catalog'
        except Exception as e:
            self.ids.status_label.text = f"Koneksi Gagal: {e}"

    def sign_in_with_google(self):
        app = App.get_running_app()
        helper = app.google_signin

        if not helper.is_supported():
            Popup(
                title='Tidak Tersedia',
                content=Label(text='Login Google hanya berfungsi di aplikasi\nAndroid (APK), belum di mode pengetesan PC.', halign='center'),
                size_hint=(0.85, 0.3)
            ).open()
            return

        self.ids.status_label.text = "Membuka Google Sign-In..."

        def on_result(success, data):
            def update_ui(dt):
                if not success:
                    self.ids.status_label.text = f"Login Google gagal: {data}"
                    return
                app.id_token = data["idToken"]
                app.user_email = data.get("email", "")
                app.user_uid = data["localId"]
                app.kasir_name = (app.user_email.split('@')[0].capitalize()
                                  if app.user_email else "Kasir Google")
                app.cart = []
                app.save_session(data.get("refreshToken", ""))
                self.manager.current = 'catalog'
            Clock.schedule_once(update_ui, 0)

        helper.sign_in(on_result)

    def reset_password(self):
        email = self.ids.email_input.text.strip()
        if not email:
            self.ids.status_label.text = "Isi email dulu untuk reset password!"
            return

        url = f"https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key={FIREBASE_WEB_API_KEY}"
        payload = {"requestType": "PASSWORD_RESET", "email": email}

        try:
            res = requests.post(url, json=payload, timeout=10)
            data = res.json()

            if "error" in data:
                self.ids.status_label.text = f"Reset Gagal: {data['error']['message']}"
            else:
                Popup(
                    title="Email Dikirim",
                    content=Label(text=f"Link reset password telah dikirim ke:\n{email}"),
                    size_hint=(0.85, 0.3)
                ).open()
        except Exception as e:
            self.ids.status_label.text = f"Koneksi Gagal: {e}"


class SettingsScreen(Screen):
    def scan_bluetooth_printers(self):
        app = App.get_running_app()
        printer = app.bluetooth_printer

        if not printer.is_supported():
            Popup(
                title='Tidak Tersedia',
                content=Label(text='Bluetooth printer hanya berfungsi di\naplikasi Android (APK), bukan di PC.', halign='center'),
                size_hint=(0.85, 0.3)
            ).open()
            return

        def do_scan(dt=None):
            devices = printer.list_paired_devices()
            if not devices:
                Popup(
                    title='Tidak Ada Perangkat',
                    content=Label(text='Belum ada printer yang di-pairing.\nPasangkan dulu lewat Pengaturan Bluetooth HP.', halign='center'),
                    size_hint=(0.85, 0.3)
                ).open()
                return

            content = BoxLayout(orientation='vertical', padding=12, spacing=8)
            scroll = ScrollView()
            list_box = BoxLayout(orientation='vertical', size_hint_y=None, spacing=6)
            list_box.bind(minimum_height=list_box.setter('height'))

            popup = Popup(title='Pilih Printer', content=content, size_hint=(0.85, 0.6))

            for name, address in devices:
                btn = Button(
                    text=f"{name}\n{address}",
                    size_hint_y=None,
                    height=54,
                    background_normal='',
                    background_color=(0.118, 0.161, 0.231, 1),
                    color=(1, 1, 1, 1)
                )

                def make_connect(addr=address, popup_ref=popup):
                    def _connect(inst):
                        popup_ref.dismiss()
                        ok, msg = printer.connect(addr)
                        app.printer_connected = ok
                        app.printer_status = msg
                        Popup(title='Sukses' if ok else 'Gagal', content=Label(text=msg, halign='center'), size_hint=(0.85, 0.28)).open()
                    return _connect

                btn.bind(on_press=make_connect())
                list_box.add_widget(btn)

            scroll.add_widget(list_box)
            content.add_widget(scroll)
            popup.open()

        # Minta izin bluetooth dulu (Android 12+ butuh izin runtime)
        printer.request_permissions(lambda perms, grants: Clock.schedule_once(do_scan, 0.3))
        Clock.schedule_once(do_scan, 0.3)

    def test_print(self):
        app = App.get_running_app()
        printer = app.bluetooth_printer

        if not printer.is_connected():
            Popup(title='Belum Terhubung', content=Label(text='Sambungkan printer dulu sebelum tes cetak.'), size_hint=(0.85, 0.25)).open()
            return

        dummy_items = [{'nama': 'Contoh Produk', 'harga': 10000, 'qty': 1}]
        ok, msg = printer.print_receipt(app.kasir_name, datetime.datetime.now(), dummy_items, 10000)
        Popup(title='Sukses' if ok else 'Gagal', content=Label(text=msg, halign='center'), size_hint=(0.85, 0.28)).open()

    def save_kasir_name(self):
        app = App.get_running_app()
        new_name = self.ids.kasir_name_input.text.strip()
        if new_name:
            app.kasir_name = new_name
            Popup(title="Sukses", content=Label(text="Nama kasir berhasil diperbarui!"), size_hint=(0.8, 0.25)).open()

    def save_ip_camera_url(self):
        app = App.get_running_app()
        app.ip_camera_url = self.ids.ip_camera_input.text.strip()
        Popup(title="Sukses", content=Label(text="URL IP Camera berhasil disimpan!"), size_hint=(0.8, 0.25)).open()

    def change_password(self):
        app = App.get_running_app()
        new_pwd = self.ids.new_password_input.text.strip()

        if len(new_pwd) < 6:
            Popup(title="Gagal", content=Label(text="Password minimal 6 karakter!"), size_hint=(0.8, 0.25)).open()
            return

        url = f"https://identitytoolkit.googleapis.com/v1/accounts:update?key={FIREBASE_WEB_API_KEY}"
        payload = {
            "idToken": app.id_token,
            "password": new_pwd,
            "returnSecureToken": True
        }

        try:
            res = requests.post(url, json=payload, timeout=10)
            data = res.json()

            if "error" in data:
                Popup(title="Gagal", content=Label(text=f"Gagal ganti password: {data['error']['message']}"), size_hint=(0.85, 0.3)).open()
            else:
                app.id_token = data["idToken"]
                app.save_session(data.get("refreshToken", ""))
                self.ids.new_password_input.text = ""
                Popup(title="Sukses", content=Label(text="Password berhasil diubah!"), size_hint=(0.8, 0.25)).open()
        except Exception as e:
            Popup(title="Error", content=Label(text=f"Gagal koneksi: {e}"), size_hint=(0.85, 0.3)).open()


class CatalogScreen(Screen):
    all_products = []
    selected_category = "Semua"

    def load_products(self):
        app = App.get_running_app()
        if not db or not app.user_uid:
            self.render_products([])
            return

        try:
            self.all_products = []
            categories_set = set()

            docs = db.collection("products").where("users_id", "==", app.user_uid).stream()
            for doc in docs:
                p = doc.to_dict()
                p['doc_id'] = doc.id
                self.all_products.append(p)
                
                cat = p.get('kategori', '').strip()
                if cat:
                    categories_set.add(cat)

            chips_layout = self.ids.category_chips_layout
            chips_layout.clear_widgets()

            sorted_cats = ["Semua"] + sorted(list(categories_set))
            
            for cat_name in sorted_cats:
                is_active = (cat_name == self.selected_category)
                btn = Button(
                    text=cat_name,
                    size_hint_x=None,
                    padding=[16, 0],
                    font_size='12sp',
                    bold=is_active,
                    color=(1, 1, 1, 1) if is_active else (0.6, 0.67, 0.77, 1),
                    background_normal='',
                    background_color=(0, 0, 0, 0)
                )
                btn.texture_update()
                btn.width = max(btn.texture_size[0] + 28, 70)

                with btn.canvas.before:
                    if is_active:
                        Color(0.231, 0.51, 0.965, 1)
                    else:
                        Color(0.118, 0.161, 0.231, 1)
                    btn.rect = RoundedRectangle(pos=btn.pos, size=btn.size, radius=[16,])

                btn.bind(pos=self._update_chip_rect, size=self._update_chip_rect)
                btn.bind(on_press=lambda inst, c=cat_name: self.select_category(c))
                chips_layout.add_widget(btn)

            self.filter_products()
        except Exception as e:
            print("Gagal mengambil produk:", e)

    def _update_chip_rect(self, instance, value):
        if hasattr(instance, 'rect'):
            instance.rect.pos = instance.pos
            instance.rect.size = instance.size

    def select_category(self, cat_name):
        self.selected_category = cat_name
        self.load_products()

    def filter_products(self, *args):
        query = self.ids.search_input.text.lower().strip()

        filtered = []
        for p in self.all_products:
            matches_query = (
                not query or 
                query in p.get('nama', '').lower() or 
                query in str(p.get('barcode', '')).lower()
            )
            matches_category = (
                self.selected_category == "Semua" or 
                p.get('kategori', 'Umum') == self.selected_category
            )

            if matches_query and matches_category:
                filtered.append(p)

        self.render_products(filtered)

    def render_products(self, products_list):
        grid = self.ids.product_grid
        grid.clear_widgets()

        if not products_list:
            grid.add_widget(
                Label(
                    text="Belum ada produk.\nSilakan tambah di menu 'Kelola'.",
                    color=(0.5, 0.57, 0.67, 1),
                    halign="center",
                    valign="center",
                    size_hint_y=None,
                    height=200
                )
            )
            return

        for p in products_list:
            doc_id = p['doc_id']
            stok_val = int(p.get('stok', 0))
            card = Factory.CustomCard()
            
            lbl_name = Label(
                text=p.get('nama', '-'),
                bold=True,
                color=(0.95, 0.96, 0.98, 1),
                font_size='13sp',
                size_hint_y=None,
                height=22,
                halign='left',
                valign='middle'
            )
            lbl_name.bind(size=lbl_name.setter('text_size'))

            stock_color = (0.937, 0.267, 0.267, 1) if stok_val < 5 else (0.063, 0.725, 0.506, 1)
            lbl_stock = Label(
                text=f"Stok: {stok_val}",
                color=stock_color,
                font_size='11sp',
                bold=True,
                size_hint_y=None,
                height=18,
                halign='left',
                valign='middle'
            )
            lbl_stock.bind(size=lbl_stock.setter('text_size'))

            lbl_price = Label(
                text=f"Rp {int(p.get('harga', 0)):,}",
                color=(0.95, 0.96, 0.98, 1),
                font_size='14sp',
                bold=True,
                size_hint_y=None,
                height=22,
                halign='left',
                valign='middle'
            )
            lbl_price.bind(size=lbl_price.setter('text_size'))

            btn = Factory.PrimaryButton(
                text='+ Keranjang',
                size_hint_y=None,
                height=34,
                font_size='11sp'
            )
            btn.bind(on_press=lambda inst, item=p, pid=doc_id: self.add_to_cart(item, pid))

            card.add_widget(lbl_name)
            card.add_widget(lbl_stock)
            card.add_widget(lbl_price)
            card.add_widget(Widget())
            card.add_widget(btn)

            grid.add_widget(card)

    def add_to_cart(self, item, doc_id):
        app = App.get_running_app()
        stok_tersedia = int(item.get('stok', 0))

        if stok_tersedia <= 0:
            Popup(title='Stok Habis', content=Label(text='Stok produk ini habis!'), size_hint=(0.8, 0.25)).open()
            return

        for cart_item in app.cart:
            if cart_item['id'] == doc_id:
                if cart_item['qty'] + 1 > stok_tersedia:
                    Popup(
                        title='Peringatan Stok Limit',
                        content=Label(text=f"Stok tidak mencukupi!\nMaksimal tersedia: {stok_tersedia} pcs", halign='center'),
                        size_hint=(0.85, 0.28)
                    ).open()
                else:
                    cart_item['qty'] += 1
                    Popup(title='Berhasil', content=Label(text='Jumlah ditambah!'), size_hint=(0.7, 0.22)).open()
                return

        app.cart.append({
            'id': doc_id,
            'nama': item.get('nama', ''),
            'harga': int(item.get('harga', 0)),
            'stok': stok_tersedia,
            'qty': 1
        })
        Popup(title='Berhasil', content=Label(text='Masuk ke keranjang!'), size_hint=(0.7, 0.22)).open()


class CartScreen(Screen):
    def update_cart_ui(self):
        app = App.get_running_app()
        cart_list = self.ids.cart_list
        cart_list.clear_widgets()

        total = 0
        for idx, item in enumerate(app.cart):
            subtotal = item['harga'] * item['qty']
            total += subtotal

            row = Factory.CustomCard(size_hint_y=None, height=72, orientation='horizontal', padding=12)

            info_box = BoxLayout(orientation='vertical')
            info_box.add_widget(Label(text=item['nama'], bold=True, size_hint_y=None, height=20, halign='left', text_size=(150, 20), color=(0.95, 0.96, 0.98, 1)))
            
            lbl_subtotal = Label(
                text=f"Rp {item['harga']:,} x {item['qty']}", 
                font_size='12sp', 
                color=(0.5, 0.57, 0.67, 1),
                halign='left',
                text_size=(150, 20)
            )
            info_box.add_widget(lbl_subtotal)

            qty_box = BoxLayout(size_hint_x=None, width=110, spacing=4)
            
            btn_minus = Button(text='-', size_hint_x=None, width=32, background_normal='', background_color=(0.18, 0.23, 0.32, 1), color=(1, 1, 1, 1))
            btn_minus.bind(on_press=lambda inst, i=idx: self.change_qty(i, -1))
            
            txt_qty = TextInput(text=str(item['qty']), multiline=False, input_filter='int', size_hint_x=None, width=40, background_normal='', background_color=(0.059, 0.09, 0.165, 1), foreground_color=(1, 1, 1, 1), padding=[8, 8])
            txt_qty.bind(on_text_validate=lambda inst, i=idx: self.on_qty_input_changed(i, inst.text))
            txt_qty.bind(focus=lambda inst, focused, i=idx: self.on_qty_input_changed(i, inst.text) if not focused else None)

            btn_plus = Button(text='+', size_hint_x=None, width=32, background_normal='', background_color=(0.18, 0.23, 0.32, 1), color=(1, 1, 1, 1))
            btn_plus.bind(on_press=lambda inst, i=idx: self.change_qty(i, 1))

            qty_box.add_widget(btn_minus)
            qty_box.add_widget(txt_qty)
            qty_box.add_widget(btn_plus)

            row.add_widget(info_box)
            row.add_widget(qty_box)
            cart_list.add_widget(row)

        self.ids.total_label.text = f"Total: Rp {total:,}"

    def change_qty(self, index, delta):
        app = App.get_running_app()
        if 0 <= index < len(app.cart):
            item = app.cart[index]
            max_stok = item.get('stok', 0)

            if delta > 0 and (item['qty'] + delta) > max_stok:
                Popup(
                    title='Peringatan Stok Limit',
                    content=Label(text=f"Stok '{item['nama']}' terbatas!\nMaksimal tersedia: {max_stok} pcs", halign='center'),
                    size_hint=(0.85, 0.28)
                ).open()
                return

            item['qty'] += delta
            if item['qty'] <= 0:
                app.cart.pop(index)
            self.update_cart_ui()

    def on_qty_input_changed(self, index, val):
        app = App.get_running_app()
        if index >= len(app.cart):
            return

        item = app.cart[index]
        max_stok = item.get('stok', 0)

        if not val.strip() or int(val) <= 0:
            app.cart.pop(index)
        else:
            inputted_qty = int(val)
            if inputted_qty > max_stok:
                item['qty'] = max_stok
                Popup(
                    title='Peringatan Stok Limit',
                    content=Label(text=f"Jumlah melebihi stok!\nOtomatis disesuaikan ke maksimal: {max_stok} pcs", halign='center'),
                    size_hint=(0.85, 0.28)
                ).open()
            else:
                item['qty'] = inputted_qty

        self.update_cart_ui()

    def process_checkout(self):
        app = App.get_running_app()
        app.cart = [item for item in app.cart if item['qty'] > 0]
        
        if not app.cart or not db:
            return

        try:
            total_bayar = 0
            items_summary = []

            for item in app.cart:
                doc_ref = db.collection("products").document(item['id'])
                doc = doc_ref.get()
                
                if doc.exists:
                    current_data = doc.to_dict()
                    current_stok = int(current_data.get('stok', 0))
                    
                    if item['qty'] > current_stok:
                        Popup(
                            title='Checkout Gagal',
                            content=Label(text=f"Stok '{item['nama']}' tersisa {current_stok} pcs.\nSilakan sesuaikan jumlah belanja.", halign='center'),
                            size_hint=(0.85, 0.28)
                        ).open()
                        return

                    new_stok = max(0, current_stok - item['qty'])
                    doc_ref.update({'stok': new_stok})

                subtotal = item['harga'] * item['qty']
                total_bayar += subtotal
                items_summary.append(item)

            now_dt = datetime.datetime.now()
            db.collection("transactions").add({
                'users_id': app.user_uid,
                'kasir_nama': app.kasir_name,
                'kasir_email': app.user_email,
                'total': total_bayar,
                'items': items_summary,
                'timestamp': firestore.SERVER_TIMESTAMP
            })

            pdf_filename = self.generate_pdf_receipt(total_bayar, items_summary, now_dt)
            self.show_onscreen_receipt(total_bayar, items_summary, now_dt, pdf_filename)

            app.cart = []
            self.update_cart_ui()

        except Exception as e:
            print("Checkout error:", e)
            Popup(title='Gagal', content=Label(text=f'Error: {e}'), size_hint=(0.85, 0.3)).open()

    def show_onscreen_receipt(self, total, items, now_dt, pdf_filename):
        app = App.get_running_app()
        content = BoxLayout(orientation='vertical', padding=14, spacing=10)
        
        receipt_text = f"===============================\n"
        receipt_text += f"        POS NAUFAL APP         \n"
        receipt_text += f"Jl. Veteran No.99, Kediri, Jawa Timur\n"
        receipt_text += f"===============================\n"
        receipt_text += f"Kasir : {app.kasir_name}\n"
        receipt_text += f"Tgl   : {now_dt.strftime('%d/%m/%Y %H:%M:%S')}\n"
        receipt_text += "-----------------------------------------------\n"
        for item in items:
            sub = item['harga'] * item['qty']
            receipt_text += f"{item['nama']}\n"
            receipt_text += f"  {item['qty']} x Rp {item['harga']:,} = Rp {sub:,}\n"
        receipt_text += "-----------------------------------------------\n"
        receipt_text += f"TOTAL : Rp {total:,}\n"
        receipt_text += "===============================\n\n"
        if pdf_filename:
            receipt_text += f"File PDF: {pdf_filename}\n\n"
        receipt_text += "     Terima Kasih Telah Berbelanja!     "

        scroll = ScrollView()
        lbl_receipt = Label(
            text=receipt_text,
            font_size='12sp',
            font_name='Roboto',
            color=(0.9, 0.9, 0.9, 1),
            size_hint_y=None,
            halign='center',
            valign='top'
        )
        lbl_receipt.bind(texture_size=lbl_receipt.setter('size'))
        scroll.add_widget(lbl_receipt)

        btn_row = BoxLayout(size_hint_y=None, height=42, spacing=8)

        btn_print = Button(
            text='Cetak Bluetooth',
            background_color=(0.063, 0.725, 0.506, 1),
            background_normal='',
            bold=True
        )

        def do_bluetooth_print(inst):
            printer = app.bluetooth_printer
            if not printer.is_connected():
                Popup(
                    title='Printer Belum Terhubung',
                    content=Label(text='Sambungkan printer Bluetooth dulu\nlewat menu Akun > Printer Struk.', halign='center'),
                    size_hint=(0.85, 0.3)
                ).open()
                return
            ok, msg = printer.print_receipt(app.kasir_name, now_dt, items, total)
            Popup(title='Sukses' if ok else 'Gagal', content=Label(text=msg, halign='center'), size_hint=(0.85, 0.28)).open()

        btn_print.bind(on_press=do_bluetooth_print)

        btn_close = Button(
            text='Selesai',
            background_color=(0.231, 0.51, 0.965, 1),
            background_normal='',
            bold=True
        )

        btn_row.add_widget(btn_print)
        btn_row.add_widget(btn_close)

        content.add_widget(scroll)
        content.add_widget(btn_row)

        popup = Popup(
            title='Struk Transaksi Digital',
            content=content,
            size_hint=(0.88, 0.8),
            auto_dismiss=False
        )
        btn_close.bind(on_press=popup.dismiss)
        popup.open()

        # Cetak otomatis ke printer bluetooth jika sudah tersambung
        if app.bluetooth_printer.is_connected():
            app.bluetooth_printer.print_receipt(app.kasir_name, now_dt, items, total)

    def generate_pdf_receipt(self, total, items, now_dt):
        if not HAS_REPORTLAB:
            return None
        
        app = App.get_running_app()
        filename = f"struk_{now_dt.strftime('%Y%m%d_%H%M%S')}.pdf"
        
        page_height = max(380 + (len(items) * 25), 440)
        c = canvas.Canvas(filename, pagesize=(220, page_height))
        
        # --- LOGO STRUK PDF ---
        logo_path = "logo.png"
        if os.path.exists(logo_path):
            c.drawImage(logo_path, 85, page_height - 55, width=50, height=50, preserveAspectRatio=True, mask='auto')
            y_offset = 65
        else:
            y_offset = 30

        c.setFont("Helvetica-Bold", 12)
        c.drawCentredString(110, page_height - y_offset, "POS NAUFAL APP")
        
        c.setFont("Helvetica", 7)
        c.drawCentredString(110, page_height - y_offset - 12, "Jl. Veteran No.99, Kediri, Jawa Timur, Indonesia")
        
        c.setFont("Helvetica", 8)
        c.drawCentredString(110, page_height - y_offset - 24, f"Kasir: {app.kasir_name}")
        c.drawCentredString(110, page_height - y_offset - 34, f"Tgl  : {now_dt.strftime('%d/%m/%Y %H:%M')}")
        
        c.setLineWidth(0.5)
        c.line(15, page_height - y_offset - 42, 205, page_height - y_offset - 42)

        y = page_height - y_offset - 57
        c.setFont("Helvetica-Bold", 8)
        c.drawString(15, y, "ITEM")
        c.drawRightString(205, y, "TOTAL")
        
        y -= 12
        c.setFont("Helvetica", 8)

        for item in items:
            c.drawString(15, y, item['nama'])
            y -= 10
            c.drawString(20, y, f"{item['qty']} x Rp {item['harga']:,}")
            c.drawRightString(205, y, f"Rp {item['harga']*item['qty']:,}")
            y -= 15

        c.line(15, y + 5, 205, y + 5)
        y -= 10
        c.setFont("Helvetica-Bold", 10)
        c.drawString(15, y, "TOTAL BAYAR:")
        c.drawRightString(205, y, f"Rp {total:,}")

        y -= 30
        c.setFont("Helvetica-Oblique", 8)
        c.drawCentredString(110, y, "--- Terima Kasih ---")
        c.drawCentredString(110, y - 10, "Barang yang dibeli tidak dapat ditukar")

        c.save()
        return filename


class ProductEditScreen(Screen):
    temp_form_data = {}

    def load_manage_products(self):
        manage_list = self.ids.manage_list
        manage_list.clear_widgets()

        app = App.get_running_app()
        if not db or not app.user_uid:
            return

        try:
            docs = db.collection("products").where("users_id", "==", app.user_uid).stream()
            for doc in docs:
                p = doc.to_dict()
                p['doc_id'] = doc.id
                
                row = Factory.CustomCard(size_hint_y=None, height=82, orientation='horizontal', padding=12, spacing=8)

                info_box = BoxLayout(orientation='vertical')
                lbl_n = Label(text=p.get('nama', '-'), bold=True, size_hint_y=None, height=20, halign='left', text_size=(130, 20), color=(0.95, 0.96, 0.98, 1))
                lbl_d = Label(
                    text=f"Rp {int(p.get('harga', 0)):,} | Stok: {p.get('stok', 0)}\nKat: {p.get('kategori', 'Umum')}",
                    font_size='11sp',
                    color=(0.5, 0.57, 0.67, 1),
                    halign='left',
                    text_size=(130, 32)
                )
                info_box.add_widget(lbl_n)
                info_box.add_widget(lbl_d)

                btn_box = BoxLayout(size_hint_x=None, width=125, spacing=6)

                btn_edit = Button(
                    text='Edit',
                    size_hint_x=None,
                    width=58,
                    background_color=(0.231, 0.51, 0.965, 1),
                    background_normal='',
                    bold=True,
                    font_size='11sp'
                )
                btn_edit.bind(on_press=lambda inst, item=p: self.open_edit_popup(item))

                btn_delete = Button(
                    text='Hapus',
                    size_hint_x=None,
                    width=58,
                    background_color=(0.937, 0.267, 0.267, 1),
                    background_normal='',
                    bold=True,
                    font_size='11sp'
                )
                btn_delete.bind(on_press=lambda inst, item=p: self.confirm_delete_product(item))

                btn_box.add_widget(btn_edit)
                btn_box.add_widget(btn_delete)

                row.add_widget(info_box)
                row.add_widget(btn_box)
                manage_list.add_widget(row)

        except Exception as e:
            print("Gagal muat daftar edit produk:", e)

    def confirm_delete_product(self, item):
        content = BoxLayout(orientation='vertical', padding=15, spacing=10)
        content.add_widget(Label(
            text=f"Apakah Anda yakin ingin menghapus\n'{item.get('nama', 'produk ini')}'?",
            halign='center',
            valign='middle'
        ))

        btn_box = BoxLayout(size_hint_y=None, height=40, spacing=10)
        btn_cancel = Button(text='Batal', background_color=(0.5, 0.57, 0.67, 1), background_normal='')
        btn_yes = Button(text='Ya, Hapus', background_color=(0.937, 0.267, 0.267, 1), background_normal='', bold=True)

        btn_box.add_widget(btn_cancel)
        btn_box.add_widget(btn_yes)
        content.add_widget(btn_box)

        popup = Popup(title='Konfirmasi Hapus', content=content, size_hint=(0.85, 0.3))

        btn_cancel.bind(on_press=popup.dismiss)
        btn_yes.bind(on_press=lambda inst: self.delete_product(item.get('doc_id'), popup))
        popup.open()

    def delete_product(self, doc_id, popup_instance):
        if not db or not doc_id:
            return

        try:
            db.collection("products").document(doc_id).delete()
            popup_instance.dismiss()
            self.load_manage_products()
            Popup(title='Sukses', content=Label(text='Produk berhasil dihapus!'), size_hint=(0.8, 0.25)).open()
        except Exception as e:
            print("Gagal menghapus produk:", e)
            Popup(title='Gagal', content=Label(text=f'Gagal menghapus: {e}'), size_hint=(0.85, 0.3)).open()

    def open_edit_popup(self, item=None, scanned_code=None):
        is_edit = item is not None or self.temp_form_data.get('doc_id') is not None
        title_text = "Edit Produk" if is_edit else "Tambah Produk"

        default_nama = item.get('nama', '') if item else self.temp_form_data.get('nama', '')
        default_harga = str(item.get('harga', '')) if item else self.temp_form_data.get('harga', '')
        default_stok = str(item.get('stok', '')) if item else self.temp_form_data.get('stok', '')
        default_kategori = item.get('kategori', '') if item else self.temp_form_data.get('kategori', '')

        if scanned_code:
            default_barcode = scanned_code
        else:
            default_barcode = str(item.get('barcode', '')) if item else self.temp_form_data.get('barcode', '')

        current_doc_id = item.get('doc_id') if item else self.temp_form_data.get('doc_id')

        content = BoxLayout(orientation='vertical', padding=15, spacing=8)

        in_name = Factory.ModernTextInput(hint_text='Nama Produk', text=default_nama)
        in_category = Factory.ModernTextInput(hint_text='Kategori (Contoh: Makanan, Minuman)', text=default_kategori)
        in_price = Factory.ModernTextInput(hint_text='Harga (Rp)', text=default_harga, input_filter='int')
        in_stock = Factory.ModernTextInput(hint_text='Stok Produk', text=default_stok, input_filter='int')
        
        barcode_box = BoxLayout(orientation='horizontal', spacing=8, size_hint_y=None, height=44)
        in_barcode = Factory.ModernTextInput(hint_text='Barcode', text=default_barcode)

        btn_scan_barcode = Button(
            text='Scan',
            size_hint_x=None,
            width=70,
            background_color=(0.231, 0.51, 0.965, 1),
            background_normal='',
            bold=True
        )

        barcode_box.add_widget(in_barcode)
        barcode_box.add_widget(btn_scan_barcode)

        btn_save = Button(
            text='SIMPAN' if is_edit else 'TAMBAH PRODUK',
            size_hint_y=None,
            height=44,
            background_color=(0.063, 0.725, 0.506, 1),
            background_normal='',
            bold=True
        )

        content.add_widget(in_name)
        content.add_widget(in_category)
        content.add_widget(in_price)
        content.add_widget(in_stock)
        content.add_widget(barcode_box)
        content.add_widget(Widget())
        content.add_widget(btn_save)

        popup = Popup(title=title_text, content=content, size_hint=(0.9, 0.85))

        def trigger_scan(inst):
            self.temp_form_data = {
                'doc_id': current_doc_id,
                'nama': in_name.text,
                'kategori': in_category.text,
                'harga': in_price.text,
                'stok': in_stock.text,
                'barcode': in_barcode.text
            }
            popup.dismiss()
            
            app = App.get_running_app()
            app.scan_mode = 'fill_input'
            app.previous_screen = 'product_edit'
            self.manager.current = 'scanner'

        btn_scan_barcode.bind(on_press=trigger_scan)

        def save_action(inst):
            app = App.get_running_app()
            name = in_name.text.strip()
            category = in_category.text.strip().title()
            price = in_price.text.strip()
            stock = in_stock.text.strip()
            barcode = in_barcode.text.strip()

            if not name or not price or not stock or not db:
                Popup(title='Peringatan', content=Label(text='Nama, Harga, dan Stok wajib diisi!'), size_hint=(0.8, 0.25)).open()
                return

            try:
                data = {
                    'users_id': app.user_uid,
                    'nama': name,
                    'kategori': category if category else "Umum",
                    'harga': int(price),
                    'stok': int(stock),
                    'barcode': barcode
                }

                if current_doc_id:
                    db.collection("products").document(current_doc_id).update(data)
                else:
                    db.collection("products").add(data)

                self.temp_form_data = {}
                popup.dismiss()
                self.load_manage_products()
                Popup(title='Sukses', content=Label(text='Data produk berhasil disimpan!'), size_hint=(0.8, 0.25)).open()
            except Exception as e:
                print("Gagal simpan produk:", e)

        btn_save.bind(on_press=save_action)
        popup.open()


class ReportScreen(Screen):
    def confirm_reset_report(self):
        app = App.get_running_app()
        if not db or not app.user_uid:
            return

        content = BoxLayout(orientation='vertical', padding=15, spacing=10)
        content.add_widget(Label(
            text="Reset laporan akan MENGHAPUS SEMUA\nriwayat transaksi Anda secara permanen.\nStok produk TIDAK akan dikembalikan.\n\nLanjutkan?",
            halign='center',
            valign='middle'
        ))

        btn_box = BoxLayout(size_hint_y=None, height=42, spacing=10)
        btn_cancel = Button(text='Batal', background_color=(0.5, 0.57, 0.67, 1), background_normal='')
        btn_yes = Button(text='Ya, Reset Semua', background_color=(0.937, 0.267, 0.267, 1), background_normal='', bold=True)

        btn_box.add_widget(btn_cancel)
        btn_box.add_widget(btn_yes)
        content.add_widget(btn_box)

        popup = Popup(title='Konfirmasi Reset Laporan', content=content, size_hint=(0.88, 0.4))

        btn_cancel.bind(on_press=popup.dismiss)
        btn_yes.bind(on_press=lambda inst: self.reset_report(popup))
        popup.open()

    def reset_report(self, popup_instance):
        app = App.get_running_app()
        popup_instance.dismiss()

        if not db or not app.user_uid:
            return

        try:
            docs = db.collection("transactions").where("users_id", "==", app.user_uid).stream()
            deleted = 0
            for doc in docs:
                doc.reference.delete()
                deleted += 1

            self.load_financial_report()
            Popup(
                title='Sukses',
                content=Label(text=f'Laporan berhasil direset.\n{deleted} riwayat transaksi dihapus.', halign='center'),
                size_hint=(0.85, 0.3)
            ).open()
        except Exception as e:
            print("Gagal reset laporan:", e)
            Popup(title='Gagal', content=Label(text=f'Gagal reset laporan: {e}'), size_hint=(0.85, 0.3)).open()

    def load_financial_report(self):
        layout = self.ids.report_content_layout
        layout.clear_widgets()

        app = App.get_running_app()
        if not db or not app.user_uid:
            return

        try:
            docs = db.collection("transactions").where("users_id", "==", app.user_uid).stream()
            
            total_omzet = 0
            total_transaksi = 0
            items_terjual = {}

            for doc in docs:
                t = doc.to_dict()
                total_omzet += t.get('total', 0)
                total_transaksi += 1
                
                for item in t.get('items', []):
                    nama = item.get('nama', 'Produk')
                    qty = item.get('qty', 0)
                    items_terjual[nama] = items_terjual.get(nama, 0) + qty

            summary_card = Factory.CustomCard(size_hint_y=None, height=110, orientation='vertical', padding=14)
            summary_card.add_widget(Label(text="Ringkasan Keseluruhan", bold=True, color=(0.063, 0.725, 0.506, 1), size_hint_y=None, height=20, halign='left', text_size=(300, 20)))
            summary_card.add_widget(Label(text=f"Total Transaksi: {total_transaksi} Penjualan", font_size='12sp', color=(0.5, 0.57, 0.67, 1), halign='left', text_size=(300, 20)))
            summary_card.add_widget(Label(text=f"Total Pendapatan: Rp {total_omzet:,}", font_size='16sp', bold=True, color=(0.95, 0.96, 0.98, 1), halign='left', text_size=(300, 28)))
            layout.add_widget(summary_card)

            layout.add_widget(Widget(size_hint_y=None, height=5))
            layout.add_widget(Label(text="Produk Terjual:", bold=True, size_hint_y=None, height=25, color=(0.5, 0.57, 0.67, 1), halign='left', text_size=(320, 25)))

            if not items_terjual:
                layout.add_widget(Label(text="Belum ada data penjualan.", color=(0.5, 0.57, 0.67, 1), size_hint_y=None, height=50))
            else:
                for nama_prod, qty_total in sorted(items_terjual.items(), key=lambda x: x[1], reverse=True):
                    item_card = Factory.CustomCard(size_hint_y=None, height=52, orientation='horizontal', padding=12)
                    item_card.add_widget(Label(text=nama_prod, halign='left', valign='middle', text_size=(180, 30), color=(0.95, 0.96, 0.98, 1)))
                    item_card.add_widget(Label(text=f"{qty_total} pcs", halign='right', valign='middle', color=(0.231, 0.51, 0.965, 1), bold=True, text_size=(100, 30)))
                    layout.add_widget(item_card)

        except Exception as e:
            print("Gagal memuat laporan:", e)


class ScannerScreen(Screen):
    capture = None

    def on_enter(self):
        if not HAS_BARCODE:
            Popup(title='Error', content=Label(text='Library OpenCV/Pyzbar belum terinstall!'), size_hint=(0.8, 0.25)).open()
            self.cancel_scan()
            return

        app = App.get_running_app()
        camera_source = app.ip_camera_url.strip() if app.ip_camera_url.strip() else 0

        try:
            self.capture = cv2.VideoCapture(camera_source)
            if not self.capture.isOpened():
                raise Exception(f"Gagal membuka kamera: {camera_source}")
            
            Clock.schedule_interval(self.update_frame, 1.0 / 30.0)
        except Exception as e:
            Popup(title='Error Kamera', content=Label(text=str(e)), size_hint=(0.85, 0.25)).open()
            self.cancel_scan()

    def update_frame(self, dt):
        if self.capture is None or not self.capture.isOpened():
            return

        ret, frame = self.capture.read()
        if not ret or frame is None:
            return

        try:
            barcodes = pyzbar.decode(frame)
            for barcode in barcodes:
                barcode_data = barcode.data.decode('utf-8')
                self.stop_camera()
                self.handle_scanned_barcode(barcode_data)
                return

            buf1 = cv2.flip(frame, 0).tobytes()
            texture = Texture.create(size=(frame.shape[1], frame.shape[0]), colorfmt='bgr')
            texture.blit_buffer(buf1, colorfmt='bgr', bufferfmt='ubyte')
            self.ids.camera_preview.texture = texture
        except Exception as e:
            print("Frame error:", e)

    def handle_scanned_barcode(self, code):
        app = App.get_running_app()

        if app.scan_mode == 'fill_input':
            edit_screen = self.manager.get_screen('product_edit')
            self.manager.current = 'product_edit'
            edit_screen.open_edit_popup(scanned_code=code)
        else:
            self.search_product_by_barcode(code)

    def search_product_by_barcode(self, code):
        app = App.get_running_app()
        if not db or not app.user_uid:
            self.manager.current = 'catalog'
            return
        
        try:
            docs = db.collection("products").where("users_id", "==", app.user_uid).where("barcode", "==", code).stream()
            found = False
            for doc in docs:
                found = True
                p = doc.to_dict()
                self.manager.get_screen('catalog').add_to_cart(p, doc.id)
                break

            if not found:
                Popup(title='Tidak Ditemukan', content=Label(text=f'Barcode: {code}\ntidak terdaftar pada akun ini'), size_hint=(0.8, 0.25)).open()
        except Exception as e:
            print("Error barcode:", e)
        
        self.manager.current = 'catalog'

    def cancel_scan(self):
        self.stop_camera()
        app = App.get_running_app()
        if app.scan_mode == 'fill_input':
            self.manager.current = 'product_edit'
            self.manager.get_screen('product_edit').open_edit_popup()
        else:
            self.manager.current = app.previous_screen or 'catalog'

    def stop_camera(self):
        Clock.unschedule(self.update_frame)
        if self.capture is not None and self.capture.isOpened():
            self.capture.release()
            self.capture = None

    def on_leave(self):
        self.stop_camera()


class POSNaufalApp(App):
    cart = ListProperty([])
    id_token = StringProperty('')
    user_email = StringProperty('')
    user_uid = StringProperty('')
    kasir_name = StringProperty('Kasir 1')
    ip_camera_url = StringProperty('')
    current_screen = StringProperty('login')
    previous_screen = StringProperty('catalog')
    scan_mode = StringProperty('cart')

    printer_status = StringProperty('Belum terhubung')
    printer_connected = BooleanProperty(False)

    def build(self):
        self.bluetooth_printer = BluetoothPrinterManager()
        self.google_signin = GoogleSignInHelper()

        # Penyimpanan sesi login supaya user tidak perlu login ulang tiap buka app
        self.store = JsonStore(os.path.join(self.user_data_dir, 'session.json'))

        root = Builder.load_string(KV)
        # Transisi antar layar yang lebih halus (fade) daripada perpindahan instan
        root.transition = FadeTransition(duration=0.15)
        return root

    def on_start(self):
        self.try_auto_login()

    def try_auto_login(self):
        """Cek sesi tersimpan; kalau ada refresh token yang valid, login otomatis
        tanpa menampilkan layar login sama sekali."""
        if not self.store.exists('session'):
            return

        data = self.store.get('session')
        refresh_token = data.get('refresh_token', '')
        if not refresh_token:
            return

        url = f"https://securetoken.googleapis.com/v1/token?key={FIREBASE_WEB_API_KEY}"
        payload = {"grant_type": "refresh_token", "refresh_token": refresh_token}

        try:
            res = requests.post(url, data=payload, timeout=10)
            result = res.json()

            if "error" in result:
                # Refresh token kadaluarsa/dicabut -> hapus sesi, minta login manual
                self.clear_session()
                return

            self.id_token = result["id_token"]
            self.user_uid = result["user_id"]
            self.user_email = data.get('email', '')
            self.kasir_name = data.get('kasir_name', 'Kasir 1')
            self.cart = []

            # Simpan lagi (refresh_token biasanya tetap sama, tapi jaga-jaga jika berubah)
            self.save_session(result.get("refresh_token", refresh_token))

            if self.root:
                self.root.current = 'catalog'
        except Exception as e:
            print("Auto-login gagal (mungkin tidak ada koneksi):", e)

    def save_session(self, refresh_token):
        """Simpan refresh token secara lokal agar sesi bertahan meski app ditutup."""
        if not refresh_token:
            return
        try:
            self.store.put(
                'session',
                refresh_token=refresh_token,
                email=self.user_email,
                kasir_name=self.kasir_name
            )
        except Exception as e:
            print("Gagal simpan sesi:", e)

    def clear_session(self):
        try:
            if self.store.exists('session'):
                self.store.delete('session')
        except Exception as e:
            print("Gagal hapus sesi:", e)

    def logout(self):
        self.id_token = ''
        self.user_email = ''
        self.user_uid = ''
        self.kasir_name = 'Kasir 1'
        self.ip_camera_url = ''
        self.cart = []
        self.clear_session()
        if hasattr(self, 'bluetooth_printer'):
            self.bluetooth_printer.disconnect()
        self.root.current = 'login'


if __name__ == '__main__':
>>>>>>> 50224775c0579f5629e38468f2a2576d6815c1fe
    POSNaufalApp().run()