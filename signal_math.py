import numpy as np

BLOCK_RMS_FLOOR = 1e-12

def condition_iq(iq: np.ndarray, dc_correction: bool = True, iq_correction: bool = True):
    if iq is None or len(iq) == 0:
        return iq
    x = iq.astype(np.complex64, copy=False)
    if dc_correction:
        x = x - np.mean(x)
    # Lightweight I/Q balance correction: equalize I and Q RMS and remove quadrature DC/correlation bias.
    if iq_correction and len(x) > 16:
        i = np.real(x).astype(np.float32)
        q = np.imag(x).astype(np.float32)
        ir = float(np.sqrt(np.mean(i * i)) + 1e-12)
        qr = float(np.sqrt(np.mean(q * q)) + 1e-12)
        scale = np.sqrt(ir / qr)
        i = i / scale
        q = q * scale
        x = (i + 1j * q).astype(np.complex64)
    return x

def iq_stats(iq: np.ndarray, dc_correction: bool = True, iq_correction: bool = True):
    if iq is None or len(iq) == 0:
        return {"samples": 0, "peak": 0.0, "rms": 0.0, "dbfs": -120.0}
    x = condition_iq(iq, dc_correction, iq_correction)
    mag = np.abs(x.astype(np.complex64))
    peak = float(np.max(mag))
    rms = float(np.sqrt(np.mean(mag ** 2)))
    dbfs = 20.0 * np.log10(max(rms, BLOCK_RMS_FLOOR))
    dbfs = float(max(min(dbfs, 0.0), -120.0))
    return {"samples": int(len(x)), "peak": peak, "rms": rms, "dbfs": dbfs}

def dbfs_from_iq(iq: np.ndarray, dc_correction: bool = True, iq_correction: bool = True) -> float:
    return iq_stats(iq, dc_correction, iq_correction)["dbfs"]



def channel_dbfs_from_iq(iq: np.ndarray, sample_rate: int = 2_000_000, mode: str = "NFM", dc_correction: bool = True, iq_correction: bool = True) -> float:
    """Estimate tuned-channel RF level in dBFS from pre-demod IQ.

    Earlier builds used wideband IQ RMS.  That made the meter mostly report the
    receiver/wideband noise floor, so a quiet frequency and a real channel could
    look nearly identical.  For RFI mapping we want the power in the tuned
    channel.  This function applies a lightweight ideal FFT channel mask and
    measures the RMS of the filtered complex IQ before any demodulator/audio
    processing.

    It is not calibrated dBm; it is a practical relative dBFS signal level for
    comparing points recorded with the same SDR/gain/antenna settings.
    """
    if iq is None or len(iq) < 1024:
        return -120.0
    try:
        fs = max(1.0, float(sample_rate or 2_000_000))
        x = condition_iq(iq, dc_correction, iq_correction)
        n = min(len(x), 65536)
        # power-of-two-ish chunk keeps FFT cost predictable and stable
        x = x[-n:].astype(np.complex64, copy=False)
        mode_u = str(mode or "").upper()
        freqs = np.fft.fftfreq(n, d=1.0 / fs)

        if mode_u == "WFM":
            bw = 200_000.0
            mask = np.abs(freqs) <= (bw / 2.0)
        elif mode_u == "NFM":
            bw = 25_000.0
            mask = np.abs(freqs) <= (bw / 2.0)
        elif mode_u == "AM":
            bw = 10_000.0
            mask = np.abs(freqs) <= (bw / 2.0)
        elif mode_u in ("USB", "CW-U"):
            mask = (freqs >= 150.0) & (freqs <= 3_200.0)
        elif mode_u in ("LSB", "CW-L"):
            mask = (freqs <= -150.0) & (freqs >= -3_200.0)
        else:
            bw = 25_000.0
            mask = np.abs(freqs) <= (bw / 2.0)

        spec = np.fft.fft(x)
        spec[~mask] = 0
        y = np.fft.ifft(spec).astype(np.complex64)
        rms = float(np.sqrt(np.mean(np.abs(y) ** 2)))
        dbfs = 20.0 * np.log10(max(rms, BLOCK_RMS_FLOOR))
        return float(max(min(dbfs, 0.0), -120.0))
    except Exception:
        return dbfs_from_iq(iq, dc_correction, iq_correction)

def spectrum_bars(iq: np.ndarray, bars: int = 31, dc_correction: bool = True, iq_correction: bool = True):
    if iq is None or len(iq) < bars:
        return [0] * bars
    x = condition_iq(iq, dc_correction, iq_correction)
    n = min(len(x), 16384)
    x = x[-n:]
    window = np.hanning(len(x)).astype(np.float32)
    spec = np.fft.fftshift(np.fft.fft(x * window))
    power = 20 * np.log10(np.abs(spec) / max(np.sum(window), 1e-9) + 1e-12)

    # v0.1.12 receiver validation: do NOT notch out the center. A real tuned
    # carrier such as NOAA/NFM appears near the center, so hiding the DC bin made
    # the display look falsely flat. We use a high percentile/max-like value per
    # bar so narrow carriers are visible.
    chunks = np.array_split(power, bars)
    vals = np.array([np.percentile(c, 99) for c in chunks])
    noise = float(np.percentile(vals, 20))
    top = max(float(np.max(vals)), noise + 12.0)
    # Display roughly 35 dB of visual dynamic range above the local floor.
    levels = np.round(((vals - noise) / max(top - noise, 1e-9)) * 7)
    levels = np.clip(levels, 0, 7).astype(int)
    return levels.tolist()

BAR_CHARS = "▁▂▃▄▅▆▇█"

def bars_to_text(levels):
    return "".join(BAR_CHARS[int(max(0, min(7, x)))] for x in levels)

def spectrum_analysis(iq: np.ndarray, sample_rate: int = 2_000_000, dc_correction: bool = True, iq_correction: bool = True):
    """Return validation diagnostics for the current IQ block.

    Values are relative dBFS-ish FFT bin powers for troubleshooting. The goal is
    not lab accuracy; it is to show whether a real peak exists near center and
    whether the demodulator should be hearing it.
    """
    if iq is None or len(iq) < 1024:
        return {
            "ok": False,
            "center_db": -120.0,
            "peak_db": -120.0,
            "noise_db": -120.0,
            "peak_offset_hz": 0.0,
            "center_minus_noise_db": 0.0,
            "peak_minus_noise_db": 0.0,
            "lock": "NO IQ",
        }
    x = condition_iq(iq, dc_correction, iq_correction)
    n = min(len(x), 65536)
    x = x[-n:]
    window = np.hanning(n).astype(np.float32)
    spec = np.fft.fftshift(np.fft.fft(x * window))
    # Power per bin normalized enough to be comparable on screen.
    power = 20.0 * np.log10((np.abs(spec) / max(float(np.sum(window)), 1e-9)) + 1e-12)
    center_idx = n // 2
    # Ignore the exact DC bin when finding strongest peak, because HackRF DC spur
    # or correction artifacts can otherwise look like a fake signal lock.
    guard_hz = 4000.0
    guard_bins = max(2, int(round(guard_hz / max(float(sample_rate) / n, 1.0))))
    search = power.copy()
    search[max(0, center_idx-guard_bins):min(n, center_idx+guard_bins+1)] = -300.0
    peak_idx = int(np.argmax(search))
    peak_db = float(power[peak_idx])
    center_span_hz = 15000.0
    center_bins = max(2, int(round(center_span_hz / max(float(sample_rate) / n, 1.0))))
    lo = max(0, center_idx - center_bins)
    hi = min(n, center_idx + center_bins + 1)
    center_db = float(np.percentile(power[lo:hi], 95))
    noise_db = float(np.percentile(power, 20))
    peak_offset_hz = (peak_idx - center_idx) * (float(sample_rate) / n)
    center_snr = center_db - noise_db
    peak_snr = peak_db - noise_db
    if center_snr >= 10.0:
        lock = "CENTER LOCK"
    elif abs(peak_offset_hz) <= 25000.0 and peak_snr >= 8.0:
        lock = "NEAR CENTER"
    elif peak_snr >= 10.0:
        lock = "PEAK OFF CENTER"
    else:
        lock = "NO CLEAR PEAK"
    return {
        "ok": True,
        "center_db": center_db,
        "peak_db": peak_db,
        "noise_db": noise_db,
        "peak_offset_hz": float(peak_offset_hz),
        "center_minus_noise_db": float(center_snr),
        "peak_minus_noise_db": float(peak_snr),
        "lock": lock,
    }
