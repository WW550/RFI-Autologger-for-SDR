import ctypes
import math
import os
import threading
import time
from collections import deque
from ctypes import c_double, c_int, c_uint8, c_uint32, c_uint64, c_void_p, POINTER

import numpy as np


class HackRFStatus:
    def __init__(self, connected=False, message="NO RADIO", live=False, simulated=False):
        self.connected = connected
        self.message = message
        self.live = live
        self.simulated = simulated


class _HackRFTransfer(ctypes.Structure):
    _fields_ = [
        ("device", c_void_p),
        ("buffer", POINTER(c_uint8)),
        ("buffer_length", c_int),
        ("valid_length", c_int),
        ("rx_ctx", c_void_p),
        ("tx_ctx", c_void_p),
    ]


_RX_CALLBACK = ctypes.CFUNCTYPE(c_int, POINTER(_HackRFTransfer))


class HackRFDevice:
    """HackRF One receive wrapper.

    v0.1.28 disables the normal runtime simulator. If no HackRF is
    detected, the application reports NO SDR and does not generate fake IQ
    or fake audio. This avoids misleading field behavior and reduces CPU load.
    """

    def __init__(self, simulate_when_missing=False):
        self.connected = False
        self.live = False
        self.simulated = False
        self.message = "NO RADIO"
        self.frequency_hz = 132_000_000
        self.sample_rate = 2_000_000
        self.lna_gain = 16
        self.vga_gain = 20
        self.amp_enabled = False
        self._phase = 0.0
        self.simulate_when_missing = simulate_when_missing

        self._lib = None
        self._dev = c_void_p()
        self._rx_cb = None
        self._lock = threading.Lock()
        self._latest_iq = None
        self._audio_iq_queue = deque(maxlen=48)
        self._last_rx_time = 0.0
        self._dll_dir_handles = []
        self.last_diagnostic = ""
        self.rx_started = False
        self.rx_start_rc = None
        self.rx_callback_count = 0
        self.rx_sample_count = 0
        self.rx_last_valid_length = 0
        self.rx_last_error = ""
        self.rx_last_age = None
        self.last_gain_rc = {"lna": None, "vga": None, "amp": None}
        self.last_filter_rc = None

    def connect(self):
        self.disconnect()
        try:
            self._lib = self._load_libhackrf()
            self._bind_api(self._lib)
            rc = self._lib.hackrf_init()
            if rc != 0:
                raise RuntimeError(f"hackrf_init failed rc={rc}")
            rc = self._lib.hackrf_open(ctypes.byref(self._dev))
            if rc != 0 or not self._dev:
                raise RuntimeError(f"hackrf_open failed rc={rc}")

            self._configure_live_device()
            self._rx_cb = _RX_CALLBACK(self._rx_callback)
            rc = self._lib.hackrf_start_rx(self._dev, self._rx_cb, None)
            self.rx_start_rc = int(rc)
            if rc != 0:
                raise RuntimeError(f"hackrf_start_rx failed rc={rc}")
            self.rx_started = True

            self.connected = True
            self.live = True
            self.simulated = False
            self.message = "HackRF One CONNECTED"
            return HackRFStatus(True, self.message, live=True, simulated=False)
        except Exception as e:
            self._cleanup_live()
            # No normal runtime simulator: fail cleanly and let the UI show NO SDR.
            self.connected = False
            self.live = False
            self.simulated = False
            self.message = f"NO RADIO - {e}"
            return HackRFStatus(False, self.message, live=False, simulated=False)

    def disconnect(self):
        self._cleanup_live()
        self.connected = False
        self.live = False
        self.simulated = False
        self.message = "NO RADIO"

    def _cleanup_live(self):
        try:
            if self._lib is not None and self._dev:
                try:
                    self._lib.hackrf_stop_rx(self._dev)
                except Exception:
                    pass
                try:
                    self._lib.hackrf_close(self._dev)
                except Exception:
                    pass
        finally:
            self._dev = c_void_p()
            self._rx_cb = None
            self._lib = None
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

    def _load_libhackrf(self):
        """Load HackRF DLL with stronger Windows diagnostics.

        v0.1.6 changes:
        - keeps os.add_dll_directory handles alive
        - explicitly preloads pthreadVC2.dll and libusb-1.0.dll from the same folder
        - tries absolute paths before PATH search
        - writes hackrf_diagnostic.txt beside the app/source
        """
        import platform
        import sys

        dll_names = ["hackrf.dll", "libhackrf.dll", "libhackrf-0.dll"]
        search_files = []
        diagnostic_lines = []

        def add_diag(line):
            diagnostic_lines.append(str(line))

        add_diag("RFI Auto Logger SDR HackRF DLL diagnostic")
        add_diag(f"Python: {sys.version.split()[0]} {platform.architecture()[0]}")
        add_diag(f"Frozen EXE: {getattr(sys, 'frozen', False)}")
        add_diag(f"sys.executable: {sys.executable}")
        add_diag(f"cwd: {os.getcwd()}")
        add_diag(f"__file__: {__file__}")

        def pe_machine(path):
            try:
                with open(path, "rb") as f:
                    mz = f.read(2)
                    if mz != b"MZ":
                        return "not PE"
                    f.seek(0x3C)
                    pe_offset = int.from_bytes(f.read(4), "little")
                    f.seek(pe_offset + 4)
                    machine = int.from_bytes(f.read(2), "little")
                    return {0x8664: "x64", 0x14C: "x86", 0xAA64: "ARM64"}.get(machine, hex(machine))
            except Exception as e:
                return f"unknown ({e})"

        env_path = os.environ.get("HACKRF_DLL")
        if env_path:
            search_files.append(env_path)
            add_diag(f"HACKRF_DLL env: {env_path}")

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

        # Also try ./dlls if the user places DLLs there.
        expanded = []
        for d in base_dirs:
            expanded.append(d)
            expanded.append(os.path.join(d, "dlls"))

        seen = set()
        unique_dirs = []
        for d in expanded:
            if d and d not in seen:
                unique_dirs.append(d)
                seen.add(d)

        add_diag("Search folders:")
        for d in unique_dirs:
            add_diag(f"  {d} exists={os.path.isdir(d)}")

        # Keep these handles alive for the process lifetime.
        for d in unique_dirs:
            if os.path.isdir(d):
                try:
                    h = os.add_dll_directory(d)
                    self._dll_dir_handles.append(h)
                    add_diag(f"add_dll_directory OK: {d}")
                except Exception as e:
                    add_diag(f"add_dll_directory skipped/failed: {d}: {e}")
                # Also prepend PATH for older loaders/dependencies.
                try:
                    os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
                except Exception:
                    pass

        for d in unique_dirs:
            for name in dll_names:
                search_files.append(os.path.join(d, name))

        search_files.extend(dll_names)

        errors = []

        for path in search_files:
            try:
                is_path = os.path.sep in path or (os.path.altsep and os.path.altsep in path)
                if is_path and not os.path.exists(path):
                    errors.append(f"missing: {path}")
                    continue

                if is_path:
                    add_diag(f"candidate architecture: {pe_machine(path)} for {path}")
                folder = os.path.dirname(path) if is_path else ""
                if folder:
                    # Preload known dependencies from the same folder. This fixes many Windows dependency failures.
                    for dep_name in ("pthreadVC2.dll", "libusb-1.0.dll"):
                        dep_path = os.path.join(folder, dep_name)
                        if os.path.exists(dep_path):
                            try:
                                ctypes.CDLL(dep_path, mode=getattr(ctypes, "RTLD_GLOBAL", 0))
                                add_diag(f"preloaded dependency: {dep_path}")
                            except Exception as e:
                                add_diag(f"dependency preload failed: {dep_path}: {e}")
                        else:
                            add_diag(f"dependency not beside candidate: {dep_path}")

                add_diag(f"trying HackRF DLL: {path}")
                lib = ctypes.CDLL(path)
                add_diag(f"SUCCESS loaded HackRF DLL: {path}")
                self.last_diagnostic = "\n".join(diagnostic_lines)
                self._write_diagnostic_file(diagnostic_lines)
                self.message = f"Loaded HackRF DLL: {path}"
                return lib
            except Exception as e:
                msg = f"{path}: {type(e).__name__}: {e}"
                errors.append(msg)
                add_diag("FAILED " + msg)

        add_diag("Final failure: HackRF DLL not loaded")
        add_diag("Recent errors:")
        for e in errors[-12:]:
            add_diag("  " + e)
        self.last_diagnostic = "\n".join(diagnostic_lines)
        self._write_diagnostic_file(diagnostic_lines)
        tail = " | ".join(errors[-8:])
        raise RuntimeError("HackRF DLL not loaded. " + tail)

    def _write_diagnostic_file(self, lines):
        try:
            import sys
            if getattr(sys, "frozen", False):
                folder = os.path.dirname(sys.executable)
            else:
                folder = os.path.dirname(os.path.abspath(__file__))
            path = os.path.join(folder, "hackrf_diagnostic.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception:
            pass

    def _bind_api(self, lib):
        lib.hackrf_init.argtypes = []
        lib.hackrf_init.restype = c_int
        lib.hackrf_open.argtypes = [POINTER(c_void_p)]
        lib.hackrf_open.restype = c_int
        lib.hackrf_close.argtypes = [c_void_p]
        lib.hackrf_close.restype = c_int
        lib.hackrf_start_rx.argtypes = [c_void_p, _RX_CALLBACK, c_void_p]
        lib.hackrf_start_rx.restype = c_int
        lib.hackrf_stop_rx.argtypes = [c_void_p]
        lib.hackrf_stop_rx.restype = c_int
        lib.hackrf_set_freq.argtypes = [c_void_p, c_uint64]
        lib.hackrf_set_freq.restype = c_int
        lib.hackrf_set_sample_rate.argtypes = [c_void_p, c_double]
        lib.hackrf_set_sample_rate.restype = c_int
        lib.hackrf_set_lna_gain.argtypes = [c_void_p, c_uint32]
        lib.hackrf_set_lna_gain.restype = c_int
        lib.hackrf_set_vga_gain.argtypes = [c_void_p, c_uint32]
        lib.hackrf_set_vga_gain.restype = c_int
        lib.hackrf_set_amp_enable.argtypes = [c_void_p, c_uint8]
        lib.hackrf_set_amp_enable.restype = c_int
        # Optional in current HackRF DLLs. Use when present to make the 2 MHz
        # view match the intended sample span and reduce out-of-band energy.
        try:
            lib.hackrf_compute_baseband_filter_bw_round_down_lt.argtypes = [c_uint32]
            lib.hackrf_compute_baseband_filter_bw_round_down_lt.restype = c_uint32
            lib.hackrf_set_baseband_filter_bandwidth.argtypes = [c_void_p, c_uint32]
            lib.hackrf_set_baseband_filter_bandwidth.restype = c_int
        except Exception:
            pass

    def _configure_live_device(self):
        self._check(self._lib.hackrf_set_sample_rate(self._dev, float(self.sample_rate)), "set_sample_rate")
        self._check(self._lib.hackrf_set_freq(self._dev, c_uint64(int(self.frequency_hz))), "set_freq")
        self._check(self._lib.hackrf_set_lna_gain(self._dev, c_uint32(int(self._valid_lna(self.lna_gain)))), "set_lna_gain")
        self._check(self._lib.hackrf_set_vga_gain(self._dev, c_uint32(int(self._valid_vga(self.vga_gain)))), "set_vga_gain")
        self._check(self._lib.hackrf_set_amp_enable(self._dev, c_uint8(1 if self.amp_enabled else 0)), "set_amp_enable")
        # 1.75 MHz-ish baseband filter for a 2.0 MHz sample rate if supported.
        try:
            bw = int(self._lib.hackrf_compute_baseband_filter_bw_round_down_lt(c_uint32(1_750_000)))
            self.last_filter_rc = int(self._lib.hackrf_set_baseband_filter_bandwidth(self._dev, c_uint32(bw)))
        except Exception:
            self.last_filter_rc = None

    def _check(self, rc, where):
        if rc != 0:
            raise RuntimeError(f"HackRF {where} failed rc={rc}")

    def _rx_callback(self, transfer_ptr):
        try:
            transfer = transfer_ptr.contents
            n = int(transfer.valid_length or transfer.buffer_length or 0)
            self.rx_callback_count += 1
            self.rx_last_valid_length = n
            if n <= 1 or not transfer.buffer:
                return 0

            # Copy bytes immediately. Do not keep a numpy view into the HackRF
            # buffer after the callback returns. The samples are signed int8 I/Q.
            raw_bytes = ctypes.string_at(transfer.buffer, n)
            raw_i8 = np.frombuffer(raw_bytes, dtype=np.int8).astype(np.float32) / 128.0
            usable = (len(raw_i8) // 2) * 2
            if usable <= 1:
                return 0
            i = raw_i8[:usable:2]
            q = raw_i8[1:usable:2]
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
            return 0
        return 0

    def set_frequency(self, frequency_hz: int):
        self.frequency_hz = int(frequency_hz)
        if self.live and self._lib is not None and self._dev:
            try:
                self._lib.hackrf_set_freq(self._dev, c_uint64(self.frequency_hz))
            except Exception:
                self.connected = False
                self.live = False
                self.message = "NO RADIO - frequency set failed"

    def set_sample_rate(self, sample_rate: int):
        self.sample_rate = int(sample_rate)
        if self.live and self._lib is not None and self._dev:
            try:
                self._lib.hackrf_set_sample_rate(self._dev, float(self.sample_rate))
            except Exception:
                self.message = "HackRF sample-rate set failed"

    def set_gains(self, lna_gain: int, vga_gain: int, amp_enabled: bool):
        self.lna_gain = self._valid_lna(lna_gain)
        self.vga_gain = self._valid_vga(vga_gain)
        self.amp_enabled = bool(amp_enabled)
        if self.live and self._lib is not None and self._dev:
            try:
                self.last_gain_rc["lna"] = int(self._lib.hackrf_set_lna_gain(self._dev, c_uint32(self.lna_gain)))
                self.last_gain_rc["vga"] = int(self._lib.hackrf_set_vga_gain(self._dev, c_uint32(self.vga_gain)))
                self.last_gain_rc["amp"] = int(self._lib.hackrf_set_amp_enable(self._dev, c_uint8(1 if self.amp_enabled else 0)))
            except Exception as e:
                self.message = f"HackRF gain set failed: {e}"

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
        """Return queued live IQ blocks since the last drain.

        This is mainly for audio. The UI meter can safely use read_iq(), but
        audio needs a continuous stream. v0.1.10 used only the latest display
        block and starved the sound card, causing tic-toc/choppy audio.
        """
        if self.live:
            with self._lock:
                blocks = []
                while self._audio_iq_queue and len(blocks) < int(max_blocks):
                    blocks.append(self._audio_iq_queue.popleft().copy())
                # If the queue was badly backed up, drop old blocks to keep latency low.
                while len(self._audio_iq_queue) > 8:
                    self._audio_iq_queue.popleft()
                return blocks
        return []

    def is_receiving(self):
        if not self.live:
            return False
        return (time.time() - self._last_rx_time) < 2.0

    def _valid_lna(self, val):
        # HackRF LNA gain valid values are 0 to 40 dB in 8 dB steps.
        return int(max(0, min(40, round(int(val) / 8) * 8)))

    def _valid_vga(self, val):
        # HackRF VGA valid values are 0 to 62 dB in 2 dB steps.
        return int(max(0, min(62, round(int(val) / 2) * 2)))

    def rx_diagnostics(self):
        age = None
        if self._last_rx_time:
            age = time.time() - self._last_rx_time
        return {
            "live": self.live,
            "connected": self.connected,
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

