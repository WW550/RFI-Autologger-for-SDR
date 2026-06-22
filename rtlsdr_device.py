import ctypes
import os
import threading
import time
from collections import deque
from ctypes import c_int, c_uint32, c_uint8, c_void_p, POINTER

import numpy as np


class RTLSDRStatus:
    def __init__(self, connected=False, message="NO RADIO", live=False, simulated=False):
        self.connected = connected
        self.message = message
        self.live = live
        self.simulated = simulated


_RTL_CALLBACK = ctypes.CFUNCTYPE(None, POINTER(c_uint8), c_uint32, c_void_p)


class RTLSDRDevice:
    """RTL-SDR receive wrapper using rtlsdr.dll.

    First RTL-SDR validation backend for v0.2.0. It follows the same small
    interface used by HackRFDevice so the existing receiver/audio/logger UI can
    run with either SDR. This is intentionally conservative: one dongle, index 0,
    async receive, fixed sample rate, and a single RF gain value mapped from the
    LNA slider.
    """

    def __init__(self, simulate_when_missing=False):
        self.connected = False
        self.live = False
        self.simulated = False
        self.message = "NO RADIO"
        self.name = "RTL-SDR"
        self.frequency_hz = 132_000_000
        # RTL-SDR devices commonly behave well at 2.048 MS/s.
        self.sample_rate = 2_048_000
        self.lna_gain = 20
        self.vga_gain = 0
        self.amp_enabled = False
        self.simulate_when_missing = False

        self._lib = None
        self._dev = c_void_p()
        self._cb = None
        self._rx_thread = None
        self._stop_requested = threading.Event()
        self._lock = threading.Lock()
        self._latest_iq = None
        self._audio_iq_queue = deque(maxlen=48)
        self._dll_dir_handles = []
        self.last_diagnostic = ""
        self.rx_started = False
        self.rx_start_rc = None
        self.rx_callback_count = 0
        self.rx_sample_count = 0
        self.rx_last_valid_length = 0
        self.rx_last_error = ""
        self.rx_last_age = None
        self._last_rx_time = 0.0
        self.last_gain_rc = {"lna": None, "vga": None, "amp": None}
        self.last_filter_rc = None

    def connect(self):
        self.disconnect()
        try:
            self._lib = self._load_librtlsdr()
            self._bind_api(self._lib)
            rc = self._lib.rtlsdr_open(ctypes.byref(self._dev), c_uint32(0))
            if rc != 0 or not self._dev:
                raise RuntimeError(f"rtlsdr_open failed rc={rc}")
            self._configure_live_device()
            rc = self._lib.rtlsdr_reset_buffer(self._dev)
            if rc != 0:
                raise RuntimeError(f"rtlsdr_reset_buffer failed rc={rc}")
            self._cb = _RTL_CALLBACK(self._rx_callback)
            self._stop_requested.clear()
            self._rx_thread = threading.Thread(target=self._rx_worker, name="RTLSDR-RX", daemon=True)
            self._rx_thread.start()
            self.rx_started = True
            self.rx_start_rc = 0
            self.connected = True
            self.live = True
            self.message = "RTL-SDR CONNECTED"
            return RTLSDRStatus(True, self.message, live=True, simulated=False)
        except Exception as e:
            self._cleanup_live()
            self.connected = False
            self.live = False
            self.message = f"NO RADIO - {e}"
            return RTLSDRStatus(False, self.message, live=False, simulated=False)

    def disconnect(self):
        self._cleanup_live()
        self.connected = False
        self.live = False
        self.message = "NO RADIO"

    def _cleanup_live(self):
        try:
            self._stop_requested.set()
            if self._lib is not None and self._dev:
                try:
                    self._lib.rtlsdr_cancel_async(self._dev)
                except Exception:
                    pass
            if self._rx_thread is not None and self._rx_thread.is_alive():
                self._rx_thread.join(timeout=1.0)
            if self._lib is not None and self._dev:
                try:
                    self._lib.rtlsdr_close(self._dev)
                except Exception:
                    pass
        finally:
            self._dev = c_void_p()
            self._cb = None
            self._lib = None
            self._rx_thread = None
            with self._lock:
                self._latest_iq = None
                self._audio_iq_queue.clear()
            self.rx_started = False
            self.rx_start_rc = None
            self.rx_callback_count = 0
            self.rx_sample_count = 0
            self.rx_last_valid_length = 0
            self.rx_last_error = ""
            self.rx_last_age = None

    def _load_librtlsdr(self):
        import platform
        import sys

        names = ["rtlsdr.dll", "librtlsdr.dll", "rtl-sdr.dll"]
        diagnostic = []

        def add(line):
            diagnostic.append(str(line))

        add("RFI Autologger RTL-SDR DLL diagnostic")
        add(f"Python: {sys.version.split()[0]} {platform.architecture()[0]}")
        add(f"Frozen EXE: {getattr(sys, 'frozen', False)}")
        add(f"sys.executable: {sys.executable}")
        add(f"cwd: {os.getcwd()}")
        add(f"__file__: {__file__}")

        base_dirs = []
        try:
            base_dirs.append(os.path.dirname(os.path.abspath(__file__)))
        except Exception:
            pass
        if getattr(sys, "frozen", False):
            base_dirs.append(os.path.dirname(sys.executable))
        if hasattr(sys, "_MEIPASS"):
            base_dirs.append(sys._MEIPASS)
        base_dirs.append(os.getcwd())
        expanded = []
        for d in base_dirs:
            expanded.extend([d, os.path.join(d, "dlls"), os.path.join(d, "optional_future_dlls")])
        unique_dirs = []
        for d in expanded:
            if d and d not in unique_dirs:
                unique_dirs.append(d)

        for d in unique_dirs:
            add(f"search folder: {d} exists={os.path.isdir(d)}")
            if os.path.isdir(d):
                try:
                    h = os.add_dll_directory(d)
                    self._dll_dir_handles.append(h)
                    add(f"add_dll_directory OK: {d}")
                except Exception as e:
                    add(f"add_dll_directory skipped/failed: {d}: {e}")
                try:
                    os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
                except Exception:
                    pass

        candidates = []
        for d in unique_dirs:
            for n in names:
                candidates.append(os.path.join(d, n))
        candidates.extend(names)

        errors = []
        for path in candidates:
            try:
                is_path = os.path.sep in path or (os.path.altsep and os.path.altsep in path)
                if is_path and not os.path.exists(path):
                    errors.append(f"missing: {path}")
                    continue
                folder = os.path.dirname(path) if is_path else ""
                if folder:
                    dep = os.path.join(folder, "libusb-1.0.dll")
                    if os.path.exists(dep):
                        try:
                            ctypes.CDLL(dep, mode=getattr(ctypes, "RTLD_GLOBAL", 0))
                            add(f"preloaded dependency: {dep}")
                        except Exception as e:
                            add(f"dependency preload failed: {dep}: {e}")
                add(f"trying RTL-SDR DLL: {path}")
                lib = ctypes.CDLL(path)
                add(f"SUCCESS loaded RTL-SDR DLL: {path}")
                self.last_diagnostic = "\n".join(diagnostic)
                self._write_diagnostic_file(diagnostic)
                return lib
            except Exception as e:
                msg = f"{path}: {type(e).__name__}: {e}"
                errors.append(msg)
                add("FAILED " + msg)

        add("Final failure: RTL-SDR DLL not loaded")
        for e in errors[-12:]:
            add("  " + e)
        self.last_diagnostic = "\n".join(diagnostic)
        self._write_diagnostic_file(diagnostic)
        raise RuntimeError("RTL-SDR DLL not loaded. " + " | ".join(errors[-8:]))

    def _write_diagnostic_file(self, lines):
        try:
            import sys
            folder = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
            with open(os.path.join(folder, "rtlsdr_diagnostic.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception:
            pass

    def _bind_api(self, lib):
        lib.rtlsdr_open.argtypes = [POINTER(c_void_p), c_uint32]
        lib.rtlsdr_open.restype = c_int
        lib.rtlsdr_close.argtypes = [c_void_p]
        lib.rtlsdr_close.restype = c_int
        lib.rtlsdr_set_center_freq.argtypes = [c_void_p, c_uint32]
        lib.rtlsdr_set_center_freq.restype = c_int
        lib.rtlsdr_get_center_freq.argtypes = [c_void_p]
        lib.rtlsdr_get_center_freq.restype = c_uint32
        lib.rtlsdr_set_sample_rate.argtypes = [c_void_p, c_uint32]
        lib.rtlsdr_set_sample_rate.restype = c_int
        lib.rtlsdr_set_tuner_gain_mode.argtypes = [c_void_p, c_int]
        lib.rtlsdr_set_tuner_gain_mode.restype = c_int
        lib.rtlsdr_set_tuner_gain.argtypes = [c_void_p, c_int]
        lib.rtlsdr_set_tuner_gain.restype = c_int
        lib.rtlsdr_reset_buffer.argtypes = [c_void_p]
        lib.rtlsdr_reset_buffer.restype = c_int
        lib.rtlsdr_read_async.argtypes = [c_void_p, _RTL_CALLBACK, c_void_p, c_uint32, c_uint32]
        lib.rtlsdr_read_async.restype = c_int
        lib.rtlsdr_cancel_async.argtypes = [c_void_p]
        lib.rtlsdr_cancel_async.restype = c_int
        try:
            lib.rtlsdr_set_freq_correction.argtypes = [c_void_p, c_int]
            lib.rtlsdr_set_freq_correction.restype = c_int
        except Exception:
            pass

    def _configure_live_device(self):
        self._check(self._lib.rtlsdr_set_sample_rate(self._dev, c_uint32(int(self.sample_rate))), "set_sample_rate")
        self._check(self._lib.rtlsdr_set_center_freq(self._dev, c_uint32(int(self.frequency_hz))), "set_center_freq")
        self.set_gains(self.lna_gain, self.vga_gain, self.amp_enabled)

    def _check(self, rc, where):
        if rc != 0:
            raise RuntimeError(f"RTL-SDR {where} failed rc={rc}")

    def _rx_worker(self):
        try:
            # buf_num=0 lets the library use its default. 262144 bytes gives
            # stable audio chunks without excessive callback rate.
            rc = self._lib.rtlsdr_read_async(self._dev, self._cb, None, c_uint32(0), c_uint32(262144))
            self.rx_start_rc = int(rc)
            if rc != 0 and not self._stop_requested.is_set():
                self.rx_last_error = f"rtlsdr_read_async returned rc={rc}"
        except Exception as e:
            self.rx_last_error = f"read_async error: {type(e).__name__}: {e}"

    def _rx_callback(self, buf, length, ctx):
        try:
            n = int(length)
            self.rx_callback_count += 1
            self.rx_last_valid_length = n
            if n <= 1 or not buf:
                return
            raw = ctypes.string_at(buf, n)
            u8 = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
            # RTL-SDR IQ is unsigned 8-bit interleaved, centered at 127.5.
            vals = (u8 - 127.5) / 127.5
            usable = (len(vals) // 2) * 2
            if usable <= 1:
                return
            i = vals[:usable:2]
            q = vals[1:usable:2]
            iq = (i + 1j * q).astype(np.complex64)
            now = time.time()
            with self._lock:
                self._latest_iq = iq
                self._audio_iq_queue.append(iq)
                self._last_rx_time = now
                self.rx_last_age = 0.0
                self.rx_sample_count += int(len(iq))
                self.rx_last_error = ""
        except Exception as e:
            self.rx_last_error = f"callback error: {type(e).__name__}: {e}"

    def set_frequency(self, frequency_hz: int):
        self.frequency_hz = int(frequency_hz)
        if self.live and self._lib is not None and self._dev:
            try:
                rc = self._lib.rtlsdr_set_center_freq(self._dev, c_uint32(int(self.frequency_hz)))
                if rc != 0:
                    self.message = f"RTL-SDR frequency set failed rc={rc}"
            except Exception as e:
                self.connected = False
                self.live = False
                self.message = f"NO RADIO - frequency set failed: {e}"

    def set_sample_rate(self, sample_rate: int):
        # Keep a stable RTL-friendly value unless the caller explicitly changes it.
        self.sample_rate = int(sample_rate)
        if self.live and self._lib is not None and self._dev:
            try:
                self._lib.rtlsdr_set_sample_rate(self._dev, c_uint32(int(self.sample_rate)))
            except Exception:
                self.message = "RTL-SDR sample-rate set failed"

    def set_gains(self, lna_gain: int, vga_gain: int, amp_enabled: bool):
        # Reuse LNA slider as RTL-SDR RF gain in dB. Most dongles support a
        # discrete list.  The driver will choose/accept the nearest legal value.
        self.lna_gain = max(0, min(50, int(lna_gain)))
        self.vga_gain = 0
        self.amp_enabled = False
        if self.live and self._lib is not None and self._dev:
            try:
                self.last_gain_rc["lna"] = int(self._lib.rtlsdr_set_tuner_gain_mode(self._dev, c_int(1)))
                self.last_gain_rc["vga"] = int(self._lib.rtlsdr_set_tuner_gain(self._dev, c_int(int(self.lna_gain * 10))))
                self.last_gain_rc["amp"] = 0
            except Exception as e:
                self.message = f"RTL-SDR gain set failed: {e}"

    def read_iq(self, count=8192):
        if self.live:
            with self._lock:
                if self._latest_iq is None:
                    return None
                iq = self._latest_iq.copy()
            if len(iq) >= count:
                return iq[-count:]
            return iq
        return None

    def drain_iq_blocks(self, max_blocks=8):
        if self.live:
            with self._lock:
                blocks = []
                while self._audio_iq_queue and len(blocks) < int(max_blocks):
                    blocks.append(self._audio_iq_queue.popleft().copy())
                while len(self._audio_iq_queue) > 8:
                    self._audio_iq_queue.popleft()
                return blocks
        return []

    def is_receiving(self):
        if not self.live:
            return False
        with self._lock:
            if self._last_rx_time <= 0:
                return False
            age = time.time() - self._last_rx_time
            self.rx_last_age = age
            return age < 2.0

    def rx_diagnostics(self):
        with self._lock:
            age = None if self._last_rx_time <= 0 else time.time() - self._last_rx_time
        return {
            "rx_started": self.rx_started,
            "rx_start_rc": self.rx_start_rc,
            "callbacks": self.rx_callback_count,
            "samples": self.rx_sample_count,
            "last_valid_length": self.rx_last_valid_length,
            "last_age": age,
            "last_error": self.rx_last_error,
            "lna": self.lna_gain,
            "vga": self.vga_gain,
            "amp": self.amp_enabled,
            "gain_rc": dict(self.last_gain_rc),
            "filter_rc": self.last_filter_rc,
        }
