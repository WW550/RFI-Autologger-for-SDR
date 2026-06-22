import threading
import time
from dataclasses import dataclass

try:
    import serial
except Exception:
    serial = None

@dataclass
class GPSFix:
    status: str = "NO GPS"
    latitude: float | None = None
    longitude: float | None = None
    utc_time: str | None = None
    utc_date: str | None = None

class GPSManager:
    def __init__(self, port="COM3", baud=9600):
        self.port = port
        self.baud = baud
        self.fix = GPSFix()
        self.running = False
        self.thread = None
        self._ser = None

    def start(self):
        if serial is None:
            self.fix.status = "NO GPS - pyserial not installed"
            return
        self.running = True
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        try:
            if self._ser:
                self._ser.close()
        except Exception:
            pass

    def _worker(self):
        while self.running:
            try:
                self.fix.status = "GPS SEARCHING"
                self._ser = serial.Serial(self.port, self.baud, timeout=1)
                while self.running:
                    line = self._ser.readline().decode(errors="ignore").strip()
                    if line.startswith("$GPRMC") or line.startswith("$GNRMC"):
                        self._parse_rmc(line)
            except Exception:
                self.fix = GPSFix(status="NO GPS")
                time.sleep(2)

    def _parse_rmc(self, line):
        parts = line.split(",")
        if len(parts) < 7 or parts[2] != "A":
            self.fix.status = "GPS SEARCHING"
            return
        lat = self._nmea_to_decimal(parts[3], parts[4])
        lon = self._nmea_to_decimal(parts[5], parts[6])
        if lat is not None and lon is not None:
            self.fix = GPSFix(status="GPS LOCKED", latitude=lat, longitude=lon, utc_time=parts[1], utc_date=parts[9] if len(parts) > 9 else None)

    def _nmea_to_decimal(self, value, hemi):
        if not value:
            return None
        dot = value.find(".")
        if dot < 0:
            return None
        deg_len = dot - 2
        deg = float(value[:deg_len])
        minutes = float(value[deg_len:])
        dec = deg + minutes / 60.0
        if hemi in ("S", "W"):
            dec *= -1
        return dec
