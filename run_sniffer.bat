@echo off
echo ============================================
echo   Angels Online Game Sniffer
echo ============================================
echo.
echo NOTE: This tool requires Administrator privileges and Npcap.
echo   Download Npcap: https://npcap.com/#download
echo   Install with "WinPcap API-compatible Mode" checked.
echo.

net session >nul 2>&1
if errorlevel 1 (
    echo ERROR: Not running as Administrator.
    echo Right-click this file and select "Run as administrator".
    pause
    exit /b 1
)

echo Installing Python dependencies...
pip install scapy lzallright >nul 2>&1
if errorlevel 1 (
    echo ERROR: Failed to install dependencies. Make sure Python and pip are in your PATH.
    pause
    exit /b 1
)
echo Dependencies OK.
echo.

echo Starting packet capture...
echo Output log: logs\game_sniffer.log
echo Press Ctrl+C to stop.
echo.

python tools\game_sniffer.py
if errorlevel 1 (
    echo.
    echo Sniffer exited with an error. Check logs\game_sniffer.log for details.
    pause
)
