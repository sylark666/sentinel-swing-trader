@echo off
title Sentinel Swing Trader - Hourly Market Monitor
cd /d "c:\Users\Advait\Desktop\Auto Swing Trader"
echo =========================================================================
echo    SENTINEL SWING TRADER - HOURLY MARKET SERVICE & TELEGRAM DISPATCH
echo =========================================================================
echo Monitoring hourly checkpoints:
echo   - 09:45 AM IST (Checkpoint 1 - Morning Volatility Bypass & Open Status)
echo   - 10:45 AM IST (Checkpoint 2 - Mid-Morning Check)
echo   - 11:45 AM IST (Checkpoint 3 - Late Morning Check)
echo   - 12:45 PM IST (Checkpoint 4 - Mid-Day Check)
echo   - 01:45 PM IST (Checkpoint 5 - Post-13:15 4H Bar Sync)
echo   - 02:45 PM IST (Checkpoint 6 - Afternoon Momentum Check)
echo   - 03:20 PM IST (Checkpoint 7 - Pre-Close 10m Execution Call)
echo   - 05:30 PM IST (Checkpoint 8 - Post-Market Close Settlement & CSV Ledger)
echo.
echo Telegram: Live status of open trades sent every hour starting at 09:45 AM.
echo Gmail: Sent ONLY if action needs to be taken (Buy or Stop-Loss Hit).
echo.
"C:\Users\Advait\AppData\Local\Programs\Python\Python312\python.exe" live_market_service.py
pause
