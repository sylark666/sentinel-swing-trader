@echo off
cd /d "%~dp0"
echo ======================================================================
echo           SENTINEL-SWING : MULTI-FACTOR OPPORTUNITY SCANNER
echo ======================================================================
echo.

set PYTHON_EXE=C:\Users\Advait\AppData\Local\Programs\Python\Python312\python.exe
if not exist "%PYTHON_EXE%" (
    set PYTHON_EXE=python
)

echo Scanning local database (market_data.db) for top swing opportunities...
echo.
"%PYTHON_EXE%" screener.py
echo.
pause

