@echo off
setlocal
cd /d "%~dp0"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m PyInstaller --onefile --windowed ^
  --icon rfi_auto_logger_sdr.ico ^
  --name RFI_Autologger_for_SDR_by_N4EAC_v1.0.0-rc2 ^
  --add-binary "hackrf.dll;." ^
  --add-binary "libusb-1.0.dll;." ^
  --add-binary "pthreadVC2.dll;." ^
  --add-binary "rtlsdr.dll;." ^
  main.py
if not exist dist mkdir dist
copy /Y hackrf.dll dist\hackrf.dll >nul
copy /Y libusb-1.0.dll dist\libusb-1.0.dll >nul
copy /Y pthreadVC2.dll dist\pthreadVC2.dll >nul
copy /Y rtlsdr.dll dist\rtlsdr.dll >nul
rem SDRplay API is intentionally NOT bundled. Install official SDRplay API/driver.
copy /Y rfi_auto_logger_sdr.ico dist\rfi_auto_logger_sdr.ico >nul
echo.
echo Build complete. EXE and DLLs are in the dist folder.
pause
