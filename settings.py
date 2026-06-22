import json
from pathlib import Path
from version import APP_VERSION

SETTINGS_FILE = Path.home() / "Documents" / "RFI_Auto_Logger_SDR" / "settings.json"

DEFAULT_SETTINGS = {
    "version": APP_VERSION,
    "frequency_hz": 132_000_000,
    "ppm_correction": 0.0,
    "internal_lo_offset_hz": 10_000,
    "mode": "AM",
    "sample_rate": 2_000_000,
    "sdr_device": "HackRF",
    "spectrum_span_hz": 2_000_000,
    "lna_gain": 16,
    "vga_gain": 20,
    "amp_enabled": False,
    "atten_10db_enabled": False,
    "dc_correction": True,
    "iq_correction": True,
    "squelch_dbfs": -90.0,
    "volume": 50,
    "audio_enabled": True,
    "log_interval_sec": 1.0,
    "show_debug": False,
    "kml_green_below": -60.0,
    "kml_yellow_below": -45.0,
    "kml_orange_below": -30.0,
    "kml_signal_labels": True,
    "gps_port": "COM3",
    "gps_baud": 9600,
    "output_dir": str(Path.home() / "Documents" / "RFI_Auto_Logger_SDR" / "Logs"),
    "window_geometry": "1120x900",
}

def load_settings():
    try:
        if SETTINGS_FILE.exists():
            with SETTINGS_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            merged = DEFAULT_SETTINGS.copy()
            merged.update(data)
            return merged
    except Exception:
        pass
    return DEFAULT_SETTINGS.copy()

def save_settings(settings):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with SETTINGS_FILE.open("w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
