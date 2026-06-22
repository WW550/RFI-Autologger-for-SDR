import ctypes
import os
import sys
import platform
import threading
import time
from collections import deque
from ctypes import (
    c_double, c_float, c_int, c_uint, c_uint8, c_uint16, c_void_p,
    c_short, POINTER, byref
)

import numpy as np


class SDRplayStatus:
    def __init__(self, connected=False, message="NO RADIO", live=False, simulated=False):
        self.connected = connected
        self.message = message
        self.live = live
        self.simulated = simulated


# SDRplay API constants needed by this first live-IQ backend.
SDRPLAY_SUCCESS = 0
SDRPLAY_TUNER_A = 1
SDRPLAY_TUNER_B = 2
SDRPLAY_RSPDUO_ID = 3
SDRPLAY_RSPDUO_SINGLE_TUNER = 1
SDRPLAY_BW_1_536 = 1536
SDRPLAY_BW_0_600 = 600
SDRPLAY_BW_0_300 = 300
SDRPLAY_IF_ZERO = 0
SDRPLAY_LO_AUTO = 1
SDRPLAY_AGC_DISABLE = 0
SDRPLAY_AGC_50HZ = 2
SDRPLAY_UPDATE_DEV_FS = 0x00000001
SDRPLAY_UPDATE_DEV_PPM = 0x00000002
SDRPLAY_UPDATE_TUNER_GR = 0x00008000
SDRPLAY_UPDATE_TUNER_FRF = 0x00020000
SDRPLAY_UPDATE_TUNER_BWTYPE = 0x00040000
SDRPLAY_UPDATE_TUNER_IFTYPE = 0x00080000
SDRPLAY_UPDATE_CTRL_AGC = 0x01000000
SDRPLAY_UPDATE_CTRL_OVERLOAD_ACK = 0x04000000
SDRPLAY_EVENT_POWER_OVERLOAD = 1


class SDRplayDeviceT(ctypes.Structure):
    _fields_ = [
        ("SerNo", ctypes.c_char * 64),
        ("hwVer", c_uint8),
        ("heartBeatDisabled", c_uint8),
        ("tuner", c_int),
        ("rspDuoMode", c_int),
        ("rspDuoSampleFreq", c_double),
        ("dev", c_void_p),
    ]


class SDRplayFsFreqT(ctypes.Structure):
    _fields_ = [("fsHz", c_double), ("syncUpdate", c_uint8), ("reCal", c_uint8)]


class SDRplaySyncUpdateT(ctypes.Structure):
    _fields_ = [("sampleNum", c_uint), ("period", c_uint)]


class SDRplayResetFlagsT(ctypes.Structure):
    _fields_ = [("resetGainUpdate", c_uint8), ("resetRfUpdate", c_uint8), ("resetFsUpdate", c_uint8)]


class SDRplayRsp1aParamsT(ctypes.Structure):
    _fields_ = [("rfNotchEnable", c_uint8), ("rfDabNotchEnable", c_uint8)]


class SDRplayRsp2ParamsT(ctypes.Structure):
    _fields_ = [("extRefOutputEn", c_uint8)]


class SDRplayRspDuoParamsT(ctypes.Structure):
    _fields_ = [("extRefOutputEn", c_int)]


class SDRplayRspDxParamsT(ctypes.Structure):
    _fields_ = [
        ("hdrEnable", c_uint8),
        ("biasTEnable", c_uint8),
        ("antennaSel", c_int),
        ("rfNotchEnable", c_uint8),
        ("rfDabNotchEnable", c_uint8),
    ]


class SDRplayDevParamsT(ctypes.Structure):
    _fields_ = [
        ("ppm", c_double),
        ("fsFreq", SDRplayFsFreqT),
        ("syncUpdate", SDRplaySyncUpdateT),
        ("resetFlags", SDRplayResetFlagsT),
        ("mode", c_int),
        ("samplesPerPkt", c_uint),
        ("rsp1aParams", SDRplayRsp1aParamsT),
        ("rsp2Params", SDRplayRsp2ParamsT),
        ("rspDuoParams", SDRplayRspDuoParamsT),
        ("rspDxParams", SDRplayRspDxParamsT),
    ]


class SDRplayGainValuesT(ctypes.Structure):
    _fields_ = [("curr", c_float), ("max", c_float), ("min", c_float)]


class SDRplayGainT(ctypes.Structure):
    _fields_ = [
        ("gRdB", c_int),
        ("LNAstate", c_uint8),
        ("syncUpdate", c_uint8),
        ("minGr", c_int),
        ("gainVals", SDRplayGainValuesT),
    ]


class SDRplayRfFreqT(ctypes.Structure):
    _fields_ = [("rfHz", c_double), ("syncUpdate", c_uint8)]


class SDRplayDcOffsetTunerT(ctypes.Structure):
    _fields_ = [("dcCal", c_uint8), ("speedUp", c_uint8), ("trackTime", c_int), ("refreshRateTime", c_int)]


class SDRplayTunerParamsT(ctypes.Structure):
    _fields_ = [
        ("bwType", c_int),
        ("ifType", c_int),
        ("loMode", c_int),
        ("gain", SDRplayGainT),
        ("rfFreq", SDRplayRfFreqT),
        ("dcOffsetTuner", SDRplayDcOffsetTunerT),
    ]


class SDRplayDcOffsetT(ctypes.Structure):
    _fields_ = [("DCenable", c_uint8), ("IQenable", c_uint8)]


class SDRplayDecimationT(ctypes.Structure):
    _fields_ = [("enable", c_uint8), ("decimationFactor", c_uint8), ("wideBandSignal", c_uint8)]


class SDRplayAgcT(ctypes.Structure):
    # SDRplay API 3.x AGC structure.  Earlier prototypes used an older/wrong
    # layout.  A wrong ctypes layout can corrupt the following control fields
    # and make valid IQ look like static even when callbacks are active.
    _fields_ = [
        ("enable", c_int),
        ("setPoint_dBfs", c_int),
        ("attack_ms", c_uint16),
        ("decay_ms", c_uint16),
        ("decay_delay_ms", c_uint16),
        ("decay_threshold_dB", c_uint16),
        ("syncUpdate", c_int),
    ]


class SDRplayControlParamsT(ctypes.Structure):
    _fields_ = [
        ("dcOffset", SDRplayDcOffsetT),
        ("decimation", SDRplayDecimationT),
        ("agc", SDRplayAgcT),
        ("adsbMode", c_int),
    ]


class SDRplayRsp1aTunerParamsT(ctypes.Structure):
    _fields_ = [("biasTEnable", c_uint8)]


class SDRplayRsp2TunerParamsT(ctypes.Structure):
    _fields_ = [
        ("biasTEnable", c_uint8),
        ("amPortSel", c_int),
        ("antennaSel", c_int),
        ("rfNotchEnable", c_uint8),
    ]


class SDRplayRspDuoTunerParamsT(ctypes.Structure):
    _fields_ = [
        ("biasTEnable", c_uint8),
        ("tuner1AmPortSel", c_int),
        ("tuner1AmNotchEnable", c_uint8),
        ("rfNotchEnable", c_uint8),
        ("rfDabNotchEnable", c_uint8),
    ]


class SDRplayRspDxTunerParamsT(ctypes.Structure):
    _fields_ = [("hdrBw", c_int)]


class SDRplayRxChannelParamsT(ctypes.Structure):
    _fields_ = [
        ("tunerParams", SDRplayTunerParamsT),
        ("ctrlParams", SDRplayControlParamsT),
        ("rsp1aTunerParams", SDRplayRsp1aTunerParamsT),
        ("rsp2TunerParams", SDRplayRsp2TunerParamsT),
        ("rspDuoTunerParams", SDRplayRspDuoTunerParamsT),
        ("rspDxTunerParams", SDRplayRspDxTunerParamsT),
    ]


class SDRplayDeviceParamsT(ctypes.Structure):
    _fields_ = [
        ("devParams", POINTER(SDRplayDevParamsT)),
        ("rxChannelA", POINTER(SDRplayRxChannelParamsT)),
        ("rxChannelB", POINTER(SDRplayRxChannelParamsT)),
    ]


class SDRplayStreamCbParamsT(ctypes.Structure):
    _fields_ = [("firstSampleNum", c_uint), ("grChanged", c_int), ("rfChanged", c_int), ("fsChanged", c_int), ("numSamples", c_uint)]


class SDRplayGainCbParamT(ctypes.Structure):
    _fields_ = [("gRdB", c_uint), ("lnaGRdB", c_uint), ("currGain", c_double)]


class SDRplayPowerOverloadCbParamT(ctypes.Structure):
    _fields_ = [("powerOverloadChangeType", c_int)]


class SDRplayRspDuoModeCbParamT(ctypes.Structure):
    _fields_ = [("modeChangeType", c_int)]


class SDRplayEventParamsT(ctypes.Union):
    _fields_ = [
        ("gainParams", SDRplayGainCbParamT),
        ("powerOverloadParams", SDRplayPowerOverloadCbParamT),
        ("rspDuoModeParams", SDRplayRspDuoModeCbParamT),
    ]


_STREAM_CALLBACK = ctypes.CFUNCTYPE(None, POINTER(c_short), POINTER(c_short), POINTER(SDRplayStreamCbParamsT), c_uint, c_uint, c_void_p)
_EVENT_CALLBACK = ctypes.CFUNCTYPE(None, c_int, c_int, POINTER(SDRplayEventParamsT), c_void_p)


class SDRplayCallbackFnsT(ctypes.Structure):
    _fields_ = [("StreamACbFn", _STREAM_CALLBACK), ("StreamBCbFn", _STREAM_CALLBACK), ("EventCbFn", _EVENT_CALLBACK)]


class SDRplayDevice:
    """SDRplay API v3 live-IQ backend.

    v0.4.2 removes the local SDRplay API DLL preference and uses the official installed API first. Previous builds proved I/Q was present but WFM/NFM remained static; a stale local DLL can mismatch the SDRplay service, so this build tests the official API path. v0.4.1 added an IQ boost/normalization test on top of the I/Q validation diagnostics. This is intended to determine whether SDRplay sample amplitude is too low for the common demodulator. It accumulates small SDRplay callback packets into audio-sized IQ blocks, feeds audio from a background worker, and applies gain changes asynchronously so SDRplay hardware updates do not block the UI/audio path. It keeps the same small device
    interface used by HackRF and RTL-SDR. The API is configured for a simple
    single-tuner, zero-IF, 2 MS/s stream first, which should be enough to prove
    WFM/NFM reception before we tune model-specific controls.
    """

    def __init__(self, simulate_when_missing=False):
        self.connected = False
        self.live = False
        self.simulated = False
        self.message = "NO RADIO"
        self.name = "SDRplay"
        self.frequency_hz = 132_000_000
        self.sample_rate = 2_000_000
        self.lna_gain = 3  # SDRplay LNA state, not dB
        self.vga_gain = 40  # SDRplay gain reduction dB; higher number = lower gain
        self.amp_enabled = False
        self._lib = None
        self._dll_dir_handles = []
        self.last_diagnostic = ""
        self.api_version = None
        self.sdrplay_dll_path = ""
        self.device_count = 0
        self.device_name = "SDRplay"
        self._chosen_device = None
        self._device_params = POINTER(SDRplayDeviceParamsT)()
        self._rx_channel = None
        self._cb_stream_a = None
        self._cb_stream_b = None
        self._cb_event = None
        self._cb_fns = None
        self._api_open = False
        self._device_selected = False
        self._streaming = False
        self._lock = threading.Lock()
        self._latest_iq = None
        self._audio_iq_queue = deque(maxlen=96)
        self._audio_accum = np.zeros(0, dtype=np.complex64)
        self._audio_block_size = 49152
        self._last_rx_time = 0.0
        self.rx_started = False
        self.rx_start_rc = None
        self.rx_callback_count = 0
        self.rx_sample_count = 0
        self.rx_last_valid_length = 0
        self.rx_last_error = ""
        self.rx_last_age = None
        self.last_gain_rc = {"lna": None, "vga": None, "amp": None}
        self.last_filter_rc = None
        self.audio_blocks_drained = 0
        self.audio_samples_drained = 0
        self.raw_min = 0
        self.raw_max = 0
        self.raw_rms = 0.0
        self.raw_i_mean = 0.0
        self.raw_q_mean = 0.0
        self.raw_i_min = 0
        self.raw_i_max = 0
        self.raw_q_min = 0
        self.raw_q_max = 0
        self.raw_i_rms = 0.0
        self.raw_q_rms = 0.0
        self.xi_ptr_valid = False
        self.xq_ptr_valid = False
        self.last_num_samples = 0
        # v0.4.1: SDRplay-only DSP normalization test.  The user proved that
        # I/Q pointers and both channels are valid, but WFM/NFM still sound like
        # static.  Boost the normalized complex samples before the shared
        # demodulator to test whether SDRplay amplitude/scaling is the issue.
        # HackRF and RTL-SDR do not use this backend and are not affected.
        self.iq_boost = 1.0
        # v0.4.5: Field testing showed SDRplay NFM performs best with SWAP.
        # Stop auto-cycling MAP modes and use a stable SDRplay-only default.
        self.iq_mapping_cycle = ("SWAP",)
        self.iq_mapping_seconds = 999999.0
        self._iq_mapping_start = time.time()
        self.current_iq_mapping = "SWAP"
        # v0.3.6: SDRplay hardware gain updates can block briefly.  Sliders
        # call set_gains very frequently while dragged, so debounce and apply
        # these SDRplay-only changes from a background worker.  HackRF/RTL-SDR
        # are not affected.
        self._gain_pending = threading.Event()
        self._gain_worker_stop = threading.Event()
        self._gain_control_lock = threading.Lock()
        self._gain_desired = (self.lna_gain, self.vga_gain, self.amp_enabled)
        self._gain_request_time = 0.0
        self._gain_worker_thread = threading.Thread(target=self._gain_worker, daemon=True)
        self._gain_worker_thread.start()

    def connect(self):
        self.disconnect()
        try:
            self._lib = self._load_sdrplay_api()
            self._bind_api(self._lib)
            self._check(self._lib.sdrplay_api_Open(), "Open")
            self._api_open = True
            ver = c_float(0.0)
            rc = self._lib.sdrplay_api_ApiVersion(byref(ver))
            if rc == 0:
                self.api_version = float(ver.value)

            self._check(self._lib.sdrplay_api_LockDeviceApi(), "LockDeviceApi")
            devices = (SDRplayDeviceT * 16)()
            num = c_uint(0)
            try:
                self._check(self._lib.sdrplay_api_GetDevices(devices, byref(num), c_uint(16)), "GetDevices")
                self.device_count = int(num.value)
                if self.device_count <= 0:
                    raise RuntimeError(f"no SDRplay devices found - API v{self.api_version or 'unknown'}")
                chosen = devices[0]
                # For RSPduo, select a conservative single-tuner A mode.
                if int(chosen.hwVer) == SDRPLAY_RSPDUO_ID:
                    chosen.tuner = SDRPLAY_TUNER_A
                    chosen.rspDuoMode = SDRPLAY_RSPDUO_SINGLE_TUNER
                    chosen.rspDuoSampleFreq = float(self.sample_rate)
                else:
                    chosen.tuner = SDRPLAY_TUNER_A
                self._chosen_device = chosen
                self._check(self._lib.sdrplay_api_SelectDevice(byref(self._chosen_device)), "SelectDevice")
                self._device_selected = True
            finally:
                try:
                    self._lib.sdrplay_api_UnlockDeviceApi()
                except Exception:
                    pass

            ser = bytes(self._chosen_device.SerNo).split(b"\x00", 1)[0].decode(errors="ignore")
            self.device_name = f"SDRplay hw={int(self._chosen_device.hwVer)} {ser}".strip()
            dev_handle = c_void_p(self._chosen_device.dev)
            self._check(self._lib.sdrplay_api_GetDeviceParams(dev_handle, byref(self._device_params)), "GetDeviceParams")
            if not self._device_params:
                raise RuntimeError("GetDeviceParams returned NULL")
            self._configure_params_before_init()

            self._cb_stream_a = _STREAM_CALLBACK(self._stream_callback)
            self._cb_stream_b = _STREAM_CALLBACK(self._stream_callback_b)
            self._cb_event = _EVENT_CALLBACK(self._event_callback)
            self._cb_fns = SDRplayCallbackFnsT(self._cb_stream_a, self._cb_stream_b, self._cb_event)
            rc = self._lib.sdrplay_api_Init(dev_handle, byref(self._cb_fns), None)
            self.rx_start_rc = int(rc)
            self._check(rc, "Init")
            self._streaming = True
            self.rx_started = True
            self.connected = True
            self.live = True
            self.message = "SDRPLAY CONNECTED"
            return SDRplayStatus(True, self.message, live=True, simulated=False)
        except Exception as e:
            self._cleanup_api()
            self.connected = False
            self.live = False
            self.message = f"NO SDRPLAY - {e}"
            return SDRplayStatus(False, self.message, live=False, simulated=False)

    def disconnect(self):
        self._cleanup_api()
        self.connected = False
        self.live = False
        self.message = "NO RADIO"

    def _cleanup_api(self):
        try:
            if self._lib is not None:
                if self._streaming and self._chosen_device is not None:
                    try:
                        self._lib.sdrplay_api_Uninit(c_void_p(self._chosen_device.dev))
                    except Exception:
                        pass
                if self._device_selected and self._chosen_device is not None:
                    try:
                        self._lib.sdrplay_api_LockDeviceApi()
                    except Exception:
                        pass
                    try:
                        self._lib.sdrplay_api_ReleaseDevice(byref(self._chosen_device))
                    except Exception:
                        pass
                    try:
                        self._lib.sdrplay_api_UnlockDeviceApi()
                    except Exception:
                        pass
                if self._api_open:
                    try:
                        self._lib.sdrplay_api_Close()
                    except Exception:
                        pass
                try:
                    time.sleep(0.25)
                except Exception:
                    pass
        finally:
            self._lib = None
            self._chosen_device = None
            self._device_params = POINTER(SDRplayDeviceParamsT)()
            self._rx_channel = None
            self._cb_stream_a = None
            self._cb_stream_b = None
            self._cb_event = None
            self._cb_fns = None
            self._api_open = False
            self._device_selected = False
            self._streaming = False
            with self._lock:
                self._latest_iq = None
                self._audio_iq_queue.clear()
                self._audio_accum = np.zeros(0, dtype=np.complex64)
            self.rx_started = False
            self.rx_start_rc = None
            self.rx_callback_count = 0
            self.rx_sample_count = 0
            self.rx_last_valid_length = 0
            self.rx_last_error = ""
            self.rx_last_age = None
            self.audio_blocks_drained = 0
            self.audio_samples_drained = 0

    def _check(self, rc, where):
        if int(rc) != SDRPLAY_SUCCESS:
            err = self._error_string(int(rc))
            raise RuntimeError(f"SDRplay {where} failed rc={int(rc)} {err}")

    def _error_string(self, rc):
        try:
            if self._lib is not None:
                s = self._lib.sdrplay_api_GetErrorString(c_int(int(rc)))
                if s:
                    return ctypes.cast(s, ctypes.c_char_p).value.decode(errors="ignore")
        except Exception:
            pass
        return ""

    def _configure_params_before_init(self):
        dp = self._device_params.contents
        if dp.devParams:
            dev = dp.devParams.contents
            dev.fsFreq.fsHz = float(self.sample_rate)
            dev.ppm = 0.0
        ch = dp.rxChannelA
        if int(self._chosen_device.tuner) == SDRPLAY_TUNER_B and dp.rxChannelB:
            ch = dp.rxChannelB
        if not ch:
            raise RuntimeError("No SDRplay receive channel params")
        self._rx_channel = ch
        rx = ch.contents
        # Conservative WFM/NFM-friendly zero-IF startup. If a given model rejects
        # this, the diagnostic rc will show Init/OutOfRange and we can switch to LIF.
        rx.tunerParams.rfFreq.rfHz = float(self.frequency_hz)
        rx.tunerParams.bwType = SDRPLAY_BW_1_536
        rx.tunerParams.ifType = SDRPLAY_IF_ZERO
        rx.tunerParams.loMode = SDRPLAY_LO_AUTO
        rx.tunerParams.gain.gRdB = int(max(20, min(59, self.vga_gain)))
        rx.tunerParams.gain.LNAstate = int(max(0, min(9, self.lna_gain)))
        rx.tunerParams.gain.minGr = 20
        # Keep SDRplay AGC enabled for the first live-IQ pass. It is more likely
        # to produce audible WFM/NFM across different RSP models. Manual fixed
        # gain can be refined later for calibrated logging.
        rx.ctrlParams.agc.enable = SDRPLAY_AGC_50HZ
        rx.ctrlParams.agc.setPoint_dBfs = -45
        rx.ctrlParams.dcOffset.DCenable = 1
        rx.ctrlParams.dcOffset.IQenable = 1

    def _stream_callback(self, xi, xq, params, numSamples, reset, cbContext):
        try:
            n = int(numSamples)
            self.rx_callback_count += 1
            self.rx_last_valid_length = n
            if reset:
                with self._lock:
                    self._audio_iq_queue.clear()
            if n <= 0 or not xi or not xq:
                return
            i_raw = np.ctypeslib.as_array(xi, shape=(n,)).astype(np.int16, copy=True)
            q_raw = np.ctypeslib.as_array(xq, shape=(n,)).astype(np.int16, copy=True)
            i = i_raw.astype(np.float32, copy=False)
            q = q_raw.astype(np.float32, copy=False)
            # SDRplay returns signed 16-bit sample values.  Keep raw stats so
            # v0.4.0 can distinguish valid I/Q from missing-channel or format errors.
            raw_i_min = int(i_raw.min(initial=0))
            raw_i_max = int(i_raw.max(initial=0))
            raw_q_min = int(q_raw.min(initial=0))
            raw_q_max = int(q_raw.max(initial=0))
            raw_min = int(min(raw_i_min, raw_q_min))
            raw_max = int(max(raw_i_max, raw_q_max))
            raw_i_rms = float(np.sqrt(np.mean((i / 32768.0) * (i / 32768.0)))) if n > 0 else 0.0
            raw_q_rms = float(np.sqrt(np.mean((q / 32768.0) * (q / 32768.0)))) if n > 0 else 0.0
            raw_rms = float(np.sqrt(np.mean((i * i + q * q) / (32768.0 * 32768.0)))) if n > 0 else 0.0
            raw_i_mean = float(np.mean(i / 32768.0)) if n > 0 else 0.0
            raw_q_mean = float(np.mean(q / 32768.0)) if n > 0 else 0.0
            # v0.4.5: Use fixed SDRplay MAP: SWAP.  Auto-cycling was useful
            # for diagnosis, but SWAP performed best in field testing and the
            # cycling made listening tests confusing.
            mapping = "SWAP"
            self.current_iq_mapping = mapping
            if mapping == "QINV":
                ci, cq = i, -q
            elif mapping == "SWAP":
                ci, cq = q, i
            elif mapping == "SWAP_QINV":
                ci, cq = q, -i
            else:
                ci, cq = i, q
            iq = (((ci / 32768.0) + 1j * (cq / 32768.0)) * float(self.iq_boost)).astype(np.complex64)
            # Keep the test boost from creating extreme values if a very strong
            # signal or overload is present. FM demod is largely amplitude
            # independent, but clipping protection keeps downstream stats/audio
            # sane during this diagnostic build.
            mag = np.abs(iq)
            too_hot = mag > 0.98
            if np.any(too_hot):
                iq = (iq / np.maximum(1.0, mag / 0.98)).astype(np.complex64)
            now = time.time()
            with self._lock:
                # SDRplay often delivers small callback packets (for example
                # around 504 samples).  The shared audio engine intentionally
                # ignores very small IQ chunks to avoid noisy under-filled
                # demod blocks.  Accumulate SDRplay packets into larger blocks
                # before handing them to the common demod/audio path.
                if self._latest_iq is None or len(self._latest_iq) < self._audio_block_size:
                    self._latest_iq = iq
                else:
                    self._latest_iq = np.concatenate((self._latest_iq[-(self._audio_block_size - len(iq)):], iq)).astype(np.complex64, copy=False)
                self._audio_accum = np.concatenate((self._audio_accum, iq)).astype(np.complex64, copy=False)
                while len(self._audio_accum) >= self._audio_block_size:
                    block = self._audio_accum[:self._audio_block_size].copy()
                    self._audio_iq_queue.append(block)
                    self._audio_accum = self._audio_accum[self._audio_block_size:]
                # Keep the accumulator bounded if audio is off or the GUI is not draining.
                max_accum = self._audio_block_size * 4
                if len(self._audio_accum) > max_accum:
                    self._audio_accum = self._audio_accum[-max_accum:]
                self._last_rx_time = now
                self.rx_last_age = 0.0
                self.rx_sample_count += int(len(iq))
                self.raw_min = raw_min
                self.raw_max = raw_max
                self.raw_rms = raw_rms
                self.raw_i_min = raw_i_min
                self.raw_i_max = raw_i_max
                self.raw_q_min = raw_q_min
                self.raw_q_max = raw_q_max
                self.raw_i_rms = raw_i_rms
                self.raw_q_rms = raw_q_rms
                self.raw_i_mean = raw_i_mean
                self.raw_q_mean = raw_q_mean
                self.xi_ptr_valid = bool(xi)
                self.xq_ptr_valid = bool(xq)
                self.last_num_samples = n
                self.rx_last_error = ""
        except Exception as e:
            self.rx_last_error = f"stream callback error: {type(e).__name__}: {e}"

    def _stream_callback_b(self, xi, xq, params, numSamples, reset, cbContext):
        # We are using Tuner A / single tuner for now.
        return

    def _event_callback(self, eventId, tuner, params, cbContext):
        try:
            if int(eventId) == SDRPLAY_EVENT_POWER_OVERLOAD and self._lib is not None and self._chosen_device is not None:
                # Acknowledge overload events so the API can continue normally.
                try:
                    self._lib.sdrplay_api_Update(c_void_p(self._chosen_device.dev), c_int(int(tuner)), c_int(SDRPLAY_UPDATE_CTRL_OVERLOAD_ACK), c_int(0))
                except Exception:
                    pass
        except Exception as e:
            self.rx_last_error = f"event callback error: {type(e).__name__}: {e}"

    def _load_sdrplay_api(self):
        """Load SDRplay API DLL.

        v0.4.2: Prefer the official installed SDRplay API over any local copied
        DLL. SDRplay's API DLL talks to a matching Windows service; copying an
        older DLL beside the EXE can partially work but behave incorrectly.
        HackRF/RTL-SDR still use bundled DLLs, but SDRplay should come from the
        official API installer.
        """
        names = ["sdrplay_api.dll", "mir_sdr_api.dll"]
        diagnostic = []

        def add(line):
            diagnostic.append(str(line))

        add("RFI Autologger SDRplay API diagnostic")
        add("v0.4.2 loader: official installed SDRplay API is preferred; local copied DLLs are last resort")
        add(f"Python: {sys.version.split()[0]} {platform.architecture()[0]}")
        add(f"Frozen EXE: {getattr(sys, 'frozen', False)}")
        add(f"sys.executable: {sys.executable}")
        add(f"cwd: {os.getcwd()}")
        add(f"__file__: {__file__}")

        # Official API locations first. This avoids using a stale DLL copied
        # next to the program that may not match the installed SDRplay service.
        official_dirs = [
            r"C:\Program Files\SDRplay\API\x64",
            r"C:\Program Files\SDRplay\API",
            r"C:\Program Files (x86)\SDRplay\API",
        ]

        local_dirs = []
        try:
            local_dirs.append(os.path.dirname(os.path.abspath(__file__)))
        except Exception:
            pass
        if getattr(sys, "frozen", False):
            local_dirs.append(os.path.dirname(sys.executable))
        if hasattr(sys, "_MEIPASS"):
            local_dirs.append(sys._MEIPASS)
        local_dirs.append(os.getcwd())

        expanded = []
        for d in official_dirs:
            expanded.extend([d, os.path.join(d, "x64"), os.path.join(d, "dlls")])
        # Local paths are intentionally last and optional.
        for d in local_dirs:
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

        candidates = []
        for d in unique_dirs:
            for n in names:
                candidates.append(os.path.join(d, n))
        # Last resort: normal Windows DLL search path.
        candidates.extend(names)

        errors = []
        for path in candidates:
            try:
                is_path = os.path.sep in path or (os.path.altsep and os.path.altsep in path)
                if is_path and not os.path.exists(path):
                    errors.append(f"missing: {path}")
                    continue
                add(f"trying SDRplay API DLL: {path}")
                lib = ctypes.CDLL(path)
                self.sdrplay_dll_path = str(path)
                add(f"SUCCESS loaded SDRplay API DLL: {path}")
                self.last_diagnostic = "\n".join(diagnostic)
                self._write_diagnostic_file(diagnostic)
                return lib
            except Exception as e:
                msg = f"{path}: {type(e).__name__}: {e}"
                errors.append(msg)
                add("FAILED " + msg)

        add("Final failure: SDRplay API DLL not loaded")
        for e in errors[-12:]:
            add("  " + e)
        self.last_diagnostic = "\n".join(diagnostic)
        self._write_diagnostic_file(diagnostic)
        raise RuntimeError("SDRplay API DLL not loaded. " + " | ".join(errors[-8:]))

    def _write_diagnostic_file(self, lines):
        try:
            folder = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
            with open(os.path.join(folder, "sdrplay_diagnostic.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception:
            pass

    def _bind_api(self, lib):
        lib.sdrplay_api_Open.argtypes = []
        lib.sdrplay_api_Open.restype = c_int
        lib.sdrplay_api_Close.argtypes = []
        lib.sdrplay_api_Close.restype = c_int
        lib.sdrplay_api_ApiVersion.argtypes = [POINTER(c_float)]
        lib.sdrplay_api_ApiVersion.restype = c_int
        lib.sdrplay_api_LockDeviceApi.argtypes = []
        lib.sdrplay_api_LockDeviceApi.restype = c_int
        lib.sdrplay_api_UnlockDeviceApi.argtypes = []
        lib.sdrplay_api_UnlockDeviceApi.restype = c_int
        lib.sdrplay_api_GetDevices.argtypes = [POINTER(SDRplayDeviceT), POINTER(c_uint), c_uint]
        lib.sdrplay_api_GetDevices.restype = c_int
        lib.sdrplay_api_SelectDevice.argtypes = [POINTER(SDRplayDeviceT)]
        lib.sdrplay_api_SelectDevice.restype = c_int
        lib.sdrplay_api_ReleaseDevice.argtypes = [POINTER(SDRplayDeviceT)]
        lib.sdrplay_api_ReleaseDevice.restype = c_int
        lib.sdrplay_api_GetDeviceParams.argtypes = [c_void_p, POINTER(POINTER(SDRplayDeviceParamsT))]
        lib.sdrplay_api_GetDeviceParams.restype = c_int
        lib.sdrplay_api_Init.argtypes = [c_void_p, POINTER(SDRplayCallbackFnsT), c_void_p]
        lib.sdrplay_api_Init.restype = c_int
        lib.sdrplay_api_Uninit.argtypes = [c_void_p]
        lib.sdrplay_api_Uninit.restype = c_int
        lib.sdrplay_api_Update.argtypes = [c_void_p, c_int, c_int, c_int]
        lib.sdrplay_api_Update.restype = c_int
        lib.sdrplay_api_GetErrorString.argtypes = [c_int]
        lib.sdrplay_api_GetErrorString.restype = c_void_p

    def set_frequency(self, frequency_hz: int):
        self.frequency_hz = int(frequency_hz)
        if self.live and self._lib is not None and self._chosen_device is not None and self._rx_channel:
            try:
                self._rx_channel.contents.tunerParams.rfFreq.rfHz = float(self.frequency_hz)
                rc = self._lib.sdrplay_api_Update(c_void_p(self._chosen_device.dev), c_int(int(self._chosen_device.tuner)), c_int(SDRPLAY_UPDATE_TUNER_FRF), c_int(0))
                if rc != 0:
                    self.message = f"SDRplay frequency set failed rc={rc} {self._error_string(rc)}"
            except Exception as e:
                self.message = f"SDRplay frequency set failed: {e}"

    def set_sample_rate(self, sample_rate: int):
        self.sample_rate = int(sample_rate)
        if self.live and self._lib is not None and self._chosen_device is not None and self._device_params:
            try:
                dp = self._device_params.contents
                if dp.devParams:
                    dp.devParams.contents.fsFreq.fsHz = float(self.sample_rate)
                    rc = self._lib.sdrplay_api_Update(c_void_p(self._chosen_device.dev), c_int(int(self._chosen_device.tuner)), c_int(SDRPLAY_UPDATE_DEV_FS), c_int(0))
                    if rc != 0:
                        self.message = f"SDRplay sample-rate set failed rc={rc} {self._error_string(rc)}"
            except Exception as e:
                self.message = f"SDRplay sample-rate set failed: {e}"

    def set_gains(self, lna_gain: int, vga_gain: int, amp_enabled: bool):
        """Queue SDRplay gain changes without blocking the GUI/audio path.

        The SDRplay API may pause the stream briefly when gain reduction/LNA
        state changes.  Applying this synchronously from a Tk slider command
        made the program feel laggy and could interrupt monitor audio.  v0.3.6
        stores the latest desired values and lets a worker apply them after a
        short debounce.
        """
        self.lna_gain = max(0, min(9, int(lna_gain)))
        self.vga_gain = max(20, min(59, int(vga_gain)))
        # SDRplay does not have a HackRF-style RF amp; keep this false but do
        # not block if the UI checkbox is clicked while SDRplay is selected.
        self.amp_enabled = False
        with self._gain_control_lock:
            self._gain_desired = (self.lna_gain, self.vga_gain, self.amp_enabled)
            self._gain_request_time = time.monotonic()
            self.last_gain_rc = {"lna": "PENDING", "vga": "PENDING", "amp": 0}
        self._gain_pending.set()

    def _gain_worker(self):
        while not self._gain_worker_stop.is_set():
            try:
                self._gain_pending.wait(0.25)
                if self._gain_worker_stop.is_set():
                    break
                if not self._gain_pending.is_set():
                    continue
                # Debounce slider movement so we send only the final/most
                # recent setting instead of dozens of SDRplay API updates.
                while True:
                    with self._gain_control_lock:
                        age = time.monotonic() - float(self._gain_request_time or 0.0)
                    if age >= 0.22:
                        break
                    time.sleep(0.05)
                self._gain_pending.clear()
                with self._gain_control_lock:
                    lna, vga, amp = self._gain_desired
                self._apply_gains_now(lna, vga, amp)
            except Exception as e:
                try:
                    self.message = f"SDRplay gain worker error: {e}"
                except Exception:
                    pass
                time.sleep(0.20)

    def _apply_gains_now(self, lna_gain: int, vga_gain: int, amp_enabled: bool):
        if not (self.live and self._lib is not None and self._chosen_device is not None and self._rx_channel):
            return
        try:
            rx = self._rx_channel.contents
            rx.tunerParams.gain.LNAstate = int(max(0, min(9, lna_gain)))
            rx.tunerParams.gain.gRdB = int(max(20, min(59, vga_gain)))
            rc = self._lib.sdrplay_api_Update(
                c_void_p(self._chosen_device.dev),
                c_int(int(self._chosen_device.tuner)),
                c_int(SDRPLAY_UPDATE_TUNER_GR),
                c_int(0),
            )
            self.last_gain_rc = {"lna": int(rc), "vga": int(rc), "amp": 0}
            if rc != 0:
                self.message = f"SDRplay gain set failed rc={rc} {self._error_string(rc)}"
        except Exception as e:
            self.last_gain_rc = {"lna": "ERR", "vga": "ERR", "amp": 0}
            self.message = f"SDRplay gain set failed: {e}"

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
                    b = self._audio_iq_queue.popleft().copy()
                    blocks.append(b)
                    self.audio_blocks_drained += 1
                    self.audio_samples_drained += int(len(b))
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
            "sdrplay_dll_path": self.sdrplay_dll_path,
            "filter_rc": self.last_filter_rc,
            "api_version": self.api_version,
            "device_count": self.device_count,
            "device_name": self.device_name,
            "audio_blocks": self.audio_blocks_drained,
            "audio_samples": self.audio_samples_drained,
            "audio_queue": len(self._audio_iq_queue),
            "audio_accum": int(len(self._audio_accum)),
            "audio_block_size": int(self._audio_block_size),
            "raw_min": self.raw_min,
            "raw_max": self.raw_max,
            "raw_rms": float(self.raw_rms),
            "raw_i_mean": float(self.raw_i_mean),
            "raw_q_mean": float(self.raw_q_mean),
            "raw_i_min": self.raw_i_min,
            "raw_i_max": self.raw_i_max,
            "raw_q_min": self.raw_q_min,
            "raw_q_max": self.raw_q_max,
            "raw_i_rms": float(self.raw_i_rms),
            "raw_q_rms": float(self.raw_q_rms),
            "xi_ptr_valid": bool(self.xi_ptr_valid),
            "xq_ptr_valid": bool(self.xq_ptr_valid),
            "last_num_samples": int(self.last_num_samples),
            "iq_boost": float(getattr(self, "iq_boost", 1.0)),
            "iq_mapping": str(getattr(self, "current_iq_mapping", "NORM")),
        }
