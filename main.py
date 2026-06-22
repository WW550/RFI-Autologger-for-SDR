import tkinter as tk
import time
import os
import ctypes
import threading
from tkinter import ttk, messagebox
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

try:
    from serial.tools import list_ports
except Exception:
    list_ports = None

from version import APP_NAME, APP_VERSION
from settings import load_settings, save_settings
from hackrf_device import HackRFDevice
from rtlsdr_device import RTLSDRDevice
from sdrplay_device import SDRplayDevice
from gps_manager import GPSManager
from log_writer import LogWriter
from signal_math import dbfs_from_iq, channel_dbfs_from_iq, spectrum_bars, bars_to_text, iq_stats, condition_iq, spectrum_analysis
from audio_engine import AudioEngine

# Simple built-in 5x7 dot-matrix font.  This avoids relying on external font files
# and lets us draw dim "disabled" dots behind active dots like a real display.
DOT_FONT = {
    "0": ["11111","10001","10011","10101","11001","10001","11111"],
    "1": ["00100","01100","00100","00100","00100","00100","01110"],
    "2": ["11110","00001","00001","11110","10000","10000","11111"],
    "3": ["11110","00001","00001","01110","00001","00001","11110"],
    "4": ["10010","10010","10010","11111","00010","00010","00010"],
    "5": ["11111","10000","10000","11110","00001","00001","11110"],
    "6": ["01111","10000","10000","11110","10001","10001","01110"],
    "7": ["11111","00001","00010","00100","01000","01000","01000"],
    "8": ["01110","10001","10001","01110","10001","10001","01110"],
    "9": ["01110","10001","10001","01111","00001","00001","11110"],
    ".": ["00000","00000","00000","00000","00000","01100","01100"],
    ":": ["00000","01100","01100","00000","01100","01100","00000"],
    "-": ["00000","00000","00000","11111","00000","00000","00000"],
    "+": ["00000","00100","00100","11111","00100","00100","00000"],
    " ": ["00000","00000","00000","00000","00000","00000","00000"],
    "A": ["01110","10001","10001","11111","10001","10001","10001"],
    "B": ["11110","10001","10001","11110","10001","10001","11110"],
    "C": ["01111","10000","10000","10000","10000","10000","01111"],
    "D": ["11110","10001","10001","10001","10001","10001","11110"],
    "d": ["00001","00001","01111","10001","10001","10001","01111"],
    "E": ["11111","10000","10000","11110","10000","10000","11111"],
    "F": ["11111","10000","10000","11110","10000","10000","10000"],
    "G": ["01111","10000","10000","10011","10001","10001","01111"],
    "H": ["10001","10001","10001","11111","10001","10001","10001"],
    "I": ["11111","00100","00100","00100","00100","00100","11111"],
    "J": ["00111","00010","00010","00010","00010","10010","01100"],
    "K": ["10001","10010","10100","11000","10100","10010","10001"],
    "L": ["10000","10000","10000","10000","10000","10000","11111"],
    "M": ["10001","11011","10101","10101","10001","10001","10001"],
    "N": ["10001","11001","10101","10011","10001","10001","10001"],
    "O": ["01110","10001","10001","10001","10001","10001","01110"],
    "P": ["11110","10001","10001","11110","10000","10000","10000"],
    "Q": ["01110","10001","10001","10001","10101","10010","01101"],
    "R": ["11110","10001","10001","11110","10100","10010","10001"],
    "S": ["01111","10000","10000","01110","00001","00001","11110"],
    "T": ["11111","00100","00100","00100","00100","00100","00100"],
    "U": ["10001","10001","10001","10001","10001","10001","01110"],
    "V": ["10001","10001","10001","10001","10001","01010","00100"],
    "W": ["10001","10001","10001","10101","10101","10101","01010"],
    "X": ["10001","10001","01010","00100","01010","10001","10001"],
    "Y": ["10001","10001","01010","00100","00100","00100","00100"],
    "Z": ["11111","00001","00010","00100","01000","10000","11111"],
}



def enable_windows_dark_title_bar(root):
    """Ask Windows 10/11 to draw a dark native title bar.

    This is a no-op on non-Windows systems or if the DWM attribute is not
    supported. It does not affect DSP/audio performance.
    """
    try:
        if os.name != "nt":
            return
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        value = ctypes.c_int(1)
        # 20 is the Windows 11/modern attribute. 19 works on some older builds.
        for attr in (20, 19):
            try:
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    ctypes.c_void_p(hwnd),
                    ctypes.c_int(attr),
                    ctypes.byref(value),
                    ctypes.sizeof(value),
                )
            except Exception:
                pass
    except Exception:
        pass


class StatusSink:
    """No-op status target used after RC2 removed the bottom status bar.

    Many existing callbacks still call self.status.config(text=...). Keeping a
    tiny sink avoids touching SDR/GPS/logging logic while removing the visual
    widget from the UI.
    """
    def config(self, *args, **kwargs):
        pass
    configure = config

class DotMatrixPanel(tk.Canvas):
    def __init__(self, master, **kwargs):
        super().__init__(master, bg="#D6C79B", highlightthickness=1, highlightbackground="#A99972", bd=0, height=270, **kwargs)
        self.active = "#4B2A74"
        self.dim = "#B8A778"
        self.glow = "#5E3A8C"
        self.freq_text = "132.000000"
        self.mode_text = "AM"
        self.range_text = ""
        self.dbfs = -120.0
        self.bars = [0] * 31
        self.gps_line = "GPS NO GPS"
        self.gps_time_line = "UTC --"
        self.gain_line = "LNA -- VGA --"
        self.bind("<Configure>", lambda e: self.redraw())

    def set_values(self, freq_text, mode_text, range_text, dbfs, bars_text, gps_line=None, gps_time_line=None, gain_line=None):
        self.freq_text = freq_text
        self.mode_text = mode_text
        self.range_text = range_text
        self.dbfs = float(dbfs)
        if gps_line is not None:
            self.gps_line = gps_line
        if gps_time_line is not None:
            self.gps_time_line = gps_time_line
        if gain_line is not None:
            self.gain_line = gain_line
        levels = {"▁":1, "▂":2, "▃":3, "▄":4, "▅":5, "▆":6, "▇":7, "█":8}
        self.bars = [levels.get(ch, 1) for ch in str(bars_text)[:31]]
        while len(self.bars) < 31:
            self.bars.append(1)
        self.redraw()

    def _dot_char(self, x, y, ch, scale=3, spacing=1, color=None):
        patt = DOT_FONT.get(ch, DOT_FONT.get(ch.upper(), DOT_FONT[" "]))
        dot = scale
        pitch = scale + spacing
        # disabled dots first
        for r, row in enumerate(patt):
            for c, bit in enumerate(row):
                cx = x + c * pitch
                cy = y + r * pitch
                self.create_oval(cx, cy, cx + dot, cy + dot, fill=self.dim, outline="")
        for r, row in enumerate(patt):
            for c, bit in enumerate(row):
                if bit == "1":
                    cx = x + c * pitch
                    cy = y + r * pitch
                    self.create_oval(cx, cy, cx + dot, cy + dot, fill=color or self.active, outline="")
        return 6 * pitch

    def _dot_text(self, x, y, text, scale=3, spacing=1, color=None, max_chars=None):
        if max_chars:
            text = text[:max_chars]
        cur = x
        for ch in str(text):
            cur += self._dot_char(cur, y, ch, scale=scale, spacing=spacing, color=color)
        return cur

    def _dot_bar(self, x, y, segments=18, active_segments=0, scale=2, spacing=1):
        """Draw a lightweight full-height signal bar.

        v0.1.41: the earlier dot-by-dot bar created hundreds of Canvas oval
        objects every refresh.  That looked nice, but on some Windows systems
        it could steal small slices of time from monitor audio.  This version
        keeps the same dot-matrix/segmented look using one rectangle per
        segment, which is far lighter to redraw.
        """
        pitch = scale + spacing
        seg_w = pitch * 3
        seg_h = pitch * 7
        seg_gap = pitch * 2
        for i in range(segments):
            active = i < active_segments
            color = self.active if active else self.dim
            sx = x + i * (seg_w + seg_gap)
            self.create_rectangle(sx, y, sx + seg_w, y + seg_h, fill=color, outline="#8F8058")
            # tiny internal slits preserve the old matrix/segmented feel without
            # drawing every dot as an independent oval.
            for cut in (1, 2):
                cx = sx + cut * pitch
                self.create_line(cx, y, cx, y + seg_h, fill="#9D8F66")

    def redraw(self):
        self.delete("all")
        w = max(720, self.winfo_width())
        h = max(220, self.winfo_height())
        for yy in range(0, h, 4):
            self.create_line(0, yy, w, yy, fill="#BCAA7A")

        # v0.1.23: spectrum removed.  The panel is now a lighter CRT
        # instrument display focused on frequency, mode, and signal only.
        self._dot_text(14, 14, "FREQ", scale=2)
        self._dot_text(92, 8, self.freq_text, scale=5, spacing=2, color=self.glow)
        self._dot_text(14, 74, f"MODE {self.mode_text}", scale=2)

        # v0.5.8: remove the graphical signal bar.  The logger now shows only
        # the numeric tuned-channel signal value used for CSV/KML mapping.
        sig_x = 14
        sig_y = 122
        self._dot_text(sig_x, sig_y, f"SIGNAL {self.dbfs:.1f} dBFS", scale=3, color=self.glow, max_chars=24)

        # GPS readout in the receiver panel.
        self._dot_text(14, 216, self.gps_line, scale=2, color=self.active, max_chars=34)
        self._dot_text(14, 242, self.gps_time_line, scale=2, color=self.active, max_chars=34)

        # RC2: LNA/VGA values moved next to their sliders; receiver panel no longer draws gain text.


class RFILoggerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        # Use the native Windows title bar/taskbar button so the OS theme controls it.
        self.title(f"{APP_NAME} v{APP_VERSION}")
        try:
            self.iconbitmap("rfi_auto_logger_sdr.ico")
        except Exception:
            pass
        # v0.1.32: request Windows 10/11 dark native title bar. This is
        # a no-op if unsupported and has no DSP/audio performance impact.
        self.after(100, lambda: enable_windows_dark_title_bar(self))
        # v0.1.23: use a safe default height and restore the last good size.
        # Earlier builds opened too short, hiding the GPS/action buttons.
        saved_geometry = self.settings.get("window_geometry", "1120x900")
        self.geometry(saved_geometry)
        self.minsize(1120, 880)
        self._is_maximized = False
        self._normal_geometry = None
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._resize_start_w = 0
        self._resize_start_h = 0
        self._resize_start_x = 0
        self._resize_start_y = 0
        self._setup_styles()

        self.device_type_var = tk.StringVar(value=self.settings.get("sdr_device", "HackRF"))
        self.radio = self._create_radio_device()
        self.gps = GPSManager(self.settings["gps_port"], self.settings["gps_baud"])
        self.logger = LogWriter(self.settings["output_dir"])
        self.audio = AudioEngine()

        self.current_dbfs = -120.0
        self.display_frequency_hz = int(self.settings.get("frequency_hz", 132_000_000))
        self.mode_var = tk.StringVar(value=self.settings["mode"])
        self.freq_var = tk.StringVar(value=f'{self.settings["frequency_hz"] / 1_000_000:.6f}')
        self.radio_status_var = tk.StringVar(value="SDR: NO RADIO")
        self.gps_status_var = tk.StringVar(value="GPS: NO GPS")
        self.signal_var = tk.StringVar(value="-120.0 dBFS")
        self.compact_meter_var = tk.StringVar(value="SIG ░░░░░░░░░░ -120.0")
        self.iq_stats_var = tk.StringVar(value="IQ: waiting")
        self.rx_diag_var = tk.StringVar(value="RX: not started")
        self.receiver_validation_var = tk.StringVar(value="Receiver validation: waiting")
        self.audio_validation_var = tk.StringVar(value="Audio validation: OFF")
        self.spectrum_var = tk.StringVar(value="▁" * 31)
        self.spectrum_range_var = tk.StringVar(value="")
        self.logging_var = tk.StringVar(value="LOGGING: OFF")
        self.logging_panel_var = tk.StringVar(value="LOGGING: STANDBY")
        self.logging_detail_var = tk.StringVar(value="CSV: not active | KML: ready")
        self._logging_started_at = None
        self._last_log_write = 0.0
        self.log_interval_var = tk.StringVar(value=str(self.settings.get("log_interval_sec", 1.0)))
        self.show_debug_var = tk.BooleanVar(value=bool(self.settings.get("show_debug", False)))
        self.kml_green_below_var = tk.StringVar(value=str(self.settings.get("kml_green_below", -60.0)))
        self.kml_yellow_below_var = tk.StringVar(value=str(self.settings.get("kml_yellow_below", -45.0)))
        self.kml_orange_below_var = tk.StringVar(value=str(self.settings.get("kml_orange_below", -30.0)))
        self.kml_signal_labels_var = tk.BooleanVar(value=bool(self.settings.get("kml_signal_labels", True)))

        self.lna_var = tk.IntVar(value=self.settings["lna_gain"])
        self.vga_var = tk.IntVar(value=self.settings.get("vga_gain", 20))
        self.lna_value_var = tk.StringVar(value=f"{self.lna_var.get()} dB")
        self.vga_value_var = tk.StringVar(value=f"{self.vga_var.get()} dB")
        self.amp_var = tk.BooleanVar(value=self.settings["amp_enabled"])
        self.atten_10db_var = tk.BooleanVar(value=bool(self.settings.get("atten_10db_enabled", False)))
        self.dc_var = tk.BooleanVar(value=self.settings["dc_correction"])
        self.iq_var = tk.BooleanVar(value=self.settings["iq_correction"])
        self.volume_var = tk.IntVar(value=self.settings["volume"])
        self.squelch_var = tk.DoubleVar(value=self.settings["squelch_dbfs"])
        self.audio_enabled_var = tk.BooleanVar(value=bool(self.settings.get("audio_enabled", True)))
        self.gps_port_var = tk.StringVar(value=self.settings.get("gps_port", "COM3"))
        self.gps_baud_var = tk.StringVar(value=str(self.settings.get("gps_baud", 9600)))
        self.ppm_correction_var = tk.DoubleVar(value=float(self.settings.get("ppm_correction", 0.0)))
        self.internal_lo_offset_hz = int(self.settings.get("internal_lo_offset_hz", 10000))
        # v0.1.23: keep the audio path responsive by throttling expensive
        # CRT/spectrum/validation redraw work. The HackRF + audio processing
        # still runs every loop, but the big Canvas display updates at ~4 FPS.
        self._last_visual_update = 0.0
        self._last_diag_update = 0.0
        self._last_signal_update = 0.0
        self._last_sdrplay_peak_update = 0.0
        self.sdrplay_peak_offset_hz = 0.0
        self.sdrplay_peak_snr_db = 0.0
        self.sdrplay_audio_offset_hz = 0.0
        self.sdrplay_peak_lock = "NO PEAK"
        self._last_spectrum_text = "▁" * 31
        self._closing = False
        self._update_after_id = None
        # v0.3.5: SDRplay callbacks arrive in small packets and the GUI loop
        # can leave the audio queue under-filled.  Feed SDRplay audio from a
        # small background worker so monitor audio does not depend on Tk redraw
        # timing.  HackRF/RTL-SDR keep their existing path unchanged.
        self._audio_worker_running = True
        self._audio_worker_thread = threading.Thread(target=self._sdrplay_audio_worker, daemon=True)
        self._audio_worker_thread.start()

        self._build_ui()
        self._fit_window_to_content()
        self._update_spectrum_range_label()
        self.connect_radio()
        if self.audio_enabled_var.get():
            self.after(100, self.toggle_audio)
        self._schedule_update_loop(250)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _setup_styles(self):
        self.configure(bg="#C8B889")
        # Make dropdown lists readable in the CRT theme. Native ttk comboboxes
        # otherwise keep a white listbox/background on Windows.
        self.option_add("*TCombobox*Listbox.background", "#D6C79B")
        self.option_add("*TCombobox*Listbox.foreground", "#4B2A74")
        self.option_add("*TCombobox*Listbox.selectBackground", "#A99972")
        self.option_add("*TCombobox*Listbox.selectForeground", "#5E3A8C")
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("CRT.TFrame", background="#C8B889")
        style.configure("CRT.TLabel", background="#C8B889", foreground="#4B2A74", font=("Consolas", 10))
        style.configure("AmberDebug.TLabel", background="#C8B889", foreground="#6B3FA0", font=("Consolas", 11, "bold"))
        style.configure("CRTHeader.TLabel", background="#C8B889", foreground="#5E3A8C", font=("Consolas", 16, "bold"))
        style.configure("CRTDisplay.TLabel", background="#D6C79B", foreground="#4B2A74", font=("Consolas", 30, "bold"), padding=4)
        style.configure("CRTSmall.TLabel", background="#D6C79B", foreground="#4B2A74", font=("Consolas", 10))
        style.configure("CRT.TLabelframe", background="#C8B889", foreground="#4B2A74", bordercolor="#6A4A8E")
        style.configure("CRT.TLabelframe.Label", background="#C8B889", foreground="#5E3A8C", font=("Consolas", 10, "bold"))
        style.configure("CRT.Horizontal.TProgressbar", troughcolor="#C8B889", background="#4B2A74", bordercolor="#6A4A8E", lightcolor="#4B2A74", darkcolor="#5E3A8C")
        style.configure("TFrame", background="#C8B889")
        style.configure("TLabel", background="#C8B889", foreground="#4B2A74", font=("Consolas", 10))
        style.configure("TButton", background="#B9AA80", foreground="#5E3A8C", bordercolor="#6A4A8E", focusthickness=0)
        style.map("TButton", background=[("active", "#B8A778")], foreground=[("active", "#F8F1D5")])
        # Connection-state buttons: dark when inactive, illuminated when live.
        style.configure("Active.TButton", background="#6F4AA0", foreground="#F0E6C8", bordercolor="#5E3A8C", focusthickness=0, font=("Consolas", 10, "bold"))
        style.map("Active.TButton", background=[("active", "#7C56AC")], foreground=[("active", "#24143A")])
        style.configure("Inactive.TButton", background="#B9AA80", foreground="#5E3A8C", bordercolor="#6A4A8E", focusthickness=0)
        style.map("Inactive.TButton", background=[("active", "#B8A778")], foreground=[("active", "#F8F1D5")])
        # Dedicated receiver mode buttons: selected mode looks like an
        # illuminated field-instrument pushbutton.
        style.configure("Mode.TButton", background="#B9AA80", foreground="#5E3A8C", bordercolor="#6A4A8E", focusthickness=0, font=("Consolas", 10, "bold"), padding=(8, 4))
        style.map("Mode.TButton", background=[("active", "#AFA06F")], foreground=[("active", "#5E3A8C")])
        style.configure("ModeActive.TButton", background="#6F4AA0", foreground="#F0E6C8", bordercolor="#5E3A8C", focusthickness=0, font=("Consolas", 10, "bold"), padding=(8, 4))
        style.map("ModeActive.TButton", background=[("active", "#7C56AC")], foreground=[("active", "#24143A")])
        style.configure("TCheckbutton", background="#C8B889", foreground="#4B2A74")
        style.map("TCheckbutton", background=[("active", "#C8B889")], foreground=[("active", "#5E3A8C")])
        style.configure("TEntry", fieldbackground="#D6C79B", foreground="#4B2A74", insertcolor="#4B2A74")
        style.configure("TCombobox", fieldbackground="#D6C79B", foreground="#4B2A74", background="#B9AA80", arrowcolor="#4B2A74")
        style.map("TCombobox",
                  fieldbackground=[("readonly", "#D6C79B")],
                  foreground=[("readonly", "#4B2A74")],
                  selectbackground=[("readonly", "#D6C79B")],
                  selectforeground=[("readonly", "#4B2A74")])

    def _available_com_ports(self):
        if list_ports is None:
            return []
        try:
            return [p.device for p in list_ports.comports()]
        except Exception:
            return []

    def _build_titlebar(self):
        bar = tk.Frame(self, bg="#111C11", height=28, highlightthickness=1, highlightbackground="#355835")
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        # BeOS/old UNIX workstation style: compact square buttons and a tab-like title.
        def make_btn(text, command, bg="#1D2A1D", fg="#C8FFC8"):
            return tk.Button(
                bar, text=text, command=command, width=3, height=1,
                bg=bg, fg=fg, activebackground="#335533", activeforeground="#F8F1D5",
                relief="raised", bd=1, highlightthickness=0, font=("Consolas", 9, "bold")
            )

        make_btn("_", self._minimize_window, bg="#2A2412", fg="#FFE28A").pack(side="left", padx=(5, 2), pady=3)
        make_btn("□", self._toggle_maximize, bg="#172817", fg="#A8FFA8").pack(side="left", padx=2, pady=3)
        make_btn("×", self.on_close, bg="#321414", fg="#FF9A9A").pack(side="left", padx=2, pady=3)

        title_tab = tk.Label(
            bar, text=f"  {APP_NAME}  v{APP_VERSION}  ",
            bg="#CDBB72", fg="#12180C", font=("Consolas", 10, "bold"),
            relief="raised", bd=1
        )
        title_tab.pack(side="left", padx=(10, 4), pady=3)

        status = tk.Label(
            bar, text="HACKRF FIELD RECEIVER", bg="#111C11", fg="#4B2A74",
            font=("Consolas", 9)
        )
        status.pack(side="left", padx=8)

        grip = tk.Label(bar, text="::::", bg="#111C11", fg="#456845", font=("Consolas", 10, "bold"))
        grip.pack(side="right", padx=8)

        for widget in (bar, title_tab, status, grip):
            widget.bind("<ButtonPress-1>", self._start_move)
            widget.bind("<B1-Motion>", self._do_move)
            widget.bind("<Double-Button-1>", lambda e: self._toggle_maximize())

    def _start_move(self, event):
        self._drag_start_x = event.x_root - self.winfo_x()
        self._drag_start_y = event.y_root - self.winfo_y()

    def _do_move(self, event):
        if self._is_maximized:
            return
        x = event.x_root - self._drag_start_x
        y = event.y_root - self._drag_start_y
        self.geometry(f"+{x}+{y}")

    def _minimize_window(self):
        self.iconify()

    def _restore_override_if_visible(self):
        try:
            if self.state() != "iconic":
                pass
            else:
                self.after(250, self._restore_override_if_visible)
        except Exception:
            pass

    def _toggle_maximize(self):
        if not self._is_maximized:
            self._normal_geometry = self.geometry()
            w = self.winfo_screenwidth()
            h = self.winfo_screenheight()
            self.geometry(f"{w}x{h}+0+0")
            self._is_maximized = True
        else:
            if self._normal_geometry:
                self.geometry(self._normal_geometry)
            self._is_maximized = False

    def _start_resize(self, event):
        self._resize_start_w = self.winfo_width()
        self._resize_start_h = self.winfo_height()
        self._resize_start_x = event.x_root
        self._resize_start_y = event.y_root

    def _do_resize(self, event):
        if self._is_maximized:
            return
        min_w, min_h = self.minsize()
        new_w = max(min_w, self._resize_start_w + (event.x_root - self._resize_start_x))
        new_h = max(min_h, self._resize_start_h + (event.y_root - self._resize_start_y))
        self.geometry(f"{int(new_w)}x{int(new_h)}")


    def _crt_scale(self, master, variable, from_, to, command=None):
        """Theme-friendly Tk scale. ttk.Scale is hard to recolor on Windows."""
        return tk.Scale(
            master, from_=from_, to=to, orient="horizontal", variable=variable,
            command=command, showvalue=False, resolution=1,
            bg="#C8B889", fg="#4B2A74", troughcolor="#B4A577",
            activebackground="#4B2A74", highlightthickness=1,
            highlightbackground="#6A4A8E", bd=1, sliderrelief="raised",
            length=300
        )

    def _build_menu(self):
        """Create the native menu bar. RC2 moves GPS configuration here."""
        try:
            menu = tk.Menu(self)
            settings_menu = tk.Menu(menu, tearoff=0)
            settings_menu.add_command(label="GPS Settings...", command=self.open_gps_settings)
            menu.add_cascade(label="Settings", menu=settings_menu)
            self.config(menu=menu)
        except Exception:
            pass

    def open_gps_settings(self):
        """GPS configuration dialog moved out of the main field panel."""
        win = tk.Toplevel(self)
        win.title("GPS Settings")
        win.configure(bg="#C8B889")
        win.resizable(False, False)
        try:
            win.transient(self)
        except Exception:
            pass

        frame = ttk.Frame(win, padding=12, style="CRT.TFrame")
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="GPS Settings", style="CRTHeader.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        ttk.Label(frame, text="COM Port:").grid(row=1, column=0, sticky="w", pady=4)
        ports = self._available_com_ports()
        self.gps_port_combo = ttk.Combobox(frame, textvariable=self.gps_port_var, values=ports, width=12)
        self.gps_port_combo.grid(row=1, column=1, sticky="w", padx=6, pady=4)

        ttk.Label(frame, text="Baud:").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Combobox(frame, textvariable=self.gps_baud_var, values=["4800", "9600", "38400", "57600", "115200"], width=10).grid(row=2, column=1, sticky="w", padx=6, pady=4)

        btns = ttk.Frame(frame, style="CRT.TFrame")
        btns.grid(row=3, column=0, columnspan=3, pady=(12, 0), sticky="w")
        ttk.Button(btns, text="Refresh Ports", command=self.refresh_gps_ports).pack(side="left", padx=4)
        self.connect_gps_button = ttk.Button(btns, text="Connect GPS", command=self.connect_gps, style="Inactive.TButton")
        self.connect_gps_button.pack(side="left", padx=4)
        ttk.Button(btns, text="Disconnect GPS", command=self.disconnect_gps).pack(side="left", padx=4)
        ttk.Button(btns, text="Close", command=win.destroy).pack(side="left", padx=4)
        self._refresh_connection_buttons()

    def _build_ui(self):
        self._build_menu()
        root = ttk.Frame(self, padding=12, style="CRT.TFrame")
        root.pack(fill="both", expand=True)

        top = ttk.Frame(root, style="CRT.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text=f"{APP_NAME} v{APP_VERSION}", style="CRTHeader.TLabel").pack(side="left")

        panel = ttk.LabelFrame(root, text="Receiver Panel", style="CRT.TLabelframe")
        panel.pack(fill="x", pady=10)
        freq_frame = ttk.Frame(panel, padding=10, style="CRT.TFrame")
        freq_frame.pack(fill="x")
        ttk.Label(freq_frame, text="Frequency MHz:").grid(row=0, column=0, sticky="w")
        self.freq_entry = ttk.Entry(freq_frame, textvariable=self.freq_var, width=16, font=("Consolas", 18, "bold"))
        self.freq_entry.grid(row=0, column=1, padx=8)
        self.freq_entry.bind("<Return>", lambda e: self.tune_from_entry())
        self.freq_entry.bind("<KP_Enter>", lambda e: self.tune_from_entry())
        # RC2: ENTER tunes; Tune button removed.
        # v0.1.43: replace dropdown with radio-like mode pushbuttons.
        # This is faster in the field and avoids the light dropdown list that
        # did not match the instrument theme on some Windows systems.
        ttk.Label(freq_frame, text="Mode:").grid(row=0, column=3, padx=(20, 4))
        self.mode_button_frame = ttk.Frame(freq_frame, style="CRT.TFrame")
        self.mode_button_frame.grid(row=0, column=4, columnspan=5, sticky="w")
        self.mode_buttons = {}
        for mode in ["AM", "NFM", "WFM", "USB", "LSB", "CW-U", "CW-L"]:
            btn = ttk.Button(
                self.mode_button_frame,
                text=mode,
                style="Mode.TButton",
                command=lambda m=mode: self.set_mode(m),
            )
            btn.pack(side="left", padx=2)
            self.mode_buttons[mode] = btn
        self._update_mode_buttons()
        ttk.Label(freq_frame, text="PPM Correction:").grid(row=1, column=0, sticky="w", pady=(6,0))
        self.ppm_entry = ttk.Entry(freq_frame, textvariable=self.ppm_correction_var, width=10)
        self.ppm_entry.grid(row=1, column=1, sticky="w", padx=8, pady=(6,0))
        self.ppm_entry.bind("<Return>", lambda e: self.tune_from_entry())
        self.ppm_entry.bind("<KP_Enter>", lambda e: self.tune_from_entry())
        self.connect_sdr_button = ttk.Button(freq_frame, text="Connect / Refresh SDR", command=self.connect_radio, style="Inactive.TButton")
        self.connect_sdr_button.grid(row=1, column=4, sticky="w", padx=(4,0), pady=(6,0))

        self.dot_panel = DotMatrixPanel(panel)
        self.dot_panel.pack(fill="x", padx=10, pady=(4, 8))

        steps = ttk.Frame(panel, padding=(10, 0, 10, 8), style="CRT.TFrame")
        steps.pack(fill="x")
        for label, step in [("-1M", -1_000_000), ("-100k", -100_000), ("-10k", -10_000), ("+10k", 10_000), ("+100k", 100_000), ("+1M", 1_000_000)]:
            ttk.Button(steps, text=label, command=lambda s=step: self.step_frequency(s)).pack(side="left", padx=2)

        log_panel = ttk.LabelFrame(root, text="Logging Status", style="CRT.TLabelframe")
        log_panel.pack(fill="x", pady=6)
        ttk.Label(log_panel, textvariable=self.logging_panel_var, font=("Consolas", 18, "bold")).pack(pady=(8, 2))
        ttk.Label(log_panel, textvariable=self.logging_detail_var, font=("Consolas", 10)).pack(pady=(0, 4))
        log_opts = ttk.Frame(log_panel, style="CRT.TFrame")
        log_opts.pack(pady=(0, 8))
        ttk.Label(log_opts, text="Log interval:").pack(side="left", padx=(0, 4))
        self.log_interval_combo = ttk.Combobox(
            log_opts,
            textvariable=self.log_interval_var,
            values=["0.5", "1", "2", "5", "10", "30"],
            width=6,
            state="readonly",
        )
        self.log_interval_combo.pack(side="left")
        ttk.Label(log_opts, text="seconds").pack(side="left", padx=(4, 18))
        self.log_interval_combo.bind("<<ComboboxSelected>>", lambda e: self.on_log_interval_changed())
        log_buttons = ttk.Frame(log_panel, style="CRT.TFrame")
        log_buttons.pack(pady=(0, 8))
        ttk.Button(log_buttons, text="Start Logging", command=self.start_logging).pack(side="left", padx=4)
        ttk.Button(log_buttons, text="Stop Logging", command=self.stop_logging).pack(side="left", padx=4)
        ttk.Button(log_buttons, text="Save KML", command=self.save_kml).pack(side="left", padx=4)
        ttk.Label(log_opts, text="KML dBFS bands: Green <").pack(side="left")
        ttk.Entry(log_opts, textvariable=self.kml_green_below_var, width=6).pack(side="left", padx=(3, 6))
        ttk.Label(log_opts, text="Yellow <").pack(side="left")
        ttk.Entry(log_opts, textvariable=self.kml_yellow_below_var, width=6).pack(side="left", padx=(3, 6))
        ttk.Label(log_opts, text="Orange <").pack(side="left")
        ttk.Entry(log_opts, textvariable=self.kml_orange_below_var, width=6).pack(side="left", padx=(3, 10))
        ttk.Checkbutton(log_opts, text="KML signal text", variable=self.kml_signal_labels_var).pack(side="left", padx=(4, 0))

        controls = ttk.LabelFrame(root, text="SDR Controls", style="CRT.TLabelframe")
        controls.pack(fill="x", pady=6)
        row0 = ttk.Frame(controls, padding=(8, 8, 8, 0))
        row0.pack(fill="x")
        ttk.Label(row0, text="SDR Device:").pack(side="left")
        self.device_combo = ttk.Combobox(row0, textvariable=self.device_type_var, values=["HackRF", "RTL-SDR", "SDRplay"], width=12, state="readonly")
        self.device_combo.pack(side="left", padx=6)
        self.device_combo.bind("<<ComboboxSelected>>", lambda e: self.on_device_changed())
        self.sdrplay_map_var = tk.StringVar(value="MAP: --")
        self.sdrplay_map_label = ttk.Label(row0, textvariable=self.sdrplay_map_var, style="AmberDebug.TLabel", font=("Consolas", 10, "bold"))
        self.sdrplay_debug_var = tk.StringVar(value="SDRPLAY DEBUG: --")
        self.sdrplay_debug_label = ttk.Label(row0, textvariable=self.sdrplay_debug_var, style="AmberDebug.TLabel", font=("Consolas", 9, "bold"))
        self._debug_widgets = [self.sdrplay_map_label, self.sdrplay_debug_label]
        # Debug widgets are hidden by default in v0.5.10; they can be re-enabled in a future diagnostic build.
        for _w in self._debug_widgets:
            try:
                _w.pack_forget()
            except Exception:
                pass

        row1 = ttk.Frame(controls, padding=8)
        row1.pack(fill="x")
        self.lna_label = ttk.Label(row1, text="LNA")
        self.lna_label.grid(row=0, column=0, sticky="w")
        self._crt_scale(row1, self.lna_var, 0, 40, command=lambda e: self.on_gain_slider_changed()).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Label(row1, textvariable=self.lna_value_var, width=8).grid(row=0, column=2, padx=4)
        ttk.Label(row1, text="VGA").grid(row=1, column=0, sticky="w", pady=(6,0))
        self._crt_scale(row1, self.vga_var, 0, 62, command=lambda e: self.on_gain_slider_changed()).grid(row=1, column=1, sticky="ew", padx=6, pady=(6,0))
        ttk.Label(row1, textvariable=self.vga_value_var, width=8).grid(row=1, column=2, padx=4, pady=(6,0))
        row1.columnconfigure(1, weight=1)
        self.atten_button = ttk.Button(row1, text="ATT -10dB", command=self.toggle_10db_attenuation, style="Inactive.TButton")
        self.atten_button.grid(row=0, column=3, padx=12)
        self._update_atten_button()
        ttk.Checkbutton(row1, text="AMP", variable=self.amp_var, command=self.apply_gains).grid(row=0, column=4, padx=6)
        ttk.Checkbutton(row1, text="DC", variable=self.dc_var).grid(row=0, column=5, padx=6)
        ttk.Checkbutton(row1, text="IQ", variable=self.iq_var).grid(row=0, column=6, padx=6)

        row2 = ttk.Frame(controls, padding=(8, 0, 8, 8))
        row2.pack(fill="x")
        ttk.Checkbutton(row2, text="Audio Monitor", variable=self.audio_enabled_var, command=self.toggle_audio).pack(side="left")
        ttk.Label(row2, text="Volume").pack(side="left", padx=(18, 4))
        self._crt_scale(row2, self.volume_var, 0, 100, command=lambda e: self.update_audio_params()).pack(side="left", fill="x", expand=True, padx=4)
        self.audio_status_var = tk.StringVar(value="Audio: OFF")
        # v0.1.38: AUDIO LED removed. The field tool now uses the
        # receiver-panel signal bargraph as the primary activity indicator.

        # RC2: GPS configuration moved to Settings > GPS Settings.
        # RC2: action buttons moved near their functional panels; bottom status text removed.
        self.status = StatusSink()


    def _fit_window_to_content(self):
        """Open large enough for all controls while staying on screen."""
        try:
            self.update_idletasks()
            req_w = max(self.winfo_reqwidth(), 1120)
            req_h = max(self.winfo_reqheight(), 880)
            screen_w = self.winfo_screenwidth()
            screen_h = self.winfo_screenheight()
            # Leave room for the Windows taskbar and borders.
            fit_w = min(req_w, max(1120, screen_w - 80))
            fit_h = min(req_h, max(880, screen_h - 120))
            # Preserve user's saved position if present, otherwise center.
            geom = str(self.settings.get("window_geometry", ""))
            if "+" in geom:
                # Keep saved origin but correct the size.
                parts = geom.split("+")
                if len(parts) >= 3:
                    self.geometry(f"{int(fit_w)}x{int(fit_h)}+{parts[1]}+{parts[2]}")
                    return
            x = max(0, int((screen_w - fit_w) / 2))
            y = max(0, int((screen_h - fit_h) / 2))
            self.geometry(f"{int(fit_w)}x{int(fit_h)}+{x}+{y}")
        except Exception:
            self.geometry("1120x900")

    def _schedule_update_loop(self, delay_ms=40):
        if getattr(self, "_closing", False):
            return
        try:
            self._update_after_id = self.after(int(delay_ms), self.update_loop)
        except Exception:
            self._update_after_id = None

    def _refresh_connection_buttons(self):
        """Illuminate GPS/SDR connect buttons when the subsystem is active."""
        try:
            if hasattr(self, "connect_sdr_button"):
                self.connect_sdr_button.configure(style="Active.TButton" if getattr(self.radio, "live", False) else "Inactive.TButton")
        except Exception:
            pass
        try:
            gps_status = str(getattr(getattr(self, "gps", None), "fix", None).status if getattr(getattr(self, "gps", None), "fix", None) else "NO GPS")
            gps_active = not gps_status.upper().startswith("NO GPS")
            if hasattr(self, "connect_gps_button"):
                self.connect_gps_button.configure(style="Active.TButton" if gps_active else "Inactive.TButton")
        except Exception:
            pass

    def refresh_gps_ports(self):
        ports = self._available_com_ports()
        self.gps_port_combo["values"] = ports
        if ports and self.gps_port_var.get() not in ports:
            self.gps_port_var.set(ports[0])
        self.status.config(text=f"GPS ports refreshed: {', '.join(ports) if ports else 'none found'}")

    def connect_gps(self):
        self.disconnect_gps(update_status=False)
        port = self.gps_port_var.get().strip()
        try:
            baud = int(self.gps_baud_var.get().strip())
        except Exception:
            messagebox.showerror("GPS Error", "Invalid GPS baud rate.")
            return
        self.gps = GPSManager(port, baud)
        self.gps.start()
        self.settings["gps_port"] = port
        self.settings["gps_baud"] = baud
        self.gps_status_var.set("GPS: SEARCHING")
        self._refresh_connection_buttons()
        self.status.config(text=f"GPS connect requested on {port} at {baud} baud.")

    def disconnect_gps(self, update_status=True):
        try:
            self.gps.stop()
        except Exception:
            pass
        if update_status:
            self.gps_status_var.set("GPS: NO GPS")
            self._refresh_connection_buttons()
            self.status.config(text="GPS disconnected.")

    def _create_radio_device(self):
        device = str(self.device_type_var.get() if hasattr(self, "device_type_var") else self.settings.get("sdr_device", "HackRF")).upper()
        if "SDRPLAY" in device or "SDR PLAY" in device:
            return SDRplayDevice(simulate_when_missing=False)
        if "RTL" in device:
            return RTLSDRDevice(simulate_when_missing=False)
        return HackRFDevice(simulate_when_missing=False)

    def _radio_label(self):
        device = str(self.device_type_var.get()).upper()
        if "SDRPLAY" in device or "SDR PLAY" in device:
            return "SDRplay"
        return "RTL-SDR" if "RTL" in device else "HackRF"

    def _update_device_ui(self):
        if hasattr(self, "lna_label"):
            self.lna_label.config(text="LNA")
        self._update_gain_value_labels()

    def on_device_changed(self):
        try:
            self.audio.stop()
        except Exception:
            pass
        try:
            self.audio.reset_demod_state()
        except Exception:
            pass
        try:
            self.radio.disconnect()
        except Exception:
            pass
        self.settings["sdr_device"] = self.device_type_var.get()
        self.radio = self._create_radio_device()
        self._update_device_ui()
        self._update_atten_button()
        self.radio_status_var.set("SDR: NO SDR")
        self.status.config(text=f"Selected {self._radio_label()}. Click Connect / Refresh SDR.")
        if self.audio_enabled_var.get():
            self.after(100, self.toggle_audio)

    def connect_radio(self):
        self._update_device_ui()
        status = self.radio.connect()
        self.apply_gains()
        self.tune_from_entry(show_errors=False)
        label = self._radio_label()
        if status.connected:
            if status.live:
                self.radio_status_var.set(f"SDR: {label} LIVE")
            else:
                self.radio_status_var.set(f"SDR: {status.message}")
        else:
            self.radio_status_var.set("SDR: NO SDR")
        self.status.config(text=status.message)
        self._refresh_connection_buttons()

    def _frequency_hz_from_entry(self):
        """Parse displayed MHz using Decimal, then store as integer Hz.

        Floating point MHz math caused values such as 124.299989 after
        repeated tune steps.  The application now treats frequency as an
        integer Hz value internally and only formats MHz for display.
        """
        txt = self.freq_var.get().strip().replace(",", "")
        if not txt:
            raise ValueError("empty frequency")
        try:
            mhz = Decimal(txt)
        except InvalidOperation as exc:
            raise ValueError(f"invalid MHz value: {txt}") from exc
        hz = (mhz * Decimal(1000000)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return int(hz)

    def _format_mhz(self, frequency_hz):
        return f"{int(frequency_hz) / 1_000_000:.6f}"


    def _format_gps_datetime(self):
        fix = self.gps.fix
        t = fix.utc_time or ""
        d = getattr(fix, "utc_date", None) or ""
        try:
            hh = t[0:2] or "--"
            mm = t[2:4] or "--"
            ss = t[4:6] or "--"
            time_txt = f"{hh}:{mm}:{ss} UTC"
            if len(d) >= 6:
                day = d[0:2]
                mon = d[2:4]
                yy = int(d[4:6])
                year = 2000 + yy if yy < 80 else 1900 + yy
                return f"UTC {year:04d}-{mon}-{day} {hh}:{mm}:{ss}"
            return f"UTC {time_txt}"
        except Exception:
            return "UTC --"

    def _gps_panel_lines(self):
        fix = self.gps.fix
        if fix.status == "GPS LOCKED" and fix.latitude is not None and fix.longitude is not None:
            return (f"GPS {fix.latitude:.5f} {fix.longitude:.5f}", self._format_gps_datetime())
        return (f"GPS {fix.status}", "UTC --")

    def _update_spectrum_range_label(self):
        try:
            center = self._frequency_hz_from_entry()
        except Exception:
            center = self.radio.frequency_hz
        span = int(self.settings.get("spectrum_span_hz", 2_000_000))
        low = (center - span // 2) / 1_000_000
        mid = center / 1_000_000
        high = (center + span // 2) / 1_000_000
        self.spectrum_range_var.set(f"{low:9.3f} MHz        {mid:9.3f} MHz        {high:9.3f} MHz")

    def _update_mode_buttons(self):
        """Refresh selected/inactive styles for the receiver mode buttons."""
        try:
            active = self.mode_var.get().upper()
            for mode, btn in getattr(self, "mode_buttons", {}).items():
                btn.configure(style="ModeActive.TButton" if mode == active else "Mode.TButton")
        except Exception:
            pass

    def set_mode(self, mode):
        self.mode_var.set(mode)
        self.on_mode_changed()

    def on_mode_changed(self):
        self.settings["mode"] = self.mode_var.get()
        self._update_mode_buttons()
        self.update_audio_params()
        self.tune_from_entry(show_errors=False)
        self.status.config(text=f"Mode set to {self.mode_var.get()} for active demodulator")

    def _ppm_correction(self):
        try:
            return float(self.ppm_correction_var.get())
        except Exception:
            return 0.0

    def _ppm_corrected_frequency_hz(self, requested_hz):
        # PPM is an oscillator correction, not a fixed-Hz offset.
        # Positive values tune the HackRF LO slightly higher. Default is 0.
        return int(round(float(requested_hz) * (1.0 + self._ppm_correction() / 1_000_000.0)))

    def _active_internal_lo_offset_hz(self):
        # Use a hidden low-IF offset for HackRF/RTL modes that suffer when the
        # desired carrier/channel is placed directly on the zero-IF/DC area.
        # v0.3.9: SDRplay no longer uses the fixed +250 kHz test offset. It
        # stays direct-center; the audio path can optionally apply a diagnostic
        # peak-lock shift if a clear RF peak is detected.
        try:
            if self._radio_label() == "SDRplay":
                return 0
        except Exception:
            pass
        if self.mode_var.get().upper() in ("NFM", "AM"):
            return int(self.internal_lo_offset_hz)
        return 0

    def _active_audio_tuning_offset_hz(self):
        # AudioEngine offset is independent of the hardware LO.  For SDRplay
        # v0.3.9, if the strongest RF peak is at +X Hz, use -X Hz so the audio
        # demodulator listens to that peak instead of dead center.
        try:
            if self._radio_label() == "SDRplay" and self.mode_var.get().upper() in ("WFM", "NFM", "AM"):
                # v0.4.5: direct-center with fixed MAP:SWAP.  Peak-lock offset
                # experiments are disabled because SWAP produced the best real
                # NOAA audio and automatic peak chasing can lock onto noise.
                return 0
        except Exception:
            pass
        return int(self._active_internal_lo_offset_hz())

    def tune_from_entry(self, show_errors=True):
        try:
            hz = self._frequency_hz_from_entry()
            ppm = self._ppm_correction()
            internal = self._active_internal_lo_offset_hz()
            ppm_hz = self._ppm_corrected_frequency_hz(hz)
            applied = ppm_hz + internal
            self.display_frequency_hz = int(hz)
            self.freq_var.set(self._format_mhz(self.display_frequency_hz))
            self.radio.set_frequency(applied)
            try:
                self.audio.reset_demod_state()
            except Exception:
                pass
            self.settings["frequency_hz"] = hz
            self.settings["ppm_correction"] = ppm
            self.settings["internal_lo_offset_hz"] = self.internal_lo_offset_hz
            self._update_spectrum_range_label()
            self.update_audio_params()
            self.status.config(text=f"Tuned {hz / 1_000_000:.6f} MHz, {self._radio_label()} LO {applied / 1_000_000:.6f} MHz, PPM {ppm:+.2f}, internal LO {internal:+d} Hz, {self.mode_var.get()}")
        except Exception as e:
            if show_errors:
                messagebox.showerror("Tune Error", f"Invalid frequency: {e}")

    def step_frequency(self, step_hz):
        hz = self._frequency_hz_from_entry() + step_hz
        self.freq_var.set(self._format_mhz(hz))
        self.tune_from_entry(show_errors=False)

    def _attenuation_factor(self):
        """Return software attenuation factor for the optional -10 dB pad.

        This is a digital attenuation applied inside the app to audio/signal
        processing. It does not protect the SDR front-end from overload like a
        physical RF pad would, but it is useful for taming hot signals and for
        keeping logged/displayed levels consistent when intentionally enabled.
        """
        return 10.0 ** (-10.0 / 20.0) if bool(self.atten_10db_var.get()) else 1.0

    def _update_atten_button(self):
        try:
            self.atten_button.configure(style="Active.TButton" if self.atten_10db_var.get() else "Inactive.TButton")
        except Exception:
            pass

    def toggle_10db_attenuation(self):
        self.atten_10db_var.set(not bool(self.atten_10db_var.get()))
        self._update_atten_button()
        try:
            self.settings["atten_10db_enabled"] = bool(self.atten_10db_var.get())
        except Exception:
            pass
        self.status.config(text=f"10 dB software attenuation {'enabled' if self.atten_10db_var.get() else 'disabled'}.")

    def _update_gain_value_labels(self):
        try:
            self.lna_value_var.set(f"{int(self.lna_var.get())} dB")
            self.vga_value_var.set(f"{int(self.vga_var.get())} dB")
        except Exception:
            pass

    def on_gain_slider_changed(self):
        self._update_gain_value_labels()
        self.apply_gains()

    def apply_gains(self):
        self._update_gain_value_labels()
        self.radio.set_gains(self.lna_var.get(), self.vga_var.get(), self.amp_var.get())

    def toggle_audio(self):
        if self.audio_enabled_var.get():
            self.update_audio_params()
            if not self.audio.start():
                self.audio_enabled_var.set(False)
            self.audio_status_var.set(self.audio.status)
        else:
            self.audio.stop()
            self.audio_status_var.set(self.audio.status)

    def update_audio_params(self):
        try:
            vol = float(self.volume_var.get()) / 100.0
        except Exception:
            vol = 0.4
        self.audio.set_params(self.mode_var.get(), vol, self.radio.sample_rate)
        try:
            # v0.5.2: explicit device profile keeps SDRplay beta DSP isolated.
            # HackRF/RTL-SDR must stay on the stable v0.2.0-style audio path.
            selected = str(self.device_type_var.get()).upper()
            self.audio.set_device_profile("SDRplay" if "SDRPLAY" in selected else "Stable")
        except Exception:
            pass
        try:
            self.audio.set_tuning_offset_hz(self._active_audio_tuning_offset_hz())
        except Exception:
            pass
        if hasattr(self, "audio_status_var"):
            self.audio_status_var.set(self.audio.status)
        self.audio_validation_var.set(f"Audio active mode: {self.mode_var.get()} | rate {self.audio.audio_rate} Hz")


    def _update_audio_led(self):
        if not hasattr(self, "audio_led"):
            return
        if not self.audio_enabled_var.get() or not getattr(self.audio, "enabled", False):
            level = 0.0
        else:
            level = max(0.0, min(1.0, float(getattr(self.audio, "audio_level", 0.0))))

        # v0.1.31: less-sensitive three-color panel LED.
        # OFF: dark amber lens. LOW: amber/yellow. NORMAL: green. HIGH: flashing red.
        # Performance note: recolor existing Canvas items only; do not redraw shapes.
        effective = 0.0 if level < 0.05 else min(1.0, (level - 0.05) / 0.75)
        high_flash_on = int(time.monotonic() * 8) % 2 == 0

        if effective <= 0.01:
            glow = "#C8B889"
            bezel = "#100E06"
            lens = "#2A2108"
            core = "#3A2A08"
            highlight = "#6A4A12"
            outline = "#5A4510"
        elif effective < 0.22:
            glow = "#201800"
            bezel = "#181306"
            lens = "#7A5A10"
            core = "#E0A820"
            highlight = "#FFD878"
            outline = "#C09020"
        elif effective < 0.72:
            glow = "#082408"
            bezel = "#081508"
            lens = "#185818"
            core = "#38C838"
            highlight = "#5E3A8C"
            outline = "#6F4AA0"
        else:
            if high_flash_on:
                glow = "#3A0505"
                bezel = "#180808"
                lens = "#8A1616"
                core = "#FF3030"
                highlight = "#FFC0C0"
                outline = "#FF7070"
            else:
                glow = "#100606"
                bezel = "#120707"
                lens = "#3A1010"
                core = "#7A1818"
                highlight = "#B85858"
                outline = "#7A3030"

        try:
            self.audio_led.itemconfig(self.audio_led_glow, fill=glow)
            self.audio_led.itemconfig(self.audio_led_bezel, fill=bezel, outline="#2A2A18")
            self.audio_led.itemconfig(self.audio_led_lens, fill=lens, outline=outline)
            self.audio_led.itemconfig(self.audio_led_core, fill=core)
            self.audio_led.itemconfig(self.audio_led_highlight, fill=highlight)
        except Exception:
            pass


    def _log_interval_seconds(self):
        """Return the selected logging interval as a safe positive float.

        v0.1.36 accidentally referenced this method without defining it.  That
        caused the Tk callback to fail as soon as Start Logging was pressed,
        which made the receiver/audio appear frozen and prevented any CSV rows
        from being written.  Keep this helper defensive so a bad value in the
        settings file cannot break the receive loop.
        """
        try:
            value = float(str(self.log_interval_var.get()).strip())
        except Exception:
            value = 1.0
        if value not in (0.5, 1.0, 2.0, 5.0, 10.0, 30.0):
            # Allow nearby string variants such as "1.0", but clamp anything
            # unexpected to the field-safe default.
            allowed = [0.5, 1.0, 2.0, 5.0, 10.0, 30.0]
            value = min(allowed, key=lambda x: abs(x - value)) if value > 0 else 1.0
        return max(0.5, float(value))

    def on_log_interval_changed(self):
        """Persist and display log interval changes without touching SDR/audio."""
        interval = self._log_interval_seconds()
        # Normalize combobox display so settings like "1.0" become "1".
        text = str(int(interval)) if float(interval).is_integer() else str(interval)
        self.log_interval_var.set(text)
        self.settings["log_interval_sec"] = interval
        if getattr(self.logger, "active", False):
            self.logging_detail_var.set(
                f"CSV: {self.logger.csv_path.name} | points: {len(self.logger.rows)} | interval: {interval:g}s | elapsed: 0s | GPS: {self.gps.fix.status}"
            )
        else:
            self.logging_detail_var.set(
                f"CSV: {self.logger.csv_path.name} | points: {len(self.logger.rows)} | interval: {interval:g}s | GPS: {self.gps.fix.status}"
            )
        self.status.config(text=f"Logging interval set to {interval:g} seconds")

    def start_logging(self):
        if not self.radio.live:
            messagebox.showerror("No SDR", "Cannot start logging because no live SDR is detected.")
            return
        if self.gps.fix.status.startswith("NO GPS"):
            if not messagebox.askyesno("No GPS", "GPS is not available. Continue logging without GPS coordinates?"):
                return
        self.logger.start()
        self._logging_started_at = time.monotonic()
        self._last_log_write = 0.0
        self.logging_var.set("LOGGING: ACTIVE")
        self.logging_panel_var.set("● LOGGING IN PROGRESS")
        self.logging_detail_var.set(f"CSV: {self.logger.csv_path.name} | points: {len(self.logger.rows)} | interval: {self._log_interval_seconds():g}s | KML: ready")

    def stop_logging(self):
        self.logger.stop()
        self._logging_started_at = None
        self.logging_var.set("LOGGING: OFF")
        self.logging_panel_var.set("LOGGING: STOPPED")
        self.logging_detail_var.set(f"CSV: {self.logger.csv_path.name} | points: {len(self.logger.rows)} | KML: ready")

    def _kml_thresholds(self):
        """Read editable KML thresholds safely."""
        def f(var, default):
            try:
                return float(str(var.get()).strip())
            except Exception:
                return float(default)
        vals = sorted([
            f(self.kml_green_below_var, -60.0),
            f(self.kml_yellow_below_var, -45.0),
            f(self.kml_orange_below_var, -30.0),
        ])
        # Normalize UI values after sorting so the user can see what will be used.
        try:
            self.kml_green_below_var.set(str(vals[0]))
            self.kml_yellow_below_var.set(str(vals[1]))
            self.kml_orange_below_var.set(str(vals[2]))
        except Exception:
            pass
        return {"green_below": vals[0], "yellow_below": vals[1], "orange_below": vals[2],
                "show_signal_labels": bool(self.kml_signal_labels_var.get()),
                "kml_green_below": vals[0], "kml_yellow_below": vals[1], "kml_orange_below": vals[2],
                "kml_signal_labels": bool(self.kml_signal_labels_var.get())}

    def save_kml(self):
        path = self.logger.save_kml(self._kml_thresholds())
        messagebox.showinfo("KML Saved", f"Saved KML:\n{path}")

    def update_loop(self):
        if getattr(self, "_closing", False):
            return
        iq = self.radio.read_iq(131072)
        now = time.monotonic()

        # Feed audio first so expensive visual work cannot starve the monitor.
        if self.audio_enabled_var.get() and str(self.device_type_var.get()).upper() != "SDRPLAY":
            # v0.1.40: demodulating too many IQ blocks on the Tk thread can
            # make audio lightly choppy. Two blocks are enough for HackRF/RTL.
            # v0.3.5: SDRplay is handled by the background audio feeder below.
            audio_blocks = self.radio.drain_iq_blocks(max_blocks=2)
            # AM envelope detection needs the RF carrier. Do not remove complex
            # DC/mean before AM demodulation.
            if self.mode_var.get().upper() == "AM":
                audio_blocks = [condition_iq(b, False, self.iq_var.get()) for b in audio_blocks if b is not None]
            else:
                audio_blocks = [condition_iq(b, self.dc_var.get(), self.iq_var.get()) for b in audio_blocks if b is not None]
            att = self._attenuation_factor()
            if att != 1.0:
                audio_blocks = [b * att for b in audio_blocks]
            self.audio.push_iq_blocks(audio_blocks)

        if self.radio.live and not self.radio.is_receiving():
            self.radio_status_var.set(f"SDR: {self._radio_label()} LIVE - NO IQ")
        elif self.radio.live:
            self.radio_status_var.set(f"SDR: {self._radio_label()} LIVE")
        elif not self.radio.connected:
            self.radio_status_var.set("SDR: NO SDR")

        # v0.1.40: Signal math does not need to run at audio cadence.
        # Updating dBFS about 5 times per second is plenty for a field meter
        # and leaves more time for continuous monitor audio.
        diag = self.radio.rx_diagnostics()
        stats = None
        if now - self._last_signal_update >= 0.20:
            self._last_signal_update = now
            stats = iq_stats(iq, self.dc_var.get(), self.iq_var.get())
            # v0.5.8: CSV/KML signal is now tuned-channel pre-demod IQ dBFS,
            # not wideband IQ RMS or post-demod audio level.  This gives better
            # separation between no antenna, quiet frequencies, and real signals.
            self.current_dbfs = channel_dbfs_from_iq(
                iq,
                sample_rate=int(getattr(self.radio, "sample_rate", 2_000_000)),
                mode=self.mode_var.get(),
                dc_correction=self.dc_var.get(),
                iq_correction=self.iq_var.get(),
            )
            if self.atten_10db_var.get():
                self.current_dbfs -= 10.0
            self.signal_var.set(f"{self.current_dbfs:.1f} dBFS")
            self.compact_meter_var.set(f"SIGNAL {self.current_dbfs:6.1f} dBFS")

        # v0.4.1 SDRplay IQ boost/validation diagnostic.  If the SDRplay stream is real but
        # audio is static, the signal may be arriving at a non-zero IF.  Analyze
        # the RF peak offset and feed that as a temporary audio tuning offset.
        if str(self.device_type_var.get()).upper() == "SDRPLAY" and iq is not None and now - self._last_sdrplay_peak_update >= 0.50:
            self._last_sdrplay_peak_update = now
            try:
                pa = spectrum_analysis(iq, sample_rate=int(getattr(self.radio, "sample_rate", 2_000_000)), dc_correction=self.dc_var.get(), iq_correction=self.iq_var.get())
                self.sdrplay_peak_offset_hz = float(pa.get("peak_offset_hz", 0.0))
                self.sdrplay_peak_snr_db = float(pa.get("peak_minus_noise_db", 0.0))
                self.sdrplay_peak_lock = str(pa.get("lock", "NO PEAK"))
                # Only auto-shift for a convincing peak and avoid chasing exact DC.
                if self.sdrplay_peak_snr_db >= 8.0 and abs(self.sdrplay_peak_offset_hz) >= 5000.0:
                    self.sdrplay_audio_offset_hz = -self.sdrplay_peak_offset_hz
                else:
                    self.sdrplay_audio_offset_hz = 0.0
                try:
                    self.audio.set_tuning_offset_hz(self._active_audio_tuning_offset_hz())
                except Exception:
                    pass
            except Exception:
                self.sdrplay_peak_lock = "PEAK ERR"

        # Lighter diagnostics at ~2 FPS.
        if now - self._last_diag_update >= 0.50:
            self._last_diag_update = now
            if stats is None:
                stats = iq_stats(iq, self.dc_var.get(), self.iq_var.get())
            self.iq_stats_var.set(f"IQ block: {stats['samples']} samples   peak: {stats['peak']:.3f}   rms: {stats['rms']:.4f}")
            age_txt = "none" if diag['last_age'] is None else f"{diag['last_age']:.2f}s"
            err_txt = diag['last_error'] if diag['last_error'] else "OK"
            rc = diag.get("gain_rc", {}) or {}
            def rc_txt(name):
                val = rc.get(name)
                if val is None:
                    return "--"
                if isinstance(val, str):
                    return val
                try:
                    return "OK" if int(val) == 0 else f"ERR{val}"
                except Exception:
                    return str(val)
            gain_txt = (
                f"LNA {diag.get('lna')} dB ({rc_txt('lna')})  "
                f"VGA {diag.get('vga')} dB ({rc_txt('vga')})  "
                f"AMP {'ON' if diag.get('amp') else 'OFF'} ({rc_txt('amp')})"
            )
            extra = ""
            if str(self.device_type_var.get()).upper() == "SDRPLAY":
                extra = (
                    f" | SDRplay cb:{diag.get('callbacks', 0)} samples:{diag.get('samples', 0)} "
                    f"raw min/max:{diag.get('raw_min', '--')}/{diag.get('raw_max', '--')} raw rms:{diag.get('raw_rms', 0.0):.5f} "
                    f"q:{diag.get('audio_queue', 0)} accum:{diag.get('audio_accum', 0)}/{diag.get('audio_block_size', 0)} "
                    f"audio blocks:{diag.get('audio_blocks', 0)} audio samples:{diag.get('audio_samples', 0)}"
                )
                if hasattr(self, "sdrplay_map_var"):
                    self.sdrplay_map_var.set(f"MAP: {diag.get('iq_mapping', 'NORM')}")
                if hasattr(self, "sdrplay_debug_var"):
                    self.sdrplay_debug_var.set(
                        f"SDRPLAY IQ  CB:{diag.get('callbacks', 0)}  S:{diag.get('samples', 0)}  N:{diag.get('last_num_samples', 0)}  "
                        f"I:{diag.get('raw_i_rms', 0.0):.5f} [{diag.get('raw_i_min', '--')}/{diag.get('raw_i_max', '--')}]  "
                        f"Q:{diag.get('raw_q_rms', 0.0):.5f} [{diag.get('raw_q_min', '--')}/{diag.get('raw_q_max', '--')}]  "
                        f"IPTR:{'Y' if diag.get('xi_ptr_valid') else 'N'} QPTR:{'Y' if diag.get('xq_ptr_valid') else 'N'}  "
                        f"AQ:{diag.get('audio_queue', 0)} A:{diag.get('audio_blocks', 0)}  "
                        f"API:{getattr(self.radio, 'api_version', '')}  "
                        f"DLL:{str(diag.get('sdrplay_dll_path', ''))[-32:]}  "
                        f"PK:{self.sdrplay_peak_offset_hz/1000:+.0f}k SNR:{self.sdrplay_peak_snr_db:.1f} {self.sdrplay_peak_lock}"
                    )
            else:
                if hasattr(self, "sdrplay_map_var"):
                    self.sdrplay_map_var.set("MAP: --")
                if hasattr(self, "sdrplay_debug_var"):
                    self.sdrplay_debug_var.set("SDRPLAY DEBUG: --")
            self.rx_diag_var.set(f"RX started: {diag['rx_started']} rc: {diag['rx_start_rc']} callbacks: {diag['callbacks']} total samples: {diag['samples']} last bytes: {diag['last_valid_length']} age: {age_txt} {err_txt} | {gain_txt}{extra}")
            if hasattr(self, "audio_status_var"):
                self.audio_status_var.set(self.audio.status)
            self.audio_validation_var.set(f"Audio active mode: {self.mode_var.get()} | rate {self.audio.audio_rate} Hz")

        # Lightweight CRT redraw at ~1 FPS. The signal bar is now intentionally
        # slower because audio stability is more important than a lively meter.
        if now - self._last_visual_update >= 1.00:
            self._last_visual_update = now
            if hasattr(self, "dot_panel"):
                self.dot_panel.set_values(self.freq_var.get(), self.mode_var.get(), "", self.current_dbfs, "", *self._gps_panel_lines(), gain_line=f"LNA {self.lna_var.get():02d} VGA {self.vga_var.get():02d}")
            internal_note = f"audio offset {self._active_audio_tuning_offset_hz()/1000:+.0f}k" if self._active_audio_tuning_offset_hz() else "direct center"
            self.receiver_validation_var.set(
                f"REQ {self.display_frequency_hz/1_000_000:.6f} MHz | {self._radio_label()} {self.radio.frequency_hz/1_000_000:.6f} MHz | "
                f"PPM {self._ppm_correction():+.2f} | MODE {self.mode_var.get()} | {internal_note}"
            )

        self.gps_status_var.set(f"GPS: {self.gps.fix.status}")

        if self.logger.active:
            elapsed = int(time.monotonic() - self._logging_started_at) if self._logging_started_at else 0
            gps_txt = self.gps.fix.status
            interval = self._log_interval_seconds()
            self.logging_panel_var.set("● LOGGING IN PROGRESS")
            self.logging_detail_var.set(
                f"CSV: {self.logger.csv_path.name} | points: {len(self.logger.rows)} | interval: {interval:g}s | elapsed: {elapsed}s | GPS: {gps_txt}"
            )
            if now - self._last_log_write >= interval:
                self._last_log_write = now
                self.logger.write(
                    self.gps.fix.latitude,
                    self.gps.fix.longitude,
                    self.display_frequency_hz,
                    self.mode_var.get(),
                    self.current_dbfs,
                    self.lna_var.get(),
                    self.vga_var.get(),
                    self.amp_var.get(),
                )
        elif self.logging_panel_var.get() == "LOGGING: STANDBY":
            self.logging_detail_var.set(f"CSV: {self.logger.csv_path.name} | points: {len(self.logger.rows)} | GPS: {self.gps.fix.status}")
        self._refresh_connection_buttons()
        self._schedule_update_loop(40)

    def _sdrplay_audio_worker(self):
        """Background SDRplay monitor-audio feeder.

        SDRplay packets are smaller and more timing-sensitive than the HackRF/RTL
        blocks in this prototype.  Running this in a daemon thread keeps NOAA/WFM
        audio fed even when the Tk canvas or status labels are repainting.
        """
        while getattr(self, "_audio_worker_running", False):
            try:
                if (str(self.device_type_var.get()).upper() == "SDRPLAY"
                        and self.audio_enabled_var.get()
                        and getattr(self.audio, "enabled", False)
                        and getattr(self.radio, "live", False)):
                    blocks = self.radio.drain_iq_blocks(max_blocks=4)
                    if blocks:
                        mode = self.mode_var.get().upper()
                        if mode == "AM":
                            blocks = [condition_iq(b, False, self.iq_var.get()) for b in blocks if b is not None]
                        else:
                            blocks = [condition_iq(b, self.dc_var.get(), self.iq_var.get()) for b in blocks if b is not None]
                        att = self._attenuation_factor()
                        if att != 1.0:
                            blocks = [b * att for b in blocks]
                        self.audio.push_iq_blocks(blocks)
                    # Faster than GUI refresh, slower than busy-wait.
                    time.sleep(0.018)
                else:
                    time.sleep(0.08)
            except Exception:
                time.sleep(0.10)

    def toggle_debug(self):
        """Show/hide SDRplay debug widgets safely.

        v0.5.6 added the Show Debug checkbox but the method was missing,
        which caused startup to fail while building the UI.
        """
        show = False
        try:
            show = bool(self.show_debug_var.get())
        except Exception:
            show = False
        for widget in getattr(self, "_debug_widgets", []):
            try:
                if show:
                    widget.pack(side="left", padx=(8, 4))
                else:
                    widget.pack_forget()
            except Exception:
                pass


    def on_close(self):
        """Clean shutdown for SDR/audio/GPS/logging threads.

        v0.5.9: clicking the Windows X could leave the receive/audio loops
        running because only the Tk window was destroyed.  This handler now
        stops the update loop, logging, audio, GPS and the selected SDR before
        destroying the window.
        """
        if getattr(self, "_closing", False):
            return
        self._closing = True
        try:
            self.status.config(text="Closing...")
            self.update_idletasks()
        except Exception:
            pass

        # Stop future Tk update callbacks first.
        try:
            if getattr(self, "_update_after_id", None) is not None:
                self.after_cancel(self._update_after_id)
                self._update_after_id = None
        except Exception:
            pass

        # Persist settings while widgets still exist.
        try:
            self.settings["frequency_hz"] = self.display_frequency_hz
            self.settings["sdr_device"] = self.device_type_var.get()
            self.settings["ppm_correction"] = self._ppm_correction()
            self.settings["internal_lo_offset_hz"] = self.internal_lo_offset_hz
            self.settings["mode"] = self.mode_var.get()
            self.settings["lna_gain"] = self.lna_var.get()
            self.settings["vga_gain"] = self.vga_var.get()
            self.settings["amp_enabled"] = self.amp_var.get()
            self.settings["atten_10db_enabled"] = bool(self.atten_10db_var.get())
            self.settings["dc_correction"] = self.dc_var.get()
            self.settings["iq_correction"] = self.iq_var.get()
            self.settings["volume"] = self.volume_var.get()
            self.settings["audio_enabled"] = self.audio_enabled_var.get()
            self.settings["log_interval_sec"] = self._log_interval_seconds()
            # Debug UI is intentionally hidden in normal builds; do not expose it in settings.
            self.settings.update(self._kml_thresholds())
            self.settings["gps_port"] = self.gps_port_var.get().strip()
            self.settings["window_geometry"] = self.geometry()
            try:
                self.settings["gps_baud"] = int(self.gps_baud_var.get().strip())
            except Exception:
                pass
            save_settings(self.settings)
        except Exception:
            pass

        # Stop user-visible activities.
        try:
            self.stop_logging()
        except Exception:
            try:
                self.logger.stop()
            except Exception:
                pass

        # Stop SDRplay background audio feeder before disconnecting the radio.
        self._audio_worker_running = False
        try:
            if getattr(self, "_audio_worker_thread", None) is not None and self._audio_worker_thread.is_alive():
                self._audio_worker_thread.join(timeout=0.75)
        except Exception:
            pass

        # Stop audio output before releasing SDR hardware.
        try:
            self.audio.stop()
        except Exception:
            pass

        try:
            self.radio.disconnect()
        except Exception:
            pass

        try:
            self.gps.stop()
        except Exception:
            pass
        try:
            if getattr(getattr(self, "gps", None), "thread", None) is not None and self.gps.thread.is_alive():
                self.gps.thread.join(timeout=0.75)
        except Exception:
            pass

        try:
            self.quit()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    app = RFILoggerApp()
    app.mainloop()
