@echo off
cd /d "%~dp0"
echo ======================================================================
echo       SENTINEL-SWING : TRADINGVIEW INCREMENTAL DB UPDATE
echo ======================================================================
echo.
echo This script checks the latest candle date for each stock in SQLite
echo against today's date and fetches ONLY the missing delta candles.
echo Stocks that are already up-to-date will be skipped automatically.
echo.
echo Select Timeframe to Update:
echo   [1] 4-Hour  (4H)   - Swing Trading Champion Strategy (Recommended)
echo   [2] Daily   (1D)   - Long-Term Macro Trend
echo   [3] Both    (4H & 1D) - Complete Swing Universe
echo   [4] 1-Hour  (1H)   - Base Intraday Swing
echo   [5] 15-Min  (15M)  - Granular Execution
echo   [6] Force Check All (4H) - Re-evaluates every stock regardless of cache
echo.
set /p tf_choice="Enter choice [1-6] (default is 1 for 4H): "

if "%tf_choice%"=="1" set TF_ARG=--timeframe 4H
if "%tf_choice%"=="2" set TF_ARG=--timeframe 1D
if "%tf_choice%"=="3" set TF_ARG=--timeframe 4H,1D
if "%tf_choice%"=="4" set TF_ARG=--timeframe 1H
if "%tf_choice%"=="5" set TF_ARG=--timeframe 15M
if "%tf_choice%"=="6" set TF_ARG=--timeframe 4H --force
if "%TF_ARG%"=="" set TF_ARG=--timeframe 4H

set PYTHON_EXE=C:\Users\Advait\AppData\Local\Programs\Python\Python312\python.exe
if not exist "%PYTHON_EXE%" (
    set PYTHON_EXE=python
)

echo.
echo ======================================================================
echo Running incremental update: %TF_ARG%
echo ======================================================================
echo.
"%PYTHON_EXE%" update_market_data.py %TF_ARG%
echo.
pause

