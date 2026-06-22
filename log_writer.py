import csv
from xml.sax.saxutils import escape
from datetime import datetime, timezone
from pathlib import Path

class LogWriter:
    def __init__(self, output_dir):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.csv_path = self.output_dir / f"rfi_auto_logger_sdr_{stamp}.csv"
        self.rows = []
        self.active = False

    def start(self):
        self.active = True
        if not self.csv_path.exists():
            with self.csv_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["UTC Time", "Latitude", "Longitude", "Frequency", "Mode", "Signal_dBFS", "LNA", "VGA", "AMP"])

    def stop(self):
        self.active = False

    def write(self, lat, lon, frequency_hz, mode, signal_dbfs, lna, vga, amp):
        if not self.active:
            return
        utc = datetime.now(timezone.utc).isoformat()
        row = [utc, lat, lon, frequency_hz, mode, round(signal_dbfs, 2), lna, vga, int(bool(amp))]
        self.rows.append(row)
        with self.csv_path.open("a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(row)

    def save_kml(self, thresholds=None):
        """Export the active CSV log as a color-coded KML file.

        Color bands use Signal_dBFS from the CSV. Thresholds are user-editable
        because dBFS varies with SDR model, gain, antenna, and band.
        """
        thresholds = thresholds or {}
        show_signal_labels = bool(thresholds.get("show_signal_labels", True))
        try:
            green_below = float(thresholds.get("green_below", -60.0))
            yellow_below = float(thresholds.get("yellow_below", -45.0))
            orange_below = float(thresholds.get("orange_below", -30.0))
        except Exception:
            green_below, yellow_below, orange_below = -60.0, -45.0, -30.0

        # Keep thresholds ordered even if the user enters unusual values.
        vals = sorted([green_below, yellow_below, orange_below])
        green_below, yellow_below, orange_below = vals[0], vals[1], vals[2]

        kml_path = self.csv_path.with_suffix(".kml")
        points = []
        if self.csv_path.exists():
            with self.csv_path.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    try:
                        if r["Latitude"] in ("", "None") or r["Longitude"] in ("", "None"):
                            continue
                        float(r["Latitude"])
                        float(r["Longitude"])
                        float(r["Signal_dBFS"])
                        points.append(r)
                    except Exception:
                        pass

        def style_for(db):
            """Return (style_id, KML aabbggrr color) for a dBFS value."""
            db = float(db)
            # KML color format is aabbggrr.
            if db >= orange_below:
                return "red", "ff0000ff"
            if db >= yellow_below:
                return "orange", "ff00a5ff"
            if db >= green_below:
                return "yellow", "ff00ffff"
            return "green", "ff00ff00"

        styles = {
            "green": "ff00ff00",
            "yellow": "ff00ffff",
            "orange": "ff00a5ff",
            "red": "ff0000ff",
        }

        with kml_path.open("w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            f.write('<kml xmlns="http://www.opengis.net/kml/2.2"><Document>\n')
            f.write('<name>RFI Autologger Signal Map</name>\n')
            desc = (
                f"Color bands use Signal_dBFS from the CSV: Green &lt; {green_below:g}, "
                f"Yellow {green_below:g} to {yellow_below:g}, "
                f"Orange {yellow_below:g} to {orange_below:g}, "
                f"Red &gt;= {orange_below:g}."
            )
            f.write(f'<description>{escape(desc)}</description>\n')

            for style_id, color in styles.items():
                f.write(f'<Style id="{style_id}">')
                f.write(f'<IconStyle><color>{color}</color><scale>0.85</scale>')
                f.write('<Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png</href></Icon>')
                f.write('</IconStyle></Style>\n')

            for p in points:
                db = float(p["Signal_dBFS"])
                style_id, _color = style_for(db)
                freq = p.get("Frequency", "")
                mode = p.get("Mode", "")
                utc = p.get("UTC Time", "")
                try:
                    freq_mhz = f"{float(freq) / 1_000_000:.6f} MHz"
                except Exception:
                    freq_mhz = f"{freq} Hz"
                f.write('<Placemark>\n')
                # Optional visible map label.  When disabled, Google Earth shows
                # color pins without overwhelming signal text on the map.
                label = f"{db:.2f} dBFS" if show_signal_labels else ""
                f.write(f'<name>{escape(label)}</name>\n')
                f.write(f'<styleUrl>#{style_id}</styleUrl>\n')
                f.write('<description><![CDATA[')
                f.write(f'<b>UTC:</b> {utc}<br/>')
                f.write(f'<b>Frequency:</b> {freq_mhz}<br/>')
                f.write(f'<b>Mode:</b> {mode}<br/>')
                f.write(f'<b>Signal:</b> {db:.2f} dBFS<br/>')
                f.write(']]></description>\n')
                f.write(f'<Point><coordinates>{p["Longitude"]},{p["Latitude"]},0</coordinates></Point>\n')
                f.write('</Placemark>\n')
            f.write('</Document></kml>\n')
        return kml_path
