@echo off
cd /d "%~dp0"
echo ======================================================================
echo       SENTINEL-SWING : TRADINGVIEW NIFTY 500 HISTORICAL SYNC
echo ======================================================================
echo.
echo Step 1: Select Timeframe:
echo   [1] 15-Minute  (15M)  - Granular Execution & 09:45 AM Entry
echo   [2] 1-Hour     (1H)   - Base Swing Timeframe (~3.8 yrs max)
echo   [3] 4-Hour     (4H)   - Intermediate Trend (~13 yrs max)
echo   [4] Daily      (1D)   - Long-Term Macro Trend (~20 yrs max)
echo.
set /p tf_choice="Enter timeframe choice [1-4] (default is 1 for 15M): "

if "%tf_choice%"=="1" set TIMEFRAME=15M
if "%tf_choice%"=="2" set TIMEFRAME=1H
if "%tf_choice%"=="3" set TIMEFRAME=4H
if "%tf_choice%"=="4" set TIMEFRAME=1D
if "%TIMEFRAME%"=="" set TIMEFRAME=15M

echo.
echo Step 2: Select Number of Bars per Stock (Max 5000):
echo   [1] 500 Bars   - Quick Test
echo   [2] 1000 Bars  - Moderate Depth
echo   [3] 2500 Bars  - Deep History
echo   [4] 5000 Bars  - Maximum Available
echo.
set /p bar_choice="Enter bars choice [1-4] (default is 4 for Max 5000): "

if "%bar_choice%"=="1" set BARS=500
if "%bar_choice%"=="2" set BARS=1000
if "%bar_choice%"=="3" set BARS=2500
if "%bar_choice%"=="4" set BARS=5000
if "%BARS%"=="" set BARS=5000

set PYTHON_EXE=C:\Users\Advait\AppData\Local\Programs\Python\Python312\python.exe
if not exist "%PYTHON_EXE%" (
    set PYTHON_EXE=python
)

echo.
echo ======================================================================
echo Syncing %TIMEFRAME% data (%BARS% bars) for Nifty 500 stocks...
echo ======================================================================
echo.
"%PYTHON_EXE%" sync_tradingview_nifty500.py --timeframe %TIMEFRAME% --bars %BARS%
pause
