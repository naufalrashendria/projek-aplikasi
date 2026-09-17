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
    POSNaufalApp().run()