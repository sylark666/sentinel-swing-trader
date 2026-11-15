@echo off
cd /d "%~dp0"
echo ======================================================================
echo           STARTING AUTO SWING TRADER CONSOLE DASHBOARD
echo ======================================================================
echo.

set PYTHON_EXE=C:\Users\Advait\AppData\Local\Programs\Python\Python312\python.exe

if not exist "%PYTHON_EXE%" (
    set PYTHON_EXE=python
)

echo Launching Streamlit on http://localhost:8501 ...
"%PYTHON_EXE%" -m streamlit run dashboard.py --server.port 8501

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Port 8501 may be occupied. Attempting fallback on http://localhost:8502 ...
    "%PYTHON_EXE%" -m streamlit run dashboard.py --server.port 8502
)

pause
