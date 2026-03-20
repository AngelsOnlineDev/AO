@echo off
echo ============================================
echo   Angels Online Private Server
echo ============================================
echo.

echo Installing Python dependencies...
pip install lzallright >nul 2>&1
if errorlevel 1 (
    echo ERROR: Failed to install dependencies. Make sure Python and pip are in your PATH.
    pause
    exit /b 1
)
echo Dependencies OK.
echo.

echo Starting server on 127.0.0.1...
echo   Login:  port 16768
echo   World:  port 27901
echo   File:   port 21238
echo.
echo Press Ctrl+C to stop.
echo.

python src\server.py
if errorlevel 1 (
    echo.
    echo Server exited with an error. Check logs\server.log for details.
    pause
)
