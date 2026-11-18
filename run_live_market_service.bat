@echo off
title Sentinel Swing Trader - Hourly Market Monitor
cd /d "c:\Users\Advait\Desktop\Auto Swing Trader"
echo =========================================================================
echo    SENTINEL SWING TRADER - HOURLY MARKET SERVICE & TELEGRAM DISPATCH
echo =========================================================================
echo Monitoring hourly checkpoints:
echo   - 10:15 AM IST (1h Post-Open Check)
echo   - 11:15 AM IST (Mid-Morning Check)
echo   - 12:15 PM IST (Noon Check)
echo   - 01:15 PM IST (4H Bar Sync Check)
echo   - 02:15 PM IST (Mid-Afternoon Check)
echo   - 03:15 PM IST (Pre-Close 15m Assessment)
echo   - 05:30 PM IST (Post-Market Close Settlement & CSV Ledger)
echo.
echo Telegram: Live status of open trades sent every hour.
echo Gmail: Sent ONLY if action needs to be taken (Buy or Stop-Loss Hit).
echo.
"C:\Users\Advait\AppData\Local\Programs\Python\Python312\python.exe" live_market_service.py
pause
