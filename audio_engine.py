import queue
import time

import numpy as np

try:
    import sounddevice as sd
except Exception:  # sounddevice is optional at runtime.
    sd = None


class AudioEngine:
    """Live monitor audio for HackRF IQ.

    v0.1.13 focus: make NFM usable. The important change is that NFM is no
    longer demodulated from the entire 2 MHz IQ stream. We first isolate a
    narrow center channel, then FM-discriminate that channel, de-emphasize it,
    and resample to 48 kHz.
    """

    def __init__(self, audio_rate=48000):
        self.audio_rate = int(audio_rate)
        self.enabled = False
        self.volume = 0.4
        self.mode = "AM"
        self.sample_rate = 2_000_000
        self._stream = None
        self._q = queue.Queue(maxsize=256)
        self._pending = np.zeros(0, dtype=np.float32)
        self._last_iq = None
        self._deemph_y = 0.0
        self._am_carrier_level = 0.0
        self.underruns = 0
        self.queued_chunks = 0
        self.status = "Audio: OFF"
        self.demod_status = "demod: idle"
        self.audio_level = 0.0
        self.tuning_offset_hz = 0.0
        self.device_profile = "Generic"
        self._mix_phase = 0.0

    def start(self):
        if sd is None:
            self.status = "Audio: sounddevice missing"
            self.enabled = False
            return False
        if self._stream is not None:
            self.enabled = True
            return True
        try:
            self._stream = sd.OutputStream(
                samplerate=self.audio_rate,
                channels=1,
                dtype="float32",
                # v0.1.40: larger output buffer smooths light choppiness.
                # Tradeoff: slightly higher monitor-audio latency, acceptable
                # for an RFI logger.
                blocksize=8192,
                latency="high",
                callback=self._audio_callback,
            )
            self._stream.start()
            self.enabled = True
            self.status = "Audio: ON"
            return True
        except Exception as e:
            self.status = f"Audio error: {e}"
            self.enabled = False
            self._stream = None
            return False

    def stop(self):
        self.enabled = False
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        except Exception:
            pass
        self._stream = None
        self._clear()
        self.status = "Audio: OFF"

    def set_params(self, mode, volume, sample_rate):
        new_mode = str(mode).upper()
        if new_mode != self.mode:
            # Avoid carrying FM discriminator/de-emphasis state across modes.
            self._last_iq = None
            self._deemph_y = 0.0
            self._am_carrier_level = 0.0
        self.mode = new_mode
        self.volume = max(0.0, min(1.0, float(volume)))
        self.sample_rate = int(sample_rate)

    def set_tuning_offset_hz(self, offset_hz):
        try:
            new_offset = float(offset_hz)
        except Exception:
            new_offset = 0.0
        if abs(new_offset - float(getattr(self, "tuning_offset_hz", 0.0))) > 1e-6:
            self._mix_phase = 0.0
            self._last_iq = None
        self.tuning_offset_hz = new_offset

    def set_device_profile(self, profile):
        # Allows SDRplay-specific DSP cleanup without altering HackRF/RTL-SDR.
        try:
            self.device_profile = str(profile)
        except Exception:
            self.device_profile = "Generic"

    def reset_demod_state(self):
        # v0.5.2: clear discriminator phase when changing devices/frequencies.
        # This avoids stale I/Q state crossing between SDRplay beta and stable radios.
        self._last_iq = None
        self._mix_phase = 0.0

    def push_iq(self, iq):
        if iq is None:
            return
        self.push_iq_blocks([iq])

    def push_iq_blocks(self, blocks):
        if not self.enabled:
            return
        if self.mode not in ("AM", "NFM", "WFM", "USB", "LSB", "CW-U", "CW-L"):
            self.status = f"Audio: {self.mode} not implemented"
            return
        pushed = 0
        try:
            for iq in blocks or []:
                if iq is None or len(iq) < 4096:
                    continue
                audio = self._demod(iq)
                if audio is None or len(audio) == 0:
                    continue
                # Low latency without starving. Drop oldest chunks only at full queue.
                while self._q.full():
                    try:
                        self._q.get_nowait()
                    except Exception:
                        break
                self._q.put_nowait(audio.astype(np.float32, copy=False))
                pushed += 1
            self.queued_chunks = self._q.qsize()
            self.status = "Audio: ON"
        except Exception as e:
            self.status = f"Audio demod error: {e}"

    def _demod(self, iq):
        x = iq.astype(np.complex64, copy=False)
        x = self._shift_to_requested_center(x)
        mode = self.mode
        if mode == "AM":
            y = self._demod_am(x)
        elif mode in ("USB", "LSB", "CW-U", "CW-L"):
            y = self._demod_ssb(x, mode)
        elif mode == "NFM":
            x = x - np.mean(x)
            y = self._demod_nfm(x)
        elif mode == "WFM":
            x = x - np.mean(x)
            xc = self._fft_channel_filter(x, cutoff_hz=120000.0)
            y = self._fm_discriminator(xc)
            if str(getattr(self, "device_profile", "")).strip().lower() == "sdrplay":
                # v0.4.6: SDRplay WFM could recover audio but with persistent
                # hash/static riding on top.  Broadcast FM normally uses 75 us
                # de-emphasis in North America; apply it only to the SDRplay
                # path so HackRF/RTL behavior remains unchanged.
                y = y - np.mean(y)
                y = self._deemphasis(y, float(self.sample_rate), tau=75e-6)
                y = self._lowpass_fir_real(y, float(self.sample_rate), cutoff_hz=15000.0, taps=121)
                self.demod_status = "demod: WFM SDRplay deemph/LP"
            else:
                self.demod_status = "demod: WFM basic"
            y = self._resample_to_audio(y, self.sample_rate)
        else:
            return None
        if y is None or len(y) < 16:
            return None
        y = y.astype(np.float32, copy=False)
        y = y - np.mean(y)
        # Audio high-pass-ish DC cleanup.
        y = self._remove_slow_dc(y)
        # Use RMS normalization rather than peak-only normalization so speech is audible
        # without every click/pop forcing the gain down.
        rms = float(np.sqrt(np.mean(y * y)) + 1e-9)
        target = max(0.02, self.volume * 0.30)
        # HF/AM voice often arrives much lower than WFM/NFM.  Allow a little
        # more makeup gain for these modes, but keep hard clipping protection.
        max_gain = 55.0 if mode in ("AM", "USB", "LSB", "CW-U", "CW-L") else 30.0
        gain = min(max_gain, target / rms)
        y = y * gain
        # Soft limiter before final clip reduces harshness on AM/SSB peaks.
        y = np.tanh(y * 1.25) / np.tanh(1.25)
        return np.clip(y, -0.95, 0.95)

    def _demod_am(self, x):
        # v0.1.35 AM path: remove the FFT brick-wall channel filter used in
        # v0.1.34. That filter worked for tuning validation, but block-edge
        # artifacts could sound like a rhythmic helicopter/popping noise.
        #
        # Here we use a lighter streaming-style envelope detector:
        #   1) requested AM channel has already been shifted to center,
        #   2) take magnitude/envelope,
        #   3) smooth carrier reference across buffers,
        #   4) decimate by block averaging,
        #   5) voice low-pass and resample to 48 kHz.
        # This should be much less prone to bursty/popping artifacts and is
        # cheaper than FFT filtering.
        env = np.abs(x.astype(np.complex64, copy=False)).astype(np.float32)
        if len(env) == 0:
            return env

        # Carrier reference: use a low percentile so voice peaks do not drive
        # the AGC. Smooth slowly so the reference does not pump every buffer.
        block_carrier = float(np.percentile(env, 35) + 1e-9)
        if self._am_carrier_level <= 1e-9:
            self._am_carrier_level = block_carrier
        else:
            self._am_carrier_level = 0.992 * self._am_carrier_level + 0.008 * block_carrier

        y = (env / (self._am_carrier_level + 1e-9)) - 1.0
        y = y.astype(np.float32, copy=False)

        # Efficient decimation before audio filtering. Envelope audio is slow,
        # so averaging blocks is acceptable here and greatly lowers CPU load.
        fs = float(self.sample_rate)
        decim = max(1, int(round(fs / 100000.0)))
        if decim > 1 and len(y) >= decim * 4:
            usable = (len(y) // decim) * decim
            y = y[:usable].reshape(-1, decim).mean(axis=1).astype(np.float32)
            fs2 = fs / decim
        else:
            fs2 = fs

        y = y - float(np.mean(y))
        # AM broadcast can use more audio bandwidth than ham AM; 5 kHz is a
        # safe compromise while still working for 2.4 kHz ham AM tests.
        y = self._lowpass_fir_real(y, fs2, cutoff_hz=5000.0, taps=121)
        y = self._resample_to_audio(y, fs2)
        self.demod_status = f"demod: AM envelope no-FFT voice 5.0k shift {self.tuning_offset_hz/1000:+.1f}k decim {decim}"
        return y

    def _demod_ssb(self, x, mode):
        # Basic HF voice SSB/CW demod. If the display is set to the suppressed
        # carrier frequency, USB energy is just above 0 Hz; LSB energy is just
        # below 0 Hz. We keep only the selected sideband and take the real audio.
        x = x.astype(np.complex64, copy=False) - np.mean(x)
        upper = mode in ("USB", "CW-U")
        if mode in ("CW-U", "CW-L"):
            low_hz, high_hz = 300.0, 1200.0
        else:
            low_hz, high_hz = 250.0, 3000.0
        xb = self._fft_sideband_filter(x, low_hz=low_hz, high_hz=high_hz, upper=upper)
        y = np.real(xb).astype(np.float32)
        # Speech smoothing before resampling.
        y = self._lowpass_fir_real(y, float(self.sample_rate), cutoff_hz=3400.0, taps=121)
        y = self._resample_to_audio(y, self.sample_rate)
        self.demod_status = f"demod: {mode} {low_hz:.0f}-{high_hz:.0f} Hz sideband"
        return y

    def _shift_to_requested_center(self, x):
        # If the RF LO is intentionally offset above the displayed frequency by
        # tuning_offset_hz, the desired signal appears at -tuning_offset_hz in
        # baseband. Mix it back to 0 Hz before AM/NFM demodulation.
        off = float(getattr(self, "tuning_offset_hz", 0.0))
        if abs(off) < 1.0 or len(x) == 0:
            return x
        n = np.arange(len(x), dtype=np.float32)
        phase_inc = 2.0 * np.pi * off / float(self.sample_rate)
        phase = self._mix_phase + phase_inc * n
        osc = np.exp(1j * phase).astype(np.complex64)
        self._mix_phase = float((self._mix_phase + phase_inc * len(x)) % (2.0 * np.pi))
        return (x * osc).astype(np.complex64, copy=False)

    def _demod_nfm(self, x):
        fs = float(self.sample_rate)
        is_sdrplay = str(getattr(self, "device_profile", "")).strip().lower() == "sdrplay"
        # NOAA/NFM needs the center channel, not the whole 2 MHz view.
        # 18 kHz passes 5 kHz deviation plus voice bandwidth but rejects most noise.
        cutoff = 18000.0
        if is_sdrplay:
            # v0.4.6: the FFT brick-wall channel filter was useful for fast
            # validation but can create block-edge ringing/popping.  For SDRplay
            # use a gentler FIR channel filter before the discriminator.
            cutoff = 16500.0
            xc = self._lowpass_fir_complex(x, fs, cutoff_hz=cutoff, taps=181)
        else:
            xc = self._fft_channel_filter(x, cutoff_hz=cutoff)
        # Decimate to a sane discriminator rate. The channel filtering above
        # rejects most aliases; simple stride is acceptable for this stage.
        decim = max(1, int(round(fs / 200000.0)))
        xc = xc[::decim]
        fs2 = fs / decim
        y = self._fm_discriminator(xc)
        # Remove residual DC from discriminator.
        y = y - np.mean(y)
        if is_sdrplay:
            # v0.4.6: two-stage cleanup for the SDRplay path only.  First limit
            # short impulse pops, then de-emphasize and low-pass voice audio.
            y = self._despike_real(y, sigma=4.8)
        # NOAA/NFM de-emphasis. 750 us is common for NFM receiver audio.
        y = self._deemphasis(y, fs2, tau=750e-6)
        # Voice-band smoothing before final audio rate.
        if is_sdrplay:
            y = self._lowpass_fir_real(y, fs2, cutoff_hz=3200.0, taps=151)
            y = self._despike_real(y, sigma=5.5)
        else:
            y = self._lowpass_fir_real(y, fs2, cutoff_hz=4500.0, taps=101)
        y = self._resample_to_audio(y, fs2)
        self.demod_status = f"demod: NFM center {cutoff/1000:.1f}k shift {self.tuning_offset_hz/1000:+.1f}k decim {decim} fs {fs2/1000:.0f}k"
        return y

    def _fm_discriminator(self, x):
        x = x.astype(np.complex64, copy=False)
        # Protect against zeros making phase noisy.
        if self._last_iq is not None:
            x2 = np.concatenate((np.asarray([self._last_iq], dtype=np.complex64), x))
        else:
            x2 = x
        if len(x2) < 2:
            return np.zeros(0, dtype=np.float32)
        self._last_iq = complex(x[-1])
        y = np.angle(x2[1:] * np.conj(x2[:-1])).astype(np.float32)
        return y

    def _fft_channel_filter(self, x, cutoff_hz):
        # Centered baseband low-pass using FFT masking. This is simple and robust
        # for validation; later we can replace it with a faster streaming FIR.
        n = len(x)
        if n < 1024:
            return x
        spec = np.fft.fft(x)
        freqs = np.fft.fftfreq(n, d=1.0 / float(self.sample_rate))
        mask = np.abs(freqs) <= float(cutoff_hz)
        spec *= mask
        return np.fft.ifft(spec).astype(np.complex64)

    def _fft_sideband_filter(self, x, low_hz=250.0, high_hz=3000.0, upper=True):
        # Keep only the desired analytic sideband. This is validation-quality DSP:
        # clear and robust, not yet optimized as a streaming FIR.
        n = len(x)
        if n < 1024:
            return x
        spec = np.fft.fft(x)
        freqs = np.fft.fftfreq(n, d=1.0 / float(self.sample_rate))
        low = float(low_hz)
        high = float(high_hz)
        if upper:
            mask = (freqs >= low) & (freqs <= high)
        else:
            mask = (freqs <= -low) & (freqs >= -high)
        spec *= mask
        return np.fft.ifft(spec).astype(np.complex64)

    def _despike_real(self, y, sigma=6.0):
        y = y.astype(np.float32, copy=False)
        if len(y) < 64:
            return y
        med = float(np.median(y))
        mad = float(np.median(np.abs(y - med)) + 1e-9)
        limit = float(sigma) * 1.4826 * mad
        if limit <= 1e-8:
            return y
        return np.clip(y, med - limit, med + limit).astype(np.float32)

    def _lowpass_fir_real(self, y, fs, cutoff_hz, taps=101):
        y = y.astype(np.float32, copy=False)
        if len(y) < taps or cutoff_hz <= 0:
            return y
        taps = int(taps) | 1
        fc = min(0.45, float(cutoff_hz) / float(fs))
        n = np.arange(taps, dtype=np.float32) - (taps - 1) / 2.0
        h = 2.0 * fc * np.sinc(2.0 * fc * n)
        h *= np.hamming(taps).astype(np.float32)
        h /= np.sum(h) + 1e-12
        return np.convolve(y, h.astype(np.float32), mode="same").astype(np.float32)

    def _lowpass_fir_complex(self, x, fs, cutoff_hz, taps=101):
        x = x.astype(np.complex64, copy=False)
        if len(x) < taps or cutoff_hz <= 0:
            return x
        taps = int(taps) | 1
        fc = min(0.45, float(cutoff_hz) / float(fs))
        n = np.arange(taps, dtype=np.float32) - (taps - 1) / 2.0
        h = 2.0 * fc * np.sinc(2.0 * fc * n)
        h *= np.hamming(taps).astype(np.float32)
        h /= np.sum(h) + 1e-12
        yr = np.convolve(np.real(x).astype(np.float32), h.astype(np.float32), mode="same")
        yi = np.convolve(np.imag(x).astype(np.float32), h.astype(np.float32), mode="same")
        return (yr + 1j * yi).astype(np.complex64)

    def _deemphasis(self, y, fs, tau=750e-6):
        y = y.astype(np.float32, copy=False)
        if len(y) == 0:
            return y
        dt = 1.0 / float(fs)
        alpha = dt / (float(tau) + dt)
        out = np.empty_like(y, dtype=np.float32)
        prev = float(self._deemph_y)
        for i, v in enumerate(y):
            prev = prev + alpha * (float(v) - prev)
            out[i] = prev
        self._deemph_y = prev
        return out

    def _remove_slow_dc(self, y):
        if len(y) < 32:
            return y
        # Remove a moving average around 20 ms when enough samples exist.
        win = max(8, min(len(y) // 2, int(self.audio_rate * 0.02)))
        kernel = np.ones(win, dtype=np.float32) / float(win)
        baseline = np.convolve(y.astype(np.float32, copy=False), kernel, mode="same")
        return (y - baseline).astype(np.float32)

    def _resample_to_audio(self, y, source_rate):
        if len(y) < 16:
            return y.astype(np.float32)
        target_len = max(1, int(round(len(y) * self.audio_rate / float(source_rate))))
        xp = np.linspace(0.0, 1.0, len(y), endpoint=False)
        xnew = np.linspace(0.0, 1.0, target_len, endpoint=False)
        return np.interp(xnew, xp, y).astype(np.float32)

    def _audio_callback(self, outdata, frames, time_info, status):
        out = np.zeros(frames, dtype=np.float32)
        idx = 0
        if len(self._pending):
            n = min(frames, len(self._pending))
            out[:n] = self._pending[:n]
            self._pending = self._pending[n:]
            idx = n
        while idx < frames:
            try:
                chunk = self._q.get_nowait()
            except Exception:
                break
            n = min(frames - idx, len(chunk))
            out[idx:idx+n] = chunk[:n]
            idx += n
            if n < len(chunk):
                self._pending = chunk[n:].astype(np.float32, copy=False)
                break
        if idx < frames:
            self.underruns += 1
        # User-facing audio activity meter.  Use RMS and smooth it so the UI
        # LED blinks with voice/noise without exposing buffer diagnostics.
        try:
            rms = float(np.sqrt(np.mean(out * out)) + 1e-12)
            level = max(0.0, min(1.0, rms * 8.0))
            self.audio_level = max(level, float(getattr(self, "audio_level", 0.0)) * 0.78)
        except Exception:
            self.audio_level = 0.0
        outdata[:, 0] = out

    def _clear(self):
        self._pending = np.zeros(0, dtype=np.float32)
        self._last_iq = None
        self._deemph_y = 0.0
        self._am_carrier_level = 0.0
        self.underruns = 0
        self.audio_level = 0.0
        try:
            while True:
                self._q.get_nowait()
        except Exception:
            pass
