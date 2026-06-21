# RFI Autologger for SDR by N4EAC

**Version:** `1.0.0-rc1a`  
**Platform:** Windows 10/11  
**Status:** First Release Candidate

RFI Autologger for SDR by N4EAC is a field-oriented RF interference logging tool for SDR receivers. It is designed to help locate and document RF interference by receiving a signal, measuring tuned-channel signal level in dBFS, recording GPS position, and exporting logs to CSV and Google Earth KML.

The interface is intentionally simple and instrument-like: frequency, mode, signal value, GPS status, logging status, gain controls, and SDR selection.

---

## Supported SDR Receivers

### Stable
- **HackRF One**
- **RTL-SDR** compatible dongles

### Beta
- **SDRplay** receivers using the official SDRplay API

SDRplay support is functional but still marked beta. For SDRplay, connect the receiver before launching the application. Hot-plug detection is a known limitation.

---

## Main Features

- SDR receiver selection: HackRF, RTL-SDR, SDRplay
- Modes: AM, NFM, WFM, USB, LSB, CW-U, CW-L
- Live audio monitor
- Frequency entry with Enter-to-tune
- Tuning step buttons
- LNA/VGA gain controls
- AMP and ATT -10 dB controls
- GPS serial connection support
- CSV logging with GPS, frequency, mode, signal, and gain data
- KML export for Google Earth
- Editable KML color thresholds based on `Signal_dBFS`
- Tuned-channel pre-demod IQ dBFS measurement for better RF mapping
- Dark military-style field UI
- Clean shutdown when closing with the Windows X button

---

## Recommended First Tests

Before field use, test each receiver with a known local signal.

### NOAA Weather Radio
Use NFM mode:

```text
162.400 MHz
162.425 MHz
162.450 MHz
162.475 MHz
162.500 MHz
162.525 MHz
162.550 MHz
```

### FM Broadcast
Use WFM mode with a strong local FM station, for example:

```text
94.700000 MHz
100.100000 MHz
```

---

## Recommended Baseline Gain Settings

For comparable RFI maps, keep gain settings fixed during each survey.

### HackRF
```text
LNA: 16 dB
VGA: 16 dB
AMP: OFF
ATT: OFF
```

### RTL-SDR
```text
Gain: around 20–30 dB
ATT: OFF
```

### SDRplay
```text
LNA: 15
VGA: 0
ATT: OFF
```

Use ATT only when a very strong signal appears to overload the receiver or compress the signal range.

---

## Logging

CSV logging records values such as:

- UTC timestamp
- Latitude
- Longitude
- Frequency
- Mode
- Signal_dBFS
- SDR type
- Gain values
- ATT state

The signal value is based on tuned-channel pre-demod IQ dBFS so it is more useful for RF mapping than post-demod audio level.

---

## KML Export

KML export creates color-coded map points for Google Earth using `Signal_dBFS`.

Default recommended thresholds after testing:

```text
Green   < -60 dBFS
Yellow  -60 to -50 dBFS
Orange  -50 to -40 dBFS
Red     > -40 dBFS
```

Thresholds are editable in the application before exporting KML.

KML placemarks are intentionally compact. The pin color shows signal category and the placemark includes practical values such as frequency, mode, timestamp, and dBFS.

---

## Known Limitations

- SDRplay is beta.
- SDRplay must be connected before launching the app.
- SDRplay hot-plug / refresh recovery is not reliable yet.
- dBFS is not dBm. It is useful for relative RF mapping but depends on SDR type and gain settings.
- For best RFI survey results, do not change LNA/VGA/AMP/ATT during a logging run.

---

## Build Instructions

From the source folder on Windows:

```bat
run_dev.bat
```

To build a standalone EXE:

```bat
build_exe.bat
```

The generated executable name is:

```text
RFI_Autologger_for_SDR_by_N4EAC_v1.0.0-rc1aa.exe
```

---

## Dependencies

Python packages are listed in `requirements.txt`.

Runtime SDR dependencies included or expected:

- HackRF: `hackrf.dll`, `libusb-1.0.dll`, `pthreadVC2.dll`
- RTL-SDR: `rtlsdr.dll`, `libusb-1.0.dll`
- SDRplay: install the official SDRplay API for Windows. The app prefers the official installed API and does not rely on a bundled SDRplay DLL.

---

## Project Purpose

This tool is not intended to replace a full SDR program such as SDR++, SDR#, or SDRuno. Its purpose is narrower:

```text
Receive → Listen → Measure → GPS Log → Map
```

The goal is practical RF interference hunting in the field.

## Installation
Download the zip file. Unzip it in your desired directory. Create a shortcut from there to run the program.
Make sure RTL-SDR driver is installed for RTL-SDR radio. Also ensure SDRPlay API is installed if using SDRPlay. HackRF One dll already included in the zip file.
---

## Credits

Created through iterative testing and development with N4EAC.

