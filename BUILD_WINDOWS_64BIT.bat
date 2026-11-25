@echo off
REM Build aminbiography.exe on 64-bit Windows with 64-bit Python.
py -c "import struct; print('Python architecture:', struct.calcsize('P') * 8, 'bit')"
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
py -m pip install pyinstaller
py -m PyInstaller --noconfirm --clean --windowed --name "aminbiography" --collect-all PySide6 main.py
echo.
echo Build output: dist\aminbiography\aminbiography.exe
pause
